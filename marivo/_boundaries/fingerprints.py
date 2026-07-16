"""Deterministic project and catalog continuity fingerprints."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path


def project_fingerprint(project_root: Path) -> str:
    """Hash project configuration and semantic Python sources deterministically."""
    parts: list[str] = []
    marivo_toml = project_root / "marivo.toml"
    if marivo_toml.is_file():
        parts.append(f"marivo.toml:{marivo_toml.read_text()}")
    models_dir = project_root / "models"
    if models_dir.is_dir():
        for py_file in sorted(models_dir.rglob("*.py")):
            relative = py_file.relative_to(project_root)
            parts.append(f"{relative}:{py_file.read_text()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def catalog_fingerprint(typed_ids: Iterable[str]) -> str:
    """Hash sorted semantic ids with the established byte representation."""
    return hashlib.sha256("|".join(sorted(typed_ids)).encode("utf-8")).hexdigest()
