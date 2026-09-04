from __future__ import annotations

from dataclasses import replace

from lumo.runtime.builtins import EMPTY_SCENARIO
from lumo.runtime.models import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityRef,
    RuntimeDiagnostic,
    ScenarioFeatures,
    ScenarioSecurity,
    SkillSelection,
)
from lumo.scenario_designer.models import (
    ScenarioDraft,
    ScenarioPatch,
    ScenarioProposal,
)


class ProposalCompiler:
    def __init__(self, capabilities: list[CapabilityDescriptor]) -> None:
        self.capabilities = {item.id: item for item in capabilities}

    def compile(self, proposal: ScenarioProposal) -> ScenarioDraft:
        matched: list[CapabilityDescriptor] = []
        missing: list[str] = []
        diagnostics: list[RuntimeDiagnostic] = []
        selected: dict[CapabilityKind, list[str]] = {
            kind: [] for kind in CapabilityKind
        }
        required_by_id = {
            item.id: item.required for item in proposal.capabilities
        }

        for proposed in proposal.capabilities:
            descriptor = self.capabilities.get(proposed.id)
            if descriptor is None or descriptor.kind != proposed.kind:
                missing.append(proposed.id)
                diagnostics.append(RuntimeDiagnostic(
                    severity="error" if proposed.required else "warning",
                    code="capability-not-found",
                    capability_id=proposed.id,
                    message=f"Capability '{proposed.id}' is not present in this catalog",
                    hint="Configure or install it, or remove it from the proposal.",
                ))
                continue
            if not descriptor.available:
                missing.append(proposed.id)
                diagnostics.append(RuntimeDiagnostic(
                    severity="error" if proposed.required else "warning",
                    code="capability-unavailable",
                    capability_id=proposed.id,
                    message=(
                        f"Capability '{proposed.id}' is unavailable: "
                        f"{descriptor.unavailable_reason}"
                    ),
                ))
                continue
            selected[descriptor.kind].append(descriptor.id)
            matched.append(descriptor)

        preferences = dict(proposal.feature_preferences or {})
        features = ScenarioFeatures(
            memory=bool(preferences.get("memory", True)),
            worktree=bool(preferences.get("worktree", False)),
            subagents=bool(preferences.get("subagents", False)),
            teams=bool(preferences.get("teams", False)),
            skill_install=bool(preferences.get("skill_install", False)),
        )

        def refs(kind: CapabilityKind) -> tuple[CapabilityRef, ...]:
            return tuple(
                CapabilityRef(
                    id=capability_id,
                    required=required_by_id.get(capability_id, True),
                )
                for capability_id in selected[kind]
            )

        definition = replace(
            EMPTY_SCENARIO,
            schema_version=2,
            id=proposal.scenario_id,
            name=proposal.name,
            description=proposal.description,
            persona="",
            experience=replace(
                proposal.experience,
                prompt_file="prompt.md",
                prompt_text=proposal.prompt_draft,
            ),
            packs=tuple(selected[CapabilityKind.PACK]),
            mcp=refs(CapabilityKind.MCP),
            cli=refs(CapabilityKind.CLI),
            plugins=refs(CapabilityKind.PLUGIN),
            skills=SkillSelection(
                include=tuple(selected[CapabilityKind.SKILL]),
                exclude=(),
            ),
            features=features,
            security=ScenarioSecurity(
                permission_mode="default",
                sandbox="inherit",
                confirm_tools=proposal.confirm_tool_patterns,
                deny_tools=proposal.deny_tool_patterns,
            ),
            use_all_configured_mcp=False,
        )
        return ScenarioDraft(
            definition=definition,
            prompt_text=proposal.prompt_draft,
            matched_capabilities=tuple(matched),
            missing_capability_ids=tuple(missing),
            diagnostics=tuple(diagnostics),
        )

    def apply_patch(
        self,
        current,
        patch: ScenarioPatch,
    ) -> ScenarioDraft:
        selected: dict[CapabilityKind, dict[str, object]] = {
            CapabilityKind.PACK: {
                item_id: item_id for item_id in current.packs
            },
            CapabilityKind.MCP: (
                {
                    item.id: CapabilityRef(id=item.id)
                    for item in self.capabilities.values()
                    if item.kind == CapabilityKind.MCP and item.available
                }
                if current.use_all_configured_mcp
                else {item.id: item for item in current.mcp}
            ),
            CapabilityKind.CLI: {item.id: item for item in current.cli},
            CapabilityKind.PLUGIN: {item.id: item for item in current.plugins},
            CapabilityKind.SKILL: {
                item_id: item_id
                for item_id in current.skills.include
            },
        }
        diagnostics: list[RuntimeDiagnostic] = []
        matched: list[CapabilityDescriptor] = []
        missing: list[str] = []
        for capability_id in patch.remove_capability_ids:
            for values in selected.values():
                values.pop(capability_id, None)
        for capability_id in patch.add_capability_ids:
            descriptor = self.capabilities.get(capability_id)
            if descriptor is None or not descriptor.available:
                missing.append(capability_id)
                diagnostics.append(RuntimeDiagnostic(
                    severity="error",
                    code="capability-not-found",
                    capability_id=capability_id,
                    message=f"Capability '{capability_id}' is unavailable",
                ))
                continue
            matched.append(descriptor)
            if descriptor.kind in (
                CapabilityKind.MCP,
                CapabilityKind.CLI,
                CapabilityKind.PLUGIN,
            ):
                selected[descriptor.kind][capability_id] = CapabilityRef(
                    id=capability_id
                )
            else:
                selected[descriptor.kind][capability_id] = capability_id

        experience = replace(
            current.experience,
            role=(patch.role if patch.role is not None else current.experience.role),
            mission=(
                patch.mission
                if patch.mission is not None
                else current.experience.mission
            ),
            audience=(
                patch.audience
                if patch.audience is not None
                else current.experience.audience
            ),
            prompt_text=(
                patch.prompt_replacement
                if patch.prompt_replacement is not None
                else current.experience.prompt_text
            ),
        )
        definition = replace(
            current,
            experience=experience,
            packs=tuple(selected[CapabilityKind.PACK]),
            mcp=tuple(selected[CapabilityKind.MCP].values()),
            cli=tuple(selected[CapabilityKind.CLI].values()),
            plugins=tuple(selected[CapabilityKind.PLUGIN].values()),
            skills=replace(
                current.skills,
                include=tuple(selected[CapabilityKind.SKILL]) or (),
            ),
            security=replace(
                current.security,
                deny_tools=tuple(dict.fromkeys(
                    (*current.security.deny_tools, *patch.add_deny_patterns)
                )),
                confirm_tools=tuple(dict.fromkeys((
                    *current.security.confirm_tools,
                    *patch.add_confirm_patterns,
                ))),
            ),
            use_all_configured_mcp=False,
        )
        return ScenarioDraft(
            definition=definition,
            prompt_text=experience.prompt_text,
            matched_capabilities=tuple(matched),
            missing_capability_ids=tuple(missing),
            diagnostics=tuple(diagnostics),
        )
