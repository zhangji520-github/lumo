from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile
from typing import Literal

import yaml

from lumo.runtime.errors import ScenarioValidationError
from lumo.runtime.models import ScenarioDefinition
from lumo.runtime.scenario_loader import parse_scenario, scenario_to_dict

ScenarioScope = Literal["user", "project"]


class ScenarioPackageWriter:
    """Write a scenario manifest and prompt through one contained entry point."""

    def __init__(self, work_dir: str) -> None:
        self.work_dir = Path(work_dir)
        self.user_root = Path.home() / ".lumo" / "scenarios"
        self.project_root = self.work_dir / ".lumo" / "scenarios"

    def write(
        self,
        definition: ScenarioDefinition,
        prompt_text: str,
        *,
        scope: ScenarioScope = "user",
        overwrite: bool = False,
    ) -> Path:
        if len(prompt_text.encode("utf-8")) > 20 * 1024:
            raise ScenarioValidationError("scenario prompt exceeds the 20 KiB limit")
        root = self.user_root if scope == "user" else self.project_root
        root.mkdir(parents=True, exist_ok=True)
        target = root / definition.id
        legacy_target = root / f"{definition.id}.yaml"
        if legacy_target.exists():
            raise FileExistsError(
                f"legacy scenario already exists: {legacy_target}"
            )
        if target.exists() and not overwrite:
            raise FileExistsError(f"scenario package already exists: {definition.id}")
        if target.exists() and not target.is_dir():
            raise ScenarioValidationError(
                f"scenario package path is not a directory: {target}"
            )

        packaged = replace(
            definition,
            schema_version=2,
            experience=replace(
                definition.experience,
                prompt_file="prompt.md",
                prompt_text="",
            ),
        )
        data = scenario_to_dict(packaged)
        parse_scenario(data, expected_id=packaged.id)
        _reject_inline_secrets(data)

        if not target.exists():
            temp_dir = Path(tempfile.mkdtemp(prefix=f".{definition.id}.", dir=root))
            try:
                _write_package_files(temp_dir, data, prompt_text)
                os.replace(temp_dir, target)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
            return target / "scenario.yaml"

        self._replace_file(target / "scenario.yaml", yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False
        ))
        self._replace_file(target / "prompt.md", prompt_text)
        return target / "scenario.yaml"

    @staticmethod
    def _replace_file(target: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


def _write_package_files(
    directory: Path,
    manifest: dict,
    prompt_text: str,
) -> None:
    (directory / "scenario.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (directory / "prompt.md").write_text(prompt_text, encoding="utf-8")


def _reject_inline_secrets(value, key: str = "") -> None:
    sensitive = any(
        marker in key.lower()
        for marker in ("token", "secret", "password", "api_key", "authorization")
    )
    if sensitive and isinstance(value, str) and not value.startswith("$" + "{"):
        raise ScenarioValidationError(
            f"scenario field '{key}' must use an environment reference"
        )
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            _reject_inline_secrets(item_value, str(item_key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_inline_secrets(item, key)
