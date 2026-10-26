"""Filesystem-only disclosure of retained older Stores, never a read fallback."""

from __future__ import annotations

import re
import stat
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

from marivo._compat import UTC
from marivo.analysis.materialization.layout import MaterializationLayout


@dataclass(frozen=True)
class PriorGeneration:
    generation: int
    path: Path
    file_mtime: datetime


def discover_prior_generations(layout: MaterializationLayout) -> tuple[PriorGeneration, ...]:
    """Inspect known Store filenames without opening any prior database."""
    found: list[PriorGeneration] = []
    for directory in layout.generation_dir.parent.glob("v*"):
        match = re.fullmatch(r"v([0-9]+)", directory.name)
        if match is None or not 0 < int(match[1]) < layout.generation:
            continue
        for filename in ("session_store.db", "store.sqlite3"):
            path = directory / filename
            try:
                metadata = path.stat()
            except OSError:
                # Advisory discovery must not prevent current-generation work.
                continue
            if stat.S_ISREG(metadata.st_mode):
                found.append(
                    PriorGeneration(
                        int(match[1]), path, datetime.fromtimestamp(metadata.st_mtime, UTC)
                    )
                )
    return tuple(sorted(found, key=lambda item: (item.generation, item.path)))


def prior_generation_guidance(prior: tuple[PriorGeneration, ...]) -> str:
    """Describe observed files without asserting Session counts or ID ownership."""
    files = "; ".join(
        f"v{item.generation} ({item.path}; file mtime {item.file_mtime.isoformat()})"
        for item in prior
    )
    return (
        f"Older Session Stores remain in this project: {files}. "
        "This version cannot read them; use a compatible Marivo environment to read/export them. "
        "Each generation has independent Sessions and Artifacts; current history excludes older Stores. "
        "File mtime is not the last business write time."
    )


_notified: set[tuple[Path, int, tuple[Path, ...]]] = set()
_notice_lock = Lock()


def warn_prior_generations(layout: MaterializationLayout) -> None:
    """Warn once per project/current generation/observed set in this process."""
    prior = discover_prior_generations(layout)
    if not prior:
        return
    key = (layout.project_root, layout.generation, tuple(item.path for item in prior))
    with _notice_lock:
        if key in _notified:
            return
        warnings.warn(prior_generation_guidance(prior), UserWarning, stacklevel=3)
        _notified.add(key)
