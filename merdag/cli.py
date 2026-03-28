from __future__ import annotations

from pathlib import Path
import json

import click

from merdag.decisions import pending_decisions, resolve_decision
from merdag.serve import serve_viewer
from merdag.status import available_nodes, build_status_payload, format_human_status
from merdag.simulate import run_simulation
from merdag.updater import (
    append_plan_comment,
    update_node_status,
    write_locked,
)
from merdag.watch import watch_plan

PLAN_PATH = Path("plan.mermaid")
DECISIONS_PATH = Path("decisions.md")
DEFAULT_TEMPLATE = """graph TD
    %% task: {task}
    A[Research competitors ⏳ 🏠local] --> B{{🤖 Pick channel? 🟡 🤖codex}}
    A --> C[Draft ad copy ⏳ ⚡fast]
    B -->|Social| D[Create social campaign ⏳ ⚡fast]
    B -->|Search| E[Create search campaign ⏳ ⚡fast]
    C --> F{{🧑 Approve creative? 🟡 🧑human}}
    D --> G[Build launch calendar ⏳ 🤖codex]
    E --> G
    F -->|Yes| H[Publish launch assets ⏳ 🧑human]
    F -->|No| I[Revise copy ⏳ ⚡fast]
    I --> F
    G --> H
"""


def human_option(func):
    return click.option("--human", is_flag=True, help="Pretty output")(func)


def emit(data: object, human: bool) -> None:
    if human:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return
    click.echo(json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def require_plan() -> Path:
    if not PLAN_PATH.exists():
        raise click.ClickException("plan.mermaid not found")
    return PLAN_PATH


def require_decisions() -> Path:
    if not DECISIONS_PATH.exists():
        raise click.ClickException("decisions.md not found")
    return DECISIONS_PATH


@click.group()
def main() -> None:
    """CLI for shared Mermaid execution plans."""


@main.command()
@click.argument("task_description")
@human_option
def init(task_description: str, human: bool) -> None:
    plan_content = DEFAULT_TEMPLATE.format(task=task_description)
    write_locked(PLAN_PATH, plan_content)
    write_locked(DECISIONS_PATH, f"# Decisions for: {task_description}\n")
    emit({"created": ["plan.mermaid", "decisions.md"]}, human)


@main.command(name="next")
@click.option(
    "--tier",
    type=click.Choice(["local", "fast", "codex", "human"], case_sensitive=False),
)
@human_option
def next_command(tier: str | None, human: bool) -> None:
    plan_path = require_plan()
    nodes = available_nodes(plan_path, tier.lower() if tier else None)
    emit(
        [
            {
                "id": node.id,
                "label": node.label,
                "tier": node.tier,
                "type": node.node_type,
            }
            for node in nodes
        ],
        human,
    )


@main.command()
@click.argument("node_id")
@click.option("--result", required=True)
@human_option
def done(node_id: str, result: str, human: bool) -> None:
    plan_path = require_plan()
    try:
        update_node_status(plan_path, node_id, "done")
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    append_plan_comment(plan_path, f"result({node_id}): {result}")
    emit({"node": node_id, "status": "done", "result": result}, human)


@main.command()
@click.argument("node_id")
@click.option("--reason", required=True)
@human_option
def fail(node_id: str, reason: str, human: bool) -> None:
    plan_path = require_plan()
    try:
        update_node_status(plan_path, node_id, "failed")
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    append_plan_comment(plan_path, f"failed({node_id}): {reason}")
    emit({"node": node_id, "status": "failed", "reason": reason}, human)


@main.command()
@click.argument("node_id")
@click.option("--choice", required=True)
@click.option("--reason", required=True)
@human_option
def decide(node_id: str, choice: str, reason: str, human: bool) -> None:
    plan_path = require_plan()
    decisions_path = require_decisions()
    try:
        payload = resolve_decision(
            plan_path,
            decisions_path,
            node_id=node_id,
            choice=choice,
            reason=reason,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    emit(payload, human)


@main.command()
@human_option
def status(human: bool) -> None:
    plan_path = require_plan()
    if human:
        click.echo(format_human_status(plan_path))
        return
    emit(build_status_payload(plan_path), human=False)


@main.command()
@human_option
def decisions(human: bool) -> None:
    require_decisions()
    emit(
        [
            {
                "node": entry.node,
                "label": entry.label,
                "type": entry.decision_type,
                "default": entry.default,
            }
            for entry in pending_decisions(DECISIONS_PATH)
        ],
        human,
    )


@main.command()
@click.argument("task_description")
def simulate(task_description: str) -> None:
    summary = run_simulation(task_description, emit_output=click.echo)
    click.echo(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))


@main.command()
@click.option("--on-ready")
@click.option(
    "--tier",
    type=click.Choice(["local", "fast", "codex", "human"], case_sensitive=False),
)
def watch(on_ready: str | None, tier: str | None) -> None:
    watch_plan(
        require_plan(),
        on_ready=on_ready,
        tier=tier.lower() if tier else None,
        emit_output=click.echo,
    )


@main.command()
def serve() -> None:
    try:
        serve_viewer(
            plan_path=PLAN_PATH,
            decisions_path=DECISIONS_PATH,
            emit_output=click.echo,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
