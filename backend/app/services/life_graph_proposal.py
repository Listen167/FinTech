"""把已补全的生活事件意图转换为可确认的图谱候选变更。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.schemas.life_graph import LifeGraphChangeSet


class LifeGraphProposalError(ValueError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _find_self_node(graph: dict) -> dict | None:
    return next(
        (
            node
            for node in graph["nodes"]
            if node["node_type"] == "PERSON"
            and node.get("attributes", {}).get("is_account_owner") is True
        ),
        None,
    )


def _find_related_person(graph: dict, self_node_id: str, label: str, relation_type: str) -> dict | None:
    nodes_by_id = {node["node_id"]: node for node in graph["nodes"]}
    related_ids = {
        edge["target_node_id"]
        for edge in graph["edges"]
        if edge["source_node_id"] == self_node_id
        and edge["relation_type"] == relation_type
        and edge["status"] == "ACTIVE"
    }
    relation_match = next(
        (nodes_by_id[node_id] for node_id in related_ids if node_id in nodes_by_id),
        None,
    )
    if relation_match is not None:
        return relation_match
    return next(
        (
            node
            for node in graph["nodes"]
            if node["node_type"] == "PERSON" and node["display_label"] == label
        ),
        None,
    )


def _find_existing_event(graph: dict, person_node_id: str, event_type: str, event_date: date) -> str | None:
    event_ids = {
        edge["target_node_id"]
        for edge in graph["edges"]
        if edge["source_node_id"] == person_node_id
        and edge["relation_type"] == "HAS_EVENT"
        and edge["status"] == "ACTIVE"
    }
    return next(
        (
            event["event_node_id"]
            for event in graph["events"]
            if event["event_node_id"] in event_ids
            and event["event_type"] == event_type
            and datetime.fromisoformat(event["occurs_at"]).date() == event_date
        ),
        None,
    )


def build_life_graph_proposal(
    repository: LifeGraphRepository,
    *,
    owner_user_id: str,
    slots: dict,
) -> tuple[LifeGraphChangeSet, str]:
    required = ("related_person", "relation_type", "event_type", "event_date")
    if any(not slots.get(field) for field in required):
        raise LifeGraphProposalError("LIFE_EVENT_SLOTS_INCOMPLETE")
    if slots.get("goal_required") and slots.get("target_amount") is None:
        raise LifeGraphProposalError("LIFE_GOAL_AMOUNT_REQUIRED")

    try:
        event_date = date.fromisoformat(slots["event_date"])
    except (TypeError, ValueError) as exc:
        raise LifeGraphProposalError("LIFE_EVENT_DATE_INVALID") from exc

    graph = repository.get_graph(owner_user_id)
    event_names = {
        "BIRTHDAY": "生日",
        "TUITION_DUE": "学费缴纳",
        "ANNIVERSARY": "纪念日",
    }
    event_label = slots.get("event_label") or (
        f"{slots['related_person']}{event_names.get(slots['event_type'], '生活事件')}"
    )
    nodes: list[dict] = []
    edges: list[dict] = []
    events: list[dict] = []
    goals: list[dict] = []
    actions: list[dict] = []

    self_node = _find_self_node(graph)
    if self_node is None:
        self_node_id = _new_id("person_self")
        nodes.append({
            "node_id": self_node_id,
            "node_type": "PERSON",
            "display_label": "本人",
            "sensitivity_level": "PERSONAL",
            "attributes": {"is_account_owner": True},
        })
    else:
        self_node_id = self_node["node_id"]

    related_person = _find_related_person(
        graph,
        self_node_id,
        slots["related_person"],
        slots["relation_type"],
    )
    if related_person is None:
        person_node_id = _new_id("person")
        nodes.append({
            "node_id": person_node_id,
            "node_type": "PERSON",
            "display_label": slots["related_person"],
            "sensitivity_level": "SENSITIVE",
            "attributes": {"relation_label": slots["related_person"]},
        })
        edges.append({
            "edge_id": _new_id("edge"),
            "source_node_id": self_node_id,
            "target_node_id": person_node_id,
            "relation_type": slots["relation_type"],
            "consent_scope": {"allowed_uses": ["REMINDER", "GOAL_PLANNING"]},
            "provenance": "USER_CONFIRMED",
            "confidence_bp": 10000,
            "attributes": {},
        })
    else:
        person_node_id = related_person["node_id"]

    event_node_id = _find_existing_event(
        graph, person_node_id, slots["event_type"], event_date
    )
    if event_node_id is None:
        event_node_id = _new_id("event")
        nodes.append({
            "node_id": event_node_id,
            "node_type": "EVENT",
            "display_label": event_label,
            "sensitivity_level": "SENSITIVE",
            "attributes": {},
        })
        events.append({
            "event_node_id": event_node_id,
            "event_type": slots["event_type"],
            "occurs_at": datetime.combine(event_date, time.min).isoformat(),
            "timezone": "Asia/Shanghai",
            "recurrence_rule": slots.get("recurrence_rule"),
            "details": {},
        })
        edges.append({
            "edge_id": _new_id("edge"),
            "source_node_id": person_node_id,
            "target_node_id": event_node_id,
            "relation_type": "HAS_EVENT",
            "consent_scope": {"allowed_uses": ["REMINDER", "GOAL_PLANNING"]},
            "provenance": "USER_CONFIRMED",
            "confidence_bp": 10000,
            "attributes": {},
        })

    goal_node_id: str | None = None
    if slots.get("goal_required"):
        target_amount = Decimal(str(slots["target_amount"])).quantize(Decimal("0.01"))
        goal_node_id = _new_id("goal")
        nodes.append({
            "node_id": goal_node_id,
            "node_type": "GOAL",
            "display_label": slots.get("goal_label") or "生活资金目标",
            "sensitivity_level": "PERSONAL",
            "attributes": {},
        })
        goals.append({
            "goal_node_id": goal_node_id,
            "target_amount": str(target_amount),
            "reserved_amount": "0.00",
            "currency": slots.get("currency") or "CNY",
            "due_at": datetime.combine(event_date, time.min).isoformat(),
            "funding_policy": {"mode": "RESERVE_AFTER_CONFIRMATION"},
        })
        edges.append({
            "edge_id": _new_id("edge"),
            "source_node_id": goal_node_id,
            "target_node_id": event_node_id,
            "relation_type": "TRIGGERED_BY",
            "consent_scope": {"allowed_uses": ["GOAL_PLANNING"]},
            "provenance": "USER_CONFIRMED",
            "confidence_bp": 10000,
            "attributes": {},
        })

    reminder_days = slots.get("reminder_days_before")
    if reminder_days is not None:
        scheduled_at = datetime.combine(event_date - timedelta(days=reminder_days), time(9, 0))
        actions.append({
            "action_id": _new_id("action"),
            "event_node_id": event_node_id,
            "goal_node_id": goal_node_id,
            "action_type": "REMIND_LIFE_EVENT",
            "scheduled_at": scheduled_at.isoformat(),
            "idempotency_key": f"{event_node_id}:reminder:{reminder_days}",
            "payload": {
                "event_label": event_label,
                "days_before": reminder_days,
            },
        })

    change_set = LifeGraphChangeSet.model_validate({
        "nodes": nodes,
        "edges": edges,
        "events": events,
        "goals": goals,
        "actions": actions,
    })
    summary_parts = [f"记录{event_label}（{event_date.isoformat()}）"]
    if goal_node_id is not None:
        summary_parts.append(f"建立 {Decimal(str(slots['target_amount'])):.2f} 元资金目标")
    if reminder_days is not None:
        summary_parts.append(f"提前 {reminder_days} 天提醒")
    return change_set, "，".join(summary_parts)
