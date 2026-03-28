from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import re
import time
from typing import Callable

from merdag.decisions import resolve_decision
from merdag.llm import call_llm
from merdag.parser import Node, dependencies_met, outgoing_edges, parse_plan
from merdag.updater import append_plan_comment, read_locked, update_node_status, write_locked

PLAN_GENERATION_SYSTEM_PROMPT = """You are merdag, an AI planner. Generate a Mermaid flowchart for the given task.
Rules:
- Use ONLY `graph TD` format
- Task nodes: [Label STATUS TIER] where STATUS is ⏳ and TIER is one of 🏠local, ⚡fast, 🤖codex, 🧑human
- Decision nodes: {EMOJI Label 🟡} where EMOJI is 🤖 (agent decides) or 🧑 (human decides)
- Edges: A --> B or A -->|Label| B
- Generate 6-12 nodes with realistic dependencies
- Include at least 1 human decision and 1 agent decision
- Assign tiers based on task complexity
Output ONLY the mermaid code, no markdown fences, no explanation.
"""

TASK_PROMPTS = {
    "fast": (
        "You are a task executor. You complete tasks quickly and return a brief 1-2 sentence result "
        "describing what was done."
    ),
    "codex": (
        "You are a senior strategist. You analyze carefully and provide detailed, well-reasoned results "
        "for complex tasks."
    ),
    "local": "You are a privacy-focused local executor. Complete the task briefly.",
    "human": (
        "You are a human proxy. This task is normally completed by a person, but provide a concise "
        "recommended completion update so the plan can continue."
    ),
}
DECISION_PROMPT = (
    'You are a strategic decision maker. Given the context, pick the best option and explain why in 1-2 '
    'sentences. Respond with JSON: {"choice": "<option>", "reason": "<why>"}'
)
HUMAN_DECISION_PROMPT = (
    DECISION_PROMPT
    + ' This is normally a human decision, but you are providing a recommended default.'
)

LlmCallable = Callable[[str, str, str], dict[str, int | str]]
SleepCallable = Callable[[float], None]
EmitCallable = Callable[[str], None]


def run_simulation(
    task_description: str,
    *,
    plan_path: str | Path = Path("plan.mermaid"),
    decisions_path: str | Path = Path("decisions.md"),
    emit_output: EmitCallable | None = None,
    llm_callable: LlmCallable = call_llm,
    sleep_fn: SleepCallable = time.sleep,
) -> dict[str, object]:
    plan_file = Path(plan_path)
    decisions_file = Path(decisions_path)
    emit = emit_output or (lambda _message: None)

    totals_in = 0
    totals_out = 0
    models_used: Counter[str] = Counter()
    llm_calls = 0
    steps = 0

    plan_response = llm_callable("codex", PLAN_GENERATION_SYSTEM_PROMPT, task_description)
    totals_in += _as_int(plan_response["tokens_in"])
    totals_out += _as_int(plan_response["tokens_out"])
    models_used[str(plan_response["model"])] += 1
    llm_calls += 1

    plan_text = _normalize_plan_text(task_description, str(plan_response["response"]))
    write_locked(plan_file, plan_text)
    write_locked(decisions_file, f"# Decisions for: {task_description}\n")

    while True:
        plan = parse_plan(plan_file)
        ready_tasks = _ready_nodes(plan, node_type="task")
        ready_decisions = _ready_nodes(plan, node_type="decision")

        if ready_tasks:
            node = ready_tasks[0]
            if llm_calls:
                sleep_fn(1.0)
            result = _execute_task(
                node=node,
                task_description=task_description,
                plan_path=plan_file,
                llm_callable=llm_callable,
            )
            action = "complete task"
        elif ready_decisions:
            node = ready_decisions[0]
            if llm_calls:
                sleep_fn(1.0)
            result = _execute_decision(
                node=node,
                plan=plan,
                task_description=task_description,
                plan_path=plan_file,
                decisions_path=decisions_file,
                llm_callable=llm_callable,
            )
            action = "resolve decision"
        else:
            remaining = [
                node.id
                for node in plan.nodes.values()
                if node.status in {"waiting", "pending_decision"}
            ]
            if remaining:
                raise RuntimeError(f"Simulation stalled with blocked nodes: {', '.join(sorted(remaining))}")
            break

        steps += 1
        llm_calls += 1
        totals_in += _as_int(result["tokens_in"])
        totals_out += _as_int(result["tokens_out"])
        models_used[str(result["model"])] += 1
        emit(_format_step(step=steps, action=action, node=node, result=result, plan_path=plan_file))

    final_plan = parse_plan(plan_file)
    summary = {
        "status": "complete",
        "total_steps": steps,
        "total_tokens_in": totals_in,
        "total_tokens_out": totals_out,
        "models_used": dict(models_used),
        "nodes_completed": sum(
            1 for node in final_plan.nodes.values() if node.node_type == "task" and node.status == "done"
        ),
        "nodes_failed": sum(1 for node in final_plan.nodes.values() if node.status == "failed"),
        "decisions_made": sum(
            1 for node in final_plan.nodes.values() if node.node_type == "decision" and node.status == "done"
        ),
    }
    return summary


