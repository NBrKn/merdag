from __future__ import annotations

from pathlib import Path
import re

from filelock import FileLock

from merdag.parser import Node, parse_plan_text, render_node


def read_locked(path: str | Path) -> str:
    target = Path(path)
    lock = FileLock(str(target) + ".lock")
    with lock:
        return target.read_text(encoding="utf-8")


def write_locked(path: str | Path, new_content: str) -> None:
    target = Path(path)
    lock = FileLock(str(target) + ".lock")
    with lock:
        target.write_text(new_content, encoding="utf-8")


def append_locked(path: str | Path, extra_content: str) -> None:
    target = Path(path)
    lock = FileLock(str(target) + ".lock")
    with lock:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        target.write_text(existing + extra_content, encoding="utf-8")


def replace_node(path: str | Path, node: Node) -> None:
    target = Path(path)
    content = read_locked(target)
    node_pattern = re.compile(
        rf"(?P<prefix>\b{re.escape(node.id)})(?P<body>\[(?:.+?)\]|\{{(?:.+?)\}})"
    )
    replacement = render_node(node)
    updated = False
    new_lines: list[str] = []

    for line in content.splitlines():
        if updated:
            new_lines.append(line)
            continue

        def repl(match: re.Match[str]) -> str:
            nonlocal updated
            updated = True
            return replacement

        new_line, count = node_pattern.subn(repl, line, count=1)
        new_lines.append(new_line if count else line)

    if not updated:
        raise ValueError(f"Node {node.id} not found in {target}")

    suffix = "\n" if content.endswith("\n") else ""
    write_locked(target, "\n".join(new_lines) + suffix)


def update_node_status(path: str | Path, node_id: str, status: str) -> Node:
    target = Path(path)
    plan = parse_plan_text(read_locked(target))
    if node_id not in plan.nodes:
        raise ValueError(f"Node {node_id} not found in {target}")
    node = plan.nodes[node_id]
    node.status = status
    if status != "in_progress":
        node.agent = None
    replace_node(target, node)
    return node


def append_plan_comment(path: str | Path, comment: str) -> None:
    lines = comment.splitlines()
    prefixed = "\n".join(f"%% {line}" for line in lines)
    append_locked(path, f"{prefixed}\n")
