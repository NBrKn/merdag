# merdag — Build Context (Companion File)

This file provides **background context** for the merdag build spec. It does NOT override the spec — the spec is the source of truth. This file explains the *why* behind decisions and provides environmental context.

**Usage:** Place this file as `CONTEXT.md` in the repo root alongside the spec. Read it once at the start. Refer back only if something in the spec is unclear.

---

## Project Origin

merdag was conceived during a hackathon brainstorming session. The core insight:

> Existing multi-agent orchestration tools use JSON configs or custom protocols that are opaque to humans. A Mermaid flowchart is readable by both humans AND machines — it's a visual DAG that doubles as an execution plan.
> 

The name **merdag** = **mer**maid + **dag** (directed acyclic graph).

---

## Hackathon Track

**Statement 1 — Codex-Powered Services.** The project must integrate OpenAI Codex. merdag satisfies this via the `simulate` command (Stage 3), which uses Codex/GPT-4o to generate plans and execute tasks autonomously.

We originally considered Statement 2 (agent-first, no human GUI) but pivoted to Statement 1 because the Codex integration is the strongest differentiator.

---

## Key Design Decisions & Why

### Why `.mermaid` not `.md`?

The plan file is `plan.mermaid`, not `plan.md`. This ensures tools and editors recognize it as a Mermaid file natively, and avoids confusion with markdown decision files.

### Why only `graph TD` and basic shapes?

We deliberately constrain the Mermaid parser to ONLY handle `graph TD`, rectangles `[]`, diamonds `{}`, plain edges `-->`, and labeled edges `-->|label|`. This keeps the parser simple enough to build in a hackathon. Subgraphs, styles, and other Mermaid features are explicitly out of scope.

### Why `filelock` instead of `fcntl.flock`?

`fcntl.flock` is Unix-only and would break on Windows. The `filelock` package is cross-platform and pip-installable. This was a deliberate choice for WSL + Windows compatibility.

### Why static template for `merdag init` instead of LLM-generated?

Stage 2 should work **without** an API key. The `init` command uses a hardcoded template copied from `examples/plan.mermaid` (with statuses reset to ⏳). LLM-generated plans only happen in Stage 3's `simulate` command.

### Why JSON output by default, `--human` flag for pretty?

The primary consumers of merdag are **other agents**, not humans. Agents parse JSON natively. The `--human` flag is for demos and debugging only.

### Why `mermaid.render()` not `mermaid.init()`?

`mermaid.init()` only processes un-rendered elements. For live re-rendering (when the plan file changes), you must delete the old SVG, create a new div with a unique ID, and call `mermaid.render('diagram-' + counter, newContent)`. This is a known Mermaid.js gotcha.

### Why direct Python imports in simulate, not subprocess?

The simulation engine (`simulate.py`) imports parser/updater functions directly rather than shelling out to `subprocess.run("merdag ...")`. This avoids subprocess overhead, PATH issues, and keeps the simulation fast.

### Why single-line node+edge parsing matters

Mermaid allows defining nodes AND edges on the same line:

```
A[Research ✅] --> B{🤖 Decision? 🟡}
```

This single line defines node A, node B, AND edge A→B. The parser regex must handle this — it's the #1 parser gotcha.

---

## Build Agent Strategy

- **Primary agent:** Codex CLI (uses OpenAI credits)
- **Backup agent:** Claude Code (if Codex CLI hits credit limits or gets stuck)
- **Other available tools:** GitHub Copilot, Gemini CLI — useful for code review during the review window, not primary build
- **Cross-model review (Rule 9):** The reviewing agent reads git diff and appends feedback to `AGENTS.md`. It does NOT commit. This is optional but recommended.

---

## Environment Details

- **OS:** Windows with PowerShell (confirmed from Iteration 1)
- **Activation command:** `.venv\Scripts\activate` (NOT `source .venv/bin/activate`)
- **Python:** 3.11+
- **Git:** Available natively
- **Note:** `mkdir -p` doesn't work in PowerShell — use `mkdir ... -ErrorAction SilentlyContinue` or `New-Item -ItemType Directory -Force`

