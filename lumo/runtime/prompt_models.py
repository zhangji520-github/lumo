from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PromptTrust = Literal["kernel", "runtime", "scenario", "capability", "project"]


@dataclass(frozen=True)
class PromptContribution:
    id: str
    order: int
    text: str
    trust: PromptTrust
    source: str


@dataclass(frozen=True)
class RuntimePromptContext:
    scenario_id: str
    scenario_name: str
    role: str
    mission: str
    audience: str
    default_tasks: tuple[str, ...]
    output_contract: tuple[str, ...]
    scenario_prompt: str
    persona: str
    provider_name: str
    model_name: str
    protocol: str
    pack_ids: tuple[str, ...]
    tool_names: tuple[str, ...]
    degraded_capabilities: tuple[str, ...]
    work_dir: str
    surface: str


@dataclass(frozen=True)
class PromptAssembly:
    contributions: tuple[PromptContribution, ...]

    def render(self) -> str:
        ordered = sorted(self.contributions, key=lambda item: (item.order, item.id))
        return "\n\n".join(
            item.text.strip() for item in ordered if item.text.strip()
        )

