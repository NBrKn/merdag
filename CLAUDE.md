# merdag — Agent Instructions

Read these files at the start of every iteration:

1. `init_spec.md` — the build spec (source of truth)
2. `progress.md` — your memory between iterations (tells you where you left off)
3. `AGENTS.md` — persistent knowledge base (read if it exists)
4. `CONTEXT.MD` — background context and design decisions

Follow the **Ralph Loop Protocol** defined in the spec:
- Complete exactly ONE stage per iteration
- Update `progress.md` and `AGENTS.md` before committing
- Git commit with message: `merdag: Stage N — <what was done>`
- STOP after one stage. Do NOT start the next.
- If ALL stages are complete, add `<promise>COMPLETE</promise>` to `progress.md`
