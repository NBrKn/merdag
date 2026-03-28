#!/usr/bin/env python3
"""
ralph.py — Ralph Loop orchestrator for merdag.

Roles:
  Executor:  Copilot CLI (gpt-5.4)     — builds one stage per iteration
  Reviewer:  Claude Code (Opus)         — reviews diffs after each iteration
  Fallback:  Copilot (gpt-4o) or Claude Sonnet if quota issues

Usage:
    python ralph.py                        # Copilot gpt-5.4 executor (default)
    python ralph.py --executor copilot-4o  # Copilot with gpt-4o (quota fallback)
    python ralph.py --executor claude      # Claude Sonnet as executor
    python ralph.py --max 10               # limit iterations
    python ralph.py --skip-review          # skip Claude review phase
    python ralph.py --dry-run              # print commands without executing
"""

import argparse
import io
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding for emoji/unicode output
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Configuration ───────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()

# Executor profiles: name → (command, args_builder)
# Each args_builder takes (prompt: str) and returns the full command list.
EXECUTORS = {
    "copilot": {
        "label": "Copilot CLI (gpt-5.4)",
        "build_cmd": lambda prompt: [
            "copilot", "-p", prompt,
            "--model", "gpt-5.4",
            "--yolo", "--autopilot",
        ],
        "timeout": 600,
    },
    "copilot-4o": {
        "label": "Copilot CLI (gpt-4o)",
        "build_cmd": lambda prompt: [
            "copilot", "-p", prompt,
            "--model", "gpt-4o",
            "--yolo", "--autopilot",
        ],
        "timeout": 600,
    },
    "claude": {
        "label": "Claude Code (Sonnet)",
        "build_cmd": lambda prompt: [
            "claude", "--model", "sonnet", "--print", prompt,
        ],
        "timeout": 600,
    },
    "codex": {
        "label": "Codex CLI (gpt-5.4)",
        "build_cmd": lambda prompt: [
            "codex", "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            prompt,
        ],
        "timeout": 600,
    },
    "gemini": {
        "label": "Gemini CLI",
        "build_cmd": lambda prompt: [
            "gemini", "-p", prompt, "-y",
        ],
        "timeout": 600,
    },
}

DEFAULT_EXECUTOR = "copilot"

# Reviewer — Claude Code (Opus)
REVIEWER_CMD = "claude"
REVIEWER_MODEL = "opus"

# Key repo files the executor reads each iteration
SPEC_FILE = "init_spec.md"       # build spec (source of truth)
CONTEXT_FILE = "CONTEXT.MD"      # background context
PROGRESS_FILE = "progress.md"    # memory between iterations
AGENTS_FILE = "AGENTS.md"        # persistent knowledge base

# Loop settings
MAX_ITERATIONS = 20
SLEEP_BETWEEN = 2  # seconds between iterations

# Completion token
COMPLETION_TOKEN = "<promise>COMPLETE</promise>"

# Directories
LOGS_DIR = REPO_ROOT / "logs"
REVIEWS_DIR = REPO_ROOT / "reviews"

# ─── Helpers ─────────────────────────────────────────────────────────────────

_shutdown = False


def _handle_sigint(sig, frame):
    global _shutdown
    _shutdown = True
    print("\n⚠️  Ctrl+C detected — finishing current phase then exiting...")


signal.signal(signal.SIGINT, _handle_sigint)


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️ ", "OK": "✅", "WARN": "⚠️ ", "ERR": "❌", "STEP": "▶️ "}
    print(f"[{ts}] {prefix.get(level, '  ')} {msg}")


def read_file(name: str) -> str | None:
    p = REPO_ROOT / name
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def detect_current_stage() -> int:
    """Parse progress.md to determine the current (in-progress or next) stage number."""
    content = read_file(PROGRESS_FILE)
    if content is None:
        return 0
    # Find the highest completed stage (match "Status:" line containing "Complete")
    completed = re.findall(r"## Stage (\d+):.*\n(?:.*\n)*?.*Status:.*Complete", content)
    if completed:
        highest_done = max(int(s) for s in completed)
        return highest_done + 1
    # If nothing completed, check what's in progress
    in_progress = re.findall(r"## Stage (\d+):.*\n(?:.*\n)*?.*Status:.*In Progress", content, re.IGNORECASE)
    if in_progress:
        return min(int(s) for s in in_progress)
    return 0


