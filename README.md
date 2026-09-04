# Lumo Agent Harness

A terminal-first harness for composing scenario-specific Agent runtimes and
coordinating multiple agents safely inside one project.

![Lumo Coding runtime launcher](assets/screenshots/coding.png)

## Why Lumo

### Composable scenario runtimes

A scenario is more than a tool preset. Lumo can combine built-in capability
packs, MCP servers, external CLI tools, Python plugins, Skills, prompts, memory,
feature switches, and security policy into one validated `RuntimeSpec`.

Each selected scenario gets its own effective tool set, identity, prompt,
permissions, memory namespace, and session. The TUI, headless CLI, and Remote
surface all use the same resolver and runtime builder, so the declared scenario
and the runtime that actually executes stay aligned.

![Lumo Office runtime launcher](assets/screenshots/office.png)

### Multi-agent collaboration harness

Lumo can split a larger task across Coordinator and Worker agents. Each Worker
uses an isolated Git Worktree to avoid file conflicts, while a file-based
mailbox carries structured task assignments, progress updates, and shutdown
requests between agents.

This keeps task decomposition, parallel execution, progress tracking, and code
isolation inside the harness instead of leaving coordination to prompt text.

![Lumo Empty runtime launcher](assets/screenshots/empty.png)

## Development

```powershell
uv sync
uv run lumo --help
uv run pytest
```

Configuration is loaded from `.lumo/config.yaml`. Existing `.mewcode` project
data is migrated without overwriting `.lumo` files.
