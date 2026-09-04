# Findings

## Initial observations
- Lumo already contains dedicated modules for agents/task coordination, model clients, tools, MCP, skills, permissions, and sandboxing.
- The likely minimum change is a declarative assembly layer over existing modules, not conversion of every component into a Cordis-style plugin.
- DeepSeek Harness composes a runtime from ordered profile/bundle/config layers; the useful idea is composition and capability seams, while Cordis lifecycle/reversible effects are a larger commitment.
- DeepSeek's documented extension rule is "plugins, not loop changes": model providers, tools, filesystem/sandbox providers, prompts, and UI attach outside the default agent loop.
- Lumo already centralizes model-facing capabilities in `ToolRegistry`, including enable/disable and deferred schema discovery. This is a natural activation point for scenario profiles.
- Lumo's agent loop already places hook checks and permission checks before `Tool.execute`, so a composition layer can preserve the existing execution and security pipeline.
- The main assembly is currently concentrated in `LumoApp._select_provider()`: it creates the model client, permission checker, sandbox attachment, registry extras, Agent, Skill loader, Worktree, subagents, teams, and later MCP integration. This is an implicit runtime builder embedded in the UI class.
- `create_default_registry()` hard-codes a coding-oriented baseline (`ReadFile`, `WriteFile`, `EditFile`, `Bash`, `Glob`, `Grep`). A scenario profile can replace this factory input without changing the loop.
- Agent definitions already support per-agent tool allow/deny lists, but MCP tools are currently always passed through by `resolve_agent_tools()`. This is convenient for coding but unsafe/too broad for office scenario least-privilege profiles.
- DeepSeek's boot profile is much more than a list of tools: ordered bundle layers, patches, config validation, lifecycle/disposal, isolation realms, and startup invariants. Lumo does not need all of this for the requested small optimization.
- Lumo already has ordered configuration layers (`~/.lumo/config.yaml`, project config, local config), so profile selection can reuse the current configuration system rather than introducing Cordis-style patch files.
- DeepSeek agent presets scope tools and prompt sections per agent and prevent mid-history composition switches. For a minimal Lumo version, profiles should be fixed when the runtime/session starts; dynamic switching can be deferred.
- The same runtime assembly is duplicated in the TUI and headless CLI, and appears again in `remote.py`. Extracting a builder provides immediate maintenance value even before office plugins exist.

## Evidence
- `lumo/tools/__init__.py`: `ToolRegistry` owns registration, enable/disable, deferred discovery, and schema assembly; `create_default_registry()` hard-codes the coding tools.
- `lumo/agent.py`: the loop assembles tool schemas from the registry and routes calls through hook, permission, validation, and execution stages.
- `deepseek-harness/docs/architecture.md`: profiles stack bundles and patches; capabilities are separated into Definition/Provider/Consumer roles and attach on documented extension points.
- `lumo/app.py:621`: the UI constructor creates the default registry directly.
- `lumo/app.py:704`: `_select_provider()` begins the runtime assembly path.
- `lumo/app.py:719`: the existing permission pipeline is instantiated independently from the tool set.
- `lumo/app.py:778`: the assembled registry and security objects are passed to the unchanged `Agent` loop.
- `lumo/agents/tool_filter.py`: tool subsets are already copied into child registries, proving registry-level composition is compatible with existing agents.
- `lumo/config.py:249`: Lumo already merges user, project, and local configuration in precedence order.
- `lumo/__main__.py:176`: the headless path independently creates the coding registry and Agent.
- `deepseek-harness/packages/preset/agent-presets/README.md`: presets mount model-facing tools and prompt sections per agent and record the selected composition for reconstruction.

## Recommended minimal design
- Add a boot-time composition plane, not a new per-turn processing layer.
- `RuntimeProfile`: declarative scenario selection containing capability-pack ids, MCP server references, skill allow-list, feature flags, prompt/persona reference, and security-policy reference.
- `CapabilityPack`: a small Python registration function/object that contributes related tools and optional prompt guidance. Start with built-in packs only; no dynamic package loader or dependency graph.
- `RuntimeSpec`: validated, fully resolved immutable result of applying the selected profile to existing layered config. Unknown packs, MCP refs, or skills fail at startup.
- `RuntimeBuilder`: the single assembly path used by TUI, headless CLI, and Remote; it constructs the existing client, registry, permission checker, sandbox, Agent, managers, Skills, and MCP connections without modifying the Agent loop.
- `BuiltRuntime.aclose()`: centralize MCP/session/background-task cleanup; this borrows lifecycle ownership without adopting Cordis reversible effects.
- Store `profile_id` in `SessionMeta`; resume under the recorded profile or fail with an explicit mismatch. Do not switch profiles after a session has tool-call history.

## Example packs and profiles
- `core.skills`: `LoadSkill`, optional `InstallSkill`, and catalog prompt.
- `coding.files`: `ReadFile`, `WriteFile`, `EditFile`, `Glob`, `Grep` with one shared `FileStateCache`.
- `coding.shell`: `Bash`, worktree tools, coding sandbox attachment.
- `coding.agents`: subagent/team/task tools.
- `office` can initially be almost entirely MCP-backed: docs/review, knowledge-base, mail, and calendar server references plus office skills/persona, with no Bash or file-write pack.
- `coding` combines coding packs plus optional Git/GitHub/context MCP servers.

## Safety observations
- A profile should select capabilities but never bypass the existing `PermissionChecker`/hook/tool-execution path.
- MCP tools currently all use category `command`; office read/write semantics are therefore too coarse. This is not required for the first profile MVP, but the next small safety improvement should allow an MCP mapping/annotation to classify each external tool as `read`, `write`, or `command`.
- Capability/profile ids should be trusted config identifiers, not import paths. Dynamic third-party Python plugin loading is a later feature with a larger trust and lifecycle surface.

## TUI design observations
- The current startup UI already has a central provider selector, so scenario selection can extend the launcher instead of being bolted onto the chat loop.
- Existing approvals, questions, and session selection use keyboard-first inline widgets that disable chat input while active. Scenario inspection can follow this visual language, while multi-step scenario editing is better served by a dedicated full-screen Textual screen.
- The status bar currently shows permission mode, teammates, MCP connection progress, and model. A compact scenario label belongs at the left edge and makes the active runtime continuously visible.
- Existing slash-command completion and local command registration make `/scenario` a natural secondary entry point for list, inspect, create, edit, validate, and use operations.
- Because scenario changes alter tool schemas and prompt context, an active non-empty session must not hot-switch. The TUI should offer to start a new session under the selected scenario.
