"""Application paths and one-time migration helpers for Lumo."""

from __future__ import annotations

import shutil
from pathlib import Path


APP_DIR_NAME = ".lumo"
LEGACY_APP_DIR_NAME = ".mewcode"
MIGRATION_MARKER = ".migrated-from-mewcode"

_SKIPPED_NAMES = {"__pycache__", "debug.log"}


def app_dir(root: Path) -> Path:
    return root / APP_DIR_NAME


def legacy_app_dir(root: Path) -> Path:
    return root / LEGACY_APP_DIR_NAME


def migrate_legacy_app_dir(root: Path) -> int:
    """Copy missing data from ``.mewcode`` to ``.lumo`` once.

    Existing Lumo files always win and the legacy directory is preserved. Runtime
    caches and logs are intentionally not copied.
    """

    source_root = legacy_app_dir(root)
    target_root = app_dir(root)
    marker = target_root / MIGRATION_MARKER
    if not source_root.is_dir() or marker.exists():
        return 0

    copied = 0
    target_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.rglob("*"):
        relative = source.relative_to(source_root)
        if any(part in _SKIPPED_NAMES for part in relative.parts):
            continue
        if source.suffix == ".pyc":
            continue

        target = target_root / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

    marker.write_text(
        "Legacy .mewcode data was copied here by Lumo.\n",
        encoding="utf-8",
    )
    return copied


def migrate_legacy_data(work_dir: Path | None = None) -> int:
    """Migrate user and project data without deleting or overwriting anything."""

    roots = [Path.home(), (work_dir or Path.cwd()).resolve()]
    copied = 0
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        copied += migrate_legacy_app_dir(resolved)
    return copied
