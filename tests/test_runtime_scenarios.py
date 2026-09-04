from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from lumo.config import AppConfig, MCPServerConfig, ProviderConfig
from lumo.memory.session import SessionManager
from lumo.runtime.builtins import CODING_SCENARIO, OFFICE_SCENARIO
from lumo.runtime.cli_provider import CliTool, parse_cli_definition
from lumo.runtime.errors import CapabilityUnavailableError, ScenarioValidationError
from lumo.runtime.models import RuntimeSpec
from lumo.runtime.packs import create_registry_for_spec
from lumo.runtime.resolver import ScenarioResolver
from lumo.runtime.scenario_loader import (
    ScenarioLoader,
    parse_scenario,
    scenario_to_dict,
)
from lumo.tools import ToolOrigin, ToolRegistry
from lumo.tools.base import Tool, ToolResult
from pydantic import BaseModel


def _config(*mcp: MCPServerConfig) -> AppConfig:
    return AppConfig(
        providers=[
            ProviderConfig(
                name="test",
                protocol="openai",
                base_url="http://localhost",
                model="test-model",
            )
        ],
        mcp_servers=list(mcp),
    )


def test_builtin_coding_resolves_all_configured_mcp(tmp_path: Path) -> None:
    server = MCPServerConfig(name="docs", command="docs-mcp")
    spec = ScenarioResolver(_config(server), str(tmp_path)).resolve("coding")

    assert spec.scenario.id == "coding"
    assert spec.mcp_servers == (server,)
    assert spec.fingerprint
    assert {tool.name for tool in create_registry_for_spec(spec).list_tools()} == {
        "ReadFile", "WriteFile", "EditFile", "Glob", "Grep", "Bash",
    }


def test_office_has_no_coding_tools(tmp_path: Path) -> None:
    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("office")
    assert create_registry_for_spec(spec).list_tools() == []
    assert spec.scenario.features.worktree is False
    assert spec.scenario.features.subagents is False


def test_prompt_experience_matches_office_runtime(tmp_path: Path) -> None:
    from unittest.mock import MagicMock
    from lumo.agent import Agent
    from lumo.prompts import build_system_prompt

    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("office")
    agent = Agent(
        client=MagicMock(),
        registry=create_registry_for_spec(spec),
        protocol="openai",
        work_dir=str(tmp_path),
        runtime_persona=spec.scenario.persona,
        runtime_spec=spec,
        provider_name="bltcy-deepseek",
        model_name="deepseek-v4-flash",
        surface="tui",
    )
    prompt = build_system_prompt(
        runtime_context=agent._build_runtime_prompt_context(),
        persona=agent.runtime_persona,
    )

    assert "office assistant" in prompt
    assert "Configured provider: bltcy-deepseek" in prompt
    assert "Configured model: deepseek-v4-flash" in prompt
    assert "Available tools: none" in prompt
    assert "AI programming assistant" not in prompt
    assert "Software engineering workflow" not in prompt
    assert "Use Bash" not in prompt
    assert "Anthropic" not in prompt


def test_prompt_experience_matches_coding_runtime(tmp_path: Path) -> None:
    from unittest.mock import MagicMock
    from lumo.agent import Agent
    from lumo.prompts import build_system_prompt

    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("coding")
    agent = Agent(
        client=MagicMock(),
        registry=create_registry_for_spec(spec),
        protocol="openai",
        work_dir=str(tmp_path),
        runtime_persona=spec.scenario.persona,
        runtime_spec=spec,
        provider_name="test",
        model_name="test-model",
    )
    prompt = build_system_prompt(
        runtime_context=agent._build_runtime_prompt_context(),
    )

    assert "AI programming assistant" in prompt
    assert "Software engineering workflow" in prompt
    assert "Use Bash only" in prompt
    assert "ReadFile" in prompt


def test_scenario_parser_rejects_bypass() -> None:
    raw = scenario_to_dict(OFFICE_SCENARIO)
    raw["security"]["permission_mode"] = "bypassPermissions"
    with pytest.raises(ScenarioValidationError, match="cannot enable"):
        parse_scenario(raw)


