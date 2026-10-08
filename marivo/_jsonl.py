"""Private append and rolling primitives shared by local diagnostic sinks."""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path


def managed_files(directory: Path, prefix: str) -> list[tuple[date, int, Path]]:
    pattern = re.compile(rf"{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})\.(\d{{3,}})\.jsonl")
    result: list[tuple[date, int, Path]] = []
    for path in directory.glob(f"{prefix}*.jsonl"):
        match = pattern.fullmatch(path.name)
        if match is None or not path.is_file() or path.is_symlink():
            continue
        try:
            result.append((date.fromisoformat(match[1]), int(match[2]), path))
        except ValueError:
            continue
    return sorted(result)


def output_path(
    directory: Path, prefix: str, *, event_date: date, payload_bytes: int, max_bytes: int
) -> Path:
    candidates = [item for item in managed_files(directory, prefix) if item[0] == event_date]
    segment = 0
    if candidates:
        _, segment, current = candidates[-1]
        size = current.stat().st_size
        if size > 0 and size + payload_bytes > max_bytes:
            segment += 1
    return directory / f"{prefix}{event_date.isoformat()}.{segment:03d}.jsonl"


def append(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, payload) != len(payload):
            raise OSError("short JSONL append")
    finally:
        os.close(descriptor)


def prune(
    directory: Path,
    prefix: str,
    *,
    current_date: date,
    retention_days: int,
    max_historical_bytes: int,
    last_pruned: dict[Path, date],
) -> None:
    resolved = directory.resolve()
    if last_pruned.get(resolved) == current_date:
        return
    cutoff = current_date - timedelta(days=retention_days - 1)
    retained: list[tuple[Path, int]] = []
    for file_date, _, path in managed_files(directory, prefix):
        if file_date >= current_date:
            continue
        if file_date < cutoff:
            path.unlink()
        else:
            retained.append((path, path.stat().st_size))
    size = sum(count for _, count in retained)
    for path, count in retained:
        if size <= max_historical_bytes:
            break
        path.unlink()
        size -= count
    last_pruned[resolved] = current_date
