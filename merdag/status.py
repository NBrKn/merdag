from __future__ import annotations

from pathlib import Path

from merdag.parser import Node, Plan, TIER_SUFFIX, dependencies_met, node_to_dict, parse_plan

STATUS_ICON = {
    "done": "✅",
    "in_progress": "🔄",
    "waiting": "⏳",
    "failed": "❌",
    "pending_decision": "🟡",
}


def available_nodes_from_plan(plan: Plan, tier: str | None = None) -> list[Node]:
    available: list[Node] = []
    for node in sorted(plan.nodes.values(), key=lambda item: item.id):
        if node.status not in {"waiting", "pending_decision"}:
            continue
        if tier and node.tier != tier:
            continue
        if dependencies_met(plan, node.id):
            available.append(node)
    return available


def available_nodes(plan_path: str | Path, tier: str | None = None) -> list[Node]:
    return available_nodes_from_plan(parse_plan(plan_path), tier=tier)


def ready_node_payloads_from_plan(plan: Plan, tier: str | None = None) -> list[dict[str, str | None]]:
    return [
        {
            "id": node.id,
            "label": node.label,
            "tier": node.tier,
            "type": node.node_type,
        }
        for node in available_nodes_from_plan(plan, tier=tier)
    ]


def ready_node_payloads(plan_path: str | Path, tier: str | None = None) -> list[dict[str, str | None]]:
    return ready_node_payloads_from_plan(parse_plan(plan_path), tier=tier)


def build_status_payload_from_plan(plan: Plan) -> dict[str, object]:
    task_comment = next(
        (comment.split(":", 1)[1].strip() for comment in plan.comments if comment.startswith("task:")),
        "",
    )
    nodes = [node_to_dict(node) for node in sorted(plan.nodes.values(), key=lambda item: item.id)]
    return {
        "task": task_comment,
        "total": len(plan.nodes),
        "done": sum(1 for node in plan.nodes.values() if node.status == "done"),
        "in_progress": sum(1 for node in plan.nodes.values() if node.status == "in_progress"),
        "waiting": sum(1 for node in plan.nodes.values() if node.status == "waiting"),
        "failed": sum(1 for node in plan.nodes.values() if node.status == "failed"),
        "decisions_pending": sum(1 for node in plan.nodes.values() if node.status == "pending_decision"),
        "nodes": nodes,
    }


def build_status_payload(plan_path: str | Path) -> dict[str, object]:
    return build_status_payload_from_plan(parse_plan(plan_path))


def format_human_status_from_plan(plan: Plan) -> str:
    payload = build_status_payload_from_plan(plan)
    task = payload["task"] or "Untitled task"
    total = payload["total"]
    divider = "─" * 38

    lines = [
        f"📊 merdag status: {task}",
        divider,
        f"✅ Done:              {payload['done']}/{total}",
        f"🔄 In Progress:       {payload['in_progress']}/{total}",
        f"⏳ Waiting:           {payload['waiting']}/{total}",
        f"❌ Failed:            {payload['failed']}/{total}",
        f"🟡 Decisions Pending: {payload['decisions_pending']}",
        divider,
    ]

    for node in sorted(plan.nodes.values(), key=lambda item: item.id):
        tier = TIER_SUFFIX.get(node.tier, "")
        tier_text = f" {tier}" if tier else ""
        lines.append(f"{node.id:<2} {STATUS_ICON[node.status]} {node.label:<28}{tier_text}".rstrip())

    return "\n".join(lines)


def format_human_status(plan_path: str | Path) -> str:
    return format_human_status_from_plan(parse_plan(plan_path))