def is_complete() -> bool:
    """Check if progress.md contains the completion token."""
    content = read_file(PROGRESS_FILE)
    return bool(content and COMPLETION_TOKEN in content)


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT, timeout: int = 300,
            capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=capture, text=True,
            timeout=timeout, shell=(os.name == "nt"),
            encoding="utf-8", errors="replace"
        )
        return result
    except subprocess.TimeoutExpired:
        log(f"Command timed out after {timeout}s: {' '.join(cmd)}", "WARN")
        return subprocess.CompletedProcess(cmd, 1, "", "TIMEOUT")
    except FileNotFoundError:
        log(f"Command not found: {cmd[0]}", "ERR")
        return subprocess.CompletedProcess(cmd, 1, "", f"Command not found: {cmd[0]}")


def git_diff_last() -> str | None:
    """Get the diff of the most recent commit. Returns None if not possible."""
    result = run_cmd(["git", "diff", "HEAD~1", "HEAD"], cwd=REPO_ROOT)
    if result.returncode != 0:
        return None
    return result.stdout.strip() if result.stdout.strip() else None


def ensure_dirs():
    LOGS_DIR.mkdir(exist_ok=True)
    REVIEWS_DIR.mkdir(exist_ok=True)


def build_executor_prompt() -> str:
    """Build the prompt for the executor agent."""
    return (
        f"Read {SPEC_FILE} and {PROGRESS_FILE} to find the next incomplete stage. "
        f"Also read {AGENTS_FILE} (if it exists) and {CONTEXT_FILE} for context. "
        f"Complete that ONE stage fully — all checklist items and 'Done when' checks. "
        f"Update {PROGRESS_FILE} and {AGENTS_FILE} before committing. "
        f"Git commit: 'merdag: Stage N — <what was done>'. "
        f"STOP after one stage. Do NOT start the next. "
        f"If ALL stages (0-5) are done, add '{COMPLETION_TOKEN}' to {PROGRESS_FILE} and output it."
    )


# ─── Phases ──────────────────────────────────────────────────────────────────

# Fallback map: if an executor hits quota, try these instead
EXECUTOR_FALLBACKS = {
    "copilot": "copilot-4o",
    "copilot-4o": "claude",
    "codex": "copilot-4o",
}


def _is_quota_error(output: str) -> bool:
    """Check if the output indicates a quota/rate limit error."""
    patterns = [
        r"(?i)quota exceeded",
        r"(?i)rate limit",
        r"(?i)too many requests",
        r"(?i)429",
        r"(?i)capacity",
        r"(?i)billing",
        r"(?i)premium request",
        r"(?i)limit reached",
    ]
    return any(re.search(p, output) for p in patterns)


def _run_executor(executor_name: str, prompt: str, iteration: int) -> tuple[str, str, str]:
    """
    Run a single executor attempt.
    Returns (executor_name_used, output, error_type).
    error_type is "" on success, "quota" on quota error, "timeout", "not_found", or "error".
    """
    executor = EXECUTORS[executor_name]
    cmd = executor["build_cmd"](prompt)
    timeout = executor["timeout"]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=(os.name == "nt"),
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        if result.returncode != 0 and _is_quota_error(output):
            return executor_name, output, "quota"

        return executor_name, output, ""

    except subprocess.TimeoutExpired:
        return executor_name, f"TIMEOUT — executor exceeded {timeout // 60} minutes", "timeout"
    except FileNotFoundError:
        return executor_name, f"Command not found: {cmd[0]}", "not_found"


