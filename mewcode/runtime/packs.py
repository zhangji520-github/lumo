from __future__ import annotations

from typing import Any

from mewcode.runtime.models import RuntimeSpec
from mewcode.tools import ToolOrigin, ToolRegistry
from mewcode.tools.file_state_cache import FileStateCache


def create_registry_for_spec(
    spec: RuntimeSpec,
    file_history: Any = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    packs = set(spec.scenario.packs)
    cache = FileStateCache()

    if "coding.files" in packs:
        from mewcode.tools.edit_file import EditFile
        from mewcode.tools.glob import Glob
        from mewcode.tools.grep import Grep
        from mewcode.tools.read_file import ReadFile
        from mewcode.tools.write_file import WriteFile

        origin = ToolOrigin(kind="pack", provider_id="coding.files")
        registry.register(ReadFile(file_state_cache=cache), origin)
        registry.register(
            WriteFile(file_history=file_history, file_state_cache=cache), origin
        )
        registry.register(
            EditFile(file_history=file_history, file_state_cache=cache), origin
        )
        registry.register(Glob(), origin)
        registry.register(Grep(), origin)

    if "coding.shell" in packs:
        from mewcode.tools.bash import Bash
        registry.register(
            Bash(), ToolOrigin(kind="pack", provider_id="coding.shell")
        )

    return registry