def test_project_scenario_overrides_user_and_builtin(tmp_path: Path) -> None:
    loader = ScenarioLoader(str(tmp_path))
    loader.user_dir = tmp_path / "user"
    loader.project_dir = tmp_path / ".lumo" / "scenarios"
    loader.user_dir.mkdir(parents=True)
    loader.project_dir.mkdir(parents=True)
    user = scenario_to_dict(CODING_SCENARIO)
    user["name"] = "User Coding"
    project = scenario_to_dict(CODING_SCENARIO)
    project["name"] = "Project Coding"
    import yaml
    (loader.user_dir / "coding.yaml").write_text(yaml.safe_dump(user), encoding="utf-8")
    (loader.project_dir / "coding.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")

    record = loader.get("coding")
    assert record is not None
    assert record.source == "project"
    assert record.definition is not None
    assert record.definition.name == "Project Coding"


def test_scenario_save_rejects_path_like_id(tmp_path: Path) -> None:
    from dataclasses import replace

    loader = ScenarioLoader(str(tmp_path))
    loader.user_dir = tmp_path / "user"
    with pytest.raises(ScenarioValidationError):
        loader.save(replace(OFFICE_SCENARIO, id="../escape"))
    assert not (tmp_path / "escape.yaml").exists()


def test_directory_scenario_package_loads_prompt_and_conflicts(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from lumo.runtime.models import ScenarioExperience
    from lumo.runtime.scenario_package import ScenarioPackageWriter

    writer = ScenarioPackageWriter(str(tmp_path))
    writer.user_root = tmp_path / "user"
    definition = replace(
        OFFICE_SCENARIO,
        id="contract-review",
        name="Contract Review",
        experience=ScenarioExperience(
            role="contract review assistant",
            mission="Review contracts",
        ),
    )
    writer.write(
        definition,
        "# Workflow\n\nReview clauses and cite the original text.",
    )

    loader = ScenarioLoader(str(tmp_path))
    loader.user_dir = writer.user_root
    record = loader.get("contract-review")
    assert record is not None and record.healthy
    assert record.definition is not None
    assert record.definition.schema_version == 2
    assert "Review clauses" in record.definition.experience.prompt_text

    (writer.user_root / "contract-review.yaml").write_text(
        "schema_version: 1\nid: contract-review\nname: duplicate\n",
        encoding="utf-8",
    )
    conflict = loader.get("contract-review")
    assert conflict is not None
    assert not conflict.healthy
    assert "both" in conflict.error

    (writer.user_root / "contract-review.yaml").unlink()
    assert loader.delete("contract-review")
    assert not (writer.user_root / "contract-review").exists()


def test_scenario_package_rejects_inline_secret(tmp_path: Path) -> None:
    from dataclasses import replace
    from lumo.runtime.models import CapabilityRef
    from lumo.runtime.scenario_package import ScenarioPackageWriter

    definition = replace(
        OFFICE_SCENARIO,
        id="secret-office",
        plugins=(
            CapabilityRef(id="fake", config={"api_token": "plain-secret"}),
        ),
    )
    writer = ScenarioPackageWriter(str(tmp_path))
    writer.user_root = tmp_path / "user"
    with pytest.raises(ScenarioValidationError, match="environment reference"):
        writer.write(definition, "safe prompt")


def test_proposal_compiler_uses_only_catalog_capabilities() -> None:
    from lumo.runtime.models import (
        CapabilityDescriptor,
        CapabilityKind,
        ScenarioExperience,
    )
    from lumo.scenario_designer.models import (
        ProposedCapability,
        ScenarioProposal,
    )
    from lumo.scenario_designer.proposal_compiler import ProposalCompiler

    catalog = [
        CapabilityDescriptor(
            id="core.session",
            kind=CapabilityKind.PACK,
            title="Session",
            description="Session support",
            source="builtin",
            risk="read",
        ),
    ]
    proposal = ScenarioProposal(
        scenario_id="research",
        name="Research",
        description="Research assistant",
        experience=ScenarioExperience(
            role="research assistant",
            mission="Produce sourced research",
        ),
        capabilities=(
            ProposedCapability(
                id="core.session",
                kind=CapabilityKind.PACK,
                reason="Keep a session",
            ),
            ProposedCapability(
                id="invented-mail",
                kind=CapabilityKind.MCP,
                reason="Send mail",
            ),
        ),
        prompt_draft="# Workflow\n\nCite sources.",
    )
    draft = ProposalCompiler(catalog).compile(proposal)
    assert draft.definition.packs == ("core.session",)
    assert draft.definition.mcp == ()
    assert draft.missing_capability_ids == ("invented-mail",)
    assert draft.diagnostics[0].code == "capability-not-found"


def test_scenario_patch_preserves_unrequested_fields() -> None:
    from lumo.runtime.models import (
        CapabilityDescriptor,
        CapabilityKind,
    )
    from lumo.scenario_designer.models import ScenarioPatch
    from lumo.scenario_designer.proposal_compiler import ProposalCompiler

    catalog = [
        CapabilityDescriptor(
            id="calendar",
            kind=CapabilityKind.MCP,
            title="Calendar",
            description="Calendar service",
            source="config",
            risk="command",
        ),
    ]
    patch = ScenarioPatch(
        mission="Prepare office work and help with schedules",
        add_capability_ids=("calendar",),
        add_confirm_patterns=("mcp_calendar_create_*",),
    )
    draft = ProposalCompiler(catalog).apply_patch(OFFICE_SCENARIO, patch)
    assert draft.definition.experience.role == "office assistant"
    assert draft.definition.experience.mission == patch.mission
    assert draft.definition.packs == OFFICE_SCENARIO.packs
    assert draft.definition.skills.include == OFFICE_SCENARIO.skills.include
    assert draft.definition.mcp[0].id == "calendar"
    assert "mcp_calendar_create_*" in draft.definition.security.confirm_tools


def test_scenario_prompt_participates_in_runtime_fingerprint(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from lumo.runtime.models import ScenarioExperience

    first = replace(
        OFFICE_SCENARIO,
        experience=ScenarioExperience(
            role="office assistant",
            prompt_text="first workflow",
        ),
    )
    second = replace(
        first,
        experience=replace(first.experience, prompt_text="second workflow"),
    )
    one = RuntimeSpec(first, "test", ())
    two = RuntimeSpec(second, "test", ())
    assert RuntimeSpec.calculate_fingerprint(one.snapshot()) != (
        RuntimeSpec.calculate_fingerprint(two.snapshot())
    )


def test_missing_optional_mcp_is_diagnostic(tmp_path: Path) -> None:
    loader = ScenarioLoader(str(tmp_path))
    loader.project_dir.mkdir(parents=True)
    raw = scenario_to_dict(OFFICE_SCENARIO)
    raw["id"] = "optional"
    raw["name"] = "Optional"
    raw["capabilities"]["mcp"] = [{"id": "missing", "required": False}]
    import yaml
    (loader.project_dir / "optional.yaml").write_text(
        yaml.safe_dump(raw), encoding="utf-8"
    )
    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("optional")
    assert spec.mcp_servers == ()
    assert spec.diagnostics[0].code == "mcp-definition-missing"


def test_missing_required_mcp_fails(tmp_path: Path) -> None:
    loader = ScenarioLoader(str(tmp_path))
    loader.project_dir.mkdir(parents=True)
    raw = scenario_to_dict(OFFICE_SCENARIO)
    raw["id"] = "required"
    raw["name"] = "Required"
    raw["capabilities"]["mcp"] = [{"id": "missing", "required": True}]
    import yaml
    (loader.project_dir / "required.yaml").write_text(
        yaml.safe_dump(raw), encoding="utf-8"
    )
    with pytest.raises(CapabilityUnavailableError):
        ScenarioResolver(_config(), str(tmp_path)).resolve("required")


def test_pack_dependencies_are_added_in_stable_order(tmp_path: Path) -> None:
    loader = ScenarioLoader(str(tmp_path))
    loader.project_dir.mkdir(parents=True)
    raw = scenario_to_dict(OFFICE_SCENARIO)
    raw["id"] = "team"
    raw["name"] = "Team"
    raw["capabilities"]["packs"] = ["coding.teams"]
    import yaml
    (loader.project_dir / "team.yaml").write_text(
        yaml.safe_dump(raw), encoding="utf-8"
    )
    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("team")
    assert spec.scenario.packs == (
        "core.interaction",
        "coding.agents",
        "coding.teams",
    )


def test_cli_wrapper_builds_argv_without_shell(tmp_path: Path) -> None:
    definition = parse_cli_definition({
        "id": "python.echo",
        "name": "PythonEcho",
        "description": "Echo one value",
        "executable": sys.executable,
        "category": "read",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "argv": ["-c", "import sys; print(sys.argv[1])", "{value}"],
        "permission_targets": [{"parameter": "value", "access": "read"}],
    })
    tool = CliTool(definition, str(tmp_path))
    assert tool._argv({"value": "a; echo unsafe"})[-1] == "a; echo unsafe"
    assert tool.permission_targets({"value": "x"})[0].resource == "x"


def test_cli_definition_rejects_shell_executable() -> None:
    with pytest.raises(ValueError, match="single program"):
        parse_cli_definition({
            "id": "bad",
            "name": "Bad",
            "description": "Bad",
            "executable": "echo | sh",
            "parameters": {"type": "object", "properties": {}},
            "argv": [],
        })


def test_invalid_unselected_cli_does_not_break_scenario(
    tmp_path: Path,
) -> None:
    cli_dir = tmp_path / ".lumo" / "cli-tools"
    cli_dir.mkdir(parents=True)
    (cli_dir / "broken.yaml").write_text("id: broken\n", encoding="utf-8")
    spec = ScenarioResolver(_config(), str(tmp_path)).resolve("office")
    assert spec.scenario.id == "office"


class _Params(BaseModel):
    pass


class _Tool(Tool):
    name = "same"
    description = "same"
    params_model = _Params

    async def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult("ok")


def test_registry_duplicate_reports_origins() -> None:
    registry = ToolRegistry()
    registry.register(_Tool(), ToolOrigin("pack", "first"))
    with pytest.raises(ValueError, match="first"):
        registry.register(_Tool(), ToolOrigin("plugin", "second"))


def test_os_sandbox_does_not_auto_allow_external_command(tmp_path: Path) -> None:
    from lumo.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from lumo.tools.base import PermissionTarget

    class ExternalTool(_Tool):
        name = "ExternalCommand"
        category = "command"

        def permission_targets(self, arguments):
            return [PermissionTarget("remote://action", "external")]

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(),
        sandbox_enabled=True,
    )
    assert checker.check(ExternalTool(), {}).effect == "ask"


def test_all_structured_local_targets_are_sandbox_checked(tmp_path: Path) -> None:
    from lumo.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from lumo.tools.base import PermissionTarget

    class MultiPathTool(_Tool):
        name = "MultiPath"
        category = "write"

        def permission_targets(self, arguments):
            return [
                PermissionTarget(str(tmp_path / "inside.txt"), "read"),
                PermissionTarget(str(Path.home() / "outside.txt"), "write"),
            ]

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(),
    )
    decision = checker.check(MultiPathTool(), {})
    assert decision.effect == "ask"
    assert "路径沙箱" in decision.reason