def _ready_nodes(plan, *, node_type: str) -> list[Node]:
    return sorted(
        [
            node
            for node in plan.nodes.values()
            if node.node_type == node_type
            and node.status in {"waiting", "pending_decision"}
            and dependencies_met(plan, node.id)
        ],
        key=lambda item: item.id,
    )


def _execute_task(
    *,
    node: Node,
    task_description: str,
    plan_path: Path,
    llm_callable: LlmCallable,
) -> dict[str, int | str]:
    tier = node.tier or "fast"
    system_prompt = TASK_PROMPTS[tier]
    if tier == "codex":
        user_prompt = (
            f"Task: {node.label}. Context: The overall plan is '{task_description}'. "
            "Analyze and complete this task. Provide a detailed result."
        )
    elif tier == "human":
        user_prompt = (
            f"Task: {node.label}. Context: The overall plan is '{task_description}'. "
            "Provide the recommended human-completion update that should unblock the next steps."
        )
    else:
        user_prompt = (
            f"Task: {node.label}. Context: The overall plan is '{task_description}'. "
            "Complete this task and describe the result."
        )

    result = llm_callable(tier, system_prompt, user_prompt)
    update_node_status(plan_path, node.id, "done")
    append_plan_comment(plan_path, f"result({node.id}): {str(result['response']).strip()}")
    return result


def _execute_decision(
    *,
    node: Node,
    plan,
    task_description: str,
    plan_path: Path,
    decisions_path: Path,
    llm_callable: LlmCallable,
) -> dict[str, int | str]:
    options = [edge.label for edge in outgoing_edges(plan).get(node.id, []) if edge.label]
    system_prompt = HUMAN_DECISION_PROMPT if node.decision_type == "🧑" else DECISION_PROMPT
    user_prompt = (
        f"Decision: {node.label}. Options from the plan edges: {', '.join(options)}. "
        f"Context: {task_description}. Choose the best option."
    )
    result = llm_callable("human" if node.decision_type == "🧑" else "codex", system_prompt, user_prompt)
    payload = _parse_decision_payload(str(result["response"]))
    resolved = resolve_decision(
        plan_path,
        decisions_path,
        node_id=node.id,
        choice=payload["choice"],
        reason=payload["reason"],
    )
    result["response"] = json.dumps(
        {"choice": resolved["choice"], "reason": resolved["reason"]},
        ensure_ascii=False,
    )
    return result


def _normalize_plan_text(task_description: str, response_text: str) -> str:
    raw = response_text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    if not raw.startswith("graph TD"):
        raise ValueError("Generated plan must start with 'graph TD'")

    lines = raw.splitlines()
    if not any(line.strip().startswith("%% task:") for line in lines[1:]):
        lines.insert(1, f"    %% task: {task_description}")
    return "\n".join(lines).strip() + "\n"


def _parse_decision_payload(response_text: str) -> dict[str, str]:
    candidate = response_text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    if not candidate.startswith("{"):
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            raise ValueError("Decision response must be valid JSON")
        candidate = match.group(0)

    payload = json.loads(candidate)
    if "choice" not in payload or "reason" not in payload:
        raise ValueError("Decision response must include 'choice' and 'reason'")
    return {"choice": str(payload["choice"]).strip(), "reason": str(payload["reason"]).strip()}


def _format_step(
    *,
    step: int,
    action: str,
    node: Node,
    result: dict[str, int | str],
    plan_path: Path,
) -> str:
    return "\n".join(
        [
            f"--- Step {step}: {action} ---",
            f"Node: {node.id} ({node.label})",
            f"Model: {result['model']}",
            f"Tokens: {result['tokens_in']}/{result['tokens_out']}",
            f"Result: {_truncate(str(result['response']).strip())}",
            "",
            read_locked(plan_path).rstrip(),
            "---",
        ]
    )


def _truncate(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _as_int(value: object) -> int:
    return int(value) if value is not None else 0
