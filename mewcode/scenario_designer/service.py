from __future__ import annotations

import re

from pydantic import BaseModel, Field

from mewcode.agent import StreamCollector
from mewcode.conversation import ConversationManager
from mewcode.runtime.models import CapabilityKind, ScenarioExperience
from mewcode.scenario_designer.models import (
    ProposedCapability,
    ScenarioPatch,
    ScenarioProposal,
)
from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult


class ProposalParams(BaseModel):
    scenario_id: str
    name: str
    description: str
    role: str
    mission: str
    audience: str = ""
    default_tasks: list[str] = Field(default_factory=list)
    output_contract: list[str] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    deny_tool_patterns: list[str] = Field(default_factory=list)
    confirm_tool_patterns: list[str] = Field(default_factory=list)
    prompt_draft: str = ""
    unresolved_questions: list[str] = Field(default_factory=list)


class SubmitScenarioProposal(Tool):
    name = "SubmitScenarioProposal"
    description = (
        "Submit one structured scenario proposal. Use only capability IDs from "
        "the supplied catalog; put missing business decisions in unresolved_questions."
    )
    params_model = ProposalParams
    category = "read"
    is_system_tool = True

    async def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult("proposal captured")


class PatchParams(BaseModel):
    role: str | None = None
    mission: str | None = None
    audience: str | None = None
    add_capability_ids: list[str] = Field(default_factory=list)
    remove_capability_ids: list[str] = Field(default_factory=list)
    add_deny_patterns: list[str] = Field(default_factory=list)
    add_confirm_patterns: list[str] = Field(default_factory=list)
    prompt_replacement: str | None = None
    unresolved_questions: list[str] = Field(default_factory=list)


class SubmitScenarioPatch(Tool):
    name = "SubmitScenarioPatch"
    description = (
        "Submit a minimal structured patch for an existing scenario. Omit fields "
        "that the user did not ask to change."
    )
    params_model = PatchParams
    category = "read"
    is_system_tool = True

    async def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult("patch captured")


class ScenarioDesignerService:
    def __init__(self, capabilities) -> None:
        self.capabilities = list(capabilities)

    async def propose(self, description: str, client, protocol: str) -> ScenarioProposal:
        catalog_lines = [
            f"- {item.id} [{item.kind.value}] available={item.available}: "
            f"{item.description}"
            for item in self.capabilities
        ]
        system = """\
You are the MewCode Scenario Designer. Convert the user's goal into a structured
proposal by calling SubmitScenarioProposal exactly once. Recommend only capability
IDs present in the supplied catalog. Do not invent installed capabilities, credentials,
Python imports, shell commands, or bypass permissions. Ask unresolved product choices
through unresolved_questions instead of guessing. The prompt draft describes domain
workflow and output only; it must not repeat MewCode kernel or tool protocol rules.

# Capability Catalog
""" + "\n".join(catalog_lines)
        conversation = ConversationManager()
        conversation.add_user_message(description)
        registry = ToolRegistry()
        registry.register(SubmitScenarioProposal())
        collector = StreamCollector()
        async for _ in collector.consume(client.stream(
            conversation,
            system=system,
            tools=registry.get_all_schemas(protocol),
        )):
            pass
        calls = [
            call for call in collector.response.tool_calls
            if call.tool_name == "SubmitScenarioProposal"
        ]
        if len(calls) != 1:
            raise ValueError("Scenario Designer did not submit exactly one proposal")
        params = ProposalParams.model_validate(calls[0].arguments)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", params.scenario_id):
            raise ValueError("proposed scenario id is invalid")
        by_id = {item.id: item for item in self.capabilities}
        proposed = []
        for capability_id in params.capability_ids:
            descriptor = by_id.get(capability_id)
            if descriptor is None:
                continue
            proposed.append(ProposedCapability(
                id=descriptor.id,
                kind=CapabilityKind(descriptor.kind),
                reason="Recommended for the requested scenario",
                required=True,
            ))
        return ScenarioProposal(
            scenario_id=params.scenario_id,
            name=params.name,
            description=params.description,
            experience=ScenarioExperience(
                role=params.role,
                mission=params.mission,
                audience=params.audience,
                default_tasks=tuple(params.default_tasks),
                output_contract=tuple(params.output_contract),
                prompt_file="prompt.md",
                prompt_text=params.prompt_draft,
            ),
            capabilities=tuple(proposed),
            deny_tool_patterns=tuple(params.deny_tool_patterns),
            confirm_tool_patterns=tuple(params.confirm_tool_patterns),
            prompt_draft=params.prompt_draft,
            unresolved_questions=tuple(params.unresolved_questions),
        )

    async def refine(
        self,
        current,
        request: str,
        client,
        protocol: str,
    ) -> ScenarioPatch:
        catalog_lines = [
            f"- {item.id} [{item.kind.value}] available={item.available}: "
            f"{item.description}"
            for item in self.capabilities
        ]
        current_capabilities = [
            *current.packs,
            *(item.id for item in current.mcp),
            *(item.id for item in current.cli),
            *(item.id for item in current.plugins),
            *current.skills.include,
        ]
        system = """\
You are refining an existing MewCode scenario. Call SubmitScenarioPatch exactly
once. Return only requested changes: do not restate or remove fields the user did
not ask to change. Use only capability IDs in the catalog. Never add credentials,
shell commands, Python imports, or bypass permissions. Put missing business choices
in unresolved_questions.

# Existing scenario
""" + (
            f"ID: {current.id}\nRole: {current.experience.role}\n"
            f"Mission: {current.experience.mission}\n"
            f"Capabilities: {', '.join(current_capabilities)}\n"
            f"Prompt:\n{current.experience.prompt_text}\n\n"
            "# Capability Catalog\n" + "\n".join(catalog_lines)
        )
        conversation = ConversationManager()
        conversation.add_user_message(request)
        registry = ToolRegistry()
        registry.register(SubmitScenarioPatch())
        collector = StreamCollector()
        async for _ in collector.consume(client.stream(
            conversation,
            system=system,
            tools=registry.get_all_schemas(protocol),
        )):
            pass
        calls = [
            call for call in collector.response.tool_calls
            if call.tool_name == "SubmitScenarioPatch"
        ]
        if len(calls) != 1:
            raise ValueError("Scenario Designer did not submit exactly one patch")
        params = PatchParams.model_validate(calls[0].arguments)
        return ScenarioPatch(
            role=params.role,
            mission=params.mission,
            audience=params.audience,
            add_capability_ids=tuple(params.add_capability_ids),
            remove_capability_ids=tuple(params.remove_capability_ids),
            add_deny_patterns=tuple(params.add_deny_patterns),
            add_confirm_patterns=tuple(params.add_confirm_patterns),
            prompt_replacement=params.prompt_replacement,
            unresolved_questions=tuple(params.unresolved_questions),
        )
