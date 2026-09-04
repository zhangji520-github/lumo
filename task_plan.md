# Lumo Runtime Composition Research

## Goal
Compare Lumo with DeepSeek Harness/Cordis and identify the smallest architecture-preserving change that enables scenario-specific Agent Runtime composition.

## Phases

### Phase 1: Map current architectures
**Status:** complete
- Identify Lumo's five-layer boundaries and existing extension points.
- Identify the minimal DeepSeek Harness composition concepts relevant to Lumo.

### Phase 2: Design the smallest borrowing
**Status:** complete
- Define a lightweight runtime profile/capability model.
- Preserve Lumo's task orchestration, model interaction, tool execution, and security-control architecture.

### Phase 3: Validate against scenarios
**Status:** complete
- Map office and coding runtime examples.
- Identify migration steps, risks, and tests.

### Phase 4: Deliver recommendation
**Status:** complete
- Provide a concrete minimal design and staged implementation path.

### Phase 5: Inspect TUI interaction patterns
**Status:** complete
- Map existing dialogs, startup selection, status bar, commands, and session flows.
- Identify the least disruptive scenario-management interaction model.

### Phase 6: Write detailed design document
**Status:** in_progress
- Specify scenario composition, providers, plugins, security, lifecycle, and persistence.
- Specify TUI screens, user flows, states, validation, and error handling.
- Define implementation scope, tests, migration, and acceptance criteria.

### Phase 7: Review design against current code
**Status:** pending
- Verify references and compatibility with TUI, headless CLI, and Remote entry points.
- Finalize the document and handoff.

## Decisions Made
| Decision | Rationale |
|---|---|
| Perform architecture analysis only | The user asked for ideas and minimal optimization, not implementation. |
| Treat existing Lumo extension points as assets | The change must preserve the current architecture. |
| Borrow profiles and capability packs, not Cordis itself | This captures scenario composition with a small Python-native change. |
| Bind a profile at runtime/session creation | Mid-session capability changes create history and safety consistency problems. |
| Use named capability packs rather than raw tool class paths | Packs preserve dependency wiring and keep config within the existing trust model. |
| Centralize cleanup in the built runtime | MCP clients, sessions, and background tasks need one lifecycle owner. |
| Treat TUI scenario selection and editing as a primary feature | User-defined composition is only useful when discoverable and understandable. |

## Errors Encountered
| Error | Attempt | Resolution |
|---|---|---|
| Lumo directory is not a Git repository | Ran `git status` | Continue with read-only source inspection and avoid source edits. |
| PowerShell wildcard path failed in `rg` | Searched `lumo\*dialog.py` | Enumerate dialog files with `rg --files` before passing explicit paths. |

## Next Step
Write `docs/scenario-runtime-design.md` with architecture, configuration, TUI flows, security, migration, and acceptance criteria.