---

## Ralph Loop Protocol — Context

The Ralph Loop is an autonomous AI coding pattern (named after Ralph Wiggum) where:

1. The agent runs in a fresh context window each iteration
2. It reads the spec + `progress.md` to know where it left off
3. It completes exactly one stage
4. It commits and stops
5. A new iteration starts fresh

**Why this matters:** The agent has NO memory between iterations. Everything it needs to know must be in the repo files (spec, `progress.md`, `AGENTS.md`, git log).

### Optimizations added to the spec

1. **COMPLETION signal** (`<promise>COMPLETE</promise>`) — so the loop script knows to exit
2. **Feedback loops** (Stage 2+) — `pip install -e .`, `merdag --help`, parser import check before every commit
3. [**AGENTS.md**](http://AGENTS.md) — persistent knowledge base that accumulates across iterations
4. **Cross-model review** — optional reviewer agent that reads diffs between iterations

### Common Ralph Loop failure modes to watch for

- Agent tries to do multiple stages in one iteration (violates Rule 3)
- Agent relies on conversation memory instead of reading files (violates Rule 1)
- Agent skips `progress.md` updates before committing (violates Rule 2)
- Agent runs feedback checks during Stage 0 or 1 (they should be skipped)

---

## Iteration History

### Iteration 1: Stage 0 — Environment Bootstrap ✅

- **Commit:** `7055984d783622e33065c2136f998220221a8989`
- **Result:** All files created, git repo initialized, stopped cleanly
- **Observations:**
    - Ran in PowerShell (not WSL)
    - Needed a second commit attempt because `git config user.name/email` wasn't set initially
    - Did NOT try to run `pip install -e .` (correct)
    - Did NOT start Stage 1 (correct)

### Iteration 2: Stage 1 — File Convention

- **Status:** Pending

### Iteration 3: Stage 2 — CLI Agent Interface

- **Status:** Pending

---

## Hackathon Schedule

- **10:00 – 12:30:** Hands-on building
- **12:30 – 3:30:** 🦞 Lobster break (no laptop)
- **3:30 – 4:30:** Review and polish
- **4:30:** Submission deadline

---

## Model Configuration

| Env Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | (required) | API authentication |
| `MERDAG_CODEX_MODEL` | `gpt-4o` | Orchestrator model — update to hackathon Codex model when available |
| `MERDAG_FAST_MODEL` | `gpt-4o-mini` | Executor model for cheap tasks |
| `MERDAG_PORT` | `8000` | Web viewer port |

**Important:** `codex` is NOT a real OpenAI model name. The spec defaults to `gpt-4o`. Update the env var when the hackathon provides the actual Codex model name.

---

## Demo Scenario

The demo uses: **"Launch a social media marketing campaign for a new coffee brand"**

This scenario was chosen because it naturally produces:

- Multiple parallel tasks (research, copywriting, design)
- Decision points (which channel? approve creative?)
- Different complexity tiers (research = local, copy = fast, strategy = codex, approval = human)

---

## Spec Review Issues Found & Fixed

During review, 13 issues were identified and fixed in the spec:

### Critical (agent would get stuck)

1. No environment bootstrap — added Stage 0
2. Feedback loops would fail during Stage 1 — clarified "Stage 2+ ONLY"
3. `parse_plan` function name referenced but never defined — changed to `Node, Edge, Plan` import check
4. Missing `[build-system]` in pyproject.toml — added full template

### Medium (agent would be confused)

1. CLI commands for Stages 3-5 not registered in `cli.py` — added registration notes
2. `decisions.md` parsing logic undefined — added regex patterns
3. No `pathlib.Path` guidance — added cross-platform file path note
4. No `.gitignore` — added to Stage 0
5. `mermaid.live` validation not programmatically checkable — noted as manual check

### Low (nice to have)

1. No `__init__.py` content specified — noted as empty
2. Project structure missing `.gitignore` and `progress.md` — added