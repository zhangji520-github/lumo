# Lumo Agent Harness

**English** | [简体中文](README.zh-CN.md)

A terminal-first harness for composing scenario-specific Agent runtimes and
coordinating multiple agents safely inside one project.

![Lumo Coding runtime launcher](assets/screenshots/coding.png)

## Why Lumo

### Compiled long-term memory

Lumo can combine its transparent, human-editable Markdown memory with a
[GBrain](https://github.com/garrytan/gbrain) compiled knowledge brain. Choose
`markdown`, `gbrain`, or `hybrid`; the hybrid mode keeps compact standing rules
in Markdown while GBrain provides provenance-aware facts, entity relationships,
keyword/vector retrieval, correction and withdrawal, and cross-source synthesis.

Memory is placed at the runtime boundary instead of left to prompt luck. Lumo
warms GBrain context once per session, resolves relevant memory before the first
model inference, and rehydrates it after compaction. Reads fail open to Markdown
when GBrain is unavailable, while transient, explicitly-authorized `remember`
writes are queued locally for retry. Permission or validation failures never
bypass their safety boundary.

```yaml
memory:
  mode: hybrid
  gbrain_server: gbrain
  recall_timeout_seconds: 2.0
  recall_budget_tokens: 2000
  auto_capture: false

mcp_servers:
  - name: gbrain
    command: gbrain
    args: [serve, --surface, verbs]
    env:
      GBRAIN_HOME: ${LUMO_GBRAIN_HOME}
```

Initialize the optional local brain separately before starting Lumo:

```powershell
bun install -g github:garrytan/gbrain#latest-stable
gbrain init --pglite --no-embedding
gbrain doctor --json
```

The integration targets GBrain's small, frozen `MEMORY_VERBS v1` surface rather
than its internal implementation, so the memory layer remains replaceable and
does not flood the agent with a large tool catalog. Automatic GBrain conversation
capture is opt-in; explicit memories require provenance.

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
