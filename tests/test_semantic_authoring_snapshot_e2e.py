"""Acceptance proof for the complete snapshot-backed authoring state machine."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms

_AUTHORING_COLUMNS = ("query_id", "self", "region", "log_date", "log_hour", "amount")
_PRIVATE_VALUES = (
    "private.example",
    "moon-base",
    "orbital",
    "17-Jul-2041",
    "2257632000",
    "resolved-authoring-secret",
)


def _assert_fixed_time_rules(snapshot: md.DiscoverySnapshot) -> None:
    evidence = snapshot.time_dimensions(columns=("log_date", "log_hour"))
    date_match = evidence.evidence_by_column["log_date"].deterministic_matches
    hour_match = evidence.evidence_by_column["log_hour"].deterministic_matches

    assert [
        (match.rule, match.role, match.checked, match.matched, match.failed) for match in date_match
    ] == [("date.yyyymmdd", "value", 4, 3, 1)]
    assert [
        (match.rule, match.role, match.checked, match.matched, match.failed) for match in hour_match
    ] == [("time.hour_00_23", "component_only", 4, 3, 1)]


def _inspection_and_snapshot(project_root: Path):
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=_AUTHORING_COLUMNS,
    )
    return inspection, snapshot


def _assert_private_authoring_json(
    project_root: Path,
    *,
    forbidden_scalars: tuple[object, ...] = (),
) -> None:
    evidence_root = project_root / ".marivo" / "authoring"
    paths = sorted(evidence_root.glob("**/*.json"))
    assert paths
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(payload, sort_keys=True)
        nested_keys = {
            key for item in _walk_json(payload) if isinstance(item, dict) for key in item
        }
        assert not ({"rows", "preview_rows", "resolved_secrets", "default_values"} & nested_keys)
        for profile in payload.get("profiles", []):
            assert all(
                profile[field] is None
                for field in ("min_value", "max_value", "top_values", "display_samples")
            )
        assert all(value not in text for value in _PRIVATE_VALUES)
        assert all(str(value) not in text for value in forbidden_scalars)


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


def _acquire_secret_backed_snapshot(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> md.DiscoverySnapshot:
    from marivo.datasource import backends as backends_module
    from marivo.datasource import engines
    from marivo.datasource import inspection as inspection_module
    from marivo.datasource import snapshot as snapshot_module
    from marivo.datasource.engines import DUCKDB_PROFILE

    secret = "resolved-authoring-secret"
    env_var = "AUTHORING_E2E_SECRET"
    monkeypatch.setenv(env_var, secret)
    md.register(
        md.clickhouse(
            name="secure_warehouse",
            host="clickhouse.invalid",
            database="main",
            password_env=env_var,
        ),
        project_root=project_root,
    )
    description = md.load(workspace_dir=project_root).describe("secure_warehouse")
    assert description.backend_type == "clickhouse"
    assert description.env_refs == {"password": env_var}

    resolved_passwords: list[str] = []

    def connect(_name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        password = kwargs.get("password")
        assert isinstance(password, str)
        resolved_passwords.append(password)
        return ibis.duckdb.connect(
            str(project_root / "warehouse_replica.duckdb"),
            read_only=bool(kwargs.get("read_only", False)),
        )

    secure_profile = replace(DUCKDB_PROFILE, name="clickhouse", connect=connect)
    original_require = engines.require_profile_for_backend_type

    def require_profile(backend_type: str):
        if backend_type == "clickhouse":
            return secure_profile
        return original_require(backend_type)

    monkeypatch.setattr(engines, "require_profile_for_backend_type", require_profile)
    monkeypatch.setattr(backends_module, "require_profile_for_backend_type", require_profile)
    monkeypatch.setattr(inspection_module, "require_profile_for_backend_type", require_profile)
    monkeypatch.setattr(snapshot_module, "require_profile_for_backend_type", require_profile)

    inspection = md.inspect(ms.ref.datasource("secure_warehouse"), md.table("orders"))
    secure_snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=("query_id", "amount"),
    )
    assert secure_snapshot.datasource == ms.ref.datasource("secure_warehouse")
    assert resolved_passwords == [secret, secret]
    return secure_snapshot


def test_real_duckdb_authoring_snapshot_reaches_analysis_ready_refs(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snapshot = _inspection_and_snapshot(authoring_evidence_project)

    _assert_fixed_time_rules(snapshot)
    assert snapshot.entity(columns=("query_id", "self")).status == "complete"
    assert snapshot.dimensions(columns=("region",)).status == "complete"
    assert snapshot.measures(columns=("amount",)).status == "complete"

    catalog = ms.load(workspace_dir=authoring_evidence_project)
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    assert catalog.verify(revenue.ref).status == "passed"
    preview = catalog.preview(revenue.ref, using=snapshot)
    assert preview.status == "passed"
    assert preview.rows == ({"value": 751.5},)
    readiness = catalog.readiness(refs=[revenue.ref])
    assert readiness.status in {"ready", "ready_with_warnings"}
    assert not readiness.blockers

    session = mv.session.get_or_create(name="authoring-readiness")
    session_readiness = session.catalog.readiness(refs=[revenue.ref])
    assert session_readiness.status in {"ready", "ready_with_warnings"}
    assert not session_readiness.blockers
    assert revenue.ref in session_readiness.analysis_ready_refs
    secure_snapshot = _acquire_secret_backed_snapshot(authoring_evidence_project, monkeypatch)
    assert secure_snapshot.id != snapshot.id
    _assert_private_authoring_json(
        authoring_evidence_project,
        forbidden_scalars=(preview.rows[0]["value"], "resolved-authoring-secret"),
    )


def test_complete_authoring_query_count_matrix(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ibis.backends.duckdb import Backend

    counts = {"user_data": 0, "metadata": 0}
    original_execute = Backend.execute
    original_get_schema = Backend.get_schema
    original_list_tables = Backend.list_tables

    def counted_execute(self: Backend, expr: Any, *args: Any, **kwargs: Any) -> Any:
        counts["user_data"] += 1
        return original_execute(self, expr, *args, **kwargs)

    def counted_get_schema(self: Backend, *args: Any, **kwargs: Any) -> Any:
        counts["metadata"] += 1
        return original_get_schema(self, *args, **kwargs)

    def counted_list_tables(self: Backend, *args: Any, **kwargs: Any) -> Any:
        counts["metadata"] += 1
        return original_list_tables(self, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    monkeypatch.setattr(Backend, "get_schema", counted_get_schema)
    monkeypatch.setattr(Backend, "list_tables", counted_list_tables)

    matrix: dict[str, int] = {}
    before = counts["user_data"]
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    matrix["inspect"] = counts["user_data"] - before
    metadata_after_inspect = counts["metadata"]

    scope = md.unpruned(max_rows=1000, timeout_seconds=30)
    before = counts["user_data"]
    snapshot = inspection.sample(scope=scope, columns=_AUTHORING_COLUMNS)
    matrix["first_sample"] = counts["user_data"] - before

    before = counts["user_data"]
    snapshot.entity(columns=("query_id", "self"))
    snapshot.dimensions(columns=("region",))
    snapshot.values("region", limit=10)
    snapshot.time_dimensions(columns=("log_date", "log_hour"))
    snapshot.measures(columns=("amount",))
    snapshot.relationships(snapshot, left=("query_id",), right=("query_id",))
    matrix["projections"] = counts["user_data"] - before

    before = counts["user_data"]
    inspection.sample(scope=scope, columns=_AUTHORING_COLUMNS)
    matrix["fresh_cache"] = counts["user_data"] - before

    before = counts["user_data"]
    refreshed = inspection.sample(scope=scope, columns=_AUTHORING_COLUMNS, refresh=True)
    matrix["refresh"] = counts["user_data"] - before

    catalog = ms.load(workspace_dir=authoring_evidence_project)
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    before = counts["user_data"]
    assert catalog.verify(revenue.ref).status == "passed"
    matrix["verify"] = counts["user_data"] - before

    before = counts["user_data"]
    assert catalog.preview(revenue.ref, using=refreshed).status == "passed"
    matrix["single_entity_preview"] = counts["user_data"] - before

    before = counts["user_data"]
    assert catalog.readiness(refs=[revenue.ref]).status in {"ready", "ready_with_warnings"}
    matrix["readiness"] = counts["user_data"] - before

    assert matrix == {
        "inspect": 0,
        "first_sample": 1,
        "projections": 0,
        "fresh_cache": 0,
        "refresh": 1,
        "verify": 0,
        "single_entity_preview": 1,
        "readiness": 0,
    }
    assert metadata_after_inspect > 0


def test_negative_evidence_stays_unresolved_without_recommendations(
    authoring_evidence_project: Path,
) -> None:
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=(*_AUTHORING_COLUMNS, "uncommon_date", "epoch_like"),
    )
    _assert_fixed_time_rules(snapshot)
    time_evidence = snapshot.time_dimensions(
        columns=("log_date", "log_hour", "uncommon_date", "epoch_like")
    )
    entity_evidence = snapshot.entity(columns=("query_id", "self"))
    multi_relationship = snapshot.relationships(
        snapshot,
        left=("query_id", "region"),
        right=("query_id", "region"),
    )
    right_inspection = md.inspect(
        ms.ref.datasource("warehouse_replica"),
        md.table("orders_replica"),
    )
    right_snapshot = right_inspection.sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=15),
        columns=("query_id", "region"),
    )
    cross_source_relationship = snapshot.relationships(
        right_snapshot,
        left=("query_id",),
        right=("query_id",),
    )

    cases = (
        (
            "uncommon dates",
            time_evidence,
            time_evidence.evidence_by_column["uncommon_date"].deterministic_matches == (),
        ),
        (
            "epoch-looking values",
            time_evidence,
            time_evidence.evidence_by_column["epoch_like"].deterministic_matches == (),
        ),
        (
            "multi-column entity",
            entity_evidence,
            entity_evidence.columns == ("query_id", "self")
            and "primary key" not in entity_evidence.render().lower(),
        ),
        (
            "multi-column relationship",
            multi_relationship,
            multi_relationship.status == "incomplete"
            and multi_relationship.evidence_state == "unavailable",
        ),
        (
            "cross-source scope",
            cross_source_relationship,
            cross_source_relationship.scope_comparability == "unresolved"
            and cross_source_relationship.left_snapshot_id == snapshot.id
            and cross_source_relationship.right_snapshot_id == right_snapshot.id
            and cross_source_relationship.left_scope == snapshot.scope
            and cross_source_relationship.right_scope == right_snapshot.scope
            and snapshot.datasource == ms.ref.datasource("warehouse")
            and right_snapshot.datasource == ms.ref.datasource("warehouse_replica")
            and snapshot.source == md.table("orders")
            and right_snapshot.source == md.table("orders_replica")
            and right_snapshot.id != snapshot.id,
        ),
    )
    for label, result, unresolved in cases:
        assert unresolved, label
        assert "recommend" not in result.render().lower(), label
