from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
import re
import tempfile
from typing import Any, Literal

import yaml

from mewcode.runtime.builtins import BUILTIN_SCENARIOS
from mewcode.runtime.errors import ScenarioValidationError
from mewcode.runtime.models import (
    CapabilityRef,
    ScenarioDefinition,
    ScenarioFeatures,
    ScenarioExperience,
    ScenarioRecord,
    ScenarioSecurity,
    ScenarioStartup,
    SkillSelection,
)

_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VALID_RISKS = {"read", "write", "command"}


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ScenarioValidationError(f"{field_name} must be a list of strings")
    return tuple(value)


def _capability_ref(value: Any, field_name: str) -> CapabilityRef:
    if isinstance(value, str):
        return CapabilityRef(id=value)
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        raise ScenarioValidationError(f"{field_name} entries need a string id")
    tools = value.get("tools") or {}
    if not isinstance(tools, dict):
        raise ScenarioValidationError(f"{field_name}.{value['id']}.tools must be a mapping")
    risk_overrides = value.get("risk_overrides") or {}
    if not isinstance(risk_overrides, dict):
        raise ScenarioValidationError(
            f"{field_name}.{value['id']}.risk_overrides must be a mapping"
        )
    for pattern, risk in risk_overrides.items():
        if not isinstance(pattern, str) or risk not in _VALID_RISKS:
            raise ScenarioValidationError(
                f"{field_name}.{value['id']}.risk_overrides contains an invalid risk"
            )
    config = value.get("config") or {}
    if not isinstance(config, dict):
        raise ScenarioValidationError(f"{field_name}.{value['id']}.config must be a mapping")
    required = value.get("required", True)
    if not isinstance(required, bool):
        raise ScenarioValidationError(f"{field_name}.{value['id']}.required must be boolean")
    return CapabilityRef(
        id=value["id"],
        required=required,
        config=config,
        include=_strings(tools.get("include", ["*"]), f"{field_name}.tools.include"),
        exclude=_strings(tools.get("exclude", []), f"{field_name}.tools.exclude"),
        risk_overrides=dict(risk_overrides),
    )