def phase_execute(iteration: int, executor_name: str,
                  dry_run: bool = False) -> tuple[bool, str]:
    """
    EXECUTE phase: Run the chosen executor CLI with fresh context.
    Auto-falls back to gpt-4o / claude if quota is exceeded.
    Returns (completed, output).
    """
    executor = EXECUTORS[executor_name]
    log(f"EXECUTE — Iteration {iteration} ({executor['label']})", "STEP")

    prompt = build_executor_prompt()

    if dry_run:
        log(f"[DRY RUN] Would invoke: {executor_name} with prompt ({len(prompt)} chars)", "INFO")
        return False, "[dry run]"

    log_file = LOGS_DIR / f"iteration-{iteration}-execute.log"

    # Try primary executor, with fallback on quota errors
    current_executor = executor_name
    attempts = []

    for attempt in range(3):  # max 3 attempts (primary + 2 fallbacks)
        used, output, error_type = _run_executor(current_executor, prompt, iteration)
        attempts.append(f"Attempt {attempt+1}: {EXECUTORS[used]['label']} → {error_type or 'OK'}")

        if error_type == "quota" and current_executor in EXECUTOR_FALLBACKS:
            fallback = EXECUTOR_FALLBACKS[current_executor]
            log(f"Quota exceeded on {EXECUTORS[current_executor]['label']} — "
                f"falling back to {EXECUTORS[fallback]['label']}", "WARN")
            current_executor = fallback
            continue

        if error_type == "not_found":
            log(f"Executor command not found: {current_executor}", "ERR")
            if current_executor in EXECUTOR_FALLBACKS:
                fallback = EXECUTOR_FALLBACKS[current_executor]
                log(f"Trying fallback: {EXECUTORS[fallback]['label']}", "WARN")
                current_executor = fallback
                continue

        break  # success, timeout, or no fallback available

    # Write log
    log_file.write_text(
        f"=== Iteration {iteration} — Execute Phase ===\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        f"Stage: {detect_current_stage()}\n"
        f"Executor: {EXECUTORS[current_executor]['label']}\n"
        f"Attempts: {'; '.join(attempts)}\n"
        f"{'='*50}\n\n{output}",
        encoding="utf-8"
    )
    log(f"Execution log saved to {log_file.name}", "OK")

    completed = COMPLETION_TOKEN in output
    if completed:
        log("Completion token detected in executor output!", "OK")

    return completed, output


def phase_verify(iteration: int) -> list[str]:
    """
    VERIFY phase: Run feedback checks if stage >= 2.
    Returns list of failures (empty = all passed).
    """
    stage = detect_current_stage()
    last_completed = stage - 1
    if last_completed < 2:
        log(f"Stage {last_completed} completed — skipping feedback checks (Stage 2+ only)", "INFO")
        return []

    log(f"VERIFY — Running feedback checks (last completed: Stage {last_completed})", "STEP")

    checks = [
        (["pip", "install", "-e", "."], "pip install -e ."),
        (["merdag", "--help"], "merdag --help"),
        (["python", "-m", "merdag", "--help"], "python -m merdag --help"),
        (["python", "-c", "from merdag.parser import Node, Edge, Plan; print('OK')"],
         "parser import check"),
    ]

    failures = []
    for cmd, desc in checks:
        result = run_cmd(cmd, timeout=60)
        if result.returncode != 0:
            failures.append(f"{desc}: exit {result.returncode} — {result.stderr[:200]}")
            log(f"FAIL: {desc}", "ERR")
        else:
            log(f"PASS: {desc}", "OK")

    if failures:
        fail_report = f"\n\n## Verify Failures — Iteration {iteration}\n"
        for f in failures:
            fail_report += f"- {f}\n"
        agents = read_file(AGENTS_FILE)
        if agents:
            (REPO_ROOT / AGENTS_FILE).write_text(
                agents + fail_report, encoding="utf-8"
            )
            log("Appended verify failures to AGENTS.md", "WARN")

    return failures


