from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import time

from merdag.parser import parse_plan
from merdag.status import build_status_payload_from_plan, ready_node_payloads_from_plan


@dataclass
class WatchSnapshot:
    statuses: dict[str, str]
    ready_tasks: dict[str, dict[str, str | None]]
    status_payload: dict[str, object]


def get_plan_mtime(plan_path: Path) -> float:
    return plan_path.stat().st_mtime


def capture_snapshot(plan_path: str | Path, tier: str | None = None) -> WatchSnapshot:
    plan = parse_plan(plan_path)
    ready_payloads = ready_node_payloads_from_plan(plan, tier=tier)
    return WatchSnapshot(
        statuses={node_id: node.status for node_id, node in plan.nodes.items()},
        ready_tasks={task["id"]: task for task in ready_payloads},
        status_payload=build_status_payload_from_plan(plan),
    )


def diff_status_changes(previous: WatchSnapshot, current: WatchSnapshot) -> list[str]:
    changes: list[str] = []
    for node_id in sorted(set(previous.statuses) | set(current.statuses)):
        before = previous.statuses.get(node_id)
        after = current.statuses.get(node_id)
        if before == after:
            continue
        changes.append(f"[CHANGE] Node {node_id}: {before or '<missing>'} → {after or '<missing>'}")
    return changes


def emit_json(payload: object, emit_output: Callable[[str], None]) -> str:
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    emit_output(serialized)
    return serialized


def run_on_ready_command(
    command: str,
    payload_text: str,
    *,
    emit_output: Callable[[str], None],
    run_command: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = run_command(
        command,
        input=payload_text,
        text=True,
        shell=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if details:
            raise RuntimeError(f'on-ready command failed: {details}')
        raise RuntimeError(f"on-ready command failed with exit code {result.returncode}")

    if result.stdout:
        emit_output(result.stdout.rstrip("\n"))


def watch_plan(
    plan_path: str | Path,
    *,
    on_ready: str | None = None,
    tier: str | None = None,
    poll_interval: float = 1.0,
    emit_output: Callable[[str], None],
    sleep_fn: Callable[[float], None] = time.sleep,
    get_mtime: Callable[[Path], float] = get_plan_mtime,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    plan_file = Path(plan_path)
    snapshot = capture_snapshot(plan_file, tier=tier)
    last_mtime = get_mtime(plan_file)

    try:
        while True:
            sleep_fn(poll_interval)
            current_mtime = get_mtime(plan_file)
            if current_mtime == last_mtime:
                continue

            last_mtime = current_mtime
            current_snapshot = capture_snapshot(plan_file, tier=tier)
            changes = diff_status_changes(snapshot, current_snapshot)
            new_ready_ids = sorted(set(current_snapshot.ready_tasks) - set(snapshot.ready_tasks))
            if not changes and not new_ready_ids:
                snapshot = current_snapshot
                continue

            emit_json(current_snapshot.status_payload, emit_output)
            for line in changes:
                emit_output(line)

            if new_ready_ids:
                ready_payload = [current_snapshot.ready_tasks[node_id] for node_id in new_ready_ids]
                payload_text = emit_json(ready_payload, emit_output)
                if on_ready:
                    run_on_ready_command(
                        on_ready,
                        payload_text,
                        emit_output=emit_output,
                        run_command=run_command,
                    )

            snapshot = current_snapshot
    except KeyboardInterrupt:
        emit_output("[STOP] Watcher exiting.")
