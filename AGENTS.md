# merdag — Agent Knowledge Base

## Patterns & Conventions
- CLI entry point is `merdag/cli.py`, uses click
- All commands output JSON by default, `--human` for pretty print
- File locking via `filelock` package on every write
- Stage 1 keeps examples and docs in repo root plus `examples/` for shared reference artifacts
- Decision queue entries use `## Decision: <label> (node <ID>)` with `**Type:**`, `**Context:**`, `**Default (🤖 codex):**`, and `**Override:**` fields
- Stage 3 routes simulation through `merdag/simulate.py`, with `llm.py` selecting `MERDAG_CODEX_MODEL` for `codex`/`human` tiers and `MERDAG_FAST_MODEL` otherwise
- Stage 4 shares ready-task/status calculations through `merdag/status.py`, while `merdag/watch.py` handles polling, change diffs, and optional `--on-ready` command piping
- Stage 5 serves the live viewer from `merdag/serve.py`, while `merdag/status.py` now owns the pretty `status --human` formatter

## Gotchas
- Python 3 handles emoji natively in regex — no special flags needed
- Mermaid lines can define nodes AND edges on the same line — parser must handle
- Do NOT use `mermaid.init()` for re-rendering — use `mermaid.render()` with new ID
- The loop runs on Windows PowerShell, so examples and docs should stay cross-platform
- Parse tiers from explicit tokens like `🏠local`, `⚡fast`, `🤖codex`, and `🧑human`; do not infer tier from the leading decision emoji alone
- Decision branch skipping must preserve shared descendants that are still reachable from the chosen branch
- Import `openai` lazily inside `llm.call_llm()` so non-simulation commands and tests do not require the SDK at import time
- Watch-mode readiness changes can come from dependency edits even when no node status changes, so `watch.py` compares both node statuses and tier-filtered ready-task sets

## Decisions
- Chose `filelock` over `fcntl.flock` for cross-platform support
- Static template for `merdag init` (not LLM-generated) to avoid API dependency in Stage 2
- Stage 1 uses the coffee brand marketing scenario as the canonical example plan
- Stage 2 ships a click CLI with `parser.py`, `updater.py`, `decisions.py`, and `__main__.py` as the core package surface
- Stage 3 reuses a shared `resolve_decision()` helper so CLI decisions and simulation branch-skipping stay in sync
- Restricted setuptools package discovery to `merdag*` to fix editable-install failures from repo-root directories
- Stage 4 uses simple 1-second mtime polling plus shell-based stdin piping for `merdag watch --on-ready`
- Stage 5 keeps the web UI as one inline HTML page under the built-in `http.server` to stay dependency-free and cross-platform

## Review Feedback
- (appended by reviewer agent between iterations)
- Stage 2 review caught a chained-edge parsing gap; `parser.py` now scans all labeled and plain edges on a line instead of stopping after the first match
- Stage 5 review caught a viewer recovery bug; after fetch failures, the page must clear cached plan/decision text so unchanged content still re-renders on the next successful poll


## Verify Failures — Iteration 2
- pip install -e .: exit 1 —   error: subprocess-exited-with-error
  
  Getting requirements to build editable did not run successfully.
  exit code: 1
  
  [14 lines of output]
  error: Multiple top-level packages discovered in 
- merdag --help: exit 1 — 'merdag' is not recognized as an internal or external command,
operable program or batch file.

- python -m merdag --help: exit 1 — Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "C:\Users\nimis\Projects\merdag\merdag\__main__.py", 

## Verify Failures — Iteration 3
- Live Stage 3 command checks could not run in-session because every available command runner depends on `pwsh.exe`, and this machine does not have PowerShell 6+ installed.


## Verify Failures — Iteration 3
- merdag --help: exit 1 — 'merdag' is not recognized as an internal or external command,
operable program or batch file.

## Verify Failures — Iteration 4
- Live Stage 4 command checks (`python -m unittest`, `pip install -e .`, and the two-terminal `merdag watch` flow) could not run in-session because every available command runner depends on `pwsh.exe`, and this machine does not have PowerShell 6+ installed.



## Verify Failures — Iteration 4
- merdag --help: exit 1 — 'merdag' is not recognized as an internal or external command,
operable program or batch file.

## Verify Failures — Iteration 5
- Live Stage 5 command checks (`python -m unittest`, `pip install -e .`, `merdag --help`, and `python -m merdag --help`) could not run in-session because every available command runner depends on `pwsh.exe`, and this machine does not have PowerShell 6+ installed.



## Verify Failures — Iteration 5
- merdag --help: exit 1 — 'merdag' is not recognized as an internal or external command,
operable program or batch file.

