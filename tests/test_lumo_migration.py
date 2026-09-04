from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lumo.config import load_config
from lumo.memory.instructions import load_instructions
from lumo.paths import MIGRATION_MARKER, migrate_legacy_app_dir


def _write_config(path: Path, provider_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            providers:
              - name: {provider_name}
                protocol: openai
                base_url: http://localhost
                model: test-model
            """
        ),
        encoding="utf-8",
    )


def test_migration_copies_missing_files_and_preserves_legacy(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    (legacy / "sessions").mkdir(parents=True)
    (legacy / "config.yaml").write_text("legacy config", encoding="utf-8")
    (legacy / "sessions" / "one.jsonl").write_text("session", encoding="utf-8")
    (legacy / "debug.log").write_text("skip me", encoding="utf-8")

    copied = migrate_legacy_app_dir(tmp_path)

    assert copied == 2
    assert (tmp_path / ".lumo" / "config.yaml").read_text(encoding="utf-8") == "legacy config"
    assert (tmp_path / ".lumo" / "sessions" / "one.jsonl").exists()
    assert not (tmp_path / ".lumo" / "debug.log").exists()
    assert (tmp_path / ".lumo" / MIGRATION_MARKER).exists()
    assert legacy.exists()


def test_migration_never_overwrites_lumo_files(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    current = tmp_path / ".lumo"
    legacy.mkdir()
    current.mkdir()
    (legacy / "config.yaml").write_text("legacy", encoding="utf-8")
    (current / "config.yaml").write_text("current", encoding="utf-8")

    migrate_legacy_app_dir(tmp_path)

    assert (current / "config.yaml").read_text(encoding="utf-8") == "current"


def test_load_config_falls_back_to_legacy_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    _write_config(project / ".mewcode" / "config.yaml", "legacy")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)

    config = load_config()

    assert config.providers[0].name == "legacy"


def test_load_config_prefers_lumo_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    _write_config(project / ".mewcode" / "config.yaml", "legacy")
    _write_config(project / ".lumo" / "config.yaml", "lumo")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)

    config = load_config()

    assert config.providers[0].name == "lumo"


def test_load_instructions_falls_back_to_legacy_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / "MEWCODE.md").write_text("legacy project instructions", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    result = load_instructions(str(project))

    assert "legacy project instructions" in result
