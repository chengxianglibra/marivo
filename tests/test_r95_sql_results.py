"""Scoped closeout fixtures never consume local qualification evidence or drivers."""

from pathlib import Path

import pytest

from marivo.datasource.capabilities import render_provider_statement
from marivo.datasource.engines import ENGINE_PROFILES
from scripts import r9_qualification_requirements as freeze
from scripts.r95_sql_audit import audit
from scripts.r95_sql_results import BACKENDS, LEDGER, ORIGINAL_IDS, _catalog, build


def _write(path: Path, payload: freeze.Json) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(freeze.encode(payload))
    return path


def _frozen() -> dict[str, freeze.Json]:
    requirements: list[freeze.Json] = []
    mappings: dict[str, freeze.Json] = {}
    sources: dict[str, freeze.Json] = {}
    for number, identity in enumerate(ORIGINAL_IDS, 1):
        target = f"R9:{identity}:all:sql-owner:audit:classification-reachability-submission"
        requirements.append({"id": target, "family": identity, "gap_owner": "R9.5"})
        mappings[identity] = [target]
        origin = f"r0-row:{number}"
        mappings[origin] = [target]
        sources[origin] = {"path": freeze.SQL, "line": number, "sha256": "original-row-digest"}
    requirements.extend({"id": f"other:{number}"} for number in range(339))
    return {
        "requirements": requirements,
        "mappings": mappings,
        "source_rows": sources,
        "candidate": {"content_sha256": "original-frozen-candidate"},
        "results": {"default": "unverified", "runtime_passed": 0, "overrides": {}},
    }


def _record(
    backend: str, category: str, owner: str, sql: str, purpose: str
) -> dict[str, freeze.Json]:
    return {
        "backend": backend,
        "boundary": "native.execute",
        "connection": 1,
        "sql": sql,
        "category": category,
        "owner": owner,
        "purpose": purpose,
        "parameter_names": [],
        "expected_sql": sql,
        "source_identity": "source:facts" if owner == "SourceSession.batches" else None,
        "expression_identity": 7 if owner == "SourceSession.batches" else None,
        "schema": [["id", "int64"]] if owner == "SourceSession.batches" else [],
        "state": "succeeded",
        "error_type": None,
        "engine_statements": [],
    }