def test_capability_catalog_exposes_pack_and_mcp(tmp_path: Path) -> None:
    from lumo.runtime.catalog import CapabilityCatalog
    from lumo.runtime.models import CapabilityKind

    catalog = CapabilityCatalog(
        _config(MCPServerConfig(name="docs", command="docs-mcp")),
        str(tmp_path),
    ).list()
    assert any(item.id == "coding.files" and item.kind == CapabilityKind.PACK for item in catalog)
    assert any(item.id == "docs" and item.kind == CapabilityKind.MCP for item in catalog)


@pytest.mark.asyncio
async def test_plugin_setup_validates_config_and_tracks_origin(tmp_path: Path) -> None:
    from lumo.runtime.plugin_provider import PluginDescriptor, setup_plugin

    class Handle:
        closed = False

        async def aclose(self):
            self.closed = True

    class Plugin:
        async def setup(self, ctx, config):
            ctx.register_tool(_Tool())
            return Handle()

    descriptor = PluginDescriptor(
        id="fake",
        version="1",
        config_schema={
            "type": "object",
            "required": ["mode"],
            "properties": {"mode": {"type": "string"}},
            "additionalProperties": False,
        },
        plugin=Plugin(),
    )
    registry = ToolRegistry()
    handle = await setup_plugin(
        descriptor, registry, str(tmp_path), {"mode": "safe"}
    )
    assert registry.origin("same") == ToolOrigin("plugin", "fake")
    assert handle is not None
    await handle.aclose()

    with pytest.raises(ValueError, match="missing"):
        await setup_plugin(descriptor, ToolRegistry(), str(tmp_path), {})


