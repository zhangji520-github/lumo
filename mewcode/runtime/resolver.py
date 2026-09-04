from __future__ import annotations

from mewcode.config import AppConfig
from mewcode.runtime.errors import CapabilityUnavailableError, RuntimeCompositionError
from mewcode.runtime.models import RuntimeDiagnostic, RuntimeSpec
from mewcode.runtime.cli_provider import CliToolCatalog
from mewcode.runtime.plugin_provider import PluginCatalog
from mewcode.runtime.scenario_loader import ScenarioLoader
from dataclasses import replace


KNOWN_PACKS = {
    "core.session",
    "core.interaction",
    "core.skills",
    "coding.files",
    "coding.shell",
    "coding.worktree",
    "coding.agents",
    "coding.teams",
}

PACK_DEPENDENCIES = {
    "coding.agents": ("core.interaction",),
    "coding.teams": ("coding.agents",),
    "coding.worktree": ("coding.files",),
}


def _resolve_packs(requested: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    resolved: set[str] = set()
    resolving: set[str] = set()

    def visit(pack_id: str) -> None:
        if pack_id in resolved:
            return
        if pack_id in resolving:
            raise CapabilityUnavailableError(
                f"capability pack dependency cycle includes '{pack_id}'"
            )
        resolving.add(pack_id)
        for dependency in PACK_DEPENDENCIES.get(pack_id, ()):
            visit(dependency)
        resolving.remove(pack_id)
        resolved.add(pack_id)
        ordered.append(pack_id)

    for pack_id in requested:
        visit(pack_id)
    return tuple(ordered)


class ScenarioResolver:
    def __init__(self, app_config: AppConfig, work_dir: str) -> None:
        self.app_config = app_config
        self.loader = ScenarioLoader(work_dir)

    def resolve(self, scenario_id: str) -> RuntimeSpec:
        record = self.loader.get(scenario_id)
        if record is None:
            available = ", ".join(item.id for item in self.loader.list())
            raise RuntimeCompositionError(
                f"unknown scenario '{scenario_id}'. Available: {available}"
            )
        if not record.healthy or record.definition is None:
            raise RuntimeCompositionError(
                f"scenario '{scenario_id}' is invalid: {record.error}"
            )
        scenario = record.definition
        unknown_packs = sorted(set(scenario.packs) - KNOWN_PACKS)
        if unknown_packs:
            raise CapabilityUnavailableError(
                f"scenario '{scenario_id}' references unknown packs: {', '.join(unknown_packs)}"
            )
        scenario = replace(scenario, packs=_resolve_packs(scenario.packs))

        configured = {item.name: item for item in self.app_config.mcp_servers}
        diagnostics: list[RuntimeDiagnostic] = []
        if scenario.use_all_configured_mcp:
            mcp_servers = tuple(self.app_config.mcp_servers)
        else:
            selected = []
            for ref in scenario.mcp:
                server = configured.get(ref.id)
                if server is None:
                    diagnostic = RuntimeDiagnostic(
                        severity="error" if ref.required else "warning",
                        code="mcp-definition-missing",
                        capability_id=ref.id,
                        message=f"MCP server '{ref.id}' is not configured",
                        hint="Add it to mcp_servers or remove it from the scenario.",
                    )
                    diagnostics.append(diagnostic)
                    if ref.required or scenario.startup.optional_capability_failure == "error":
                        raise CapabilityUnavailableError(diagnostic.message)
                    continue
                selected.append(server)
            mcp_servers = tuple(selected)

        cli_catalog, cli_errors = CliToolCatalog(
            str(self.loader.work_dir)
        ).load_with_errors()
        selected_cli = []
        for ref in scenario.cli:
            definition = cli_catalog.get(ref.id)
            if definition is None or not definition.available:
                reason = "is not defined" if definition is None else "executable is unavailable"
                if definition is None and cli_errors:
                    reason += "; invalid CLI definitions: " + "; ".join(
                        f"{path}: {error}" for path, error in cli_errors.items()
                    )
                diagnostic = RuntimeDiagnostic(
                    severity="error" if ref.required else "warning",
                    code="cli-unavailable",
                    capability_id=ref.id,
                    message=f"CLI capability '{ref.id}' {reason}",
                )
                diagnostics.append(diagnostic)
                if ref.required or scenario.startup.optional_capability_failure == "error":
                    raise CapabilityUnavailableError(diagnostic.message)
                continue
            selected_cli.append(definition)

        plugin_catalog = PluginCatalog().discover()
        requested_plugins = []
        for ref in scenario.plugins:
            descriptor = plugin_catalog.get(ref.id)
            if descriptor is None or not descriptor.available:
                reason = (
                    "is not installed"
                    if descriptor is None
                    else f"is unavailable: {descriptor.unavailable_reason}"
                )
                diagnostic = RuntimeDiagnostic(
                    severity="error" if ref.required else "warning",
                    code="plugin-unavailable",
                    capability_id=ref.id,
                    message=f"Python plugin '{ref.id}' {reason}",
                )
                diagnostics.append(diagnostic)
                if ref.required or scenario.startup.optional_capability_failure == "error":
                    raise CapabilityUnavailableError(diagnostic.message)
                continue
            requested_plugins.append((ref, descriptor))

        selected_plugins = []
        resolved_plugin_ids: set[str] = set()
        resolving_plugin_ids: set[str] = set()

        def add_plugin(plugin_id: str) -> bool:
            if plugin_id in resolved_plugin_ids:
                return True
            if plugin_id in resolving_plugin_ids:
                raise CapabilityUnavailableError(
                    f"Python plugin dependency cycle includes '{plugin_id}'"
                )
            descriptor = plugin_catalog.get(plugin_id)
            if descriptor is None or not descriptor.available:
                return False
            resolving_plugin_ids.add(plugin_id)
            for dependency in descriptor.requires:
                if not add_plugin(dependency):
                    resolving_plugin_ids.remove(plugin_id)
                    return False
            resolving_plugin_ids.remove(plugin_id)
            resolved_plugin_ids.add(plugin_id)
            selected_plugins.append(descriptor)
            return True

        for ref, descriptor in requested_plugins:
            if add_plugin(descriptor.id):
                continue
            diagnostic = RuntimeDiagnostic(
                severity="error" if ref.required else "warning",
                code="plugin-dependency-unavailable",
                capability_id=ref.id,
                message=f"Python plugin '{ref.id}' has an unavailable dependency",
            )
            diagnostics.append(diagnostic)
            if ref.required or scenario.startup.optional_capability_failure == "error":
                raise CapabilityUnavailableError(diagnostic.message)

        provisional = RuntimeSpec(
            scenario=scenario,
            source=record.source,
            mcp_servers=mcp_servers,
            cli_definitions=tuple(selected_cli),
            plugin_descriptors=tuple(selected_plugins),
            diagnostics=tuple(diagnostics),
        )
        fingerprint = RuntimeSpec.calculate_fingerprint(provisional.snapshot())
        return RuntimeSpec(
            scenario=scenario,
            source=record.source,
            mcp_servers=mcp_servers,
            cli_definitions=tuple(selected_cli),
            plugin_descriptors=tuple(selected_plugins),
            diagnostics=tuple(diagnostics),
            fingerprint=fingerprint,
        )
