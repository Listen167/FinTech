"""生活关系图候选变更确认与原子写入服务。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.models import (
    AgentRunRecord,
    EventActionRecord,
    FinancialGoalRecord,
    LifeEventRecord,
    LifeGraphChangeRequestRecord,
    LifeGraphEdgeRecord,
    LifeGraphNodeRecord,
    utc_now,
)
from backend.app.errors import ApplicationError
from backend.app.repositories.life_graph import (
    IdempotencyConflictError,
    LifeGraphRepository,
    NewLifeGraphChangeRequest,
    hash_change_set,
)
from backend.app.schemas.life_graph import LifeGraphChangeSet
from backend.app.schemas.life_graph import LifeGraphNodeCorrection


class LifeGraphServiceError(ApplicationError):
    pass


class LifeGraphService:
    def __init__(
        self,
        engine: Engine,
        repository: LifeGraphRepository | None = None,
        *,
        action_max_attempts: int | None = None,
    ) -> None:
        self.engine = engine
        self.repository = repository or LifeGraphRepository(engine)
        self.action_max_attempts = action_max_attempts or settings.life_radar_max_attempts

    def create_change_request(
        self,
        *,
        owner_user_id: str,
        idempotency_key: str,
        summary: str,
        change_set: LifeGraphChangeSet,
        source_run_id: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        created_at = now or utc_now()
        if source_run_id is not None:
            with Session(self.engine) as database:
                run = database.get(AgentRunRecord, source_run_id)
                if run is None:
                    raise LifeGraphServiceError("SOURCE_RUN_NOT_FOUND")
                if run.user_id != owner_user_id:
                    raise LifeGraphServiceError("RESOURCE_FORBIDDEN")

        try:
            return self.repository.create_change_request(NewLifeGraphChangeRequest(
                request_id=f"change_{uuid4().hex[:16]}",
                owner_user_id=owner_user_id,
                idempotency_key=idempotency_key,
                summary=summary,
                change_set=change_set.model_dump(mode="json"),
                expires_at=created_at + timedelta(minutes=15),
                source="STRUCTURED_INPUT",
                source_run_id=source_run_id,
                schema_version=1,
                created_at=created_at,
            ))
        except IdempotencyConflictError as exc:
            raise LifeGraphServiceError(str(exc)) from exc

    def get_graph(self, owner_user_id: str) -> dict:
        return self.repository.get_graph(owner_user_id)

    def reset_owner(self, owner_user_id: str) -> dict[str, int]:
        return self.repository.reset_owner(owner_user_id)

    def get_change_request(self, owner_user_id: str, request_id: str) -> dict:
        result = self.repository.get_change_request(owner_user_id, request_id)
        if result is None:
            raise LifeGraphServiceError("CHANGE_REQUEST_NOT_FOUND")
        return result

    def list_change_requests(
        self, owner_user_id: str, *, status: str | None = None
    ) -> list[dict]:
        return self.repository.list_change_requests(owner_user_id, status=status)

    def correct_node(
        self,
        *,
        owner_user_id: str,
        node_id: str,
        correction: LifeGraphNodeCorrection,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict:
        correction_time = now or utc_now()
        correction_payload = {
            "node_id": node_id,
            "changes": correction.model_dump(mode="json", exclude_none=True),
        }
        snapshot_hash = hash_change_set(correction_payload)
        correction_id: str | None = None

        try:
            with Session(self.engine) as database, database.begin():
                existing = database.scalar(
                    select(LifeGraphChangeRequestRecord).where(
                        LifeGraphChangeRequestRecord.owner_user_id == owner_user_id,
                        LifeGraphChangeRequestRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if (
                        existing.source != "USER_CORRECTION"
                        or existing.snapshot_hash != snapshot_hash
                    ):
                        raise LifeGraphServiceError("CORRECTION_IDEMPOTENCY_CONFLICT")
                    correction_id = existing.request_id
                else:
                    node = database.get(LifeGraphNodeRecord, node_id)
                    if node is None or node.owner_user_id != owner_user_id:
                        raise LifeGraphServiceError("LIFE_GRAPH_NODE_NOT_FOUND")
                    if node.status != "ACTIVE":
                        raise LifeGraphServiceError("LIFE_GRAPH_NODE_INACTIVE")

                    if correction.display_label is not None:
                        node.display_label = correction.display_label
                        node.updated_at = correction_time

                    if correction.event_date is not None:
                        if node.node_type != "EVENT":
                            raise LifeGraphServiceError("EVENT_CORRECTION_TYPE_MISMATCH")
                        event = database.get(LifeEventRecord, node_id)
                        if event is None or event.owner_user_id != owner_user_id:
                            raise LifeGraphServiceError("EVENT_RECORD_NOT_FOUND")
                        event.occurs_at = datetime.combine(
                            correction.event_date, event.occurs_at.time() or time.min
                        )
                        event.updated_at = correction_time
                        actions = database.scalars(
                            select(EventActionRecord).where(
                                EventActionRecord.owner_user_id == owner_user_id,
                                EventActionRecord.event_node_id == node_id,
                                EventActionRecord.status.in_(("PENDING", "FAILED", "PAUSED")),
                            )
                        ).all()
                        for action in actions:
                            days_before = action.payload.get("days_before")
                            if isinstance(days_before, int) and days_before >= 0:
                                action.scheduled_at = datetime.combine(
                                    correction.event_date - timedelta(days=days_before),
                                    action.scheduled_at.time(),
                                )
                                action.updated_at = correction_time

                    if correction.target_amount is not None:
                        if node.node_type != "GOAL":
                            raise LifeGraphServiceError("GOAL_CORRECTION_TYPE_MISMATCH")
                        goal = database.get(FinancialGoalRecord, node_id)
                        if goal is None or goal.owner_user_id != owner_user_id:
                            raise LifeGraphServiceError("GOAL_RECORD_NOT_FOUND")
                        if correction.target_amount < goal.reserved_amount:
                            raise LifeGraphServiceError("TARGET_AMOUNT_BELOW_RESERVED")
                        goal.target_amount = correction.target_amount
                        goal.updated_at = correction_time

                    correction_id = f"correction_{uuid4().hex[:16]}"
                    database.add(LifeGraphChangeRequestRecord(
                        request_id=correction_id,
                        owner_user_id=owner_user_id,
                        source_run_id=None,
                        source="USER_CORRECTION",
                        status="APPROVED",
                        schema_version=1,
                        idempotency_key=idempotency_key,
                        snapshot_hash=snapshot_hash,
                        summary=f"纠正 {node.display_label}：{correction.reason}",
                        change_set=correction_payload,
                        decision_reason=correction.reason,
                        expires_at=correction_time,
                        decided_at=correction_time,
                        created_at=correction_time,
                        updated_at=correction_time,
                    ))
                    database.flush()
        except IntegrityError as exc:
            raise LifeGraphServiceError("CHANGE_SET_CONFLICT") from exc

        return {
            "correction_id": correction_id,
            "graph": self.repository.get_graph(owner_user_id),
        }

    def decide_change_request(
        self,
        *,
        owner_user_id: str,
        request_id: str,
        approved: bool,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        decision_time = now or utc_now()
        terminal_error: LifeGraphServiceError | None = None

        try:
            with Session(self.engine) as database, database.begin():
                request = database.scalar(
                    select(LifeGraphChangeRequestRecord)
                    .where(LifeGraphChangeRequestRecord.request_id == request_id)
                    .with_for_update()
                )
                if request is None:
                    raise LifeGraphServiceError("CHANGE_REQUEST_NOT_FOUND")
                if request.owner_user_id != owner_user_id:
                    raise LifeGraphServiceError("RESOURCE_FORBIDDEN")
                if request.status != "PENDING":
                    raise LifeGraphServiceError("CHANGE_REQUEST_REPLAYED")

                if decision_time > request.expires_at:
                    self._finish_request(
                        request,
                        status="EXPIRED",
                        reason="CHANGE_REQUEST_EXPIRED",
                        now=decision_time,
                    )
                    terminal_error = LifeGraphServiceError("CHANGE_REQUEST_EXPIRED")
                elif not approved:
                    self._finish_request(
                        request,
                        status="REJECTED",
                        reason=reason or "USER_REJECTED",
                        now=decision_time,
                    )
                elif hash_change_set(request.change_set, request.schema_version) != request.snapshot_hash:
                    self._finish_request(
                        request,
                        status="CANCELED",
                        reason="SNAPSHOT_MISMATCH",
                        now=decision_time,
                    )
                    terminal_error = LifeGraphServiceError("CHANGE_REQUEST_TAMPERED")
                else:
                    try:
                        change_set = LifeGraphChangeSet.model_validate(request.change_set)
                    except ValidationError:
                        self._finish_request(
                            request,
                            status="CANCELED",
                            reason="CHANGE_SET_SCHEMA_INVALID",
                            now=decision_time,
                        )
                        terminal_error = LifeGraphServiceError("CHANGE_SET_SCHEMA_INVALID")
                    else:
                        self._apply_change_set(
                            database,
                            owner_user_id=owner_user_id,
                            change_set=change_set,
                            now=decision_time,
                        )
                        self._finish_request(
                            request,
                            status="APPROVED",
                            reason=reason or "USER_APPROVED",
                            now=decision_time,
                        )
        except IntegrityError as exc:
            raise LifeGraphServiceError("CHANGE_SET_CONFLICT") from exc

        if terminal_error is not None:
            raise terminal_error
        result = self.repository.get_change_request(owner_user_id, request_id)
        if result is None:
            raise LifeGraphServiceError("CHANGE_REQUEST_NOT_FOUND")
        return result

    def _apply_change_set(
        self,
        database: Session,
        *,
        owner_user_id: str,
        change_set: LifeGraphChangeSet,
        now: datetime,
    ) -> None:
        proposed_node_types = {item.node_id: item.node_type for item in change_set.nodes}
        proposed_node_ids = set(proposed_node_types)
        referenced_node_ids = {
            node_id
            for edge in change_set.edges
            for node_id in (edge.source_node_id, edge.target_node_id)
        }
        referenced_node_ids.update(item.event_node_id for item in change_set.events)
        referenced_node_ids.update(item.goal_node_id for item in change_set.goals)

        existing_nodes = {
            row.node_id: row.node_type
            for row in database.scalars(
                select(LifeGraphNodeRecord).where(
                    LifeGraphNodeRecord.owner_user_id == owner_user_id,
                    LifeGraphNodeRecord.node_id.in_(proposed_node_ids | referenced_node_ids),
                )
            ).all()
        }
        if proposed_node_ids & existing_nodes.keys():
            raise LifeGraphServiceError("NODE_ALREADY_EXISTS")

        available_node_types = existing_nodes | proposed_node_types
        if referenced_node_ids - available_node_types.keys():
            raise LifeGraphServiceError("CHANGE_SET_REFERENCE_NOT_FOUND")
        for item in change_set.events:
            if available_node_types[item.event_node_id] != "EVENT":
                raise LifeGraphServiceError("EVENT_NODE_TYPE_MISMATCH")
        for item in change_set.goals:
            if available_node_types[item.goal_node_id] != "GOAL":
                raise LifeGraphServiceError("GOAL_NODE_TYPE_MISMATCH")

        proposed_event_ids = {item.event_node_id for item in change_set.events}
        proposed_goal_ids = {item.goal_node_id for item in change_set.goals}
        action_event_ids = {
            item.event_node_id for item in change_set.actions if item.event_node_id is not None
        }
        action_goal_ids = {
            item.goal_node_id for item in change_set.actions if item.goal_node_id is not None
        }
        existing_event_ids = set(database.scalars(
            select(LifeEventRecord.event_node_id).where(
                LifeEventRecord.owner_user_id == owner_user_id,
                LifeEventRecord.event_node_id.in_(action_event_ids),
            )
        ).all())
        existing_goal_ids = set(database.scalars(
            select(FinancialGoalRecord.goal_node_id).where(
                FinancialGoalRecord.owner_user_id == owner_user_id,
                FinancialGoalRecord.goal_node_id.in_(action_goal_ids),
            )
        ).all())
        if action_event_ids - (existing_event_ids | proposed_event_ids):
            raise LifeGraphServiceError("ACTION_EVENT_NOT_FOUND")
        if action_goal_ids - (existing_goal_ids | proposed_goal_ids):
            raise LifeGraphServiceError("ACTION_GOAL_NOT_FOUND")

        database.add_all([
            LifeGraphNodeRecord(
                node_id=item.node_id,
                owner_user_id=owner_user_id,
                node_type=item.node_type,
                display_label=item.display_label,
                sensitivity_level=item.sensitivity_level,
                status="ACTIVE",
                attributes=item.attributes,
                created_at=now,
                updated_at=now,
            )
            for item in change_set.nodes
        ])
        database.flush()

        database.add_all([
            LifeEventRecord(
                event_node_id=item.event_node_id,
                owner_user_id=owner_user_id,
                event_type=item.event_type,
                occurs_at=item.occurs_at,
                timezone=item.timezone,
                recurrence_rule=item.recurrence_rule,
                status="ACTIVE",
                details=item.details,
                created_at=now,
                updated_at=now,
            )
            for item in change_set.events
        ])
        database.add_all([
            FinancialGoalRecord(
                goal_node_id=item.goal_node_id,
                owner_user_id=owner_user_id,
                target_amount=item.target_amount,
                reserved_amount=item.reserved_amount,
                currency=item.currency,
                due_at=item.due_at,
                funding_policy=item.funding_policy,
                status="DRAFT",
                created_at=now,
                updated_at=now,
            )
            for item in change_set.goals
        ])
        database.add_all([
            LifeGraphEdgeRecord(
                edge_id=item.edge_id,
                owner_user_id=owner_user_id,
                source_node_id=item.source_node_id,
                target_node_id=item.target_node_id,
                relation_type=item.relation_type,
                consent_scope=item.consent_scope,
                provenance=item.provenance,
                confidence_bp=item.confidence_bp,
                status="ACTIVE",
                valid_from=item.valid_from,
                valid_until=item.valid_until,
                attributes=item.attributes,
                created_at=now,
                updated_at=now,
            )
            for item in change_set.edges
        ])
        database.flush()

        database.add_all([
            EventActionRecord(
                action_id=item.action_id,
                owner_user_id=owner_user_id,
                event_node_id=item.event_node_id,
                goal_node_id=item.goal_node_id,
                action_type=item.action_type,
                scheduled_at=item.scheduled_at,
                status="PENDING",
                attempt_count=0,
                max_attempts=self.action_max_attempts,
                delivery_receipt={},
                idempotency_key=item.idempotency_key,
                payload=item.payload,
                created_at=now,
                updated_at=now,
            )
            for item in change_set.actions
        ])
        database.flush()

    @staticmethod
    def _finish_request(
        request: LifeGraphChangeRequestRecord,
        *,
        status: str,
        reason: str,
        now: datetime,
    ) -> None:
        request.status = status
        request.decision_reason = reason[:255]
        request.decided_at = now
        request.updated_at = now
