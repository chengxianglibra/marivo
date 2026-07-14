"""Bounded authoring snapshot acquisition contract tests."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast, get_type_hints

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.engines.duckdb import PROFILE as DUCKDB_PROFILE
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.metadata import PartitionMetadata
from marivo.datasource.snapshot import DeterministicMatch, DiscoverySnapshot
from marivo.datasource.source import AuthoringScope


class _QuerySpy:
    def __init__(self) -> None:
        self.user_data_queries = 0
        self.user_data_sql: list[str] = []


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "acquisition-test"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def query_spy(monkeypatch: pytest.MonkeyPatch) -> _QuerySpy:
    from ibis.backends.duckdb import Backend

    spy = _QuerySpy()
    original_execute = Backend.execute

    def counted_execute(self: Backend, expr: object, *args: object, **kwargs: object) -> object:
        spy.user_data_queries += 1
        spy.user_data_sql.append(str(self.compile(expr)))
        return original_execute(self, expr, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    return spy


@pytest.fixture
def inspection(project_root: Path) -> SourceInspection:
    path = project_root / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql(
        "CREATE TABLE orders (order_id VARCHAR, amount DOUBLE, dt VARCHAR, ignored VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "('o-1', 10.0, '2026-07-10', 'x'), "
        "('o-2', 0.0, '2026-07-10', 'y'), "
        "('o-3', -5.0, '2026-07-11', 'z')"
    )
    backend.disconnect()
    md.register(md.duckdb(name="warehouse", path=str(path)), project_root=project_root)
    return md.inspect(md.ref("datasource.warehouse"), md.table("orders"))


def test_sample_return_annotation_is_runtime_resolvable() -> None:
    assert get_type_hints(SourceInspection.sample)["return"] is DiscoverySnapshot
    assert inspect.signature(SourceInspection.sample, eval_str=True).return_annotation is (
        DiscoverySnapshot
    )


def test_snapshot_exposes_projection_methods_and_affordances(
    inspection: SourceInspection,
) -> None:
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1, timeout_seconds=30),
        columns=("order_id",),
    )

    projection_names = (
        "entity",
        "dimensions",
        "values",
        "time_dimensions",
        "measures",
        "relationships",
    )
    assert all(hasattr(DiscoverySnapshot, name) for name in projection_names)
    rendered = snapshot.render()
    assert all(f".{name}(" in rendered for name in projection_names)
    assert ".contract()" in rendered
    contract = snapshot.contract()
    assert contract.subject_refs[0] == "datasource.warehouse"
    assert [(state.id, state.evidence_ids) for state in contract.states] == [
        ("evidence.acquired", (snapshot.id,)),
        ("scope.explicit", ()),
    ]


def test_sample_executes_one_query_with_limit_plus_one(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount"),
        refresh=True,
    )

    assert query_spy.user_data_queries == 1
    assert "LIMIT 3" in query_spy.user_data_sql[0].upper()

    snapshot.entity(columns=("order_id",))
    snapshot.dimensions(columns=("order_id",))
    snapshot.values("order_id", limit=2)
    snapshot.time_dimensions(columns=("order_id",))
    snapshot.measures(columns=("amount",))
    snapshot.relationships(snapshot, left=("order_id",), right=("order_id",))

    assert query_spy.user_data_queries == 1
    assert "RANDOM" not in query_spy.user_data_sql[0].upper()
    assert '"ignored"' not in query_spy.user_data_sql[0]
    assert snapshot.coverage.observed_row_count == 3
    assert snapshot.coverage.retained_row_count == 2
    assert snapshot.coverage.scope_exhaustion == "truncated"
    assert snapshot.coverage.scope_exactness == "sample_only"
    assert snapshot.coverage.sampling_method == "first_rows_limit"
    assert snapshot.coverage.pushed_predicate == ()
    assert snapshot.columns == ("order_id", "amount")


def test_sample_pushes_every_partition_predicate_and_profiles_retained_rows(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    partitioned = replace(
        inspection,
        partitioning=replace(
            inspection.partitioning,
            state="known",
            fields=(PartitionMetadata(name="dt", type="varchar"),),
        ),
    )

    snapshot = partitioned.sample(
        scope=md.partition({"dt": "2026-07-10"}, max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount"),
    )

    assert query_spy.user_data_queries == 1
    sql = query_spy.user_data_sql[0]
    assert '"dt" = ' in sql
    assert "2026-07-10" in sql
    assert "LIMIT 3" in sql.upper()
    assert snapshot.coverage.pushed_predicate == (("dt", "2026-07-10"),)
    assert snapshot.coverage.observed_row_count == 2
    assert snapshot.coverage.retained_row_count == 2
    assert snapshot.coverage.scope_exhaustion == "exhaustive"
    assert snapshot.coverage.scope_exactness == "scope_exact"

    by_name = {profile.name: profile for profile in snapshot.profiles}
    assert by_name["order_id"].sample_distinct_count == 2
    assert by_name["order_id"].scope_distinct_count == 2
    assert by_name["amount"].zero_count == 1
    assert by_name["amount"].negative_count == 0


def test_unsupported_timeout_blocks_before_execution(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    unsupported = replace(
        inspection,
        execution_capabilities=replace(
            inspection.execution_capabilities,
            timeout_enforced=False,
        ),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        unsupported.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    assert exc_info.value.effect_observed is not None
    assert exc_info.value.effect_observed.query_executed is False
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "configure"
    assert exc_info.value.repair.help_target.canonical_id == "inspect"
    assert exc_info.value.repair.preserves_evidence is False
    assert query_spy.user_data_queries == 0


def test_unknown_column_blocks_before_backend_connection(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("missing",),
        )

    assert exc_info.value.effect_observed is not None
    assert exc_info.value.effect_observed.query_executed is False
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.help_target.canonical_id == "inspect"
    assert exc_info.value.repair.preserves_evidence is True


@pytest.mark.parametrize(
    "columns",
    [
        ["order_id"],
        "order_id",
        ("order_id", 1),
    ],
)
def test_columns_must_be_exact_tuple_of_strings_before_connection(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
    columns: object,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    with pytest.raises(TypeError, match=r"columns must be tuple\[str, \.\.\.\]"):
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=cast("tuple[str, ...]", columns),
        )


@pytest.mark.parametrize(
    "scope",
    [
        md.UnprunedScope(max_rows=0, timeout_seconds=1),
        md.UnprunedScope(max_rows=True, timeout_seconds=1),
        md.UnprunedScope(max_rows=1, timeout_seconds=0),
        md.UnprunedScope(max_rows=1, timeout_seconds=False),
        md.PartitionScope(values=(), max_rows=1, timeout_seconds=1),
        md.PartitionScope(values=(("", "2026-07-10"),), max_rows=1, timeout_seconds=1),
        md.PartitionScope(values=(("dt", ""),), max_rows=1, timeout_seconds=1),
        md.PartitionScope(
            values=cast("tuple[tuple[str, str], ...]", (("dt", 20260710),)),
            max_rows=1,
            timeout_seconds=1,
        ),
    ],
)
def test_direct_scope_values_are_revalidated_before_connection(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
    scope: AuthoringScope,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    with pytest.raises((TypeError, ValueError)):
        inspection.sample(scope=scope, columns=("order_id",))


@pytest.mark.parametrize("transform", ["identity", "day"])
def test_any_transformed_partition_blocks_even_when_capability_claims_support(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
    transform: str,
) -> None:
    transformed = replace(
        inspection,
        partitioning=replace(
            inspection.partitioning,
            state="known",
            fields=(PartitionMetadata(name="dt", type="varchar", transform=transform),),
        ),
        execution_capabilities=replace(
            inspection.execution_capabilities,
            transformed_partition_supported=True,
        ),
    )
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        transformed.sample(
            scope=md.partition({"dt": "2026-07-10"}, max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    assert exc_info.value.effect_observed is not None
    assert exc_info.value.effect_observed.query_executed is False
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "configure"
    assert exc_info.value.repair.help_target.canonical_id == "inspect"
    assert exc_info.value.repair.preserves_evidence is False


def test_timeout_setup_failure_reports_no_query_executed(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def failing_timeout(_backend: object, _seconds: int) -> Iterator[None]:
        raise RuntimeError("interrupt unavailable")
        yield

    monkeypatch.setattr(
        "marivo.datasource.snapshot.require_profile_for_backend_type",
        lambda _backend_type: replace(
            DUCKDB_PROFILE,
            authoring_timeout=failing_timeout,
        ),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    assert exc_info.value.effect_observed is not None
    assert exc_info.value.effect_observed.query_executed is False
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "configure"
    assert exc_info.value.repair.help_target.canonical_id == "inspect"
    assert exc_info.value.repair.preserves_evidence is False
    assert query_spy.user_data_queries == 0


def test_typed_csv_acquisition_uses_authored_schema(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = project_root / "warehouse.duckdb"
    ibis.duckdb.connect(str(path)).disconnect()
    md.register(md.duckdb(name="warehouse", path=str(path)), project_root=project_root)
    csv_path = project_root / "orders.csv"
    csv_path.write_text("order_id,ignored\n1,x\n2,y\n")
    inspection = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv(str(csv_path), schema={"order_id": "VARCHAR", "ignored": "VARCHAR"}),
    )

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=("order_id",),
    )

    assert query_spy.user_data_queries == 1
    assert snapshot.profiles[0].display_samples == ("1", "2")
    assert snapshot.profiles[0].min_length == 1


def test_profiles_preserve_integer_range(
    project_root: Path,
) -> None:
    path = project_root / "events.duckdb"
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql("CREATE TABLE events (event_hour INTEGER, event_count INTEGER)")
    backend.raw_sql("INSERT INTO events VALUES (1, 10), (23, 20)")
    backend.disconnect()
    md.register(md.duckdb(name="events", path=str(path)), project_root=project_root)
    inspection = md.inspect(md.ref("datasource.events"), md.table("events"))

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=("event_hour", "event_count"),
    )

    by_name = {profile.name: profile for profile in snapshot.profiles}
    assert by_name["event_count"].min_value == 10
    assert isinstance(by_name["event_count"].min_value, int)
    assert by_name["event_hour"].deterministic_matches == (
        DeterministicMatch(
            rule="time.hour_00_23",
            checked=2,
            matched=2,
            failed=0,
            role="component_only",
        ),
    )


def test_parquet_source_projection_remains_expression_only(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = project_root / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql("CREATE TABLE source (order_id VARCHAR, amount DOUBLE, ignored VARCHAR)")
    backend.raw_sql("INSERT INTO source VALUES ('o-1', 10.0, 'x')")
    parquet_path = project_root / "orders.parquet"
    backend.raw_sql(f"COPY source TO '{parquet_path}' (FORMAT PARQUET)")
    backend.disconnect()
    md.register(md.duckdb(name="warehouse", path=str(path)), project_root=project_root)
    source = md.parquet(str(parquet_path), columns=("order_id", "amount"))
    inspection = md.inspect(md.ref("datasource.warehouse"), source)

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=("order_id",),
    )

    assert tuple(column.name for column in inspection.schema) == ("order_id", "amount")
    assert snapshot.columns == ("order_id",)
    assert query_spy.user_data_queries == 1
    assert "ignored" not in query_spy.user_data_sql[0]