def test_runtime_snapshot_redacts_secrets() -> None:
    env_ref = "$" + "{SAFE_REF}"
    spec = RuntimeSpec(
        scenario=CODING_SCENARIO,
        source="builtin",
        mcp_servers=(
            MCPServerConfig(
                name="secret",
                command="mcp",
                headers={"Authorization": "Bearer secret"},
                env={"TOKEN": "actual-secret", "REF": env_ref},
            ),
        ),
    )
    snapshot = spec.snapshot()
    rendered = json.dumps(snapshot)
    assert "actual-secret" not in rendered
    assert "Bearer secret" not in rendered
    assert env_ref in rendered

    from dataclasses import replace
    from lumo.runtime.models import CapabilityRef
    scenario = replace(
        CODING_SCENARIO,
        plugins=(
            CapabilityRef(
                id="plugin",
                config={"api_token": "plugin-secret"},
            ),
        ),
    )
    plugin_snapshot = RuntimeSpec(
        scenario=scenario,
        source="test",
        mcp_servers=(),
    ).snapshot()
    assert "plugin-secret" not in json.dumps(plugin_snapshot)


def test_session_records_runtime_identity(tmp_path: Path) -> None:
    manager = SessionManager(str(tmp_path))
    session = manager.create(
        scenario_id="office",
        runtime_fingerprint="abc",
        runtime_snapshot={"scenario": "office"},
    )
    session_id = session.session_id
    session.close()

    result = manager.resume(session_id)
    assert result is not None
    assert result.session.meta.scenario_id == "office"
    assert result.session.meta.runtime_fingerprint == "abc"
    snapshot = Path(result.session._sessions_dir) / result.session.meta.runtime_snapshot_file
    assert json.loads(snapshot.read_text()) == {"scenario": "office"}
    result.session.close()


