from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mewcode.runtime.models import (
    CapabilityDescriptor,
    CapabilityKind,
    RuntimeDiagnostic,
    ScenarioDefinition,
    ScenarioExperience,
)


@dataclass(frozen=True)
class ProposedCapability:
    id: str
    kind: CapabilityKind
    reason: str
    required: bool = True


@dataclass(frozen=True)
class ScenarioProposal:
    scenario_id: str
    name: str
    description: str
    experience: ScenarioExperience
    capabilities: tuple[ProposedCapability, ...]
    deny_tool_patterns: tuple[str, ...] = ()
    confirm_tool_patterns: tuple[str, ...] = ()
    feature_preferences: Mapping[str, bool | str] | None = None
    prompt_draft: str = ""
    unresolved_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioPatch:
    role: str | None = None
    mission: str | None = None
    audience: str | None = None
    add_capability_ids: tuple[str, ...] = ()
    remove_capability_ids: tuple[str, ...] = ()
    add_deny_patterns: tuple[str, ...] = ()
    add_confirm_patterns: tuple[str, ...] = ()
    prompt_replacement: str | None = None
    unresolved_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioDraft:
    definition: ScenarioDefinition
    prompt_text: str
    matched_capabilities: tuple[CapabilityDescriptor, ...]
    missing_capability_ids: tuple[str, ...]
    diagnostics: tuple[RuntimeDiagnostic, ...]
