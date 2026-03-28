# merdag

Shared Mermaid execution plan for agent fleets.

## Why

Most orchestration tools rely on JSON configs, hidden state, or custom protocols that are hard for humans to inspect quickly.

`merdag` uses a Mermaid file as the shared execution plan, so the same artifact is readable by humans and machines.

## How it works

The system uses two shared files:

```text
              +-------------------+
              |   plan.mermaid    |
              | tasks + deps +    |
              | routing + status   |
              +---------+---------+
                        |
                        v
              +-------------------+
              |   decisions.md    |
              | queued overrides  |
              | for agent/human   |
              | decisions         |
              +-------------------+
```

`plan.mermaid` is the living DAG. `decisions.md` is the queue for unresolved choices and overrides.

## Quick start

Install in editable mode:

```bash
pip install -e .
```

Five-command example:

```bash
merdag init "Launch a social media marketing campaign for a new coffee brand"
merdag next
merdag done A --result "Audience research complete"
merdag decide B --choice "Social" --reason "Best fit for visual storytelling"
merdag status
```

## Node conventions

| Syntax | Meaning |
| --- | --- |
| `[Task name ✅]` | Completed task |
| `[Task name 🔄 agent:name]` | In-progress task owned by a named agent |
| `[Task name ⏳]` | Waiting for dependencies |
| `[Task name ❌]` | Failed task |
| `{🧑 Decision label 🟡}` | Human decision node |
| `{🤖 Decision label 🟡}` | Agent decision node |

Supported Mermaid primitives are intentionally limited to `graph TD`, rectangle nodes `[]`, diamond nodes `{}`, edges `-->`, labeled edges `-->|label|`, and comments `%%`.

## Model tier tags

| Tag | Meaning |
| --- | --- |
| `🏠local` | Local model for privacy-sensitive or offline work |
| `⚡fast` | Fast, cheap model for routine execution |
| `🤖codex` | Higher-capability model for planning and agent decisions |
| `🧑human` | Human approval or execution required |

## CLI commands

| Command | Purpose |
| --- | --- |
| `merdag init "<task description>"` | Create a starter plan from the shared convention |
| `merdag next [--tier <tier>]` | Return currently available tasks, optionally filtered by tier |
| `merdag done <node_id> --result "<description>"` | Mark a task complete and attach a result |
| `merdag fail <node_id> --reason "<description>"` | Mark a task failed and record why |
| `merdag decide <node_id> --choice "<option>" --reason "<why>"` | Resolve a decision node |
| `merdag status [--human]` | Show current plan status as JSON or a human view |
| `merdag decisions` | Show queued decisions and overrides |
| `merdag simulate "<task description>"` | Run the Codex-powered simulation flow |
| `merdag watch [--tier <tier>] [--on-ready "<command>"]` | Watch the plan for changes and newly available work |
| `merdag serve` | Start the live Mermaid viewer |
| `merdag history` | Show plan evolution from git history |
| `merdag cost` | Summarize spend by model tier |
| `merdag list` | List named plans and summaries |
| `merdag export --format <png|json>` | Export the plan to another format |

## Simulation

To run the simulation loop, set `WANDB_API_KEY` and optionally override the default models with `MERDAG_CODEX_MODEL` or `MERDAG_FAST_MODEL`.

```bash
merdag simulate "Launch a social media marketing campaign for a new coffee brand"
```

The command generates `plan.mermaid`, resolves decisions programmatically, prints the updated diagram after each step, and ends with a summary JSON payload.

## Human-friendly viewer

For demos or manual inspection, start the live viewer and the terminal-friendly status view:

```bash
merdag serve
merdag status --human
```

`merdag serve` hosts a dark-theme Mermaid viewer at `http://localhost:8000` by default, or `MERDAG_PORT` if set. The page refreshes `plan.mermaid` and `decisions.md` every 2 seconds so browser output stays in sync with CLI updates.

## Record a demo

To capture a clean `.webm` of the live graph updating in real time, use the Playwright recorder script in the repo root.

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
$env:WANDB_API_KEY = "<your-api-key>"
.\.venv\Scripts\python.exe .\record_demo.py
```

The script starts `python -m merdag serve`, records the viewer at `1280x720`, and uses `python -m merdag simulate` with the coffee-brand prompt when `WANDB_API_KEY` is set. If no API key is present, it falls back to a deterministic local walkthrough so you can still generate `recordings/merdag_demo.webm` without network access. In both modes it copies the generated demo plan and decisions into `recordings/` and restores any existing root-level `plan.mermaid` and `decisions.md` after the run.

## For agents

Agent-facing usage is documented in `SKILL.md`.