def test_memory_namespace_isolated_by_scenario(tmp_path: Path) -> None:
    from lumo.memory import MemoryManager

    coding = MemoryManager(str(tmp_path), namespace="coding")
    office = MemoryManager(str(tmp_path), namespace="office")
    assert coding.project_mem_dir != office.project_mem_dir
    assert coding.project_mem_dir.parts[-2:] == ("scenarios", "coding")
    assert office.project_mem_dir.parts[-2:] == ("scenarios", "office")


@pytest.mark.asyncio
async def test_tui_starts_with_scenario_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import LumoApp

    monkeypatch.chdir(tmp_path)
    config = _config()
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id=None,
    )
    async with app.run_test():
        scenario_list = app.query_one("#scenario-list")
        assert scenario_list.option_count >= 3
        detail = app.query_one("#scenario-detail")
        assert "CAPABILITIES" in str(detail.render())
        assert app.query_one("#chat-area").display is False


@pytest.mark.asyncio
async def test_tui_scenario_launcher_updates_preview_on_highlight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import FoxMascot, LumoApp

    monkeypatch.chdir(tmp_path)
    config = _config()
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id=None,
    )
    async with app.run_test() as pilot:
        scenario_list = app.query_one("#scenario-list")
        office_index = next(
            index
            for index, record in enumerate(app._scenario_records)
            if record.id == "office"
        )
        scenario_list.highlighted = office_index
        await pilot.pause()
        detail = str(app.query_one("#scenario-detail").render())
        assert "Office Base" in detail
        assert "office assistant" in detail
        fox = app.query_one("#fox-mark", FoxMascot)
        assert fox.mood == "office"


@pytest.mark.asyncio
async def test_tui_fox_mascot_animates_expression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import FoxMascot, LumoApp, _render_fox_mark

    monkeypatch.chdir(tmp_path)
    config = _config()
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id=None,
    )
    async with app.run_test():
        fox = app.query_one("#fox-mark", FoxMascot)
        fox._animation_step = 0
        fox.update(_render_fox_mark(fox.mood, 0))
        resting_face = str(fox.render())
        fox._animation_step = 3
        fox._advance_expression()
        blinking_face = str(fox.render())
        assert resting_face != blinking_face