def phase_review(iteration: int, dry_run: bool = False) -> str:
    """
    REVIEW phase: Claude Code (Opus) reviews the latest diff.
    Returns the review text.
    """
    log(f"REVIEW — Iteration {iteration} (Claude Code, {REVIEWER_MODEL})", "STEP")

    diff = git_diff_last()
    if not diff:
        log("No diff available — skipping review", "WARN")
        return ""

    # Truncate very large diffs
    if len(diff) > 15000:
        diff = diff[:15000] + "\n\n... [TRUNCATED — diff too large] ..."

    prompt = textwrap.dedent(f"""\
        You are reviewing iteration {iteration} of the merdag project (Ralph Loop build).

        Here is the git diff from this iteration:

        ```diff
        {diff}
        ```

        Review checklist:
        1. Does this diff complete exactly ONE stage per the spec?
        2. Is progress.md updated with completed items and stage status?
        3. Is AGENTS.md updated with discoveries/gotchas?
        4. Any bugs, spec violations, regressions, or missed checklist items?
        5. Are file paths cross-platform (pathlib, not hardcoded separators)?

        Provide a concise review. Format:
        ## Review — Iteration {iteration}
        **Stage completed:** N
        **Issues found:** (list or "None")
        **Suggestions:** (list or "None")

        IMPORTANT: Do NOT make any code changes. Review only.
        Append your findings to AGENTS.md under '## Review Feedback — Iteration {iteration}'.
    """)

    if dry_run:
        log(f"[DRY RUN] Would invoke: {REVIEWER_CMD} --model {REVIEWER_MODEL} --print", "INFO")
        return "[dry run]"

    review_file = REVIEWS_DIR / f"review-{iteration}.md"

    try:
        result = subprocess.run(
            [REVIEWER_CMD, "--model", REVIEWER_MODEL, "--print", prompt],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            shell=(os.name == "nt"),
            encoding="utf-8",
            errors="replace",
        )
        review_text = result.stdout or ""
    except subprocess.TimeoutExpired:
        review_text = "TIMEOUT — reviewer exceeded 5 minutes"
        log("Reviewer timed out", "WARN")
    except FileNotFoundError:
        log(f"Reviewer command '{REVIEWER_CMD}' not found!", "ERR")
        return ""

    # Save review
    review_file.write_text(
        f"=== Review — Iteration {iteration} ===\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        f"{'='*50}\n\n{review_text}",
        encoding="utf-8"
    )
    log(f"Review saved to {review_file.name}", "OK")

    # Commit the review
    run_cmd(["git", "add", AGENTS_FILE, str(review_file)])
    commit_result = run_cmd(
        ["git", "commit", "-m", f"merdag: Review — Iteration {iteration}",
         "--allow-empty"]
    )
    if commit_result.returncode == 0:
        log("Review committed", "OK")
    else:
        log("Review commit skipped (nothing to commit or error)", "WARN")

    return review_text


