from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from pydantic import BaseModel, create_model
import yaml

from lumo.tools import ToolOrigin, ToolRegistry
from lumo.tools.base import PermissionTarget, Tool, ToolCategory, ToolResult

_PLACEHOLDER_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _python_type(schema: dict[str, Any]) -> type:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(schema.get("type", "string"), str)


def _params_model(tool_id: str, schema: dict[str, Any]) -> type[BaseModel]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for name, definition in properties.items():
        py_type = _python_type(definition)
        fields[name] = (py_type, ...) if name in required else (py_type | None, None)
    model_name = "".join(part.title() for part in re.split(r"[^A-Za-z0-9]+", tool_id)) + "Params"
    return create_model(model_name, **fields)


@dataclass(frozen=True)
class CliPermissionTarget:
    parameter: str
    access: str


@dataclass(frozen=True)
class CliToolDefinition:
    id: str
    name: str
    description: str
    executable: str
    category: ToolCategory
    parameters: dict[str, Any]
    argv: tuple[str, ...]
    timeout_seconds: float = 60.0
    working_directory: str = "workspace"
    environment_allow: tuple[str, ...] = ("PATH",)
    permission_targets: tuple[CliPermissionTarget, ...] = ()
    source: str = ""

    @property
    def available(self) -> bool:
        if os.path.isabs(self.executable):
            return Path(self.executable).is_file()
        return shutil.which(self.executable) is not None


class CliTool(Tool):
    is_concurrency_safe = False
    should_defer = True

    def __init__(self, definition: CliToolDefinition, work_dir: str) -> None:
        self.definition = definition
        self.work_dir = Path(work_dir)
        self.name = definition.name
        self.description = definition.description
        self.category = definition.category
        self.params_model = _params_model(definition.id, definition.parameters)

    def permission_targets(self, arguments: dict[str, Any]) -> list[PermissionTarget]:
        return [
            PermissionTarget(
                resource=str(arguments.get(item.parameter, "")),
                access=item.access,  # type: ignore[arg-type]
            )
            for item in self.definition.permission_targets
            if arguments.get(item.parameter) is not None
        ]

    def _argv(self, values: dict[str, Any]) -> list[str]:
        argv: list[str] = []
        for template in self.definition.argv:
            match = _PLACEHOLDER_RE.fullmatch(template)
            if match:
                name = match.group(1)
                if name not in values or values[name] is None:
                    raise ValueError(f"missing CLI argument: {name}")
                value = values[name]
                if isinstance(value, (dict, list)):
                    argv.append(json.dumps(value, ensure_ascii=False))
                else:
                    argv.append(str(value))
                continue
            if "{" in template or "}" in template:
                raise ValueError(
                    "CLI placeholders must occupy a complete argv item"
                )
            argv.append(template)
        return argv

    async def execute(self, params: BaseModel) -> ToolResult:
        values = params.model_dump(exclude_none=True)
        try:
            argv = self._argv(values)
        except ValueError as exc:
            return ToolResult(output=f"CLI definition error: {exc}", is_error=True)
        env = {
            name: os.environ[name]
            for name in self.definition.environment_allow
            if name in os.environ
        }
        if self.definition.working_directory == "workspace":
            cwd = self.work_dir
        elif self.definition.working_directory == "temp":
            import tempfile
            cwd = Path(tempfile.gettempdir())
        else:
            return ToolResult(
                output="CLI definition error: unsupported working_directory",
                is_error=True,
            )
        try:
            process = await asyncio.create_subprocess_exec(
                self.definition.executable,
                *argv,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.definition.timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    output=f"CLI tool timed out after {self.definition.timeout_seconds:g}s",
                    is_error=True,
                )
        except OSError as exc:
            return ToolResult(output=f"CLI tool failed to start: {exc}", is_error=True)
        output = stdout.decode(errors="replace")
        error = stderr.decode(errors="replace")
        if error:
            output = f"{output}\n{error}".strip()
        return ToolResult(output=output or "(no output)", is_error=process.returncode != 0)


class CliToolCatalog:
    def __init__(self, work_dir: str) -> None:
        self.work_dir = Path(work_dir)

    def _directories(self) -> tuple[Path, Path]:
        return (
            Path.home() / ".lumo" / "cli-tools",
            self.work_dir / ".lumo" / "cli-tools",
        )

    def load(self) -> dict[str, CliToolDefinition]:
        definitions, errors = self.load_with_errors()
        if errors:
            first_path, first_error = next(iter(errors.items()))
            raise ValueError(f"{first_path}: {first_error}")
        return definitions

    def load_with_errors(
        self,
    ) -> tuple[dict[str, CliToolDefinition], dict[str, str]]:
        definitions: dict[str, CliToolDefinition] = {}
        errors: dict[str, str] = {}
        for directory in self._directories():
            if not directory.is_dir():
                continue
            for file_path in sorted(directory.glob("*.yaml")):
                try:
                    definition = parse_cli_definition(
                        yaml.safe_load(file_path.read_text(encoding="utf-8")),
                        source=str(file_path),
                    )
                    definitions[definition.id] = definition
                except Exception as exc:
                    errors[str(file_path)] = str(exc)
        return definitions, errors


def parse_cli_definition(raw: Any, *, source: str = "") -> CliToolDefinition:
    if not isinstance(raw, dict):
        raise ValueError("CLI tool definition must be a mapping")
    required_fields = ("id", "name", "description", "executable", "parameters", "argv")
    missing = [name for name in required_fields if name not in raw]
    if missing:
        raise ValueError(f"CLI tool definition missing: {', '.join(missing)}")
    if not all(isinstance(raw[name], str) for name in ("id", "name", "description", "executable")):
        raise ValueError("CLI id, name, description, and executable must be strings")
    executable = raw["executable"]
    if any(token in executable for token in ("|", ";", "&&", "||", ">", "<")):
        raise ValueError("CLI executable must be a single program, not shell syntax")
    category = raw.get("category", "command")
    if category not in {"read", "write", "command"}:
        raise ValueError("CLI category must be read, write, or command")
    parameters = raw["parameters"]
    argv = raw["argv"]
    if not isinstance(parameters, dict) or not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise ValueError("CLI parameters must be a mapping and argv a string list")
    targets = []
    for item in raw.get("permission_targets", []):
        if not isinstance(item, dict) or item.get("access") not in {"read", "write", "execute", "external"}:
            raise ValueError("invalid CLI permission target")
        targets.append(CliPermissionTarget(parameter=str(item.get("parameter", "")), access=item["access"]))
    environment = raw.get("environment") or {}
    allow = environment.get("allow", ["PATH"]) if isinstance(environment, dict) else ["PATH"]
    if not isinstance(allow, list) or not all(isinstance(item, str) for item in allow):
        raise ValueError("CLI environment.allow must be a string list")
    timeout = raw.get("timeout_seconds", 60)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("CLI timeout_seconds must be positive")
    return CliToolDefinition(
        id=raw["id"],
        name=raw["name"],
        description=raw["description"],
        executable=executable,
        category=category,
        parameters=parameters,
        argv=tuple(argv),
        timeout_seconds=float(timeout),
        working_directory=raw.get("working_directory", "workspace"),
        environment_allow=tuple(allow),
        permission_targets=tuple(targets),
        source=source,
    )


def register_cli_tools(
    registry: ToolRegistry,
    definitions: list[CliToolDefinition],
    work_dir: str,
) -> None:
    for definition in definitions:
        registry.register(
            CliTool(definition, work_dir),
            origin=ToolOrigin(kind="cli", provider_id=definition.id),
        )
