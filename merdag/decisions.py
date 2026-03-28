from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from merdag.parser import outgoing_edges, parse_plan, reachable_nodes
from merdag.updater import append_locked, replace_node


@dataclass
class DecisionEntry:
    node: str
    label: str
    decision_type: str
    context: str
    default: str
    override: str


DECISION_BLOCK_RE = re.compile(r"(?ms)^## Decision:.*?(?=^## Decision:|\Z)")
HEADER_RE = re.compile(r"^## Decision:\s*(.*?)\s*\(node ([A-Za-z0-9_]+)\)", re.MULTILINE)
TYPE_RE = re.compile(r"^\*\*Type:\*\*\s*([🤖🧑])", re.MULTILINE)
CONTEXT_RE = re.compile(r"^\*\*Context:\*\*\s*(.+)$", re.MULTILINE)
DEFAULT_RE = re.compile(r"^\*\*Default \(🤖 codex\):\*\*\s*(.+)$", re.MULTILINE)
OVERRIDE_RE = re.compile(r"^\*\*Override:\*\*\s*(.*)$", re.MULTILINE)


def parse_decisions(path: str | Path = Path("decisions.md")) -> list[DecisionEntry]:
    decisions_path = Path(path)
    if not decisions_path.exists():
        return []

    raw = decisions_path.read_text(encoding="utf-8")
    entries: list[DecisionEntry] = []

    for block in DECISION_BLOCK_RE.findall(raw):
        header = HEADER_RE.search(block)
        decision_type = TYPE_RE.search(block)
        context = CONTEXT_RE.search(block)
        default = DEFAULT_RE.search(block)
        override = OVERRIDE_RE.search(block)
        if not all([header, decision_type, default, override]):
            continue
        label, node = header.groups()
        entries.append(
            DecisionEntry(
                node=node,
                label=label.strip(),
                decision_type=decision_type.group(1),
                context=context.group(1).strip() if context else "",
                default=default.group(1).strip(),
                override=override.group(1).strip(),
            )
        )

    return entries


def pending_decisions(path: str | Path = Path("decisions.md")) -> list[DecisionEntry]:
    return [
        entry
        for entry in parse_decisions(path)
        if not entry.override or entry.override == "___"
    ]


def format_decision_entry(
    *,
    label: str,
    node_id: str,
    decision_type: str,
    context: str,
    default: str,
    override: str,
) -> str:
    decision_label = "🤖 Agent decision" if decision_type == "🤖" else "🧑 Human decision"
    safe_context = context or ""
    safe_override = override or "___"
    return (
        f"## Decision: {label} (node {node_id})\n"
        f"**Type:** {decision_label}\n"
        f"**Context:** {safe_context}\n"
        f"**Default (🤖 codex):** {default}\n"
        f"**Override:** {safe_override}\n"
    )


def resolve_decision(
    plan_path: str | Path,
    decisions_path: str | Path,
    *,
    node_id: str,
    choice: str,
    reason: str,
) -> dict[str, object]:
    plan_file = Path(plan_path)
    decisions_file = Path(decisions_path)
    plan = parse_plan(plan_file)
    if node_id not in plan.nodes:
        raise ValueError(f"Node {node_id} not found")

    node = plan.nodes[node_id]
    if node.node_type != "decision":
        raise ValueError(f"Node {node_id} is not a decision")

    outgoing = [edge for edge in outgoing_edges(plan).get(node_id, []) if edge.label]
    choice_map = {edge.label.casefold(): edge for edge in outgoing}
    chosen_edge = choice_map.get(choice.casefold())
    if chosen_edge is None:
        # Fuzzy: if any edge label is a substring of choice (or vice versa), use it
        choice_lower = choice.casefold()
        for label, edge in choice_map.items():
            if label in choice_lower or choice_lower in label:
                chosen_edge = edge
                break
    if chosen_edge is None and len(outgoing) == 1:
        # Only one outgoing edge — use it regardless of label mismatch
        chosen_edge = outgoing[0]
    if chosen_edge is None:
        raise ValueError(f"Choice {choice!r} not found for node {node_id}")

    node.status = "done"
    node.agent = None
    replace_node(plan_file, node)

    protected = reachable_nodes(plan, [chosen_edge.target])
    skipped: set[str] = set()
    for edge in outgoing:
        if edge == chosen_edge:
            continue
        skipped.update(
            candidate
            for candidate in reachable_nodes(plan, [edge.target])
            if candidate not in protected
        )

    for skipped_id in sorted(skipped):
        skipped_node = parse_plan(plan_file).nodes[skipped_id]
        skipped_node.status = "failed"
        skipped_node.agent = None
        replace_node(plan_file, skipped_node)

    decision_entry = format_decision_entry(
        label=node.label,
        node_id=node.id,
        decision_type=node.decision_type or "🤖",
        context=reason,
        default=chosen_edge.label,
        override=chosen_edge.label,
    )
    append_locked(decisions_file, "\n" + decision_entry)

    return {
        "node": node_id,
        "choice": chosen_edge.label,
        "reason": reason,
        "skipped_nodes": sorted(skipped),
    }
