from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal
from enum import Enum

from mewcode.config import MCPServerConfig

ToolRisk = Literal["read", "write", "command"]
DiagnosticSeverity = Literal["info", "warning", "error"]


class CapabilityKind(str, Enum):
    PACK = "pack"
    MCP = "mcp"
    CLI = "cli"
    SKILL = "skill"
    PLUGIN = "plugin"


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    kind: CapabilityKind
    title: str
    description: str
    source: str
    risk: str
    available: bool = True
    unavailable_reason: str = ""


@dataclass(frozen=True)
class RuntimeDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    capability_id: str | None = None
    hint: str = ""


@dataclass(frozen=True)
class CapabilityRef:
    id: str
    required: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()
    risk_overrides: dict[str, ToolRisk] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillSelection:
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioFeatures:
    memory: bool = True
    worktree: bool = True
    subagents: bool = True
    teams: bool = True
    skill_install: bool = True


@dataclass(frozen=True)
class ScenarioSecurity:
    permission_mode: str = "inherit"
    sandbox: str = "inherit"
    confirm_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioStartup:
    optional_capability_failure: Literal["warn", "error"] = "warn"


@dataclass(frozen=True)
class ScenarioExperience:
    role: str = ""
    mission: str = ""
    audience: str = ""
    default_tasks: tuple[str, ...] = ()
    output_contract: tuple[str, ...] = ()
    prompt_file: str | None = None
    prompt_text: str = ""


@dataclass(frozen=True)
class ScenarioDefinition:
    schema_version: int
    id: str
    name: str
    description: str = ""
    persona: str = ""
    experience: ScenarioExperience = field(default_factory=ScenarioExperience)
    provider: str = "inherit"
    packs: tuple[str, ...] = ()
    mcp: tuple[CapabilityRef, ...] = ()
    cli: tuple[CapabilityRef, ...] = ()
    plugins: tuple[CapabilityRef, ...] = ()
    skills: SkillSelection = field(default_factory=SkillSelection)
    features: ScenarioFeatures = field(default_factory=ScenarioFeatures)
    security: ScenarioSecurity = field(default_factory=ScenarioSecurity)
    startup: ScenarioStartup = field(default_factory=ScenarioStartup)
    use_all_configured_mcp: bool = False


@dataclass(frozen=True)
class ScenarioRecord:
    id: str
    source: Literal["builtin", "user", "project"]
    path: Path | None
    definition: ScenarioDefinition | None = None
    error: str = ""

    @property
    def healthy(self) -> bool:
        return self.definition is not None and not self.error


@dataclass(frozen=True)
class RuntimeSpec:
    scenario: ScenarioDefinition
    source: str
    mcp_servers: tuple[MCPServerConfig, ...]
    cli_definitions: tuple[Any, ...] = ()
    plugin_descriptors: tuple[Any, ...] = ()
    diagnostics: tuple[RuntimeDiagnostic, ...] = ()
    fingerprint: str = ""

    def snapshot(self) -> dict[str, Any]:
        """Return a stable, secret-free representation for session identity."""
        scenario_data = _redact_secrets(asdict(self.scenario))
        mcp = []
        for cfg in self.mcp_servers:
            mcp.append({
                "name": cfg.name,
                "command": cfg.command,
                "args": list(cfg.args),
                "url": cfg.url,
                "headers": {key: "<redacted>" for key in cfg.headers},
                "env": {key: value if value.startswith("${") else "<redacted>"
                        for key, value in cfg.env.items()},
            })
        return {
            "schema_version": 1,
            "scenario": scenario_data,
            "source": self.source,
            "mcp_servers": mcp,
            "cli_ids": [getattr(item, "id", "") for item in self.cli_definitions],
            "plugins": [
                {
                    "id": getattr(item, "id", ""),
                    "version": getattr(item, "version", "unknown"),
                }
                for item in self.plugin_descriptors
            ],
        }

    @staticmethod
    def calculate_fingerprint(snapshot: dict[str, Any]) -> str:
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


def _redact_secrets(value: Any, key: str = "") -> Any:
    sensitive = any(
        marker in key.lower()
        for marker in ("token", "secret", "password", "api_key", "authorization")
    )
    if sensitive and isinstance(value, str):
        return value if value.startswith("$" + "{") else "<redacted>"
    if isinstance(value, dict):
        return {
            item_key: _redact_secrets(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item, key) for item in value]
    return value
