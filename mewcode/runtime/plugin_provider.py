from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import inspect
from typing import Any, Mapping, Protocol

from mewcode.tools import ToolOrigin, ToolRegistry
from mewcode.tools.base import Tool


class PluginHandle(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class PluginDescriptor:
    id: str
    version: str = "unknown"
    description: str = ""
    config_schema: dict[str, Any] | None = None
    requires: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str = ""
    entry_point: Any = None
    plugin: Any = None


class PluginContext:
    def __init__(self, registry: ToolRegistry, plugin_id: str, work_dir: str) -> None:
        self._registry = registry
        self.plugin_id = plugin_id
        self.work_dir = work_dir

    def register_tool(self, tool: Tool) -> None:
        self._registry.register(
            tool, origin=ToolOrigin(kind="plugin", provider_id=self.plugin_id)
        )


class PluginCatalog:
    def discover(self) -> dict[str, PluginDescriptor]:
        result: dict[str, PluginDescriptor] = {}
        try:
            candidates = metadata.entry_points(group="mewcode.plugins")
        except TypeError:
            candidates = metadata.entry_points().get("mewcode.plugins", [])
        for entry_point in candidates:
            distribution = getattr(entry_point, "dist", None)
            version = getattr(distribution, "version", "unknown")
            try:
                target = entry_point.load()
                plugin = target() if inspect.isclass(target) else target
                if callable(plugin) and not hasattr(plugin, "setup"):
                    plugin = plugin()
                plugin_id = str(getattr(plugin, "id", entry_point.name))
                if plugin_id != entry_point.name:
                    raise ValueError(
                        f"plugin id '{plugin_id}' does not match entry point "
                        f"'{entry_point.name}'"
                    )
                description = str(getattr(plugin, "description", ""))
                config_schema = getattr(plugin, "config_schema", None)
                requires = tuple(getattr(plugin, "requires", ()))
                plugin_version = str(getattr(plugin, "version", version))
                result[entry_point.name] = PluginDescriptor(
                    id=entry_point.name,
                    version=plugin_version,
                    description=description,
                    config_schema=config_schema,
                    requires=requires,
                    entry_point=entry_point,
                    plugin=plugin,
                )
            except Exception as exc:
                result[entry_point.name] = PluginDescriptor(
                    id=entry_point.name,
                    version=version,
                    available=False,
                    unavailable_reason=str(exc),
                    entry_point=entry_point,
                )
        return result


async def setup_plugin(
    descriptor: PluginDescriptor,
    registry: ToolRegistry,
    work_dir: str,
    config: Mapping[str, Any],
) -> PluginHandle | None:
    if not descriptor.available:
        raise RuntimeError(descriptor.unavailable_reason)
    _validate_config(config, descriptor.config_schema)
    plugin = descriptor.plugin
    if plugin is None:
        plugin = descriptor.entry_point.load()
        if inspect.isclass(plugin):
            plugin = plugin()
        elif callable(plugin) and not hasattr(plugin, "setup"):
            plugin = plugin()
    setup = getattr(plugin, "setup", None)
    if setup is None:
        raise TypeError(f"plugin '{descriptor.id}' does not define setup()")
    result = setup(PluginContext(registry, descriptor.id, work_dir), config)
    if inspect.isawaitable(result):
        result = await result
    if result is not None and not hasattr(result, "aclose"):
        raise TypeError(f"plugin '{descriptor.id}' setup result has no aclose()")
    return result


def _validate_config(
    config: Mapping[str, Any],
    schema: dict[str, Any] | None,
) -> None:
    if schema is None:
        return
    required = schema.get("required", [])
    missing = [name for name in required if name not in config]
    if missing:
        raise ValueError(f"plugin config missing: {', '.join(missing)}")
    properties = schema.get("properties", {})
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for name, value in config.items():
        definition = properties.get(name)
        if definition is None:
            if schema.get("additionalProperties", True) is False:
                raise ValueError(f"unknown plugin config field: {name}")
            continue
        expected = type_map.get(definition.get("type"))
        if expected is not None and not isinstance(value, expected):
            raise ValueError(f"plugin config field '{name}' has the wrong type")