def parse_scenario(raw: Any, *, expected_id: str | None = None) -> ScenarioDefinition:
    if not isinstance(raw, dict):
        raise ScenarioValidationError("scenario must be a YAML mapping")
    version = raw.get("schema_version", 1)
    if version not in (1, 2):
        raise ScenarioValidationError(f"unsupported scenario schema_version: {version}")
    scenario_id = raw.get("id")
    if not isinstance(scenario_id, str) or not _SCENARIO_ID_RE.fullmatch(scenario_id):
        raise ScenarioValidationError("scenario id must match [a-z0-9][a-z0-9-]*")
    if expected_id is not None and scenario_id != expected_id:
        raise ScenarioValidationError(
            f"scenario id '{scenario_id}' does not match filename '{expected_id}'"
        )
    name = raw.get("name", scenario_id)
    if not isinstance(name, str) or not name.strip():
        raise ScenarioValidationError("scenario name must be a non-empty string")
    capabilities = raw.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise ScenarioValidationError("capabilities must be a mapping")

    raw_mcp = capabilities.get("mcp", [])
    use_all_mcp = raw_mcp == "configured"
    if use_all_mcp:
        mcp: tuple[CapabilityRef, ...] = ()
    else:
        if not isinstance(raw_mcp, list):
            raise ScenarioValidationError("capabilities.mcp must be a list")
        mcp = tuple(_capability_ref(item, "capabilities.mcp") for item in raw_mcp)

    def refs(key: str) -> tuple[CapabilityRef, ...]:
        values = capabilities.get(key, [])
        if not isinstance(values, list):
            raise ScenarioValidationError(f"capabilities.{key} must be a list")
        return tuple(_capability_ref(item, f"capabilities.{key}") for item in values)

    raw_skills = capabilities.get("skills") or {}
    if not isinstance(raw_skills, dict):
        raise ScenarioValidationError("capabilities.skills must be a mapping")
    raw_features = raw.get("features") or {}
    if not isinstance(raw_features, dict):
        raise ScenarioValidationError("features must be a mapping")
    feature_defaults = ScenarioFeatures()
    feature_values: dict[str, bool] = {}
    for key in ("memory", "worktree", "subagents", "teams", "skill_install"):
        value = raw_features.get(key, getattr(feature_defaults, key))
        if not isinstance(value, bool):
            raise ScenarioValidationError(f"features.{key} must be boolean")
        feature_values[key] = value

    raw_security = raw.get("security") or {}
    if not isinstance(raw_security, dict):
        raise ScenarioValidationError("security must be a mapping")
    permission_mode = raw_security.get("permission_mode", "inherit")
    if permission_mode == "bypassPermissions":
        raise ScenarioValidationError("a scenario cannot enable bypassPermissions")
    if permission_mode not in {"inherit", "default", "acceptEdits", "plan"}:
        raise ScenarioValidationError(f"invalid scenario permission mode: {permission_mode}")
    sandbox = raw_security.get("sandbox", "inherit")
    if sandbox not in {"inherit", "enabled", "disabled"}:
        raise ScenarioValidationError(f"invalid scenario sandbox value: {sandbox}")

    raw_startup = raw.get("startup") or {}
    if not isinstance(raw_startup, dict):
        raise ScenarioValidationError("startup must be a mapping")
    optional_failure = raw_startup.get("optional_capability_failure", "warn")
    if optional_failure not in {"warn", "error"}:
        raise ScenarioValidationError(
            "startup.optional_capability_failure must be warn or error"
        )

    provider = raw.get("provider", "inherit")
    if not isinstance(provider, str):
        raise ScenarioValidationError("provider must be a string")
    for field_name in ("description", "persona"):
        if not isinstance(raw.get(field_name, ""), str):
            raise ScenarioValidationError(f"{field_name} must be a string")

    raw_experience = raw.get("experience") or {}
    if not isinstance(raw_experience, dict):
        raise ScenarioValidationError("experience must be a mapping")
    for field_name in ("role", "mission", "audience"):
        if not isinstance(raw_experience.get(field_name, ""), str):
            raise ScenarioValidationError(
                f"experience.{field_name} must be a string"
            )
    prompt_file = raw.get("prompt_file")
    if prompt_file is not None and not isinstance(prompt_file, str):
        raise ScenarioValidationError("prompt_file must be a string")

    return ScenarioDefinition(
        schema_version=version,
        id=scenario_id,
        name=name.strip(),
        description=raw.get("description", ""),
        persona=raw.get("persona", ""),
        experience=ScenarioExperience(
            role=raw_experience.get("role", ""),
            mission=raw_experience.get("mission", ""),
            audience=raw_experience.get("audience", ""),
            default_tasks=_strings(
                raw_experience.get("default_tasks", []),
                "experience.default_tasks",
            ),
            output_contract=_strings(
                raw_experience.get("output_contract", []),
                "experience.output_contract",
            ),
            prompt_file=prompt_file,
        ),
        provider=provider,
        packs=_strings(capabilities.get("packs", []), "capabilities.packs"),
        mcp=mcp,
        cli=refs("cli"),
        plugins=refs("plugins"),
        skills=SkillSelection(
            include=_strings(raw_skills.get("include", ["*"]), "skills.include"),
            exclude=_strings(raw_skills.get("exclude", []), "skills.exclude"),
        ),
        features=ScenarioFeatures(**feature_values),
        security=ScenarioSecurity(
            permission_mode=permission_mode,
            sandbox=sandbox,
            confirm_tools=_strings(raw_security.get("confirm_tools", []), "security.confirm_tools"),
            deny_tools=_strings(raw_security.get("deny_tools", []), "security.deny_tools"),
        ),
        startup=ScenarioStartup(optional_capability_failure=optional_failure),
        use_all_configured_mcp=use_all_mcp,
    )


