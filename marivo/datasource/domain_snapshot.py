"""Driver-owned transactions, immutable file capture authority."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from marivo.datasource.adapters import SourceSession
from marivo.datasource.errors import DatasourceSourceCapabilityError, repair
from marivo.datasource.ir import ParquetSourceIR, TableSourceIR


def fail(constraint: str, received: str, *, stage: str) -> NoReturn:
    raise DatasourceSourceCapabilityError(
        message="Source capture failed.",
        expected="a qualified native table read or immutable local Parquet capture",
        received=received,
        location=f"datasource.{stage}.{constraint}",
        repair=repair(
            kind="retry",
            action="Repair the exact source binding and retry the bounded capture.",
            canonical_id="connection",
        ),
    )


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def capture(
    source: SourceSession,
) -> Iterator[dict[str, object]]:
    """Retain bound input-read authority; remote reads may observe different source versions."""
    if source.provider.name in ("postgres", "mysql", "trino", "clickhouse"):
        if (
            source._closed
            or not source._bound_sources
            or any(
                not isinstance(binding.source, TableSourceIR)
                for binding in source._bound_sources.values()
            )
        ):
            fail(
                "input_binding",
                "independent source reads require owned native table bindings",
                stage="admission",
            )
        read_authority: dict[str, object] = {
            "kind": "independent_reads",
            "capture_id": uuid4().hex,
            "files": [],
        }
        read_authority["digest"] = hashlib.sha256(
            json.dumps(read_authority, sort_keys=True).encode()
        ).hexdigest()
        yield read_authority
        return
    if source.provider.name != "duckdb":
        fail(
            "physical_qualification",
            "domain snapshot is not qualified for this backend",
            stage="admission",
        )
    backend = source._backend
    connection = getattr(backend, "con", None)
    begin, rollback = (getattr(connection, name, None) for name in ("begin", "rollback"))
    if not callable(begin) or not callable(rollback):
        fail(
            "physical_qualification",
            "native transaction authority unavailable",
            stage="admission",
        )
    manifest: list[dict[str, object]] = []
    fingerprints: list[tuple[Path, tuple[int, int, int], str]] = []
    for identity, binding in tuple(source._bound_sources.items()):
        original = binding.source
        if isinstance(original, ParquetSourceIR):
            path = Path(original.path)
            if not path.is_file():
                fail(
                    "physical_qualification",
                    "domain file capture requires an exact local Parquet file",
                    stage="admission",
                )
            before = path.stat()
            content_digest = _hash(path)
            after = path.stat()
            file_identity = (before.st_ino, before.st_size, before.st_mtime_ns)
            if file_identity != (after.st_ino, after.st_size, after.st_mtime_ns):
                fail("input_binding", "Parquet changed during source binding", stage="prepare")
            fingerprints.append((path, file_identity, content_digest))
            relation = backend.read_parquet(str(path), hive_partitioning=original.hive_partitioning)
            if original.columns is not None:
                relation = relation.select(*original.columns)
            source._bound_sources[identity] = replace(binding, relation=relation)
            manifest.append(
                {
                    "source_id": identity,
                    "path": str(path.resolve()),
                    "sha256": content_digest,
                    "bytes": before.st_size,
                }
            )
        elif not isinstance(original, TableSourceIR):
            fail(
                "physical_qualification",
                "domain capture supports native table and local Parquet only",
                stage="admission",
            )
    authority: dict[str, object] = {
        "kind": "file_manifest" if manifest else "duckdb_transaction",
        "capture_id": uuid4().hex,
        "files": manifest,
    }
    authority["digest"] = hashlib.sha256(json.dumps(authority, sort_keys=True).encode()).hexdigest()
    begin()
    try:
        yield authority
        for path, original_identity, original_digest in fingerprints:
            current = path.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != original_identity or _hash(
                path
            ) != original_digest:
                fail("input_binding", "Parquet changed during prepared analysis", stage="execute")
    finally:
        if sys.exc_info()[0] is None:
            rollback()
        else:
            with suppress(Exception):
                rollback()
