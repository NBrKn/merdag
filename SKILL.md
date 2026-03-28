---
name: merdag
version: 0.1.0
description: "Shared Mermaid execution plan for agent fleets"
tags: [orchestration, planning, mermaid, multi-agent]
---

# What is merdag

`merdag` is a CLI convention for coordinating a fleet of agents through a shared Mermaid flowchart. The diagram is both the execution graph and the routing layer for different model tiers.

# File Convention

The repo root stores two shared coordination files:

- `plan.mermaid` contains tasks, dependencies, statuses, and tier routing.
- `decisions.md` stores pending or overridden choices for human and agent decision nodes.

Agents should treat these files as the source of truth for the current state of work.

# Node Syntax

- `[Task name ✅]` means the task is complete.
- `[Task name 🔄 agent:name]` means the task is in progress and owned by a named agent.
- `[Task name ⏳]` means the task is waiting on dependencies.
- `[Task name ❌]` means the task failed.
- `{🧑 Decision label 🟡}` means a human decision is required.
- `{🤖 Decision label 🟡}` means an agent decision is required.

Supported Mermaid syntax is intentionally narrow: `graph TD`, rectangle nodes, diamond nodes, plain edges, labeled edges, and `%%` comments.

# Model Tiers

- `🏠local` for local or privacy-sensitive work.
- `⚡fast` for cheap routine execution.
- `🤖codex` for planning, synthesis, and agent decisions.
- `🧑human` for explicit human approval or action.

# CLI Commands

- `merdag init "<task description>"`
- `merdag next [--tier <tier>]`
- `merdag done <node_id> --result "<description>"`
- `merdag fail <node_id> --reason "<description>"`
- `merdag decide <node_id> --choice "<option>" --reason "<why>"`
- `merdag status [--human]`
- `merdag decisions`
- `merdag simulate "<task description>"`
- `merdag watch [--tier <tier>] [--on-ready "<command>"]`
- `merdag serve`
- `merdag history`
- `merdag cost`
- `merdag list`
- `merdag export --format <png|json>`

# How to use as an agent

1. Read `plan.mermaid` and `decisions.md` to understand current state and dependencies.
2. Call `merdag next` to discover which tasks are available right now.
3. Execute the task you picked using the tier and status information from the node.
4. Call `merdag done <node_id> --result "<description>"` when the task completes.
5. Repeat the loop by calling `merdag next` again and taking the next ready task.
