from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mewcode.client import LLMClient, create_client
from mewcode.config import AppConfig, ProviderConfig
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.runtime.capabilities import ActiveCapabilities, activate_external_capabilities
from mewcode.runtime.models import RuntimeSpec
from mewcode.runtime.packs import create_registry_for_spec
from mewcode.runtime.resolver import ScenarioResolver
from mewcode.skills.loader import SkillLoader
from mewcode.tools import ToolRegistry


@dataclass
class RuntimeFoundation:
    spec: RuntimeSpec
    provider: ProviderConfig
    client: LLMClient
    registry: ToolRegistry
    permission_checker: PermissionChecker
    skill_loader: SkillLoader
    capabilities: ActiveCapabilities

    async def aclose(self) -> None:
        await self.capabilities.aclose()


class RuntimeBuilder:
    """Build the shared, surface-independent part of a MewCode runtime."""

    def __init__(
        self,
        app_config: AppConfig,
        work_dir: str,
        *,
        scenario_id: str | None = None,
        permission_mode: PermissionMode | None = None,
    ) -> None:
        self.app_config = app_config
        self.work_dir = work_dir
        self.scenario_id = scenario_id or app_config.default_scenario
        self.permission_mode_override = permission_mode

    def resolve(self) -> RuntimeSpec:
        return ScenarioResolver(self.app_config, self.work_dir).resolve(self.scenario_id)

    def select_provider(
        self,
        spec: RuntimeSpec,
        provider: ProviderConfig | None = None,
    ) -> ProviderConfig:
        if provider is not None:
            return provider
        if spec.scenario.provider != "inherit":
            selected = next(
                (item for item in self.app_config.providers
                 if item.name == spec.scenario.provider),
                None,
            )
            if selected is None:
                raise ValueError(
                    f"scenario provider '{spec.scenario.provider}' is not configured"
                )
            return selected
        return self.app_config.providers[0]

    async def build(
        self,
        *,
        provider: ProviderConfig | None = None,
        file_history: Any = None,
    ) -> RuntimeFoundation:
        spec = self.resolve()
        selected_provider = self.select_provider(spec, provider)
        client = create_client(selected_provider)
        registry = create_registry_for_spec(spec, file_history=file_history)

        mode = self.permission_mode_override
        if mode is None:
            scenario_mode = spec.scenario.security.permission_mode
            mode = PermissionMode(
                self.app_config.permission_mode
                if scenario_mode == "inherit"
                else scenario_mode
            )
        sandbox_enabled = (
            self.app_config.sandbox.enabled
            and self.app_config.sandbox.auto_allow
            and spec.scenario.security.sandbox != "disabled"
        )
        home = Path.home()
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(self.work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".mewcode" / "permissions.yaml",
                project_rules_path=Path(self.work_dir) / ".mewcode" / "permissions.yaml",
                local_rules_path=Path(self.work_dir) / ".mewcode" / "permissions.local.yaml",
            ),
            mode=mode,
            sandbox_enabled=sandbox_enabled,
            scenario_confirm_tools=spec.scenario.security.confirm_tools,
            scenario_deny_tools=spec.scenario.security.deny_tools,
        )

        self._attach_os_sandbox(registry, spec)
        skill_loader = SkillLoader(self.work_dir)
        skill_loader.set_selection(
            spec.scenario.skills.include,
            spec.scenario.skills.exclude,
        )
        skill_loader.load_all()
        capabilities = await activate_external_capabilities(
            spec, registry, self.work_dir
        )
        return RuntimeFoundation(
            spec=spec,
            provider=selected_provider,
            client=client,
            registry=registry,
            permission_checker=checker,
            skill_loader=skill_loader,
            capabilities=capabilities,
        )

    def _attach_os_sandbox(
        self,
        registry: ToolRegistry,
        spec: RuntimeSpec,
    ) -> None:
        if (
            not self.app_config.sandbox.enabled
            or spec.scenario.security.sandbox == "disabled"
        ):
            return
        bash_tool = registry.get("Bash")
        if bash_tool is None:
            return
        from mewcode.sandbox import SandboxConfig, create_sandbox
        os_sandbox = create_sandbox()
        if os_sandbox is None or not os_sandbox.available():
            return
        bash_tool.sandbox = os_sandbox
        bash_tool.sandbox_config = SandboxConfig(
            allow_write=[self.work_dir, "/tmp"],
            deny_write=[
                f"{self.work_dir}/.mewcode/config.yaml",
                f"{self.work_dir}/.mewcode/permissions.local.yaml",
            ],
            network_enabled=self.app_config.sandbox.network_enabled,
        )
