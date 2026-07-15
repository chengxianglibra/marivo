"""Shared fingerprint computations for cross-surface handoff continuity.

The analysis-side handoff validator and the semantic-side handoff producer
must agree on exactly the same project and catalog fingerprints. Both
algorithms live here so there is one truth per fingerprint, shared by
``marivo.analysis`` and ``marivo.semantic`` without either importing the
other.

This module is part of the neutral live foundation: it uses only the
standard library and must not import ``marivo.semantic``,
``marivo.datasource``, or ``marivo.analysis`` at module load.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path


def project_fingerprint(project_root: Path) -> str:
    """Return a deterministic fingerprint of the project root state.

    Hashes ``marivo.toml`` and every ``models/**/*.py`` file (contents keyed
    by repo-relative path, sorted). Matches the analysis
    ``Session._project_fingerprint`` contract exactly.
    """
    parts: list[str] = []
    marivo_toml = project_root / "marivo.toml"
    if marivo_toml.is_file():
        parts.append(f"marivo.toml:{marivo_toml.read_text()}")
    models_dir = project_root / "models"
    if models_dir.is_dir():
        for py_file in sorted(models_dir.rglob("*.py")):
            rel = py_file.relative_to(project_root)
            parts.append(f"{rel}:{py_file.read_text()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def catalog_fingerprint(typed_ids: Iterable[str]) -> str:
    """Return a deterministic fingerprint of the loaded catalog state.

    Hashes the sorted set of typed object ids joined by ``|``. Matches the
    analysis ``Session._catalog_fingerprint`` contract exactly.
    """
    digest = hashlib.sha256("|".join(sorted(typed_ids)).encode("utf-8")).hexdigest()
    return digest