def _receipt(backend: str, *, serialized_origins: bool = True) -> dict[str, freeze.Json]:
    catalog, _ = _catalog()
    statement = next(
        item
        for item in catalog[backend].values()
        if f"datasource.metadata.{backend}" in item.allowed_purposes
    )
    values: dict[str, object] = dict.fromkeys(statement.literal_slots, "facts")
    for name, lower, _ in statement.integer_ranges:
        values[name] = lower
    sql = render_provider_statement(
        statement,
        ENGINE_PROFILES[backend],
        values=values,
        identifiers=dict.fromkeys(statement.identifier_slots, "facts"),
    )
    records = [
        _record(
            backend,
            "governed_ibis",
            "SourceSession.batches",
            "SELECT id FROM facts",
            "analysis.members",
        ),
        _record(
            backend,
            "governed_ibis",
            "datasource.adapters._probe_backend",
            "SELECT 1",
            "datasource.connectivity",
        ),
        _record(backend, "provider", statement.statement_id, sql, f"datasource.metadata.{backend}"),
        _record(
            backend,
            "raw_sql_terminal",
            "datasource.manage.raw_sql",
            "SELECT 7 AS marker",
            "native owner witness",
        ),
        _record(
            "sqlite",
            "store",
            "analysis.materialization.store.SessionStore",
            "SELECT id FROM sessions",
            "persistence",
        ),
        _record(
            backend,
            "ibis_metadata_preparation",
            f"backends/{backend}/__init__.py:get_schema",
            "SELECT id FROM facts LIMIT 0",
            "get_schema",
        ),
    ]
    if not serialized_origins:
        for item in records:
            for key in ("expected_sql", "source_identity", "expression_identity", "schema"):
                del item[key]
            if item["category"] == "store":
                item["state"] = "submitted"
                item["boundary"] = "sqlite3.Connection.set_trace_callback"
    return {
        "environment": {
            "backend": backend,
            "profile": {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table"),
        },
        "assertions": {
            "public_probe": True,
            "public_metadata": True,
            "public_members": 2,
            "terminal_original_text": "SELECT 7 AS marker",
            "store_connection_identity": True,
            "resources_released": True,
            "business_methods_replayed": False,
        },
        "submissions": freeze.checked(records),
        "classification_counts": {
            "governed_ibis": 2,
            "provider": 1,
            "raw_sql_terminal": 1,
            "store": 1,
            "ibis_metadata_preparation": 1,
        },
        "unknown_submissions": 0,
        "boundary": "synthetic native SQL witness input",
    }


def _provider_receipt(backend: str, identity: str, purpose: str) -> dict[str, freeze.Json]:
    catalog, _ = _catalog()
    statement = catalog[backend][identity]
    record = _record(backend, "provider", identity, statement.template, purpose)
    if backend == "clickhouse":
        record["boundary"] = "clickhouse_connect.query"
        record["parameter_names"] = ["id", "user"]
    return {
        "environment": {"backend": backend},
        "assertions": {"scoped_credentials": True, "credentials_absent_from_sql": True}
        if backend == "duckdb"
        else {"owned_control": True},
        "submissions": [record],
        "classification_counts": {"provider": 1},
        "unknown_submissions": 0,
        "boundary": "synthetic native provider witness input",
    }


@pytest.fixture
def closeout_inputs(tmp_path: Path) -> tuple[Path, list[Path], dict[str, freeze.Json]]:
    module = tmp_path / "marivo" / "provenance.py"
    module.parent.mkdir()
    module.write_text("value = 'SELECT 1'\n")
    ledger = tmp_path / LEDGER
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes((freeze.ROOT / LEDGER).read_bytes())
    report = freeze.checked(audit(tmp_path).json())
    audit_path = _write(tmp_path / "audit.json", report)
    receipts = [
        _write(
            tmp_path / f"native-{backend}.json",
            _receipt(backend, serialized_origins=backend not in ("postgres", "mysql", "trino")),
        )
        for backend in BACKENDS
    ]
    receipts.append(
        _write(
            tmp_path / "native-clickhouse-control.json",
            _provider_receipt(
                "clickhouse",
                "clickhouse.analysis.cancel_owned_query",
                "analysis.cancel_owned_query",
            ),
        )
    )
    receipts.append(
        _write(
            tmp_path / "native-duckdb-http.json",
            _provider_receipt("duckdb", "duckdb.http_secret_bearer", "datasource.http_credentials"),
        )
    )
    return audit_path, receipts, _frozen()


def test_scoped_closeout_preserves_the_freeze_and_original_native_capture(
    tmp_path: Path, closeout_inputs: tuple[Path, list[Path], dict[str, freeze.Json]]
) -> None:
    audit_path, receipts, frozen = closeout_inputs
    before = freeze.encode(frozen)
    result = build(audit_path, receipts, root=tmp_path, frozen=frozen)
    assert result == build(audit_path, list(reversed(receipts)), root=tmp_path, frozen=frozen)
    assert freeze.encode(frozen) == before
    assert result["status"] == "scoped_implementation_closed"
    original = freeze.obj(result["original_freeze"])
    assert original["requirement_count"] == 394
    assert original["candidate"] == frozen["candidate"]
    assert original["original_results"] == frozen["results"]
    rows = [freeze.obj(item) for item in freeze.arr(result["original_sql_mappings"])]
    assert [item["id"] for item in rows] == list(ORIGINAL_IDS)
    assert all(item["original_source_rows"] for item in rows)
    assert next(item for item in rows if item["id"] == "AN33")["physical_retirement"] is True
    control = freeze.obj(result["appended_control"])
    assert control["id"] == "DS23"
    assert freeze.obj(control["statement"])["allowed_purposes"] == ["analysis.cancel_owned_query"]
    witnesses = [freeze.obj(item) for item in freeze.arr(result["native_receipts"])]
    assert [item["backend"] for item in witnesses if item["public_owner_witness"] is True] == list(
        BACKENDS
    )
    sources = {path.relative_to(tmp_path).as_posix(): path for path in receipts}
    for item in witnesses:
        path = sources[str(freeze.obj(item["source"])["path"])]
        assert item["original_receipt"] == freeze.read(path)
        assert freeze.obj(item["native_origin_binding"])["serialized_optional_origin_fields"] is (
            item["backend"] not in ("postgres", "mysql", "trino")
        )
    scope = freeze.obj(result["scope"])
    assert scope["full_r9_qualification"] is False
    assert scope["all_profile_final_candidate_qualification"] is False
    assert len(freeze.arr(control["native_evidence"])) == 1
    assert len(freeze.arr(result["ds15_scoped_http_native_evidence"])) == 1


def test_closeout_requires_the_complete_six_backend_witness_set(
    tmp_path: Path, closeout_inputs: tuple[Path, list[Path], dict[str, freeze.Json]]
) -> None:
    audit_path, receipts, frozen = closeout_inputs
    with pytest.raises(ValueError, match="each of the six backends"):
        build(audit_path, receipts[1:], root=tmp_path, frozen=frozen)


def test_historical_supplement_preserves_original_candidate_and_time(
    tmp_path: Path, closeout_inputs: tuple[Path, list[Path], dict[str, freeze.Json]]
) -> None:
    audit_path, receipts, frozen = closeout_inputs
    package = tmp_path / "original-provider-package"
    original: dict[str, freeze.Json] = {
        "candidate_sha256": "historical-candidate",
        "started_at": "2026-10-04T00:00:00Z",
        "finished_at": "2026-10-04T00:01:00Z",
        "exit_code": 0,
    }
    run = _write(package / "run.json", original)
    attachment = package / "original-receipt.bin"
    attachment.write_bytes(b"original provider/control/raw_sql artifact")
    result = build(audit_path, receipts, [package], root=tmp_path, frozen=frozen)
    supplement = freeze.obj(freeze.arr(result["historical_supplements"])[0])
    metadata = freeze.obj(supplement["original_metadata"])
    assert metadata[run.relative_to(tmp_path).as_posix()] == original
    files = [freeze.obj(item) for item in freeze.arr(supplement["files"])]
    assert {item["sha256"] for item in files} == {
        freeze.digest(run.read_bytes()),
        freeze.digest(attachment.read_bytes()),
    }
    assert freeze.read(run) == original