@pytest.mark.asyncio
async def test_tui_builds_selected_office_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import LumoApp

    monkeypatch.chdir(tmp_path)
    config = _config()
    config.providers[0].api_key = "test-key"
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id="office",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.agent is not None
        assert app.runtime_spec.scenario.id == "office"
        assert app.registry.get("Bash") is None
        assert app.registry.get("ReadFile") is None
        assert "office" in str(app.query_one("#scenario-label").render())
        if app.session is not None:
            app.session.close()


@pytest.mark.asyncio
async def test_shift_tab_cycles_permission_mode_with_visible_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import LumoApp
    from lumo.permissions import PermissionMode

    monkeypatch.chdir(tmp_path)
    config = _config()
    config.providers[0].api_key = "test-key"
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id="coding",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.agent is not None
        assert app.agent.permission_mode == PermissionMode.DEFAULT

        await pilot.press("shift+tab")
        await pilot.pause()

        assert app.agent.permission_mode == PermissionMode.ACCEPT_EDITS
        assert "mode:" in str(app.query_one("#mode-label").render())
        messages = [
            str(widget.render()) for widget in app.query(".system-message")
        ]
        assert any("Permission mode changed" in message for message in messages)
        if app.session is not None:
            app.session.close()


@pytest.mark.asyncio
async def test_scenario_switch_shows_comparison_before_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import LumoApp

    monkeypatch.chdir(tmp_path)
    config = _config()
    config.providers[0].api_key = "test-key"
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id="coding",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_scenario_switch("office")
        await pilot.pause()

        widget = app.query_one("#scenario-switch-inline")
        content = str(widget.query_one("#scenario-switch-content").render())
        assert "Switch Agent Runtime" in content
        assert "Coding" in content
        assert "Office" in content
        assert "new session" in content

        widget.action_cursor_down()
        widget.action_select()
        await pilot.pause()
        assert len(app.query("#scenario-switch-inline")) == 0
        assert app.runtime_spec.scenario.id == "coding"

        app._show_scenario_switch("office")
        await pilot.pause()
        confirm = app.query_one("#scenario-switch-inline")
        confirm.action_select()
        for _ in range(10):
            await pilot.pause()
            if app.return_value is not None:
                break
        assert app.return_value["scenario_id"] == "office"
        assert app.return_value["transition"]["from_id"] == "coding"
        assert app.return_value["transition"]["to_id"] == "office"
        if app.session is not None:
            app.session.close()


@pytest.mark.asyncio
async def test_model_assisted_scenario_creation_opens_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumo.app import LumoApp
    from lumo.runtime.models import (
        CapabilityKind,
        ScenarioExperience,
    )
    from lumo.scenario_designer.models import (
        ProposedCapability,
        ScenarioProposal,
    )
    from lumo.scenario_designer.service import ScenarioDesignerService

    async def fake_propose(self, description, client, protocol):
        return ScenarioProposal(
            scenario_id="contract-review",
            name="Contract Review",
            description="Review contracts",
            experience=ScenarioExperience(
                role="contract review assistant",
                mission="Find and explain contract risk",
            ),
            capabilities=(
                ProposedCapability(
                    id="core.session",
                    kind=CapabilityKind.PACK,
                    reason="Keep the review session",
                ),
            ),
            prompt_draft="# Workflow\n\nReview clauses and cite original text.",
        )

    monkeypatch.setattr(ScenarioDesignerService, "propose", fake_propose)
    monkeypatch.chdir(tmp_path)
    config = _config()
    config.providers[0].api_key = "test-key"
    app = LumoApp(
        providers=config.providers,
        app_config=config,
        scenario_id="coding",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._start_scenario_designer(
            "Create a read-only contract review assistant"
        )
        await pilot.pause()
        review = app.query_one("#scenario-draft-review")
        content = str(review.query_one("#scenario-draft-content").render())
        assert "Contract Review" in content
        assert "core.session" in content or "Session" in content
        review.action_cancel()
        await pilot.pause()
        assert len(app.query("#scenario-draft-review")) == 0
        if app.session is not None:
            app.session.close()
