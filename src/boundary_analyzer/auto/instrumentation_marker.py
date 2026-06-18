from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from boundary_analyzer import __version__

logger = logging.getLogger(__name__)

INSTRUMENTATION_MARKER = ".mba-instrumented"


@dataclass
class MarkerArtifact:
    type: str
    path: str | None = None
    original: str | None = None
    backup: str | None = None


@dataclass
class InstrumentationMarker:
    version: str = __version__
    mode: str = "full"
    artifacts: list[MarkerArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": self.mode,
            "artifacts": [
                {k: v for k, v in a.__dict__.items() if v is not None}
                for a in self.artifacts
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstrumentationMarker:
        artifacts = [
            MarkerArtifact(**a) if isinstance(a, dict) else a
            for a in data.get("artifacts", [])
        ]
        return cls(version=data.get("version", "0.0.0"), mode=data.get("mode", "full"), artifacts=artifacts)


def marker_path(project_root: Path) -> Path:
    return project_root / INSTRUMENTATION_MARKER


def read_marker(project_root: Path) -> InstrumentationMarker | None:
    mpath = marker_path(project_root)
    if not mpath.exists():
        return None
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
        return InstrumentationMarker.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt marker file %s: %s", mpath, e)
        return None


def write_marker(project_root: Path, marker: InstrumentationMarker) -> None:
    mpath = marker_path(project_root)
    try:
        mpath.write_text(
            json.dumps(marker.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Cannot write marker %s: %s", mpath, e)


def delete_marker(project_root: Path) -> None:
    mpath = marker_path(project_root)
    try:
        if mpath.exists():
            mpath.unlink()
    except OSError as e:
        logger.warning("Cannot delete marker %s: %s", mpath, e)


def check_stale_instrumentation(project_root: Path) -> bool:
    """Check if project has stale instrumentation from a different MBA version.

    Returns True if cleanup was performed (stale instrumentation found and removed).
    Returns False if no marker exists or version matches.
    """
    marker = read_marker(project_root)
    if marker is None:
        return False

    if marker.version == __version__:
        return False

    logger.info(
        "Stale instrumentation from MBA v%s detected (current: v%s). Cleaning up...",
        marker.version, __version__,
    )
    cleanup_instrumentation(project_root, marker)
    return True


def cleanup_instrumentation(project_root: Path, marker: InstrumentationMarker) -> None:
    """Revert all artifacts listed in the marker file.

    Restores backup files, deletes generated overrides and Dockerfiles,
    and removes the marker itself.
    """
    for arti in marker.artifacts:
        try:
            _revert_artifact(project_root, arti)
        except Exception as e:
            logger.warning("Failed to revert artifact %s: %s", arti, e)

    delete_marker(project_root)


def _revert_artifact(project_root: Path, arti: MarkerArtifact) -> None:
    if arti.type == "backup":
        if arti.original and arti.backup:
            original_path = (project_root / arti.original).resolve()
            backup_path = (project_root / arti.backup).resolve()
            if backup_path.exists():
                shutil.copy2(backup_path, original_path)
                backup_path.unlink()
                logger.debug("Restored %s from %s", arti.original, arti.backup)
            else:
                logger.warning("Backup not found: %s", arti.backup)

    elif arti.type == "compose_override":
        if arti.path:
            override_path = (project_root / arti.path).resolve()
            if override_path.exists():
                override_path.unlink()
                logger.debug("Deleted override %s", arti.path)

    elif arti.type == "dockerfile_override":
        if arti.path:
            df_path = (project_root / arti.path).resolve()
            if df_path.exists():
                df_path.unlink()
                logger.debug("Deleted Dockerfile override %s", arti.path)


def cleanup_orphans(project_root: Path) -> bool:
    """Find and remove orphan artifacts from pre-v0.4.0 runs (no marker file).

    Scans for orphan .mba_bak, .mba-Dockerfile, and .mba-compose-override.yml
    that exist without a .mba-instrumented marker. Restores backup files and
    deletes the rest.

    Returns True if any orphans were found and cleaned up.
    """
    if marker_path(project_root).exists():
        return False

    orphans = _find_orphan_artifacts(project_root)
    if not orphans:
        return False

    _remove_orphan_artifacts(project_root, orphans)
    return True


def _find_orphan_artifacts(project_root: Path) -> list[Path]:
    """Scan for orphan artifact files without a marker.

    Uses os.walk with directory pruning to avoid descending into
    .venv, node_modules, etc.
    """
    orphans: list[Path] = []
    _skip = {".venv", "venv", "node_modules", "__pycache__", ".git", ".idea", ".tox", ".mypy_cache", ".pytest_cache"}

    override = project_root / ".mba-compose-override.yml"
    if override.exists():
        orphans.append(override)

    for dirpath_str, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _skip]
        dirpath = Path(dirpath_str)

        for fn in filenames:
            if fn == ".mba-Dockerfile" or fn.endswith(".mba_bak"):
                orphans.append(dirpath / fn)

    return orphans


def _remove_orphan_artifacts(project_root: Path, orphans: list[Path]) -> None:
    """Remove orphan artifacts: restore backups, delete generated files."""
    for path in orphans:
        try:
            if path.suffix == ".mba_bak":
                original = path.with_suffix("")
                if original.exists():
                    shutil.copy2(path, original)
                path.unlink()
                logger.info("Restored %s from orphan backup", original)
            elif path.name == ".mba-compose-override.yml":
                path.unlink()
                logger.info("Removed orphan compose override %s", path)
            elif path.name == ".mba-Dockerfile":
                path.unlink()
                logger.info("Removed orphan Dockerfile %s", path)
        except OSError as e:
            logger.warning("Cannot remove orphan artifact %s: %s", path, e)