class ScenarioLoader:
    def __init__(self, work_dir: str) -> None:
        self.work_dir = Path(work_dir)
        self.user_dir = Path.home() / ".mewcode" / "scenarios"
        self.project_dir = self.work_dir / ".mewcode" / "scenarios"

    @staticmethod
    def _scan(path: Path, source: Literal["user", "project"]) -> list[ScenarioRecord]:
        records: list[ScenarioRecord] = []
        if not path.is_dir():
            return records

        file_entries = {item.stem: item for item in path.glob("*.yaml")}
        package_entries = {
            item.name: item / "scenario.yaml"
            for item in path.iterdir()
            if item.is_dir() and (item / "scenario.yaml").is_file()
        }
        for scenario_id in sorted(set(file_entries) | set(package_entries)):
            file_path = file_entries.get(scenario_id)
            package_path = package_entries.get(scenario_id)
            if file_path is not None and package_path is not None:
                records.append(ScenarioRecord(
                    id=scenario_id,
                    source=source,
                    path=package_path,
                    error=(
                        f"scenario '{scenario_id}' exists as both "
                        f"{file_path.name} and {scenario_id}/scenario.yaml"
                    ),
                ))
                continue
            manifest_path = package_path or file_path
            assert manifest_path is not None
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                definition = parse_scenario(raw, expected_id=scenario_id)
                if package_path is not None:
                    prompt_file = definition.experience.prompt_file
                    if prompt_file:
                        relative = Path(prompt_file)
                        if relative.is_absolute() or ".." in relative.parts:
                            raise ScenarioValidationError(
                                "prompt_file must stay inside the scenario directory"
                            )
                        package_dir = package_path.parent.resolve()
                        prompt_path = (package_path.parent / relative).resolve()
                        try:
                            prompt_path.relative_to(package_dir)
                        except ValueError as exc:
                            raise ScenarioValidationError(
                                "prompt_file escapes the scenario directory"
                            ) from exc
                        if not prompt_path.is_file():
                            raise ScenarioValidationError(
                                f"prompt_file not found: {prompt_file}"
                            )
                        if prompt_path.stat().st_size > 20 * 1024:
                            raise ScenarioValidationError(
                                "prompt_file exceeds the 20 KiB limit"
                            )
                        definition = replace(
                            definition,
                            experience=replace(
                                definition.experience,
                                prompt_text=prompt_path.read_text(encoding="utf-8"),
                            ),
                        )
                records.append(ScenarioRecord(
                    id=scenario_id,
                    source=source,
                    path=manifest_path,
                    definition=definition,
                ))
            except (OSError, yaml.YAMLError, ScenarioValidationError) as exc:
                records.append(ScenarioRecord(
                    id=scenario_id,
                    source=source,
                    path=manifest_path,
                    error=str(exc),
                ))
        return records

    def list(self) -> list[ScenarioRecord]:
        selected: dict[str, ScenarioRecord] = {
            scenario_id: ScenarioRecord(
                id=scenario_id,
                source="builtin",
                path=None,
                definition=definition,
            )
            for scenario_id, definition in BUILTIN_SCENARIOS.items()
        }
        for record in self._scan(self.user_dir, "user"):
            selected[record.id] = record
        for record in self._scan(self.project_dir, "project"):
            selected[record.id] = record
        return sorted(selected.values(), key=lambda item: (item.definition.name if item.definition else item.id).lower())

    def get(self, scenario_id: str) -> ScenarioRecord | None:
        return next((record for record in self.list() if record.id == scenario_id), None)

    def save(
        self,
        definition: ScenarioDefinition,
        *,
        scope: Literal["user", "project"] = "user",
        overwrite: bool = False,
    ) -> Path:
        data = scenario_to_dict(definition)
        # Validate again at the write boundary so callers cannot bypass the
        # filename/id containment rule by constructing a dataclass directly.
        parse_scenario(data, expected_id=definition.id)
        target_dir = self.user_dir if scope == "user" else self.project_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{definition.id}.yaml"
        if target.exists() and not overwrite:
            raise FileExistsError(f"scenario already exists: {definition.id}")
        fd, temp_name = tempfile.mkstemp(prefix=f".{definition.id}.", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return target

    def delete(self, scenario_id: str) -> bool:
        record = self.get(scenario_id)
        if record is None or record.source == "builtin" or record.path is None:
            return False
        if (
            record.path.name == "scenario.yaml"
            and record.path.parent.name == scenario_id
        ):
            import shutil
            root = self.user_dir if record.source == "user" else self.project_dir
            resolved_root = root.resolve()
            resolved_package = record.path.parent.resolve()
            try:
                resolved_package.relative_to(resolved_root)
            except ValueError as exc:
                raise ScenarioValidationError(
                    "scenario package path escapes its configured root"
                ) from exc
            if resolved_package == resolved_root:
                raise ScenarioValidationError(
                    "refusing to delete the scenario root"
                )
            shutil.rmtree(resolved_package)
        else:
            record.path.unlink()
        return True


def scenario_to_dict(definition: ScenarioDefinition) -> dict[str, Any]:
    def ref(item: CapabilityRef) -> dict[str, Any]:
        result: dict[str, Any] = {"id": item.id}
        if not item.required:
            result["required"] = False
        if item.config:
            result["config"] = item.config
        if item.include != ("*",) or item.exclude:
            result["tools"] = {"include": list(item.include), "exclude": list(item.exclude)}
        if item.risk_overrides:
            result["risk_overrides"] = item.risk_overrides
        return result

    mcp: Any = "configured" if definition.use_all_configured_mcp else [ref(item) for item in definition.mcp]
    return {
        "schema_version": definition.schema_version,
        "id": definition.id,
        "name": definition.name,
        "description": definition.description,
        "persona": definition.persona,
        "prompt_file": definition.experience.prompt_file,
        "experience": {
            "role": definition.experience.role,
            "mission": definition.experience.mission,
            "audience": definition.experience.audience,
            "default_tasks": list(definition.experience.default_tasks),
            "output_contract": list(definition.experience.output_contract),
        },
        "provider": definition.provider,
        "capabilities": {
            "packs": list(definition.packs),
            "mcp": mcp,
            "cli": [ref(item) for item in definition.cli],
            "plugins": [ref(item) for item in definition.plugins],
            "skills": {
                "include": list(definition.skills.include),
                "exclude": list(definition.skills.exclude),
            },
        },
        "features": {
            "memory": definition.features.memory,
            "worktree": definition.features.worktree,
            "subagents": definition.features.subagents,
            "teams": definition.features.teams,
            "skill_install": definition.features.skill_install,
        },
        "security": {
            "permission_mode": definition.security.permission_mode,
            "sandbox": definition.security.sandbox,
            "confirm_tools": list(definition.security.confirm_tools),
            "deny_tools": list(definition.security.deny_tools),
        },
        "startup": {
            "optional_capability_failure": definition.startup.optional_capability_failure,
        },
    }
