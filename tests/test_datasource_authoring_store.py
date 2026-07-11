"""Privacy-aware project-local authoring snapshot store contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.cli import init_project
from marivo.datasource.inspection import SourceInspection


class _QuerySpy:
    def __init__(self) -> None:
        self.user_data_queries = 0


@pytest.fixture(autouse=True)
def clear_snapshot_memory() -> None:
    from marivo.datasource import authoring_store

    authoring_store._SNAPSHOT_MEMORY.clear()


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "store-test"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def query_spy(monkeypatch: pytest.MonkeyPatch) -> _QuerySpy:
    from ibis.backends.duckdb import Backend

    spy = _QuerySpy()
    original_execute = Backend.execute

    def counted_execute(self: Backend, expr: object, *args: object, **kwargs: object) -> object:
        spy.user_data_queries += 1
        return original_execute(self, expr, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    return spy


@pytest.fixture
def inspection(project_root: Path) -> SourceInspection:
    path = project_root / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql(
        "CREATE TABLE orders (email VARCHAR, amount DOUBLE, region VARCHAR, dt VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "('alice@example.com', 10.0, 'east', '2026-07-10'), "
        "('bob@example.com', 20.0, 'west', '2026-07-10'), "
        "('carol@example.com', 30.0, 'east', '2026-07-11')"
    )
    backend.raw_sql("CREATE TABLE orders_copy AS SELECT * FROM orders")
    backend.disconnect()
    md.register(md.duckdb(name="warehouse", path=str(path)), project_root=project_root)
    return md.inspect(md.ref("datasource.warehouse"), md.table("orders"))


def _snapshot_path(project_root: Path, snapshot_id: str) -> Path:
    return project_root / ".marivo" / "authoring" / "snapshots" / f"{snapshot_id}.json"


def _payload_digest(payload: dict[str, object]) -> str:
    digest_payload = {key: value for key, value in payload.items() if key != "payload_digest"}
    encoded = json.dumps(
        digest_payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rewrite_payload(path: Path, payload: dict[str, object], *, resign: bool) -> None:
    if resign:
        payload["payload_digest"] = _payload_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_snapshot_cache_omits_values_and_credentials_by_default(
    project_root: Path,
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource import snapshot as snapshot_module

    datasource = snapshot_module._store.load_one("warehouse", project_root=project_root)
    assert datasource is not None
    secret = "resolved-credential-must-not-be-written"
    monkeypatch.setenv("WAREHOUSE_PASSWORD", secret)
    monkeypatch.setattr(
        snapshot_module._store,
        "load_one",
        lambda *_args, **_kwargs: replace(
            datasource,
            env_refs={"password": "WAREHOUSE_PASSWORD"},
        ),
    )
    monkeypatch.setattr(
        snapshot_module._backends,
        "build_backend",
        lambda *_args, **_kwargs: ibis.duckdb.connect(str(project_root / "warehouse.duckdb")),
    )

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=100, timeout_seconds=30),
        columns=("email", "amount"),
        persist_values=False,
        refresh=True,
    )

    path = _snapshot_path(project_root, snapshot.id)
    payload = path.read_text(encoding="utf-8")
    assert "alice@example.com" not in payload
    assert secret not in payload
    assert "WAREHOUSE_PASSWORD" not in payload
    assert "password" not in payload
    assert "rows" not in json.loads(payload)
    assert json.loads(payload)["payload_digest"] == _payload_digest(json.loads(payload))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.parent.stat().st_mode & 0o777 == 0o700


def test_value_policy_changes_identity_and_persists_only_bounded_values(
    project_root: Path,
    inspection: SourceInspection,
) -> None:
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    private = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=False,
    )
    persisted = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=True,
        refresh=True,
    )

    assert private.id != persisted.id
    assert persisted.profiles[0].top_values is not None
    assert len(persisted.profiles[0].top_values) <= 10
    payload = json.loads(_snapshot_path(project_root, persisted.id).read_text(encoding="utf-8"))
    profile = payload["profiles"][0]
    assert len(profile["top_values"]) <= 10
    assert len(profile["display_samples"]) <= 10
    assert set(profile) >= {"min_value", "max_value", "top_values", "display_samples"}
    assert "rows" not in payload


def test_memory_hit_reuses_live_values_without_a_query(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(scope=scope, columns=("region",), refresh=True)
    second = inspection.sample(scope=scope, columns=("region",))

    assert query_spy.user_data_queries == 1
    assert first.cache_status == "fresh"
    assert second.cache_status == "cached"
    assert second.value_evidence_state == "available"
    assert second.profiles[0].display_samples == first.profiles[0].display_samples


def test_private_disk_reload_marks_values_unavailable_without_querying(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    from marivo.datasource import authoring_store

    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    inspection.sample(scope=scope, columns=("region",), persist_values=False, refresh=True)
    authoring_store._SNAPSHOT_MEMORY.clear()

    reloaded = inspection.sample(scope=scope, columns=("region",), persist_values=False)

    assert query_spy.user_data_queries == 1
    assert reloaded.cache_status == "cached"
    assert reloaded.value_evidence_state == "value_evidence_unavailable"
    assert reloaded.profiles[0].min_value is None
    assert reloaded.profiles[0].max_value is None
    assert reloaded.profiles[0].top_values is None
    assert reloaded.profiles[0].display_samples is None


def test_private_disk_reload_rejects_tampered_value_fields(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    from marivo.datasource import authoring_store

    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=False,
        refresh=True,
    )
    path = _snapshot_path(project_root, first.id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    profile = payload["profiles"][0]
    profile["min_value"] = "private-min"
    profile["max_value"] = "private-max"
    profile["top_values"] = [["private-top", 3]]
    profile["display_samples"] = ["private-display"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    authoring_store._SNAPSHOT_MEMORY.clear()

    reloaded = inspection.sample(scope=scope, columns=("region",), persist_values=False)

    assert query_spy.user_data_queries == 2
    assert reloaded.cache_status == "mismatched"
    assert reloaded.value_evidence_state == "available"
    assert reloaded.profiles[0] == first.profiles[0]


def test_opted_in_disk_reload_preserves_only_bounded_values_without_querying(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    from marivo.datasource import authoring_store

    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=True,
        refresh=True,
    )
    authoring_store._SNAPSHOT_MEMORY.clear()

    reloaded = inspection.sample(scope=scope, columns=("region",), persist_values=True)

    assert query_spy.user_data_queries == 1
    assert reloaded.cache_status == "cached"
    assert reloaded.value_evidence_state == "available"
    assert reloaded.profiles[0].min_value == first.profiles[0].min_value
    assert reloaded.profiles[0].max_value == first.profiles[0].max_value
    assert reloaded.profiles[0].top_values == first.profiles[0].top_values
    assert reloaded.profiles[0].display_samples == first.profiles[0].display_samples


def test_persistence_policy_mismatch_reacquires_once_without_refresh(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    private = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=False,
    )

    persisted = inspection.sample(
        scope=scope,
        columns=("region",),
        persist_values=True,
    )

    assert query_spy.user_data_queries == 2
    assert persisted.id != private.id
    assert persisted.cache_status == "mismatched"
    assert persisted.persist_values is True


def test_evidence_format_mismatch_reacquires_once_and_repairs_artifact(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    from marivo.datasource import authoring_store

    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(scope=scope, columns=("region",), refresh=True)
    path = _snapshot_path(project_root, first.id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["evidence_format_version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    authoring_store._SNAPSHOT_MEMORY.clear()

    repaired = inspection.sample(scope=scope, columns=("region",))

    assert query_spy.user_data_queries == 2
    assert repaired.id == first.id
    assert repaired.cache_status == "mismatched"
    repaired_payload = json.loads(path.read_text(encoding="utf-8"))
    assert repaired_payload["evidence_format_version"] == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown_rule",
        "impossible_match_counts",
        "profile_column_mismatch",
        "false_exhaustive_rows",
    ],
)
def test_query_free_snapshot_lookup_rejects_resigned_invalid_payloads(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
    tamper: str,
) -> None:
    from marivo.datasource import authoring_store

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("dt",),
        refresh=True,
    )
    path = _snapshot_path(project_root, snapshot.id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    profile = payload["profiles"][0]
    if tamper == "unknown_rule":
        profile["deterministic_matches"].append(
            {"rule": "time.seventh_rule", "checked": 1, "matched": 1, "failed": 0, "role": "value"}
        )
    elif tamper == "impossible_match_counts":
        match = profile["deterministic_matches"][0]
        match["matched"] = match["checked"] + 1
    elif tamper == "profile_column_mismatch":
        profile["name"] = "different_column"
    else:
        payload["coverage"]["scope_exhaustion"] = "exhaustive"
        payload["coverage"]["scope_exactness"] = "scope_exact"
    _rewrite_payload(path, payload, resign=True)
    authoring_store._SNAPSHOT_MEMORY.clear()
    from marivo.datasource import store as datasource_store

    datasource = datasource_store.load_one("warehouse", project_root=project_root)
    assert datasource is not None

    valid = authoring_store.AuthoringStore(project_root).valid_snapshots(
        datasource=inspection.datasource,
        datasource_fingerprint=authoring_store.datasource_spec_fingerprint(datasource),
        source=inspection.source,
    )

    assert valid == ()
    assert query_spy.user_data_queries == 1


@pytest.mark.parametrize("tamper", ["payload_mutation", "digest_missing"])
def test_query_free_snapshot_lookup_rejects_digest_failure(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
    tamper: str,
) -> None:
    from marivo.datasource import authoring_store

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=100, timeout_seconds=30),
        columns=("region",),
        refresh=True,
    )
    path = _snapshot_path(project_root, snapshot.id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "payload_mutation":
        payload["coverage"]["retained_row_count"] = 0
    else:
        payload.pop("payload_digest", None)
    _rewrite_payload(path, payload, resign=False)
    authoring_store._SNAPSHOT_MEMORY.clear()
    from marivo.datasource import store as datasource_store

    datasource = datasource_store.load_one("warehouse", project_root=project_root)
    assert datasource is not None

    valid = authoring_store.AuthoringStore(project_root).valid_snapshots(
        datasource=inspection.datasource,
        datasource_fingerprint=authoring_store.datasource_spec_fingerprint(datasource),
        source=inspection.source,
    )

    assert valid == ()
    assert query_spy.user_data_queries == 1


def test_expired_snapshot_reacquires_once_and_reports_stale(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource import authoring_store

    now = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(authoring_store, "_utc_now", lambda: now)
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(scope=scope, columns=("region",), refresh=True)
    monkeypatch.setattr(authoring_store, "_utc_now", lambda: now + timedelta(hours=25))

    expired = inspection.sample(scope=scope, columns=("region",))

    assert query_spy.user_data_queries == 2
    assert expired.id == first.id
    assert expired.cache_status == "stale"
    assert expired.created_at == now + timedelta(hours=25)
    assert expired.expires_at == now + timedelta(hours=49)


@pytest.mark.parametrize("change", ["schema", "source", "scope", "columns", "datasource"])
def test_identity_inputs_invalidate_cache_and_report_mismatch(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
    change: str,
) -> None:
    scope = md.unpruned(max_rows=2, timeout_seconds=30)
    first = inspection.sample(scope=scope, columns=("region",), refresh=True)
    changed_inspection = inspection
    changed_scope = scope
    changed_columns = ("region",)
    if change == "schema":
        first_column = inspection.schema[0]
        changed_inspection = replace(
            inspection,
            schema=(
                replace(first_column, nullable=not first_column.nullable),
                *inspection.schema[1:],
            ),
        )
    elif change == "source":
        changed_inspection = replace(inspection, source=md.table("orders_copy"))
    elif change == "scope":
        changed_scope = md.unpruned(max_rows=3, timeout_seconds=30)
    elif change == "columns":
        changed_columns = ("amount",)
    else:
        copied_path = project_root / "warehouse-copy.duckdb"
        shutil.copy2(project_root / "warehouse.duckdb", copied_path)
        md.register(
            md.duckdb(name="warehouse", path=str(copied_path)),
            project_root=project_root,
        )

    changed = changed_inspection.sample(
        scope=changed_scope,
        columns=changed_columns,
    )

    assert query_spy.user_data_queries == 2
    assert changed.id != first.id
    assert changed.cache_status == "mismatched"


def test_refresh_bypasses_both_caches_and_executes_exactly_once(
    inspection: SourceInspection,
    query_spy: _QuerySpy,
) -> None:
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    first = inspection.sample(scope=scope, columns=("region",), refresh=True)
    refreshed = inspection.sample(scope=scope, columns=("region",), refresh=True)

    assert query_spy.user_data_queries == 2
    assert refreshed.id == first.id
    assert refreshed.cache_status == "fresh"
    assert refreshed.created_at >= first.created_at


def test_snapshot_write_is_same_directory_atomic_and_owner_only(
    project_root: Path,
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource import authoring_store

    replacements: list[tuple[Path, Path, int]] = []
    original_replace = os.replace

    def checked_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacements.append((source_path, destination_path, source_path.stat().st_mode & 0o777))
        original_replace(source, destination)

    monkeypatch.setattr(authoring_store.os, "replace", checked_replace)
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=100, timeout_seconds=30),
        columns=("region",),
        refresh=True,
    )

    assert len(replacements) == 1
    temporary, destination, mode = replacements[0]
    assert temporary.parent == destination.parent
    assert destination == _snapshot_path(project_root, snapshot.id)
    assert mode == 0o600
    assert destination.stat().st_mode & 0o777 == 0o600


def test_failed_atomic_write_is_not_published_to_memory(
    project_root: Path,
    inspection: SourceInspection,
    query_spy: _QuerySpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource import authoring_store

    original_replace = os.replace
    replacement_attempts = 0

    def fail_once_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal replacement_attempts
        replacement_attempts += 1
        if replacement_attempts == 1:
            raise OSError("injected atomic write failure")
        original_replace(source, destination)

    monkeypatch.setattr(authoring_store.os, "replace", fail_once_replace)
    scope = md.unpruned(max_rows=100, timeout_seconds=30)

    with pytest.raises(OSError, match="injected atomic write failure"):
        inspection.sample(scope=scope, columns=("region",), refresh=True)

    recovered = inspection.sample(scope=scope, columns=("region",))

    assert query_spy.user_data_queries == 2
    assert replacement_attempts == 2
    assert recovered.cache_status == "fresh"
    assert _snapshot_path(project_root, recovered.id).is_file()


def test_repository_and_scaffold_cover_the_whole_state_root(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    ignored = (repository_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    init_project(project_dir=tmp_path)

    assert ".marivo/" in ignored
    assert (tmp_path / ".marivo").is_dir()
