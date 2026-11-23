"""Project-local persistence for privacy-aware authoring evidence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from marivo._kernel.compat import UTC
from marivo._kernel.config import (
    AUTHORING_DIR,
    AUTHORING_SNAPSHOT_DIR,
    STATE_DIR,
)
from marivo._kernel.refs import DatasourceKind, Ref
from marivo.datasource.ir import QueryParamScalar, QueryParamScalarList
from marivo.datasource.source import (
    AuthoringScope,
    TableSource,
)
from marivo.datasource.store.evidence import (
    DiscoveryEvidence,
)
from marivo.datasource.store.evidence_payload import (
    _SNAPSHOT_PAYLOAD_FIELDS,
    SNAPSHOT_TTL,
    CacheMissStatus,
    _boolean,
    _column_profile,
    _coverage,
    _encoded,
    _integer,
    _mapping,
    _payload_digest,
    _require_fields,
    _retained_values,
    _scope_payload,
    _sequence,
    _source_param_value,
    _string,
    _timestamp,
    _validate_snapshot_consistency,
    authoring_scope_from_payload,
    snapshot_identity,
)

EVIDENCE_FORMAT_VERSION = 4


_SNAPSHOT_MEMORY: dict[str, DiscoveryEvidence] = {}


@dataclass(frozen=True)
class SnapshotCacheLookup:
    """Internal cache lookup result for the single-query acquisition path."""

    snapshot: DiscoveryEvidence | None
    status: CacheMissStatus
    now: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthoringStore:
    """Persist and reload authoring evidence below one project-local state root."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.snapshot_dir = self.project_root / AUTHORING_SNAPSHOT_DIR

    def _memory_key(self, snapshot_id: str) -> str:
        return f"{self.project_root}:{snapshot_id}"

    def _snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshot_dir / f"{snapshot_id}.json"

    def _ensure_directories(self) -> None:
        for path in (
            self.project_root / STATE_DIR,
            self.project_root / AUTHORING_DIR,
            self.snapshot_dir,
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _read_payload(self, path: Path) -> dict[str, object] | None:
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
            return _mapping(decoded, field="snapshot")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _metadata(
        self,
        *,
        snapshot_id: str,
        datasource: Ref[DatasourceKind],
        datasource_fingerprint: str,
        source: TableSource,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        schema_fingerprint: str,
        persist_values: bool,
        source_params: tuple[tuple[str, QueryParamScalar | QueryParamScalarList], ...] = (),
    ) -> dict[str, object]:
        return {
            "evidence_format_version": EVIDENCE_FORMAT_VERSION,
            "id": snapshot_id,
            "datasource": datasource.path,
            "datasource_fingerprint": datasource_fingerprint,
            "source": source.to_dict(),
            "source_params": dict(source_params),
            "scope": _scope_payload(scope),
            "columns": columns,
            "schema_fingerprint": schema_fingerprint,
            "persist_values": persist_values,
        }

    def _has_related_snapshot(self, datasource: Ref[DatasourceKind]) -> bool:
        if not self.snapshot_dir.is_dir():
            return False
        for path in self.snapshot_dir.glob("*.json"):
            payload = self._read_payload(path)
            if payload is not None and payload.get("datasource") == datasource.path:
                return True
        return False

    def _read_valid_snapshot(
        self,
        path: Path,
        *,
        datasource: Ref[DatasourceKind],
        datasource_fingerprint: str,
        source: TableSource,
    ) -> DiscoveryEvidence | None:
        payload = self._read_payload(path)
        if payload is None:
            return None
        try:
            _require_fields(payload, _SNAPSHOT_PAYLOAD_FIELDS, field="snapshot")
            payload_digest = _string(payload.get("payload_digest"), field="payload_digest")
            if payload_digest != _payload_digest(payload):
                return None
            if (
                _integer(payload.get("evidence_format_version"), field="evidence_format_version")
                != EVIDENCE_FORMAT_VERSION
            ):
                return None
            snapshot_id = _string(payload.get("id"), field="id")
            if path != self._snapshot_path(snapshot_id):
                return None
            if _string(payload.get("datasource"), field="datasource") != datasource.path:
                return None
            if (
                _string(payload.get("datasource_fingerprint"), field="datasource_fingerprint")
                != datasource_fingerprint
            ):
                return None
            if _mapping(payload.get("source"), field="source") != source.to_dict():
                return None
            scope = authoring_scope_from_payload(payload.get("scope"))
            columns = tuple(
                _string(item, field="columns item")
                for item in _sequence(payload.get("columns"), field="columns")
            )
            if not columns or any(not column for column in columns):
                return None
            schema_fingerprint = _string(
                payload.get("schema_fingerprint"), field="schema_fingerprint"
            )
            persist_values = _boolean(payload.get("persist_values"), field="persist_values")
            raw_source_params = _mapping(payload.get("source_params"), field="source_params")
            source_params: list[tuple[str, QueryParamScalar | QueryParamScalarList]] = []
            for name, value in raw_source_params.items():
                normalized_value = _source_param_value(value)
                if normalized_value is None:
                    return None
                source_params.append((name, normalized_value))
            source_param_items = tuple(source_params)
            expected_id = snapshot_identity(
                datasource_fingerprint=datasource_fingerprint,
                source=source,
                scope=scope,
                columns=columns,
                schema_fingerprint=schema_fingerprint,
                persist_values=persist_values,
                source_params=source_param_items,
            )
            if snapshot_id != expected_id:
                return None
            created_at = _timestamp(payload.get("created_at"), field="created_at")
            expires_at = _timestamp(payload.get("expires_at"), field="expires_at")
            if expires_at != created_at + SNAPSHOT_TTL:
                return None
            profiles = tuple(
                _column_profile(item)
                for item in _sequence(payload.get("profiles"), field="profiles")
            )
            coverage = _coverage(payload.get("coverage"))
            _validate_snapshot_consistency(
                scope=scope,
                columns=columns,
                profiles=profiles,
                coverage=coverage,
            )
            retained_values = _retained_values(
                payload.get("retained_values"),
                columns=columns,
                retained_row_count=coverage.retained_row_count,
                persist_values=persist_values,
            )
            if not persist_values:
                profiles = tuple(
                    replace(
                        profile,
                        min_value=None,
                        max_value=None,
                        top_values=None,
                        display_samples=None,
                    )
                    for profile in profiles
                )
            return DiscoveryEvidence(
                id=snapshot_id,
                _datasource=datasource,
                source=source,
                scope=scope,
                columns=columns,
                schema_fingerprint=schema_fingerprint,
                profiles=profiles,
                coverage=coverage,
                persist_values=persist_values,
                value_evidence_state=(
                    "available" if persist_values else "value_evidence_unavailable"
                ),
                cache_status="cached",
                created_at=created_at,
                expires_at=expires_at,
                _project_root=self.project_root,
                source_params=source_param_items,
                retained_values=retained_values,
            )
        except (TypeError, ValueError):
            return None

    def valid_snapshots(
        self,
        *,
        datasource: Ref[DatasourceKind],
        datasource_fingerprint: str,
        source: TableSource,
        now: datetime | None = None,
    ) -> tuple[DiscoveryEvidence, ...]:
        """Read structurally valid persisted snapshot metadata without using memory.

        Snapshot age is reported through ``cache_status`` and ``expires_at``;
        it does not exclude otherwise matching evidence from this result.
        """
        if not self.snapshot_dir.is_dir():
            return ()
        checked_at = now or _utc_now()
        snapshots: list[DiscoveryEvidence] = []
        for path in self.snapshot_dir.glob("*.json"):
            snapshot = self._read_valid_snapshot(
                path,
                datasource=datasource,
                datasource_fingerprint=datasource_fingerprint,
                source=source,
            )
            if snapshot is None:
                continue
            snapshots.append(
                replace(snapshot, cache_status="stale")
                if snapshot.expires_at <= checked_at
                else snapshot
            )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.created_at, reverse=True))

    def lookup_snapshot(
        self,
        *,
        snapshot_id: str,
        datasource: Ref[DatasourceKind],
        datasource_fingerprint: str,
        source: TableSource,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        schema_fingerprint: str,
        persist_values: bool,
        source_params: tuple[tuple[str, QueryParamScalar | QueryParamScalarList], ...] = (),
        refresh: bool,
    ) -> SnapshotCacheLookup:
        """Return a matching cache hit or why one acquisition is required.

        Expiration is informational: an expired matching snapshot is returned
        with ``cache_status="stale"`` instead of forcing another data query.
        """
        now = _utc_now()
        if refresh:
            return SnapshotCacheLookup(snapshot=None, status="fresh", now=now)
        path = self._snapshot_path(snapshot_id)
        persisted = self._read_valid_snapshot(
            path,
            datasource=datasource,
            datasource_fingerprint=datasource_fingerprint,
            source=source,
        )
        if persisted is None:
            status: CacheMissStatus = (
                "mismatched"
                if path.is_file() or self._has_related_snapshot(datasource)
                else "fresh"
            )
            return SnapshotCacheLookup(snapshot=None, status=status, now=now)
        if (
            persisted.scope != scope
            or persisted.columns != columns
            or persisted.schema_fingerprint != schema_fingerprint
            or persisted.persist_values != persist_values
            or persisted.source_params != source_params
        ):
            return SnapshotCacheLookup(snapshot=None, status="mismatched", now=now)
        memory_key = self._memory_key(snapshot_id)
        remembered = _SNAPSHOT_MEMORY.get(memory_key)
        if (
            remembered is not None
            and remembered.created_at == persisted.created_at
            and remembered.expires_at == persisted.expires_at
        ):
            is_stale = remembered.expires_at <= now
            cached = replace(remembered, cache_status="stale" if is_stale else "cached")
            _SNAPSHOT_MEMORY[memory_key] = cached
            return SnapshotCacheLookup(
                snapshot=cached,
                status="stale" if is_stale else "fresh",
                now=now,
            )

        is_stale = persisted.expires_at <= now
        cached = replace(persisted, cache_status="stale" if is_stale else "cached")
        _SNAPSHOT_MEMORY[memory_key] = cached
        return SnapshotCacheLookup(
            snapshot=cached,
            status="stale" if is_stale else "fresh",
            now=now,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        self._ensure_directories()
        encoded = _encoded(payload)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        descriptor_open = True
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor_open = False
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except BaseException:
            if descriptor_open:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    def write_snapshot(
        self,
        snapshot: DiscoveryEvidence,
        *,
        datasource_fingerprint: str,
    ) -> None:
        """Atomically persist one snapshot while retaining live values in memory."""
        persisted_profiles = snapshot.profiles
        if not snapshot.persist_values:
            persisted_profiles = tuple(
                replace(
                    profile,
                    min_value=None,
                    max_value=None,
                    top_values=None,
                    display_samples=None,
                )
                for profile in snapshot.profiles
            )
        payload = {
            **self._metadata(
                snapshot_id=snapshot.id,
                datasource=snapshot._datasource,
                datasource_fingerprint=datasource_fingerprint,
                source=snapshot.source,
                scope=snapshot.scope,
                columns=snapshot.columns,
                schema_fingerprint=snapshot.schema_fingerprint,
                persist_values=snapshot.persist_values,
                source_params=snapshot.source_params,
            ),
            "created_at": snapshot.created_at,
            "expires_at": snapshot.expires_at,
            "profiles": persisted_profiles,
            "coverage": snapshot.coverage,
            "retained_values": snapshot.retained_values if snapshot.persist_values else (),
        }
        payload["payload_digest"] = _payload_digest(payload)
        self._write_json(self._snapshot_path(snapshot.id), payload)
        _SNAPSHOT_MEMORY[self._memory_key(snapshot.id)] = snapshot
