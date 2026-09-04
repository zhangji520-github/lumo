from __future__ import annotations

from mewcode.config import AppConfig
from mewcode.runtime.cli_provider import CliToolCatalog
from mewcode.runtime.models import CapabilityDescriptor, CapabilityKind
from mewcode.runtime.plugin_provider import PluginCatalog
from mewcode.runtime.resolver import KNOWN_PACKS
from mewcode.skills.loader import SkillLoader

_PACK_DESCRIPTIONS = {
    "core.session": "Session, memory, and file history",
    "core.interaction": "Tool search, questions, and plan interaction",
    "core.skills": "Skill catalog and skill execution",
    "coding.files": "Read, write, edit, glob, and grep files",
    "coding.shell": "Run commands through Bash and the OS sandbox",
    "coding.worktree": "Create and manage Git worktrees",
    "coding.agents": "Delegate work to sub-agents",
    "coding.teams": "Coordinate teams and shared tasks",
}


class CapabilityCatalog:
    def __init__(self, app_config: AppConfig, work_dir: str) -> None:
        self.app_config = app_config
        self.work_dir = work_dir

    def list(self) -> list[CapabilityDescriptor]:
        items: list[CapabilityDescriptor] = []
        for pack_id in sorted(KNOWN_PACKS):
            items.append(CapabilityDescriptor(
                id=pack_id,
                kind=CapabilityKind.PACK,
                title=pack_id,
                description=_PACK_DESCRIPTIONS.get(pack_id, ""),
                source="builtin",
                risk="varies",
            ))
        for server in self.app_config.mcp_servers:
            transport = "stdio" if server.command else "http"
            items.append(CapabilityDescriptor(
                id=server.name,
                kind=CapabilityKind.MCP,
                title=server.name,
                description=f"MCP server ({transport})",
                source="config",
                risk="command",
            ))
        cli_definitions, cli_errors = CliToolCatalog(
            self.work_dir
        ).load_with_errors()
        for path, error in cli_errors.items():
            items.append(CapabilityDescriptor(
                id=f"broken:{path}",
                kind=CapabilityKind.CLI,
                title="Broken CLI definition",
                description=error,
                source=path,
                risk="command",
                available=False,
                unavailable_reason=error,
            ))
        for definition in cli_definitions.values():
            items.append(CapabilityDescriptor(
                id=definition.id,
                kind=CapabilityKind.CLI,
                title=definition.name,
                description=definition.description,
                source=definition.source,
                risk=definition.category,
                available=definition.available,
                unavailable_reason=(
                    "" if definition.available else "executable not found"
                ),
            ))
        for descriptor in PluginCatalog().discover().values():
            items.append(CapabilityDescriptor(
                id=descriptor.id,
                kind=CapabilityKind.PLUGIN,
                title=descriptor.id,
                description=(
                    descriptor.description
                    or f"Installed Python plugin {descriptor.version}"
                ),
                source="entry-point",
                risk="trusted-code",
                available=descriptor.available,
                unavailable_reason=descriptor.unavailable_reason,
            ))
        loader = SkillLoader(self.work_dir)
        loader.load_all()
        for name, description in loader.get_catalog():
            items.append(CapabilityDescriptor(
                id=name,
                kind=CapabilityKind.SKILL,
                title=name,
                description=description,
                source="skill-loader",
                risk="prompt",
            ))
        return sorted(items, key=lambda item: (item.kind.value, item.title.lower()))
