from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

STATUS_BY_EMOJI = {
    "✅": "done",
    "🔄": "in_progress",
    "⏳": "waiting",
    "❌": "failed",
    "🟡": "pending_decision",
}

STATUS_BY_NAME = {value: key for key, value in STATUS_BY_EMOJI.items()}
TIER_PATTERNS = {
    "local": re.compile(r"🏠\s*local"),
    "fast": re.compile(r"⚡\s*fast"),
    "codex": re.compile(r"🤖\s*codex"),
    "human": re.compile(r"🧑\s*human"),
}
TIER_SUFFIX = {
    "local": "🏠local",
    "fast": "⚡fast",
    "codex": "🤖codex",
    "human": "🧑human",
}
TASK_NODE_RE = re.compile(r"([A-Za-z0-9_]+)\[(.+?)\]")
DECISION_NODE_RE = re.compile(r"([A-Za-z0-9_]+)\{(.+?)\}")
LABELED_EDGE_RE = re.compile(r"([A-Za-z0-9_]+)\s*-->\|(.+?)\|\s*(?=([A-Za-z0-9_]+))")
PLAIN_EDGE_RE = re.compile(r"([A-Za-z0-9_]+)\s*-->\s*(?=([A-Za-z0-9_]+))" )
COMMENT_RE = re.compile(r"^\s*%%\s*(.+)$")
AGENT_RE = re.compile(r"agent:(\S+)")
DECISION_TYPE_RE = re.compile(r"^\s*([🤖🧑])\s*")


@dataclass
class Node:
    id: str
    label: str
    status: str
    tier: str | None
    node_type: str
    decision_type: str | None
    agent: str | None


@dataclass
class Edge:
    source: str
    target: str
    label: str | None


@dataclass
class Plan:
    nodes: dict[str, Node]
    edges: list[Edge]
    comments: list[str]
    raw: str


def parse_plan(path: str | Path = Path("plan.mermaid")) -> Plan:
    plan_path = Path(path)
    return parse_plan_text(plan_path.read_text(encoding="utf-8"))


def parse_plan_text(raw: str) -> Plan:
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    comments: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "graph TD":
            continue

        comment_match = COMMENT_RE.match(line)
        if comment_match:
            comments.append(comment_match.group(1).strip())
            continue

        for match in TASK_NODE_RE.finditer(line):
            node_id, raw_label = match.groups()
            if node_id not in nodes:
                nodes[node_id] = _parse_node(node_id, raw_label, "task")

        for match in DECISION_NODE_RE.finditer(line):
            node_id, raw_label = match.groups()
            if node_id not in nodes:
                nodes[node_id] = _parse_node(node_id, raw_label, "decision")

        line_for_edges = line
        line_for_edges = TASK_NODE_RE.sub(r"\1", line_for_edges)
        line_for_edges = DECISION_NODE_RE.sub(r"\1", line_for_edges)

        for labeled_edge in LABELED_EDGE_RE.finditer(line_for_edges):
            source, label, target = labeled_edge.groups()
            edges.append(Edge(source=source, target=target, label=label.strip()))

        for plain_edge in PLAIN_EDGE_RE.finditer(line_for_edges):
            source, target = plain_edge.groups()
            edges.append(Edge(source=source, target=target, label=None))

    return Plan(nodes=nodes, edges=edges, comments=comments, raw=raw)


def node_to_dict(node: Node) -> dict[str, str | None]:
    return {
        "id": node.id,
        "label": node.label,
        "status": node.status,
        "tier": node.tier,
        "type": node.node_type,
        "agent": node.agent,
    }


def incoming_edges(plan: Plan) -> dict[str, list[Edge]]:
    incoming: dict[str, list[Edge]] = {node_id: [] for node_id in plan.nodes}
    for edge in plan.edges:
        incoming.setdefault(edge.target, []).append(edge)
    return incoming


def outgoing_edges(plan: Plan) -> dict[str, list[Edge]]:
    outgoing: dict[str, list[Edge]] = {node_id: [] for node_id in plan.nodes}
    for edge in plan.edges:
        outgoing.setdefault(edge.source, []).append(edge)
    return outgoing


def get_back_edges(plan: Plan) -> set[tuple[str, str]]:
    outgoing = outgoing_edges(plan)
    visited = set()
    visiting = set()
    back_edges = set()

    def dfs(node_id: str):
        visited.add(node_id)
        visiting.add(node_id)
        for edge in outgoing.get(node_id, []):
            if edge.target in visiting:
                back_edges.add((edge.source, edge.target))
            elif edge.target not in visited:
                dfs(edge.target)
        visiting.remove(node_id)

    incoming = incoming_edges(plan)
    roots = [n for n in plan.nodes if not incoming.get(n)]
    if not roots and plan.nodes:
        roots = [next(iter(plan.nodes))]

    for root in sorted(roots):
        if root not in visited:
            dfs(root)

    for node_id in sorted(plan.nodes):
        if node_id not in visited:
            dfs(node_id)

    return back_edges


def dependencies_met(plan: Plan, node_id: str) -> bool:
    incoming = incoming_edges(plan).get(node_id, [])
    if not incoming:
        return True

    back_edges = get_back_edges(plan)

    for edge in incoming:
        if (edge.source, edge.target) in back_edges:
            continue

        upstream = plan.nodes.get(edge.source)
        if upstream is None or upstream.status not in {"done", "failed"}:
            return False
            
    return True


def reachable_nodes(plan: Plan, start_ids: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    outgoing = outgoing_edges(plan)
    stack = list(start_ids)
    visited: set[str] = set()
    back_edges = get_back_edges(plan)

    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        for edge in outgoing.get(node_id, []):
            if (edge.source, edge.target) not in back_edges:
                stack.append(edge.target)

    return visited


def render_node(node: Node) -> str:
    parts: list[str] = []
    if node.node_type == "decision" and node.decision_type:
        parts.append(node.decision_type)
    parts.append(node.label)
    parts.append(STATUS_BY_NAME[node.status])
    if node.node_type == "task" and node.status == "in_progress" and node.agent:
        parts.append(f"agent:{node.agent}")
    if node.tier:
        parts.append(TIER_SUFFIX[node.tier])
    rendered = " ".join(parts)
    if node.node_type == "task":
        return f"{node.id}[{rendered}]"
    return f"{node.id}{{{rendered}}}"


def _parse_node(node_id: str, raw_label: str, node_type: str) -> Node:
    status = next((name for emoji, name in STATUS_BY_EMOJI.items() if emoji in raw_label), "waiting")
    tier = next((name for name, pattern in TIER_PATTERNS.items() if pattern.search(raw_label)), None)
    agent_match = AGENT_RE.search(raw_label)
    agent = agent_match.group(1) if agent_match else None
    decision_type = None

    clean_label = raw_label
    if node_type == "decision":
        decision_match = DECISION_TYPE_RE.match(clean_label)
        if decision_match:
            decision_type = decision_match.group(1)
            clean_label = clean_label[decision_match.end():]

    clean_label = AGENT_RE.sub("", clean_label)
    for emoji in STATUS_BY_EMOJI:
        clean_label = clean_label.replace(emoji, "")
    for pattern in TIER_PATTERNS.values():
        clean_label = pattern.sub("", clean_label)
    clean_label = re.sub(r"\s+", " ", clean_label).strip()

    return Node(
        id=node_id,
        label=clean_label,
        status=status,
        tier=tier,
        node_type=node_type,
        decision_type=decision_type,
        agent=agent,
    )
