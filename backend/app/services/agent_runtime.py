"""Agent Run 状态机：连接 AI 计划、策略引擎和银行沙箱。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ai.agent.orchestrator import handle_message
from ai.agent.llm_parser import PROMPT_DIGEST, PROMPT_VERSION
from ai.agent.planner import PLANNER_VERSION
from ai.agent.task_graph import (
    TaskGraphContractError,
    TaskNode,
    TaskNodeReceiptKind,
    TaskNodeStatus,
)
from backend.app.core.config import settings
from backend.app.errors import error_definition
from backend.app.repositories.memory import MemoryRepository, repository
from backend.app.services.bank_service import BankService
from backend.app.services.audit_integrity import audit_record_digest, next_chain_link
from backend.app.services.life_event_workflow import LifeEventWorkflow
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError
from backend.app.services.life_goal_conflict_service import LifeGoalConflictService
from backend.app.services.mfa_service import MfaCodeService
from backend.app.services.operation_passport_service import (
    PASSPORT_SCHEMA_VERSION,
    OperationPassportService,
    redact_sensitive,
)
from backend.app.services.policy_engine import POLICY_VERSION, evaluate_operation
from backend.app.services.task_graph_executor import (
    EXECUTOR_VERSION,
    TaskExecutionContext,
    TaskGraphExecutor,
    TaskNodeOutcome,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_snapshot(value: dict) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _format_money(value: float) -> str:
    return f"{value:,.2f} 元"


def _format_transaction_time(value: str) -> str:
    try:
        occurred_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return occurred_at.astimezone(BEIJING_TIMEZONE).strftime("%m月%d日 %H:%M")
    except (TypeError, ValueError):
        return "时间待核对"


def _format_bill_analysis(data: dict) -> str:
    summary = data["summary"]
    income = float(summary["income"])
    expense = float(summary["expense"])
    lines = [
        "本月账单体检已完成：",
        f"- 收入 {_format_money(income)}，支出 {_format_money(expense)}，净结余 {_format_money(income - expense)}。",
    ]

    categories = data.get("categories", [])[:3]
    if categories:
        category_text = "、".join(
            f"{item['name']} {_format_money(float(item['amount']))}（{float(item['percentage']):.1f}%）"
            for item in categories
        )
        lines.append(f"- 支出前三类：{category_text}。")

    abnormal = data.get("abnormal_transactions", [])
    if not abnormal:
        lines.append("- 本月未发现达到规则阈值的异常交易。")
    else:
        lines.append(f"- 发现 {len(abnormal)} 笔需要核对的异常交易：")
        for index, item in enumerate(abnormal, start=1):
            lines.append(
                f"  {index}. {item['merchant']}｜{_format_money(abs(float(item['amount'])))}｜"
                f"{_format_transaction_time(item['occurred_at'])}"
            )
            reasons = "；".join(item.get("anomaly_reasons", []))
            if reasons:
                lines.append(f"     原因：{reasons}。")
        lines.append("- 建议：先核对商户和交易时间；若非本人交易，请立即冻结相关卡片并联系银行。")

    return "\n".join(lines)


class AgentRuntime:
    def __init__(
        self,
        repo: MemoryRepository,
        mfa: MfaCodeService | None = None,
        life_graph_service: LifeGraphService | None = None,
        goal_conflict_service: LifeGoalConflictService | None = None,
    ) -> None:
        self.repo = repo
        self.bank = BankService(repo)
        self.mfa = mfa or MfaCodeService()
        self.life_graph = life_graph_service
        if self.life_graph is None and getattr(repo, "engine", None) is not None:
            self.life_graph = LifeGraphService(repo.engine)
        self.goal_conflicts = goal_conflict_service
        if self.goal_conflicts is None and self.life_graph is not None:
            self.goal_conflicts = LifeGoalConflictService(
                self.life_graph.engine, self.life_graph.repository
            )
        self.life_workflow = LifeEventWorkflow(self.life_graph, self.goal_conflicts)
        self.recover_incomplete_workflows()

    def recover_incomplete_workflows(self) -> int:
        recovered = 0
        for run in self.repo.runs.values():
            if run.get("intent", {}).get("name") != "life.event_plan":
                continue
            if run.get("state") not in {
                "WAITING_INPUT",
                "WAITING_CONFIRMATION",
                "WAITING_HUMAN",
            }:
                continue

            previous = run.get("workflow")
            if not isinstance(previous, dict):
                pending = run.get("pending_action", {}).get(
                    "life_graph_change_request", {}
                )
                previous = {
                    "engine": "langgraph",
                    "version": 1,
                    "stage": run["state"],
                    "checkpoint_seq": 0,
                    "trace": [],
                    "missing_slots": run.get("data", {}).get("missing_slots", []),
                    "change_request_id": pending.get("request_id"),
                }

            session = self.repo.sessions.get(run["session_id"])
            if run["state"] == "WAITING_INPUT" and session is not None:
                expires_at = session.get("pending_expires_at")
                if expires_at and _now() > datetime.fromisoformat(expires_at):
                    workflow_state = self.life_workflow.invoke({
                        "event": "ABORT",
                        "run_id": run["run_id"],
                        "user_id": run["user_id"],
                        "session_id": run["session_id"],
                        "previous_workflow": previous,
                        "trace": list(previous.get("trace", [])),
                    })
                    run.update(
                        state="ABORTED",
                        message="补充信息已超时，请重新发起任务。",
                        pending_action={"type": "NONE"},
                    )
                    session["pending_intent"] = None
                    session["pending_run_id"] = None
                    session["pending_expires_at"] = None
                else:
                    workflow_state = self._recover_workflow_state(run, previous)
                    if not session.get("pending_intent"):
                        session["pending_intent"] = deepcopy(run["intent"])
                        session["pending_run_id"] = run["run_id"]
                        session["pending_expires_at"] = (
                            _now() + timedelta(minutes=10)
                        ).isoformat()
            else:
                workflow_state = self._recover_workflow_state(run, previous)

            run["workflow"] = self.life_workflow.checkpoint(
                workflow_state,
                previous,
            )
            run["state"] = workflow_state.get("run_state", run["state"])
            run["message"] = workflow_state.get("message", run["message"])
            request = workflow_state.get("change_request")
            if request is not None:
                if isinstance(run.get("data", {}).get("life_graph_change_request"), dict):
                    run["data"]["life_graph_change_request"] = request
                if request["status"] == "PENDING":
                    run["pending_action"] = {
                        "type": "LIFE_GRAPH_CHANGE_REQUEST",
                        "life_graph_change_request": self._public_life_request(request),
                    }
                else:
                    run["pending_action"] = {"type": "NONE"}
            elif run["state"] in {"ABORTED", "SUCCEEDED", "WAITING_HUMAN"}:
                run["pending_action"] = {"type": "NONE"}
            run["updated_at"] = _now().isoformat()
            recovered += 1

        if recovered:
            self.repo.flush()
        return recovered + self._recover_executable_plans()

    def _recover_executable_plans(self) -> int:
        recovered = 0
        for run in list(self.repo.runs.values()):
            plan = run.get("plan") or {}
            if plan.get("execution_mode") != "EXECUTABLE":
                continue
            running_nodes = [
                node for node in plan.get("nodes", [])
                if node.get("runtime", {}).get("status") == "RUNNING"
            ]
            waiting_node = next(
                (
                    node for node in plan.get("nodes", [])
                    if node.get("runtime", {}).get("status") == "WAITING"
                ),
                None,
            )
            authorization = self._recovery_authorization(run, waiting_node)
            if authorization is not None and waiting_node is not None:
                self._execute_plan(
                    run,
                    resume_node_id=waiting_node["id"],
                    authorization=authorization,
                    recovery=True,
                )
                recovered += 1
            elif waiting_node is not None and run.get("state") in {
                "ABORTED",
                "BLOCKED",
                "FAILED",
                "SUCCEEDED",
            }:
                self._skip_unfinished_plan(
                    run,
                    reason=f"RECOVERED_{run['state']}",
                )
                recovered += 1
            elif running_nodes:
                authorization = self._recovery_authorization(run, None)
                self._execute_plan(
                    run,
                    authorization=authorization,
                    recovery=True,
                    recover_interrupted=True,
                )
                recovered += 1
        return recovered

    def _recovery_authorization(
        self,
        run: dict,
        waiting_node: dict | None,
    ) -> dict | None:
        for confirmation in self.repo.confirmations.values():
            if confirmation["run_id"] == run["run_id"] and confirmation["status"] == "USED":
                node_id = confirmation["action"].get("authorization_node_id")
                if node_id and (waiting_node is None or waiting_node["id"] == node_id):
                    return {
                        "node_id": node_id,
                        "approved": True,
                        "method": "CONFIRMATION_RECOVERY",
                        "reference_id": confirmation["confirmation_id"],
                        "evidence_digest": confirmation["operation_snapshot_hash"],
                        "action": confirmation["action"],
                    }
        for verification in self.repo.verifications.values():
            if verification["run_id"] == run["run_id"] and verification["status"] == "VERIFIED":
                node_id = verification["action"].get("authorization_node_id")
                if node_id and (waiting_node is None or waiting_node["id"] == node_id):
                    return {
                        "node_id": node_id,
                        "approved": True,
                        "method": "MFA_RECOVERY",
                        "reference_id": verification["verification_id"],
                        "evidence_digest": verification["operation_snapshot_hash"],
                        "action": verification["action"],
                    }
        if run.get("intent", {}).get("name") == "life.event_plan" and self.life_graph is not None:
            request_id = (run.get("workflow") or {}).get("change_request_id")
            if request_id:
                try:
                    request = self.life_graph.get_change_request(run["user_id"], request_id)
                except LifeGraphServiceError:
                    return None
                if request["status"] == "APPROVED":
                    node_id = waiting_node["id"] if waiting_node else next(
                        (
                            node["id"] for node in run["plan"]["nodes"]
                            if node.get("type") == "HUMAN_CONFIRM"
                        ),
                        None,
                    )
                    if node_id:
                        return {
                            "node_id": node_id,
                            "approved": True,
                            "method": "LIFE_GRAPH_RECOVERY",
                            "reference_id": request_id,
                            "evidence_digest": request["snapshot_hash"],
                        }
        return None

    def _recover_workflow_state(self, run: dict, previous: dict) -> dict:
        return self.life_workflow.invoke({
            "event": "RECOVER",
            "run_id": run["run_id"],
            "user_id": run["user_id"],
            "session_id": run["session_id"],
            "previous_workflow": previous,
            "run_state": run["state"],
            "message": run["message"],
            "trace": list(previous.get("trace", [])),
        })

    @staticmethod
    def _public_life_request(request: dict) -> dict:
        return {
            key: request[key]
            for key in (
                "request_id",
                "summary",
                "status",
                "expires_at",
                "snapshot_hash",
            )
        }

    def _audit(self, run: dict, action: str, result: str, details: dict | None = None) -> str:
        audit_id = f"audit_{uuid4().hex[:12]}"
        chain_index, previous_digest = next_chain_link(
            self.repo.audit_logs,
            run["run_id"],
        )
        record = {
            "audit_id": audit_id,
            "user_id": run["user_id"],
            "run_id": run["run_id"],
            "session_id": run["session_id"],
            "action": action,
            "result": result,
            "details": details or {},
            "created_at": _now().isoformat(),
            "chain_index": chain_index,
            "previous_digest": previous_digest,
        }
        record["record_digest"] = audit_record_digest(record)
        self.repo.audit_logs.insert(0, record)
        run["audit_id"] = audit_id
        return audit_id

    def _base_run(self, user_id: str, session_id: str, message: str, agent_result: dict) -> dict:
        model_trace = agent_result.get("model_trace") or {}
        return {
            "run_id": f"run_{uuid4().hex[:12]}",
            "user_id": user_id,
            "session_id": session_id,
            "state": "RECEIVED",
            "message": "已接收请求。",
            "user_message": message,
            "intent": agent_result.get("intent"),
            "plan": agent_result.get("plan"),
            "safety": agent_result.get("safety"),
            "model_trace": agent_result.get("model_trace"),
            "versions": {
                "model": {
                    "provider": model_trace.get("provider", "deepseek"),
                    "requested_model": model_trace.get("requested_model"),
                    "response_model": model_trace.get("response_model"),
                    "status": model_trace.get("status", "NOT_INVOKED"),
                },
                "prompt": {
                    "version": PROMPT_VERSION,
                    "digest": PROMPT_DIGEST,
                },
                "planner": PLANNER_VERSION,
                "executor": EXECUTOR_VERSION,
                "policy": POLICY_VERSION,
                "passport_schema": PASSPORT_SCHEMA_VERSION,
            },
            "policy": None,
            "pending_action": {"type": "NONE"},
            "data": {},
            "workflow": None,
            "audit_id": None,
            "created_at": _now().isoformat(),
            "updated_at": _now().isoformat(),
        }

    def _get_session(self, user_id: str, session_id: str) -> dict:
        if session_id not in self.repo.sessions:
            raise KeyError("SESSION_NOT_FOUND")
        session = self.repo.sessions[session_id]
        if session["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        return session

    def create_session(self, *, user_id: str, channel: str = "WEB") -> dict:
        now = _now().isoformat()
        session_id = f"session_{uuid4().hex}"
        session = {
            "session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "title": "新会话",
            "run_ids": [],
            "pending_intent": None,
            "pending_run_id": None,
            "pending_expires_at": None,
            "last_recipient": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repo.sessions[session_id] = session
        self.repo.flush()
        return self._session_response(session)

    def list_sessions(self, *, user_id: str) -> list[dict]:
        sessions = [
            session for session in self.repo.sessions.values()
            if session["user_id"] == user_id
        ]
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
        return [self._session_summary(session) for session in sessions]

    def _session_summary(self, session: dict) -> dict:
        run_ids = session.get("run_ids", [])
        last_run = self.repo.runs.get(run_ids[-1]) if run_ids else None
        first_run = self.repo.runs.get(run_ids[0]) if run_ids else None
        title = session.get("title")
        if not title and first_run:
            first_message = " ".join(first_run.get("user_message", "").split())
            title = first_message[:28] + ("..." if len(first_message) > 28 else "")
        return {
            "session_id": session["session_id"],
            "title": title or "新会话",
            "channel": session.get("channel", "WEB"),
            "run_count": len(run_ids),
            "last_message": last_run.get("message") if last_run else None,
            "last_state": last_run.get("state") if last_run else None,
            "has_pending_input": bool(session.get("pending_intent")),
            "created_at": session.get("created_at", session["updated_at"]),
            "updated_at": session["updated_at"],
        }

    def _session_response(self, session: dict) -> dict:
        result = self._session_summary(session)
        result.update({
            "runs": [
                self.repo.runs[run_id].copy()
                for run_id in session.get("run_ids", [])
                if run_id in self.repo.runs
            ],
            "pending": {
                "run_id": session.get("pending_run_id"),
                "missing_slots": (session.get("pending_intent") or {}).get("missing_slots", []),
                "expires_at": session.get("pending_expires_at"),
            },
            "last_recipient": session.get("last_recipient"),
        })
        return result

    def _conversation_context(self, session: dict) -> dict:
        expires_at = session.get("pending_expires_at")
        if expires_at and _now() > datetime.fromisoformat(expires_at):
            self._clear_pending(session, previous_state="ABORTED", message="补充信息已超时，请重新发起任务。")
        last_completed_intent = None
        for run_id in reversed(session.get("run_ids", [])):
            previous_run = self.repo.runs.get(run_id)
            if not previous_run or previous_run.get("state") != "SUCCEEDED":
                continue
            intent_name = (previous_run.get("intent") or {}).get("name")
            if intent_name and intent_name != "unknown":
                last_completed_intent = intent_name
                break
        return {
            "pending_intent": deepcopy(session.get("pending_intent")),
            "last_recipient": session.get("last_recipient"),
            "last_completed_intent": last_completed_intent,
        }

    def _register_run(self, session: dict, run: dict) -> None:
        if not session["run_ids"]:
            title = " ".join(run["user_message"].split())
            session["title"] = title[:28] + ("..." if len(title) > 28 else "")
        session["run_ids"].append(run["run_id"])
        session["updated_at"] = _now().isoformat()
        run["conversation"] = {
            "turn_index": len(session["run_ids"]),
            "continued_from_run_id": session.get("pending_run_id"),
        }

    def _clear_pending(self, session: dict, previous_state: str = "RESOLVED", message: str | None = None) -> None:
        previous_run_id = session.get("pending_run_id")
        if previous_run_id and previous_run_id in self.repo.runs:
            previous_run = self.repo.runs[previous_run_id]
            if previous_run["state"] == "WAITING_INPUT":
                previous_run["state"] = previous_state
                previous_run["message"] = message or "缺失字段已在后续轮次补充。"
                previous_run["updated_at"] = _now().isoformat()
                workflow = previous_run.get("workflow")
                if isinstance(workflow, dict) and workflow.get("engine") == "langgraph":
                    workflow.update(
                        stage=previous_state,
                        resumable=False,
                        updated_at=_now().isoformat(),
                    )
        session["pending_intent"] = None
        session["pending_run_id"] = None
        session["pending_expires_at"] = None

    def create_run(
        self,
        session_id: str,
        message: str,
        idempotency_key: str | None = None,
        *,
        user_id: str,
    ) -> dict:
        idempotency_scope = (user_id, idempotency_key) if idempotency_key else None
        if idempotency_scope and idempotency_scope in self.repo.idempotency:
            return self.get_run(self.repo.idempotency[idempotency_scope], user_id=user_id)

        session = self._get_session(user_id, session_id)
        context = self._conversation_context(session)
        agent_result = handle_message(session_id, message, context)
        run = self._base_run(user_id, session_id, message, agent_result)
        self.repo.runs[run["run_id"]] = run
        self._register_run(session, run)
        if idempotency_scope:
            self.repo.idempotency[idempotency_scope] = run["run_id"]

        if agent_result["type"] == "CANCELLED":
            self._clear_pending(session, previous_state="ABORTED", message="任务已由用户在后续轮次取消。")
            run.update(state="ABORTED", message=agent_result["message"])
            self._audit(run, "FOLLOW_UP_CANCEL", "ABORTED")
            return self._touch(run)

        if agent_result["type"] == "BLOCKED":
            self._clear_pending(session, previous_state="BLOCKED", message="后续输入触发安全拦截，原任务已关闭。")
            run.update(state="BLOCKED", message=agent_result["message"])
            self._audit(run, "SAFETY_BLOCK", "BLOCKED", agent_result["safety"])
            return self._touch(run)

        intent = run["intent"]
        if intent["name"] == "life.event_plan" and intent.get("missing_slots"):
            return self._handle_life_event_workflow(
                run,
                session=session,
                context=context,
                agent_result=agent_result,
            )

        if intent.get("missing_slots"):
            missing_labels = {
                "amount": "金额",
                "recipient": "收款人",
                "related_person": "关系对象",
                "relation_type": "关系类型",
                "event_type": "事件类型",
                "event_date": "具体日期",
                "target_amount": "目标金额",
            }
            missing = "、".join(missing_labels.get(item, item) for item in intent["missing_slots"])
            run.update(
                state="WAITING_INPUT",
                message=f"还需要补充{missing}。在补充完成前，不会执行任何资金操作。",
                data={"clarification_required": True, "missing_slots": intent["missing_slots"]},
            )
            if context.get("pending_intent"):
                self._clear_pending(session, previous_state="RESOLVED", message="已收到部分补充信息，继续等待剩余字段。")
            if intent.get("slots", {}).get("recipient"):
                session["last_recipient"] = intent["slots"]["recipient"]
            session["pending_intent"] = deepcopy(intent)
            session["pending_run_id"] = run["run_id"]
            session["pending_expires_at"] = (_now() + timedelta(minutes=10)).isoformat()
            self._audit(run, "MISSING_SLOTS", "NO_EXECUTION", {"missing": intent["missing_slots"]})
            return self._touch(run)

        if context.get("pending_intent"):
            previous_state = "RESOLVED" if agent_result.get("resolved_slots") else "ABORTED"
            previous_message = (
                "缺失字段已在后续轮次补充。"
                if previous_state == "RESOLVED"
                else "用户已切换到新的业务请求。"
            )
            self._clear_pending(session, previous_state=previous_state, message=previous_message)

        recipient = intent.get("slots", {}).get("recipient")
        if recipient:
            session["last_recipient"] = recipient

        return self._execute_plan(run)

    def _handle_life_event_workflow(
        self,
        run: dict,
        *,
        session: dict,
        context: dict,
        agent_result: dict,
    ) -> dict:
        source_run_id = None
        if getattr(self.repo, "engine", None) is not None:
            self.repo.flush()
            source_run_id = run["run_id"]

        workflow_state = self.life_workflow.invoke({
            "event": "MESSAGE",
            "run_id": run["run_id"],
            "user_id": run["user_id"],
            "session_id": run["session_id"],
            "source_run_id": source_run_id,
            "intent": run["intent"],
            "trace": [],
        })
        run["workflow"] = self.life_workflow.checkpoint(
            workflow_state,
            run.get("workflow"),
        )

        if context.get("pending_intent"):
            previous_state = "RESOLVED" if agent_result.get("resolved_slots") else "ABORTED"
            previous_message = (
                "缺失字段已在后续轮次补充。"
                if previous_state == "RESOLVED"
                else "用户已切换到新的业务请求。"
            )
            self._clear_pending(
                session,
                previous_state=previous_state,
                message=previous_message,
            )

        if workflow_state["run_state"] == "WAITING_INPUT":
            session["pending_intent"] = deepcopy(run["intent"])
            session["pending_run_id"] = run["run_id"]
            session["pending_expires_at"] = (_now() + timedelta(minutes=10)).isoformat()
            run.update(
                state="WAITING_INPUT",
                message=workflow_state["message"],
                data={
                    "clarification_required": True,
                    "missing_slots": workflow_state["missing_slots"],
                },
            )
            self._audit(
                run,
                "LIFE_WORKFLOW_WAIT_INPUT",
                "NO_EXECUTION",
                {
                    "missing": workflow_state["missing_slots"],
                    "workflow_trace": workflow_state["trace"],
                },
            )
            return self._touch(run)

        if workflow_state["run_state"] == "FAILED":
            run.update(
                state="FAILED",
                message=workflow_state["message"],
                data={"error_code": workflow_state.get("error_code")},
            )
            self._audit(
                run,
                "LIFE_WORKFLOW_FAILED",
                "FAILED",
                {
                    "error_code": workflow_state.get("error_code"),
                    "workflow_trace": workflow_state["trace"],
                },
            )
            return self._touch(run)

        request = workflow_state["change_request"]
        conflict_analysis = workflow_state.get("conflict_analysis")
        public_request = self._public_life_request(request)
        run.update(
            state=workflow_state["run_state"],
            message=workflow_state["message"],
            pending_action={
                "type": "LIFE_GRAPH_CHANGE_REQUEST",
                "life_graph_change_request": public_request,
            },
            data={
                "life_graph_change_request": request,
                "goal_conflict_analysis": conflict_analysis,
            },
        )
        self._audit(
            run,
            "LIFE_WORKFLOW_WAIT_CONFIRMATION",
            "WAITING_CONFIRMATION",
            {
                "request_id": request["request_id"],
                "snapshot_hash": request["snapshot_hash"],
                "conflict_status": (
                    conflict_analysis["status"]
                    if conflict_analysis
                    else "NOT_ANALYZED"
                ),
                "workflow_trace": workflow_state["trace"],
            },
        )
        return self._touch(run)

    def _task_handlers(self) -> dict:
        return {
            "node:VALIDATION": self._task_validation,
            "node:HUMAN_CONFIRM": self._task_human_confirmation,
            "node:RESPONSE": self._task_response,
            "transaction.list": self._task_transaction_list,
            "bill.analyze": self._task_bill_analyze,
            "subscription.list": self._task_subscription_list,
            "subscription.cancel.preview": self._task_subscription_cancel_preview,
            "subscription.cancel.execute": self._task_subscription_cancel_execute,
            "contacts.search": self._task_contacts_search,
            "transfer.preview": self._task_transfer_preview,
            "transfer.execute": self._task_transfer_execute,
            "policy.evaluate": self._task_policy_evaluate,
            "life_graph.change_request.create": self._task_life_change_create,
            "life_graph.change_request.decide": self._task_life_change_decide,
        }

    def _execute_plan(
        self,
        run: dict,
        *,
        authorization: dict | None = None,
        resume_node_id: str | None = None,
        recovery: bool = False,
        recover_interrupted: bool = False,
        values: dict | None = None,
    ) -> dict:
        if not run.get("plan"):
            run.update(
                state="FAILED",
                message="任务缺少可执行计划，系统未执行任何业务操作。",
                data={"error_code": "TASK_GRAPH_PLAN_MISSING"},
            )
            self._audit(run, "TASK_GRAPH_EXECUTION", "FAILED", {"error_code": "TASK_GRAPH_PLAN_MISSING"})
            return self._touch(run)

        if run.get("state") not in {"WAITING_CONFIRMATION", "WAITING_VERIFICATION"}:
            run["state"] = "RUNNING"
        context = TaskExecutionContext(
            run=run,
            authorization=authorization,
            recovery=recovery,
            values=values or {},
        )

        def persist(plan: dict) -> None:
            run["plan"] = plan
            run["updated_at"] = _now().isoformat()
            self.repo.flush()

        executor = TaskGraphExecutor(self._task_handlers(), persist=persist)
        try:
            run["plan"] = executor.execute(
                run["plan"],
                context,
                resume_node_id=resume_node_id,
                recover_interrupted=recover_interrupted,
            )
        except TaskGraphContractError as error:
            run.update(
                state="BLOCKED",
                message=error.message,
                pending_action={"type": "NONE"},
            )
            run.setdefault("data", {})["error_code"] = error.code
            self._audit(
                run,
                "TASK_GRAPH_CONTRACT",
                "BLOCKED",
                {"error_code": error.code, "details": error.details},
            )
            return self._touch(run)

        failed = next(
            (
                node
                for node in run["plan"]["nodes"]
                if node["runtime"]["status"] == TaskNodeStatus.FAILED.value
            ),
            None,
        )
        if failed is not None and run.get("state") != "BLOCKED":
            code = failed["runtime"].get("error_code", "TASK_GRAPH_NODE_EXECUTION_FAILED")
            run.update(
                state="FAILED",
                message=error_definition(code).message,
                pending_action={"type": "NONE"},
            )
            run.setdefault("data", {})["error_code"] = code
            self._audit(run, "TASK_GRAPH_EXECUTION", "FAILED", {"node_id": failed["id"], "error_code": code})
        elif all(
            node["runtime"]["status"] in {
                TaskNodeStatus.SUCCEEDED.value,
                TaskNodeStatus.SKIPPED.value,
            }
            for node in run["plan"]["nodes"]
        ) and run.get("state") == "RUNNING":
            run["state"] = "SUCCEEDED"
        return self._touch(run)

    def _skip_unfinished_plan(self, run: dict, *, reason: str) -> None:
        if not run.get("plan"):
            return
        context = TaskExecutionContext(run=run)

        def persist(plan: dict) -> None:
            run["plan"] = plan
            run["updated_at"] = _now().isoformat()
            self.repo.flush()

        executor = TaskGraphExecutor(self._task_handlers(), persist=persist)
        try:
            run["plan"] = executor.skip_unfinished(run["plan"], context, reason=reason)
        except TaskGraphContractError as error:
            run.setdefault("data", {})["task_graph_error"] = error.code

    def _workflow_step(self, run: dict, event: str, **values: object) -> dict:
        previous = run.get("workflow") or {}
        state = self.life_workflow.invoke({
            "event": event,
            "run_id": run["run_id"],
            "user_id": run["user_id"],
            "session_id": run["session_id"],
            "previous_workflow": previous,
            "trace": list(previous.get("trace", [])),
            **values,
        })
        run["workflow"] = self.life_workflow.checkpoint(state, previous)
        return state

    @staticmethod
    def _success(
        summary: str,
        output: object,
        *,
        kind: TaskNodeReceiptKind = TaskNodeReceiptKind.SYSTEM_RESULT,
        reference_id: str | None = None,
    ) -> TaskNodeOutcome:
        return TaskNodeOutcome.succeeded(
            summary=summary,
            output=output,
            receipt_kind=kind,
            reference_id=reference_id,
        )

    def _task_validation(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        missing = list(run.get("intent", {}).get("missing_slots", []))
        if missing:
            return TaskNodeOutcome.failed(
                error_code="TASK_INPUT_INCOMPLETE",
                reason="TASK_INPUT_INCOMPLETE",
            )
        if run.get("intent", {}).get("name") == "life.event_plan":
            state = self._workflow_step(
                run,
                "VALIDATE",
                intent=run["intent"],
            )
            if state.get("missing_slots"):
                return TaskNodeOutcome.failed(
                    error_code="TASK_INPUT_INCOMPLETE",
                    reason="TASK_INPUT_INCOMPLETE",
                )
        return self._success("输入字段校验通过", {"valid": True})

    def _task_transaction_list(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        count = sum(
            1 for item in self.repo.transactions
            if item["user_id"] == context.run["user_id"]
        )
        return self._success(
            f"已读取 {count} 笔银行沙箱交易",
            {"transaction_count": count},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
        )

    def _task_bill_analyze(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        analysis = self.bank.analyze_bill(run["user_id"])
        run.setdefault("data", {})["bill_analysis"] = analysis
        run["policy"] = evaluate_operation("bill.analyze").model_dump()
        run["message"] = _format_bill_analysis(analysis)
        self._audit(
            run,
            "BILL_ANALYZE",
            "SUCCEEDED",
            {"abnormal_count": len(analysis["abnormal_transactions"])},
        )
        return self._success(
            "账单分析已生成",
            {
                "expense": analysis["summary"]["expense"],
                "abnormal_count": len(analysis["abnormal_transactions"]),
            },
            kind=TaskNodeReceiptKind.TOOL_RESULT,
        )

    def _task_subscription_list(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        if run["intent"]["name"] == "subscription.cancel":
            subscription = self.bank.find_subscription(
                run["user_id"],
                run["intent"]["slots"].get("keyword"),
            )
            if subscription is None:
                run["message"] = "未找到可取消的订阅。"
                return TaskNodeOutcome.failed(
                    error_code="SUBSCRIPTION_NOT_FOUND",
                    reason="SUBSCRIPTION_NOT_FOUND",
                )
            run.setdefault("data", {})["subscription"] = subscription
            context.values["subscription"] = subscription
            return self._success(
                f"已定位订阅 {subscription['name']}",
                {"subscription_id": subscription["id"], "status": subscription["status"]},
                kind=TaskNodeReceiptKind.TOOL_RESULT,
                reference_id=subscription["id"],
            )

        subscriptions = self.bank.list_subscriptions(run["user_id"])
        run.setdefault("data", {})["subscriptions"] = subscriptions
        run["policy"] = evaluate_operation("subscription.list").model_dump()
        return self._success(
            f"已读取 {len(subscriptions)} 项周期扣费",
            {"subscription_count": len(subscriptions)},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
        )

    def _subscription_action(self, run: dict) -> dict:
        subscription = run.get("data", {}).get("subscription")
        if not subscription:
            raise ValueError("SUBSCRIPTION_NOT_FOUND")
        resource_snapshot = {
            "user_id": run["user_id"],
            "subscription_id": subscription["id"],
            "amount": subscription["amount"],
            "status": subscription["status"],
        }
        return {
            "kind": "subscription.cancel",
            "user_id": run["user_id"],
            "subscription_id": subscription["id"],
            "resource_snapshot_hash": _hash_snapshot(resource_snapshot),
        }

    def _task_subscription_cancel_preview(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        action = self._subscription_action(run)
        context.values["operation_action"] = action
        subscription = run["data"]["subscription"]
        return self._success(
            f"已生成取消 {subscription['name']} 的操作预览",
            {
                "subscription_id": subscription["id"],
                "amount": subscription["amount"],
                "resource_snapshot_hash": action["resource_snapshot_hash"],
            },
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=subscription["id"],
        )

    def _task_contacts_search(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        recipient = self.bank.resolve_recipient(
            run["user_id"], run["intent"]["slots"]["recipient"]
        )
        run.setdefault("data", {})["resolved_recipient"] = recipient
        context.values["recipient"] = recipient
        return self._success(
            f"已核对收款人 {recipient['name']}",
            {"recipient_id": recipient["id"], "trusted": recipient["trusted"]},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=recipient["id"],
        )

    def _task_transfer_preview(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        recipient = context.values.get("recipient") or run.get("data", {}).get("resolved_recipient")
        if not recipient:
            return TaskNodeOutcome.failed(
                error_code="RECIPIENT_NOT_RESOLVED",
                reason="RECIPIENT_NOT_RESOLVED",
            )
        slots = run["intent"]["slots"]
        preview = self.bank.create_transfer_preview(
            run["user_id"], recipient, slots["amount"], run["intent"]["risk_hints"]
        )
        run.setdefault("data", {})["transfer_preview"] = preview
        return self._success(
            "不可变转账预览已生成",
            {
                "preview_id": preview["preview_id"],
                "recipient_id": recipient["id"],
                "amount": preview["amount"],
                "currency": preview["currency"],
            },
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=preview["preview_id"],
        )

    def _transfer_action(self, run: dict) -> dict:
        preview = run.get("data", {}).get("transfer_preview")
        if not preview:
            raise ValueError("TRANSFER_PREVIEW_NOT_FOUND")
        resource_snapshot = {
            "user_id": run["user_id"],
            "preview_id": preview["preview_id"],
            "from_account_id": preview["from_account_id"],
            "recipient_id": preview["recipient"]["id"],
            "amount": preview["amount"],
            "currency": preview["currency"],
        }
        return {
            "kind": "transfer.execute",
            "user_id": run["user_id"],
            "preview_id": preview["preview_id"],
            "resource_snapshot_hash": _hash_snapshot(resource_snapshot),
        }

    def _task_policy_evaluate(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        intent_name = run["intent"]["name"]
        if intent_name == "transfer.create":
            preview = run.get("data", {}).get("transfer_preview") or {}
            recipient = preview.get("recipient") or {}
            policy = evaluate_operation(
                "transfer.create",
                run["intent"]["slots"]["amount"],
                {
                    "new_recipient": not recipient.get("trusted", False),
                    "risk_hints": run["intent"].get("risk_hints", []),
                },
            )
        elif intent_name == "subscription.cancel":
            policy = evaluate_operation("subscription.cancel")
        elif intent_name == "life.event_plan":
            request = run.get("data", {}).get("life_graph_change_request")
            state = self._workflow_step(
                run,
                "ANALYZE_CONFLICTS",
                change_request=request,
            )
            run.setdefault("data", {})["goal_conflict_analysis"] = state.get("conflict_analysis")
            policy = evaluate_operation("life.event_plan")
        else:
            policy = evaluate_operation("bill.analyze")
        run["policy"] = policy.model_dump()
        if policy.level.value == "BLOCKED":
            run.update(state="BLOCKED", message=policy.reason, pending_action={"type": "NONE"})
            self._audit(run, "TASK_POLICY", "BLOCKED", policy.model_dump())
            return TaskNodeOutcome.failed(error_code="POLICY_BLOCKED", reason="POLICY_BLOCKED")
        return self._success(
            f"策略评估结果为 {policy.level.value}",
            {
                "level": policy.level.value,
                "action": policy.action,
                "risk_score": policy.risk_score,
                "risk_factors": policy.risk_factors,
            },
            kind=TaskNodeReceiptKind.POLICY_DECISION,
        )

    def _task_life_change_create(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        source_run_id = None
        if getattr(self.repo, "engine", None) is not None:
            self.repo.flush()
            source_run_id = run["run_id"]
        state = self._workflow_step(
            run,
            "CREATE_CANDIDATE",
            intent=run["intent"],
            source_run_id=source_run_id,
        )
        if state.get("error_code"):
            run.update(state="FAILED", message=state.get("message", "生活事件候选计划生成失败。"))
            return TaskNodeOutcome.failed(
                error_code=state["error_code"],
                reason=state["error_code"],
            )
        request = state["change_request"]
        run.setdefault("data", {})["life_graph_change_request"] = request
        return self._success(
            "生活图谱候选变更已生成",
            {"request_id": request["request_id"], "snapshot_hash": request["snapshot_hash"]},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=request["request_id"],
        )

    def _task_human_confirmation(self, node: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        authorization = context.authorization
        if authorization is not None:
            if authorization.get("node_id") != node.id or not authorization.get("approved"):
                return TaskNodeOutcome.failed(
                    error_code="AUTHORIZATION_EVIDENCE_INVALID",
                    reason="AUTHORIZATION_EVIDENCE_INVALID",
                )
            return self._success(
                "用户授权证据已核验",
                {
                    "decision": "APPROVED",
                    "method": authorization.get("method", "UNKNOWN"),
                    "evidence_digest": authorization.get("evidence_digest"),
                },
                kind=TaskNodeReceiptKind.USER_DECISION,
                reference_id=authorization.get("reference_id"),
            )

        intent_name = run["intent"]["name"]
        if intent_name == "life.event_plan":
            request = run["data"]["life_graph_change_request"]
            state = self._workflow_step(
                run,
                "AWAIT_CONFIRMATION",
                change_request=request,
                conflict_analysis=run["data"].get("goal_conflict_analysis"),
            )
            run.update(
                state="WAITING_CONFIRMATION",
                message=state["message"],
                pending_action={
                    "type": "LIFE_GRAPH_CHANGE_REQUEST",
                    "life_graph_change_request": self._public_life_request(request),
                },
            )
            self._audit(
                run,
                "LIFE_WORKFLOW_WAIT_CONFIRMATION",
                "WAITING_CONFIRMATION",
                {"request_id": request["request_id"], "snapshot_hash": request["snapshot_hash"]},
            )
            return TaskNodeOutcome.waiting(reason="LIFE_GRAPH_CONFIRMATION_REQUIRED")

        if intent_name == "subscription.cancel":
            action = self._subscription_action(run)
            subscription = run["data"]["subscription"]
            summary = f"取消 {subscription['name']}，停止每月 {subscription['amount']:.2f} 元扣费"
        elif intent_name == "transfer.create":
            action = self._transfer_action(run)
            preview = run["data"]["transfer_preview"]
            summary = f"向 {preview['recipient']['name']} 转账 {preview['amount']:.2f} 元，手续费 0.00 元"
        else:
            return TaskNodeOutcome.failed(
                error_code="AUTHORIZATION_SCENARIO_NOT_SUPPORTED",
                reason="AUTHORIZATION_SCENARIO_NOT_SUPPORTED",
            )
        action["authorization_node_id"] = node.id

        existing_verification = next(
            (
                item for item in self.repo.verifications.values()
                if item["run_id"] == run["run_id"] and item["status"] == "PENDING"
            ),
            None,
        )
        if existing_verification is not None:
            run.update(
                state="WAITING_VERIFICATION",
                message="该转账风险较高，仍在等待已发送验证码的强验证。",
                pending_action={
                    "type": "VERIFICATION",
                    "verification": self._public_verification(existing_verification),
                },
            )
            return TaskNodeOutcome.waiting(reason="STRONG_VERIFICATION_REQUIRED")
        existing_confirmation = next(
            (
                item for item in self.repo.confirmations.values()
                if item["run_id"] == run["run_id"] and item["status"] == "PENDING"
            ),
            None,
        )
        if existing_confirmation is not None:
            run.update(
                state="WAITING_CONFIRMATION",
                message="操作仍在等待用户确认。",
                pending_action={
                    "type": "CONFIRMATION",
                    "confirmation": self._public_confirmation(existing_confirmation),
                },
            )
            return TaskNodeOutcome.waiting(reason="USER_CONFIRMATION_REQUIRED")

        if run["policy"]["level"] == "RED":
            verification = self._create_verification(run, action)
            run.update(
                state="WAITING_VERIFICATION",
                message="该转账风险较高，验证码已发送至账户预留手机号。完成强验证后才会执行。",
                pending_action={"type": "VERIFICATION", "verification": self._public_verification(verification)},
            )
            self._audit(run, "TASK_AUTHORIZATION", "WAITING_VERIFICATION", {"node_id": node.id})
            return TaskNodeOutcome.waiting(reason="STRONG_VERIFICATION_REQUIRED")

        confirmation = self._create_confirmation(run, action, summary)
        run.update(
            state="WAITING_CONFIRMATION",
            message=(
                "取消订阅会改变扣费状态，请核对后确认。"
                if intent_name == "subscription.cancel"
                else "请核对收款人和金额，确认后才会执行转账。"
            ),
            pending_action={"type": "CONFIRMATION", "confirmation": self._public_confirmation(confirmation)},
        )
        self._audit(run, "TASK_AUTHORIZATION", "WAITING_CONFIRMATION", {"node_id": node.id})
        return TaskNodeOutcome.waiting(reason="USER_CONFIRMATION_REQUIRED")

    @staticmethod
    def _authorized_action(context: TaskExecutionContext, expected_kind: str) -> dict:
        authorization = context.authorization or {}
        action = authorization.get("action")
        if not isinstance(action, dict) or action.get("kind") != expected_kind:
            raise ValueError("AUTHORIZED_ACTION_MISSING")
        return action

    def _task_subscription_cancel_execute(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        try:
            action = self._authorized_action(context, "subscription.cancel")
            current = next(
                (
                    item for item in self.repo.subscriptions
                    if item["id"] == action["subscription_id"]
                    and item["user_id"] == run["user_id"]
                ),
                None,
            )
            if current is not None and current["status"] == "CANCELED":
                result = current.copy()
            else:
                if not self._resource_matches(action, run["user_id"]):
                    return TaskNodeOutcome.failed(error_code="SNAPSHOT_MISMATCH", reason="SNAPSHOT_MISMATCH")
                result = self.bank.cancel_subscription(run["user_id"], action["subscription_id"])
        except (KeyError, ValueError):
            return TaskNodeOutcome.failed(
                error_code="SUBSCRIPTION_CANCEL_REJECTED",
                reason="SUBSCRIPTION_CANCEL_REJECTED",
            )
        run.update(
            state="SUCCEEDED",
            message=f"已取消 {result['name']}，后续不再自动扣费。",
            pending_action={"type": "NONE"},
            data={"subscription": result},
        )
        self._audit(run, "SUBSCRIPTION_CANCEL_EXECUTE", "SUCCEEDED", {"subscription_id": result["id"]})
        return self._success(
            "订阅取消已由银行沙箱确认",
            {"subscription_id": result["id"], "status": result["status"]},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=result["id"],
        )

    def _task_transfer_execute(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        try:
            action = self._authorized_action(context, "transfer.execute")
            if not self._resource_matches(action, run["user_id"]):
                return TaskNodeOutcome.failed(error_code="SNAPSHOT_MISMATCH", reason="SNAPSHOT_MISMATCH")
            preview = self.repo.previews[action["preview_id"]]
            if preview.get("status") == "SUCCESS" and preview.get("transaction_id"):
                result = {
                    "transaction_id": preview["transaction_id"],
                    "status": "SUCCESS",
                    "preview": preview.copy(),
                }
            else:
                result = self.bank.execute_transfer(run["user_id"], action["preview_id"])
        except (KeyError, PermissionError, ValueError) as error:
            code = str(error) if str(error) else "TRANSFER_EXECUTION_REJECTED"
            return TaskNodeOutcome.failed(error_code=code, reason=code)
        run.update(
            state="SUCCEEDED",
            message="转账已由银行沙箱确认成功。",
            pending_action={"type": "NONE"},
            data={"transfer_result": result},
        )
        self._audit(run, "TRANSFER_EXECUTE", "SUCCEEDED", {"transaction_id": result["transaction_id"]})
        return self._success(
            "银行沙箱返回转账成功回执",
            {"transaction_id": result["transaction_id"], "status": result["status"]},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=result["transaction_id"],
        )

    def _task_life_change_decide(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        service = context.values.get("life_graph_service") or self.life_graph
        request_id = run["data"]["life_graph_change_request"]["request_id"]
        if service is None:
            return TaskNodeOutcome.failed(
                error_code="LIFE_GRAPH_NOT_CONFIGURED",
                reason="LIFE_GRAPH_NOT_CONFIGURED",
            )
        try:
            request = service.get_change_request(run["user_id"], request_id)
            if request["status"] == "PENDING":
                request = service.decide_change_request(
                    owner_user_id=run["user_id"],
                    request_id=request_id,
                    approved=True,
                    reason="用户通过任务图授权节点确认",
                )
            elif request["status"] != "APPROVED":
                return TaskNodeOutcome.failed(
                    error_code=f"CHANGE_REQUEST_{request['status']}",
                    reason=f"CHANGE_REQUEST_{request['status']}",
                )
        except LifeGraphServiceError as error:
            return TaskNodeOutcome.failed(error_code=error.code, reason=error.code)

        state = self._workflow_step(run, "DECISION", decision_status="APPROVED")
        run["data"]["life_graph_change_request"] = request
        run.update(
            state=state["run_state"],
            message=state["message"],
            pending_action={"type": "NONE"},
        )
        self._audit(run, "LIFE_GRAPH_DECISION", "APPROVED", {"request_id": request_id})
        return self._success(
            "生活图谱候选已原子写入",
            {"request_id": request_id, "status": request["status"]},
            kind=TaskNodeReceiptKind.TOOL_RESULT,
            reference_id=request_id,
        )

    def _task_response(self, _: TaskNode, context: TaskExecutionContext) -> TaskNodeOutcome:
        run = context.run
        intent_name = run["intent"]["name"]
        if intent_name == "subscription.list":
            subscriptions = run.get("data", {}).get("subscriptions", [])
            unused = sum(1 for item in subscriptions if item.get("last_used_days", 0) >= 60)
            run["message"] = f"已找到 {len(subscriptions)} 项周期扣费，其中 {unused} 项长期未使用。"
            self._audit(run, "SUBSCRIPTION_LIST", "SUCCEEDED", {"count": len(subscriptions)})
        elif intent_name == "finance.cross_scenario":
            run.setdefault("data", {})["suggested_prompts"] = ["取消云盘会员", "给女儿转 200 元生活费"]
            run["message"] = "跨场景财务体检已完成。为避免一次确认多个写操作，我已将取消和转账整理为后续建议。"
            self._audit(run, "CROSS_SCENARIO_ANALYZE", "SUCCEEDED", {"write_actions_executed": 0})
        elif intent_name == "unknown":
            run["message"] = "我可以办理账单分析、订阅管理、转账和生活事件资金规划。请描述具体需求。"
            run.setdefault("data", {})["clarification_required"] = True
            self._audit(run, "CLARIFICATION", "SUCCEEDED")
        run["state"] = "SUCCEEDED"
        return self._success("已生成用户可见响应", {"state": "SUCCEEDED"})

    def _handle_bill(self, run: dict) -> dict:
        policy = evaluate_operation("bill.analyze")
        data = self.bank.analyze_bill(run["user_id"])
        run.update(
            state="SUCCEEDED",
            message=_format_bill_analysis(data),
            policy=policy.model_dump(),
            data={"bill_analysis": data},
        )
        self._audit(run, "BILL_ANALYZE", "SUCCEEDED", {"abnormal_count": len(data["abnormal_transactions"])})
        return self._touch(run)

    def _handle_subscription_list(self, run: dict) -> dict:
        policy = evaluate_operation("subscription.list")
        subscriptions = self.bank.list_subscriptions(run["user_id"])
        run.update(state="SUCCEEDED", message="已找到 3 项周期扣费，其中 1 项长期未使用。", policy=policy.model_dump(), data={"subscriptions": subscriptions})
        self._audit(run, "SUBSCRIPTION_LIST", "SUCCEEDED", {"count": len(subscriptions)})
        return self._touch(run)

    def _handle_cross_scenario(self, run: dict) -> dict:
        policy = evaluate_operation("bill.analyze")
        run.update(
            state="SUCCEEDED",
            message="跨场景财务体检已完成。为避免一次确认多个写操作，我已将取消和转账整理为后续建议。",
            policy=policy.model_dump(),
            data={
                "bill_analysis": self.bank.analyze_bill(run["user_id"]),
                "subscriptions": self.bank.list_subscriptions(run["user_id"]),
                "suggested_prompts": ["取消云盘会员", "给女儿转 200 元生活费"],
            },
        )
        self._audit(run, "CROSS_SCENARIO_ANALYZE", "SUCCEEDED", {"write_actions_executed": 0})
        return self._touch(run)

    def _handle_subscription_cancel(self, run: dict) -> dict:
        subscription = self.bank.find_subscription(run["user_id"], run["intent"]["slots"].get("keyword"))
        if subscription is None:
            run.update(state="FAILED", message="未找到可取消的订阅。")
            self._audit(run, "SUBSCRIPTION_CANCEL_PREVIEW", "FAILED")
            return self._touch(run)
        policy = evaluate_operation("subscription.cancel")
        resource_snapshot = {
            "user_id": run["user_id"],
            "subscription_id": subscription["id"],
            "amount": subscription["amount"],
            "status": subscription["status"],
        }
        action = {
            "kind": "subscription.cancel",
            "user_id": run["user_id"],
            "subscription_id": subscription["id"],
            "resource_snapshot_hash": _hash_snapshot(resource_snapshot),
        }
        summary = f"取消 {subscription['name']}，停止每月 {subscription['amount']:.2f} 元扣费"
        confirmation = self._create_confirmation(run, action, summary)
        run.update(
            state="WAITING_CONFIRMATION",
            message="取消订阅会改变扣费状态，请核对后确认。",
            policy=policy.model_dump(),
            pending_action={"type": "CONFIRMATION", "confirmation": self._public_confirmation(confirmation)},
            data={"subscription": subscription},
        )
        self._audit(run, "SUBSCRIPTION_CANCEL_PREVIEW", "WAITING_CONFIRMATION", action)
        return self._touch(run)

    def _handle_transfer(self, run: dict) -> dict:
        slots = run["intent"]["slots"]
        recipient = self.bank.resolve_recipient(run["user_id"], slots["recipient"])
        preview = self.bank.create_transfer_preview(
            run["user_id"], recipient, slots["amount"], run["intent"]["risk_hints"]
        )
        policy = evaluate_operation(
            "transfer.create",
            slots["amount"],
            {"new_recipient": not recipient["trusted"], "risk_hints": run["intent"]["risk_hints"]},
        )
        run.update(policy=policy.model_dump(), data={"transfer_preview": preview})
        resource_snapshot = {
            "user_id": run["user_id"],
            "preview_id": preview["preview_id"],
            "from_account_id": preview["from_account_id"],
            "recipient_id": preview["recipient"]["id"],
            "amount": preview["amount"],
            "currency": preview["currency"],
        }
        action = {
            "kind": "transfer.execute",
            "user_id": run["user_id"],
            "preview_id": preview["preview_id"],
            "resource_snapshot_hash": _hash_snapshot(resource_snapshot),
        }

        if policy.level.value == "BLOCKED":
            run.update(state="BLOCKED", message=policy.reason)
            self._audit(run, "TRANSFER_POLICY", "BLOCKED", policy.model_dump())
        elif policy.level.value == "RED":
            verification = self._create_verification(run, action)
            run.update(
                state="WAITING_VERIFICATION",
                message="该转账风险较高，验证码已发送至账户预留手机号。完成强验证后才会执行。",
                pending_action={"type": "VERIFICATION", "verification": self._public_verification(verification)},
            )
            self._audit(run, "TRANSFER_POLICY", "WAITING_VERIFICATION", policy.model_dump())
        else:
            summary = f"向 {recipient['name']} 转账 {slots['amount']:.2f} 元，手续费 0.00 元"
            confirmation = self._create_confirmation(run, action, summary)
            run.update(
                state="WAITING_CONFIRMATION",
                message="请核对收款人和金额，确认后才会执行转账。",
                pending_action={"type": "CONFIRMATION", "confirmation": self._public_confirmation(confirmation)},
            )
            self._audit(run, "TRANSFER_POLICY", "WAITING_CONFIRMATION", policy.model_dump())
        return self._touch(run)

    def _create_confirmation(self, run: dict, action: dict, summary: str) -> dict:
        confirmation = {
            "confirmation_id": f"confirm_{uuid4().hex[:12]}",
            "user_id": run["user_id"],
            "run_id": run["run_id"],
            "action": action,
            "operation_snapshot_hash": _hash_snapshot(action),
            "summary": summary,
            "status": "PENDING",
            "expires_at": (_now() + timedelta(minutes=5)).isoformat(),
        }
        self.repo.confirmations[confirmation["confirmation_id"]] = confirmation
        return confirmation

    def _create_verification(self, run: dict, action: dict) -> dict:
        verification_id = f"verify_{uuid4().hex[:12]}"
        issued_at = _now()
        verification = {
            "verification_id": verification_id,
            "user_id": run["user_id"],
            "run_id": run["run_id"],
            "action": action,
            "operation_snapshot_hash": _hash_snapshot(action),
            "status": "PENDING",
            "failed_attempts": 0,
            "otp_hash": self.mfa.issue(verification_id),
            "delivery_channel": "SMS",
            "delivery_hint": "账户预留手机号",
            "issued_at": issued_at.isoformat(),
            "resend_available_at": (
                issued_at + timedelta(seconds=settings.mfa_resend_cooldown_seconds)
            ).isoformat(),
            "expires_at": (
                issued_at + timedelta(seconds=settings.mfa_code_ttl_seconds)
            ).isoformat(),
        }
        self.repo.verifications[verification["verification_id"]] = verification
        return verification

    @staticmethod
    def _public_confirmation(value: dict) -> dict:
        keys = ("confirmation_id", "summary", "operation_snapshot_hash", "expires_at", "status")
        return {key: value[key] for key in keys}

    @staticmethod
    def _public_verification(value: dict) -> dict:
        keys = (
            "verification_id",
            "expires_at",
            "status",
            "failed_attempts",
            "delivery_channel",
            "delivery_hint",
            "resend_available_at",
        )
        return {key: value[key] for key in keys}

    def decide_confirmation(
        self, confirmation_id: str, confirmed: bool, *, user_id: str
    ) -> dict:
        confirmation = self.repo.confirmations.get(confirmation_id)
        if not confirmation:
            raise KeyError("CONFIRMATION_NOT_FOUND")
        if confirmation["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        if confirmation["status"] != "PENDING":
            raise ValueError("REPLAY_DETECTED")
        run = self.repo.runs[confirmation["run_id"]]
        if _now() > datetime.fromisoformat(confirmation["expires_at"]):
            confirmation["status"] = "EXPIRED"
            confirmation["decided_at"] = _now().isoformat()
            self._skip_unfinished_plan(run, reason="AUTHORIZATION_EXPIRED")
            run.update(state="ABORTED", message="确认已过期，请重新发起操作。", pending_action={"type": "NONE"})
            self._audit(run, "USER_CONFIRMATION", "EXPIRED")
            return self._touch(run)
        if run["state"] != "WAITING_CONFIRMATION":
            raise ValueError("RUN_NOT_CONFIRMABLE")
        if not confirmed:
            confirmation["status"] = "REJECTED"
            confirmation["decided_at"] = _now().isoformat()
            self._skip_unfinished_plan(run, reason="USER_REJECTED")
            run.update(state="ABORTED", message="操作已由用户取消。", pending_action={"type": "NONE"})
            self._audit(run, "USER_CONFIRMATION", "REJECTED")
            return self._touch(run)
        if _hash_snapshot(confirmation["action"]) != confirmation["operation_snapshot_hash"] or not self._resource_matches(confirmation["action"], user_id):
            confirmation["status"] = "TAMPERED"
            confirmation["decided_at"] = _now().isoformat()
            self._skip_unfinished_plan(run, reason="SNAPSHOT_TAMPERED")
            run.update(state="BLOCKED", message="操作快照校验失败，疑似发生参数篡改。", pending_action={"type": "NONE"})
            self._audit(run, "SNAPSHOT_VERIFY", "BLOCKED")
            return self._touch(run)
        confirmation["status"] = "USED"
        confirmation["decided_at"] = _now().isoformat()
        node_id = confirmation["action"].get("authorization_node_id")
        if not node_id:
            return self._execute_action(run, confirmation["action"])
        return self._execute_plan(
            run,
            resume_node_id=node_id,
            authorization={
                "node_id": node_id,
                "approved": True,
                "method": "CONFIRMATION",
                "reference_id": confirmation_id,
                "evidence_digest": confirmation["operation_snapshot_hash"],
                "action": confirmation["action"],
            },
        )

    def verify(self, verification_id: str, code: str, *, user_id: str) -> dict:
        verification = self.repo.verifications.get(verification_id)
        if not verification:
            raise KeyError("VERIFICATION_NOT_FOUND")
        if verification["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        run = self.repo.runs[verification["run_id"]]
        if verification["status"] != "PENDING":
            raise ValueError("REPLAY_DETECTED")
        if _now() > datetime.fromisoformat(verification["expires_at"]):
            verification["status"] = "EXPIRED"
            verification["decided_at"] = _now().isoformat()
            self.mfa.discard(verification_id)
            self._skip_unfinished_plan(run, reason="AUTHORIZATION_EXPIRED")
            run.update(state="ABORTED", message="强验证会话已过期，请重新发起操作。", pending_action={"type": "NONE"})
            self._audit(run, "STRONG_VERIFICATION", "EXPIRED")
            return self._touch(run)
        if run["state"] != "WAITING_VERIFICATION":
            raise ValueError("RUN_NOT_VERIFIABLE")
        if not self.mfa.matches(verification_id, code, verification["otp_hash"]):
            verification["failed_attempts"] += 1
            if verification["failed_attempts"] >= settings.mfa_max_attempts:
                verification["status"] = "LOCKED"
                verification["decided_at"] = _now().isoformat()
                self.mfa.discard(verification_id)
                run.update(
                    state="BLOCKED",
                    message=f"验证码连续错误 {settings.mfa_max_attempts} 次，操作已熔断。",
                    pending_action={"type": "NONE"},
                )
                self._skip_unfinished_plan(run, reason="MFA_CIRCUIT_BREAKER")
            else:
                remaining = settings.mfa_max_attempts - verification["failed_attempts"]
                run["message"] = f"验证码错误，还可尝试 {remaining} 次。"
            self._audit(run, "STRONG_VERIFICATION", "FAILED", {"attempts": verification["failed_attempts"]})
            return self._touch(run)
        if _hash_snapshot(verification["action"]) != verification["operation_snapshot_hash"] or not self._resource_matches(verification["action"], user_id):
            verification["status"] = "TAMPERED"
            verification["decided_at"] = _now().isoformat()
            self.mfa.discard(verification_id)
            self._skip_unfinished_plan(run, reason="SNAPSHOT_TAMPERED")
            run.update(state="BLOCKED", message="强验证绑定的操作快照已变化，执行被拒绝。", pending_action={"type": "NONE"})
            self._audit(run, "SNAPSHOT_VERIFY", "BLOCKED")
            return self._touch(run)
        verification["status"] = "VERIFIED"
        verification["verified_at"] = _now().isoformat()
        verification["decided_at"] = verification["verified_at"]
        self.mfa.discard(verification_id)
        node_id = verification["action"].get("authorization_node_id")
        if not node_id:
            return self._execute_action(run, verification["action"])
        return self._execute_plan(
            run,
            resume_node_id=node_id,
            authorization={
                "node_id": node_id,
                "approved": True,
                "method": "MFA",
                "reference_id": verification_id,
                "evidence_digest": verification["operation_snapshot_hash"],
                "action": verification["action"],
            },
        )

    def resend_verification(self, verification_id: str, *, user_id: str) -> dict:
        verification = self.repo.verifications.get(verification_id)
        if not verification:
            raise KeyError("VERIFICATION_NOT_FOUND")
        if verification["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        if verification["status"] != "PENDING":
            raise ValueError("VERIFICATION_NOT_PENDING")
        now = _now()
        if now > datetime.fromisoformat(verification["expires_at"]):
            verification["status"] = "EXPIRED"
            self.mfa.discard(verification_id)
            self.repo.flush()
            raise ValueError("VERIFICATION_EXPIRED")
        if (
            self.mfa.demo_code(verification_id) is not None
            and now < datetime.fromisoformat(verification["resend_available_at"])
        ):
            raise ValueError("MFA_RESEND_COOLDOWN")

        verification.update(
            otp_hash=self.mfa.issue(verification_id),
            failed_attempts=0,
            issued_at=now.isoformat(),
            resend_available_at=(
                now + timedelta(seconds=settings.mfa_resend_cooldown_seconds)
            ).isoformat(),
            expires_at=(now + timedelta(seconds=settings.mfa_code_ttl_seconds)).isoformat(),
        )
        run = self.repo.runs[verification["run_id"]]
        self._audit(run, "STRONG_VERIFICATION_RESEND", "SUCCEEDED")
        self._touch(run)
        return self._public_verification(verification)

    def get_demo_verification_code(self, verification_id: str, *, user_id: str) -> dict:
        verification = self.repo.verifications.get(verification_id)
        if not verification:
            raise KeyError("VERIFICATION_NOT_FOUND")
        if verification["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        if verification["status"] != "PENDING":
            raise ValueError("VERIFICATION_NOT_PENDING")
        if _now() > datetime.fromisoformat(verification["expires_at"]):
            raise ValueError("VERIFICATION_EXPIRED")
        code = self.mfa.demo_code(verification_id)
        if code is None:
            raise ValueError("DEMO_CODE_UNAVAILABLE")
        return {
            "verification_id": verification_id,
            "code": code,
            "delivery_channel": "MOCK_SMS",
            "expires_at": verification["expires_at"],
        }

    def reset_demo(self, *, user_id: str | None = None) -> None:
        self.mfa.clear()
        self.repo.reset()
        if user_id is not None and self.life_graph is not None:
            self.life_graph.reset_owner(user_id)

    def _resource_matches(self, action: dict, user_id: str) -> bool:
        if action.get("user_id") != user_id:
            return False
        if action["kind"] == "transfer.execute":
            preview = self.repo.previews.get(action["preview_id"])
            if not preview or preview["user_id"] != user_id:
                return False
            current = {
                "user_id": preview["user_id"],
                "preview_id": preview["preview_id"],
                "from_account_id": preview["from_account_id"],
                "recipient_id": preview["recipient"]["id"],
                "amount": preview["amount"],
                "currency": preview["currency"],
            }
        elif action["kind"] == "subscription.cancel":
            subscription = next(
                (
                    item
                    for item in self.repo.subscriptions
                    if item["id"] == action["subscription_id"] and item["user_id"] == user_id
                ),
                None,
            )
            if not subscription:
                return False
            current = {
                "user_id": subscription["user_id"],
                "subscription_id": subscription["id"],
                "amount": subscription["amount"],
                "status": subscription["status"],
            }
        else:
            return False
        return _hash_snapshot(current) == action["resource_snapshot_hash"]

    def _execute_action(self, run: dict, action: dict) -> dict:
        if action.get("user_id") != run["user_id"]:
            raise PermissionError("RESOURCE_FORBIDDEN")
        run.update(state="RUNNING", pending_action={"type": "NONE"})
        try:
            if action["kind"] == "subscription.cancel":
                result = self.bank.cancel_subscription(run["user_id"], action["subscription_id"])
                run.update(state="SUCCEEDED", message=f"已取消 {result['name']}，后续不再自动扣费。", data={"subscription": result})
                self._audit(run, "SUBSCRIPTION_CANCEL_EXECUTE", "SUCCEEDED", result)
            elif action["kind"] == "transfer.execute":
                result = self.bank.execute_transfer(run["user_id"], action["preview_id"])
                run.update(state="SUCCEEDED", message="转账已由银行沙箱确认成功。", data={"transfer_result": result})
                self._audit(run, "TRANSFER_EXECUTE", "SUCCEEDED", {"transaction_id": result["transaction_id"]})
        except ValueError as error:
            code = str(error)
            run.update(state="FAILED", message=f"银行沙箱拒绝执行：{code}。系统不会声称操作成功。", data={"error_code": code})
            self._audit(run, "BANK_TOOL_EXECUTE", "FAILED", {"error_code": code})
        return self._touch(run)

    def get_run(self, run_id: str, *, user_id: str) -> dict:
        if run_id not in self.repo.runs:
            raise KeyError("RUN_NOT_FOUND")
        run = self.repo.runs[run_id]
        if run["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        return run.copy()

    def decide_life_graph_change(
        self,
        *,
        request_id: str,
        approved: bool,
        reason: str | None,
        user_id: str,
        service: LifeGraphService | None = None,
    ) -> dict:
        graph_service = service or self.life_graph
        if graph_service is None:
            raise LifeGraphServiceError("LIFE_GRAPH_NOT_CONFIGURED")
        try:
            request = graph_service.get_change_request(user_id, request_id)
        except LifeGraphServiceError as error:
            if error.code != "CHANGE_REQUEST_NOT_FOUND":
                raise
            return graph_service.decide_change_request(
                owner_user_id=user_id,
                request_id=request_id,
                approved=approved,
                reason=reason,
            )
        source_run_id = request.get("source_run_id")
        if source_run_id is None:
            source_run_id = next(
                (
                    candidate["run_id"]
                    for candidate in self.repo.runs.values()
                    if candidate.get("user_id") == user_id
                    and candidate.get("pending_action", {})
                    .get("life_graph_change_request", {})
                    .get("request_id") == request_id
                ),
                None,
            )
        if source_run_id is None or source_run_id not in self.repo.runs:
            return graph_service.decide_change_request(
                owner_user_id=user_id,
                request_id=request_id,
                approved=approved,
                reason=reason,
            )

        run = self.repo.runs[source_run_id]
        if run["user_id"] != user_id:
            raise LifeGraphServiceError("RESOURCE_FORBIDDEN")
        pending = run.get("pending_action", {}).get("life_graph_change_request", {})
        if pending.get("request_id") != request_id:
            raise LifeGraphServiceError("CHANGE_REQUEST_REPLAYED")

        waiting_node = next(
            (
                node
                for node in run.get("plan", {}).get("nodes", [])
                if node.get("type") == "HUMAN_CONFIRM"
                and node.get("runtime", {}).get("status") == "WAITING"
            ),
            None,
        )
        if waiting_node is None:
            raise LifeGraphServiceError("CHANGE_REQUEST_REPLAYED")

        if not approved:
            result = graph_service.decide_change_request(
                owner_user_id=user_id,
                request_id=request_id,
                approved=False,
                reason=reason,
            )
            self._skip_unfinished_plan(run, reason="USER_REJECTED")
            state = self._workflow_step(run, "DECISION", decision_status=result["status"])
            run["data"]["life_graph_change_request"] = result
            run.update(
                state=state["run_state"],
                message=state["message"],
                pending_action={"type": "NONE"},
            )
            self._audit(run, "LIFE_GRAPH_DECISION", result["status"], {"request_id": request_id})
            self._touch(run)
            return result

        self._execute_plan(
            run,
            resume_node_id=waiting_node["id"],
            authorization={
                "node_id": waiting_node["id"],
                "approved": True,
                "method": "LIFE_GRAPH_CONFIRMATION",
                "reference_id": request_id,
                "evidence_digest": request["snapshot_hash"],
            },
            values={"life_graph_service": graph_service},
        )
        result = graph_service.get_change_request(user_id, request_id)
        if result["status"] != "APPROVED":
            raise LifeGraphServiceError("LIFE_GRAPH_EXECUTION_FAILED")
        return result

    def record_life_graph_decision(
        self,
        *,
        source_run_id: str | None,
        request_id: str,
        request_status: str,
        user_id: str,
    ) -> None:
        if source_run_id is None or source_run_id not in self.repo.runs:
            return
        run = self.repo.runs[source_run_id]
        if run["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        pending = run.get("pending_action", {}).get("life_graph_change_request", {})
        if pending.get("request_id") != request_id:
            return
        if run.get("plan", {}).get("execution_mode") == "EXECUTABLE":
            waiting_node = next(
                (
                    node
                    for node in run["plan"]["nodes"]
                    if node.get("type") == "HUMAN_CONFIRM"
                    and node.get("runtime", {}).get("status") == "WAITING"
                ),
                None,
            )
            if request_status == "APPROVED" and waiting_node is not None:
                request = run["data"]["life_graph_change_request"]
                self._execute_plan(
                    run,
                    resume_node_id=waiting_node["id"],
                    authorization={
                        "node_id": waiting_node["id"],
                        "approved": True,
                        "method": "LIFE_GRAPH_EXTERNAL_DECISION",
                        "reference_id": request_id,
                        "evidence_digest": request["snapshot_hash"],
                    },
                    recovery=True,
                )
                return
            self._skip_unfinished_plan(run, reason=f"CHANGE_REQUEST_{request_status}")
        previous = run.get("workflow") or {
            "stage": "WAITING_CONFIRMATION",
            "checkpoint_seq": 0,
            "trace": [],
            "change_request_id": request_id,
        }
        workflow_state = self.life_workflow.invoke({
            "event": "DECISION",
            "run_id": run["run_id"],
            "user_id": run["user_id"],
            "session_id": run["session_id"],
            "decision_status": request_status,
            "previous_workflow": previous,
            "trace": list(previous.get("trace", [])),
        })
        run["workflow"] = self.life_workflow.checkpoint(workflow_state, previous)
        run.update(
            state=workflow_state["run_state"],
            message=workflow_state["message"],
            pending_action={"type": "NONE"},
        )
        if isinstance(run.get("data", {}).get("life_graph_change_request"), dict):
            run["data"]["life_graph_change_request"]["status"] = request_status
        self._audit(
            run,
            "LIFE_GRAPH_DECISION",
            request_status,
            {
                "request_id": request_id,
                "workflow_stage": workflow_state["stage"],
                "workflow_trace": workflow_state["trace"],
            },
        )
        self._touch(run)

    def request_human_handoff(
        self,
        run_id: str,
        *,
        user_id: str,
        reason: str,
    ) -> dict:
        self.get_run(run_id, user_id=user_id)
        run = self.repo.runs[run_id]
        if run["state"] == "WAITING_HUMAN":
            return run.copy()
        if run["state"] in {"SUCCEEDED", "BLOCKED", "ABORTED"}:
            raise ValueError("RUN_NOT_HANDOFFABLE")

        self._cancel_pending_authorizations(run)
        self._skip_unfinished_plan(run, reason="HUMAN_HANDOFF")
        candidate_status = self._reject_pending_life_candidate(
            run,
            reason="HUMAN_HANDOFF",
        )
        session = self.repo.sessions.get(run["session_id"])
        if session and session.get("pending_run_id") == run_id:
            self._clear_pending(
                session,
                previous_state="WAITING_HUMAN",
                message="自动补槽已冻结，任务转入人工处理。",
            )

        if run.get("intent", {}).get("name") == "life.event_plan":
            previous = run.get("workflow") or {
                "stage": run["state"],
                "checkpoint_seq": 0,
                "trace": [],
            }
            workflow_state = self.life_workflow.invoke({
                "event": "HANDOFF",
                "run_id": run["run_id"],
                "user_id": run["user_id"],
                "session_id": run["session_id"],
                "previous_workflow": previous,
                "handoff_reason": reason,
                "trace": list(previous.get("trace", [])),
            })
            run["workflow"] = self.life_workflow.checkpoint(
                workflow_state,
                previous,
            )
            message = workflow_state["message"]
        else:
            message = "自动流程已冻结，任务已转入人工处理队列。"

        run.update(
            state="WAITING_HUMAN",
            message=message,
            pending_action={"type": "NONE"},
        )
        run.setdefault("data", {})["human_handoff"] = {
            "status": "PENDING",
            "reason": reason,
            "requested_at": _now().isoformat(),
            "candidate_status": candidate_status,
        }
        self._audit(
            run,
            "HUMAN_HANDOFF",
            "WAITING_HUMAN",
            {
                "reason": reason,
                "candidate_status": candidate_status,
                "automatic_execution_frozen": True,
            },
        )
        return self._touch(run)

    def get_session(self, session_id: str, *, user_id: str) -> dict:
        session = self._get_session(user_id, session_id)
        return self._session_response(session)

    def list_audits(self, run_id: str | None = None, *, user_id: str) -> list[dict]:
        items = [item for item in self.repo.audit_logs if item["user_id"] == user_id]
        if run_id:
            items = [item for item in items if item["run_id"] == run_id]
        return [redact_sensitive(deepcopy(item)) for item in items]

    def get_operation_passport(self, run_id: str, *, user_id: str) -> dict:
        return OperationPassportService(self.repo).get(run_id, user_id=user_id)

    def abort(self, run_id: str, *, user_id: str) -> dict:
        self.get_run(run_id, user_id=user_id)
        run = self.repo.runs[run_id]
        if run["state"] in {"SUCCEEDED", "BLOCKED", "ABORTED"}:
            return run.copy()
        self._cancel_pending_authorizations(run)
        self._skip_unfinished_plan(run, reason="USER_ABORTED")
        candidate_status = self._reject_pending_life_candidate(run, reason="USER_ABORTED")
        session = self.repo.sessions.get(run["session_id"])
        if session and session.get("pending_run_id") == run_id:
            self._clear_pending(
                session,
                previous_state="ABORTED",
                message="任务已由用户主动中断。",
            )

        message = "任务已人工中断。"
        if run.get("intent", {}).get("name") == "life.event_plan":
            previous = run.get("workflow") or {
                "stage": run["state"],
                "checkpoint_seq": 0,
                "trace": [],
            }
            workflow_state = self.life_workflow.invoke({
                "event": "ABORT",
                "run_id": run["run_id"],
                "user_id": run["user_id"],
                "session_id": run["session_id"],
                "previous_workflow": previous,
                "trace": list(previous.get("trace", [])),
            })
            run["workflow"] = self.life_workflow.checkpoint(
                workflow_state,
                previous,
            )
            message = workflow_state["message"]
        run.update(
            state="ABORTED",
            message=message,
            pending_action={"type": "NONE"},
        )
        self._audit(
            run,
            "HUMAN_ABORT",
            "ABORTED",
            {"candidate_status": candidate_status},
        )
        return self._touch(run)

    def _cancel_pending_authorizations(self, run: dict) -> None:
        for confirmation in self.repo.confirmations.values():
            if (
                confirmation["run_id"] == run["run_id"]
                and confirmation["status"] == "PENDING"
            ):
                confirmation["status"] = "CANCELED"
        for verification in self.repo.verifications.values():
            if (
                verification["run_id"] == run["run_id"]
                and verification["status"] == "PENDING"
            ):
                verification["status"] = "CANCELED"
                self.mfa.discard(verification["verification_id"])

    def _reject_pending_life_candidate(
        self,
        run: dict,
        *,
        reason: str,
    ) -> str | None:
        pending = run.get("pending_action", {}).get("life_graph_change_request")
        if not pending or self.life_graph is None:
            return None
        request_id = pending.get("request_id")
        if not request_id:
            return None
        try:
            request = self.life_graph.decide_change_request(
                owner_user_id=run["user_id"],
                request_id=request_id,
                approved=False,
                reason=reason,
            )
        except LifeGraphServiceError as error:
            if error.code != "CHANGE_REQUEST_REPLAYED":
                raise ValueError("HANDOFF_PREPARATION_FAILED") from error
            request = self.life_graph.get_change_request(run["user_id"], request_id)
            if request["status"] == "APPROVED":
                raise ValueError("RUN_NOT_HANDOFFABLE") from error
        if isinstance(run.get("data", {}).get("life_graph_change_request"), dict):
            run["data"]["life_graph_change_request"]["status"] = request["status"]
        return request["status"]

    def _touch(self, run: dict) -> dict:
        run["updated_at"] = _now().isoformat()
        self.repo.flush()
        return run.copy()


runtime = AgentRuntime(repository)
