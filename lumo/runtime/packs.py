from __future__ import annotations

from typing import Any

from lumo.runtime.models import RuntimeSpec
from lumo.tools import ToolOrigin, ToolRegistry
from lumo.tools.file_state_cache import FileStateCache


def create_registry_for_spec(
    spec: RuntimeSpec,
    file_history: Any = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    packs = set(spec.scenario.packs)
    cache = FileStateCache()

    if "coding.files" in packs:
        from lumo.tools.edit_file import EditFile
        from lumo.tools.glob import Glob
        from lumo.tools.grep import Grep
        from lumo.tools.read_file import ReadFile
        from lumo.tools.write_file import WriteFile

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
        from lumo.tools.bash import Bash
        registry.register(
            Bash(), ToolOrigin(kind="pack", provider_id="coding.shell")
        )

    return registry
