from __future__ import annotations

from dataclasses import dataclass, field

from mewcode.mcp import MCPManager, ConnectResult
from mewcode.runtime.cli_provider import register_cli_tools
from mewcode.runtime.lifecycle import RuntimeLifecycle
from mewcode.runtime.models import RuntimeDiagnostic, RuntimeSpec
from mewcode.runtime.plugin_provider import setup_plugin
from mewcode.tools import ToolRegistry


@dataclass
class ActiveCapabilities:
    spec: RuntimeSpec
    registry: ToolRegistry
    lifecycle: RuntimeLifecycle
    mcp_manager: MCPManager | None = None
    mcp_result: ConnectResult | None = None
    diagnostics: list[RuntimeDiagnostic] = field(default_factory=list)

    async def aclose(self) -> None:
        await self.lifecycle.aclose()


async def activate_external_capabilities(
    spec: RuntimeSpec,
    registry: ToolRegistry,
    work_dir: str,
) -> ActiveCapabilities:
    lifecycle = RuntimeLifecycle()
    active = ActiveCapabilities(
        spec=spec,
        registry=registry,
        lifecycle=lifecycle,
        diagnostics=list(spec.diagnostics),
    )
    try:
        register_cli_tools(registry, list(spec.cli_definitions), work_dir)

        plugin_refs = {item.id: item for item in spec.scenario.plugins}
        for descriptor in spec.plugin_descriptors:
            ref = plugin_refs.get(descriptor.id)
            if ref is None:
                from mewcode.runtime.models import CapabilityRef
                ref = CapabilityRef(id=descriptor.id)
            try:
                plugin_registry = ToolRegistry()
                handle = await setup_plugin(
                    descriptor, plugin_registry, work_dir, ref.config
                )
                try:
                    for tool, origin in plugin_registry.list_registered():
                        registry.register(tool, origin)
                except Exception:
                    if handle is not None:
                        await handle.aclose()
                    raise
                if handle is not None:
                    lifecycle.add(handle.aclose)
            except Exception as exc:
                if (
                    ref.required
                    or spec.scenario.startup.optional_capability_failure == "error"
                ):
                    raise
                active.diagnostics.append(RuntimeDiagnostic(
                    severity="warning",
                    code="plugin-setup-failed",
                    capability_id=ref.id,
                    message=str(exc),
                ))

        if spec.mcp_servers:
            manager = MCPManager()
            manager.load_configs(list(spec.mcp_servers))
            lifecycle.add(manager.shutdown)
            selections = {item.id: item for item in spec.scenario.mcp}
            result = await manager.register_all_tools(
                registry, selections if not spec.scenario.use_all_configured_mcp else None
            )
            active.mcp_manager = manager
            active.mcp_result = result
            refs = {item.id: item for item in spec.scenario.mcp}
            for message in result.errors:
                server_id = next((name for name in refs if f"'{name}'" in message), None)
                required = refs[server_id].required if server_id in refs else False
                if (
                    required
                    or spec.scenario.startup.optional_capability_failure == "error"
                ):
                    raise RuntimeError(message)
                active.diagnostics.append(RuntimeDiagnostic(
                    severity="warning",
                    code="mcp-connect-failed",
                    capability_id=server_id,
                    message=message,
                ))
        return active
    except Exception:
        await lifecycle.aclose()
        raise