# ─── Main Loop ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ralph Loop orchestrator for merdag",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Available executors:
              copilot    — Copilot CLI with gpt-5.4 (default, auto-falls back to 4o on quota)
              copilot-4o — Copilot CLI with gpt-4o (cheaper, for quota fallback)
              claude     — Claude Code Sonnet (claude --model sonnet --print)
              codex      — Codex CLI (codex exec)
              gemini     — Gemini CLI (gemini -p -y)

            Auto-fallback chain: copilot (5.4) → copilot-4o → claude
        """),
    )
    parser.add_argument("--max", type=int, default=MAX_ITERATIONS,
                        help=f"Max iterations (default: {MAX_ITERATIONS})")
    parser.add_argument("--executor", choices=EXECUTORS.keys(), default=DEFAULT_EXECUTOR,
                        help=f"Which CLI to use as executor (default: {DEFAULT_EXECUTOR})")
    parser.add_argument("--skip-review", action="store_true",
                        help="Skip the Claude Code review phase")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing")
    parser.add_argument("--start-from", type=int, default=1,
                        help="Starting iteration number (for resuming)")
    args = parser.parse_args()

    ensure_dirs()

    executor = EXECUTORS[args.executor]

    print("=" * 60)
    print("🐛 RALPH LOOP — merdag autonomous build")
    print(f"   Executor:  {executor['label']} ({args.executor})")
    print(f"   Reviewer:  {REVIEWER_CMD} --model {REVIEWER_MODEL} (Claude Code)")
    print(f"   Max iter:  {args.max}")
    print(f"   Review:    {'ON' if not args.skip_review else 'OFF'}")
    print(f"   Dry run:   {args.dry_run}")
    print(f"   Spec:      {SPEC_FILE}")
    print(f"   Started:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Pre-flight checks
    if not (REPO_ROOT / SPEC_FILE).exists():
        log(f"{SPEC_FILE} not found in repo root!", "ERR")
        sys.exit(1)
    if not (REPO_ROOT / PROGRESS_FILE).exists():
        log(f"{PROGRESS_FILE} not found — is Stage 0 complete?", "ERR")
        sys.exit(1)

    if is_complete():
        log("Project already complete (completion token found).", "OK")
        sys.exit(0)

    current_stage = detect_current_stage()
    log(f"Detected current stage: {current_stage}", "INFO")

    # Tracking
    iterations_done = 0
    stages_completed = []
    verify_failures_total = 0
    review_issues_total = 0

    for i in range(args.start_from, args.start_from + args.max):
        if _shutdown:
            log("Shutdown requested — exiting loop", "WARN")
            break

        print(f"\n{'━'*60}")
        print(f"  ITERATION {i}  |  Stage {detect_current_stage()}  |  "
              f"{datetime.now().strftime('%H:%M:%S')}")
        print(f"{'━'*60}")

        # ── Phase 1: Check completion ──
        if is_complete():
            log("Completion token found — all stages done!", "OK")
            break

        # ── Phase 2: Execute ──
        stage_before = detect_current_stage()
        completed, output = phase_execute(i, args.executor, dry_run=args.dry_run)

        if _shutdown:
            break

        if completed:
            log("Executor signaled completion!", "OK")
            iterations_done += 1
            break

        stage_after = detect_current_stage()
        if stage_after > stage_before:
            stages_completed.append(stage_before)
            log(f"Stage {stage_before} → Stage {stage_after} (advanced)", "OK")
        else:
            log(f"Stage unchanged at {stage_after} (may still be in progress)", "WARN")

        # ── Phase 3: Verify ──
        failures = phase_verify(i)
        verify_failures_total += len(failures)

        if _shutdown:
            break

        # ── Phase 4: Review ──
        if not args.skip_review:
            review = phase_review(i, dry_run=args.dry_run)
            if review:
                issues = len(re.findall(
                    r"(?i)(bug|issue|violation|regression|missing|fail)", review
                ))
                review_issues_total += issues
                if issues:
                    log(f"Review flagged ~{issues} potential issue(s)", "WARN")
                else:
                    log("Review: no major issues found", "OK")
        else:
            log("Review skipped (--skip-review)", "INFO")

        iterations_done += 1

        if _shutdown:
            break

        log(f"Sleeping {SLEEP_BETWEEN}s before next iteration...", "INFO")
        time.sleep(SLEEP_BETWEEN)

    # ─── Summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("📊 RALPH LOOP — Summary")
    print(f"{'='*60}")
    print(f"  Executor used:          {executor['label']}")
    print(f"  Iterations completed:   {iterations_done}")
    print(f"  Stages completed:       {stages_completed or 'None detected'}")
    print(f"  Final stage:            {detect_current_stage()}")
    print(f"  Verify failures total:  {verify_failures_total}")
    print(f"  Review issues flagged:  {review_issues_total}")
    print(f"  Completed:              {'✅ Yes' if is_complete() else '❌ No'}")
    print(f"  Ended:                  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if is_complete():
        print("\n🎉 merdag build is COMPLETE!")
    elif _shutdown:
        print("\n⚠️  Loop was interrupted. Resume with:")
        print(f"   python ralph.py --start-from {args.start_from + iterations_done}")
    else:
        print(f"\n⚠️  Max iterations ({args.max}) reached without completion.")
        print(f"   Resume with: python ralph.py --start-from {args.start_from + iterations_done}")


if __name__ == "__main__":
    main()
