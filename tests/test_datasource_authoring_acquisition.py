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
import marivo.semantic as ms
from marivo.datasource.engines.duckdb import PROFILE as DUCKDB_PROFILE
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata
from marivo.datasource.snapshot import DeterministicMatch, DiscoverySnapshot
from marivo.datasource.source import AuthoringScope
from marivo.render import AgentResult


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
        "CREATE TABLE orders (order_id VARCHAR, amount DOUBLE, dt VARCHAR, ts TIMESTAMP, ignored VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "('o-1', 10.0, '2026-07-10', TIMESTAMP '2026-07-10 01:00:00', 'x'), "
        "('o-2', 0.0, '2026-07-10', TIMESTAMP '2026-07-10 12:00:00', 'y'), "
        "('o-3', -5.0, '2026-07-11', TIMESTAMP '2026-07-11 01:00:00', 'z')"
    )
    backend.disconnect()
    md.register(md.duckdb(name="warehouse", path=str(path)), project_root=project_root)
    return md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))


def _projected_orders_inspection(inspection: SourceInspection) -> SourceInspection:
    """Inspect the real projected schema and supply captured partition evidence."""
    projected = md.inspect(
        inspection.datasource,
        md.table(
            "orders",
            columns={
                "event_day": md.source_column("dt", data_type="string"),
                "order_key": md.source_column("order_id", data_type="string"),
                "value": md.source_column("amount", data_type="float64"),
            },
        ),
    )
    return replace(
        projected,
        partitioning=replace(
            projected.partitioning,
            state="known",
            fields=(PartitionMetadata(name="event_day", type="string"),),
        ),
    )


def test_sample_return_annotation_is_runtime_resolvable() -> None:
    assert get_type_hints(SourceInspection.sample)["return"] is DiscoverySnapshot
    assert inspect.signature(SourceInspection.sample, eval_str=True).return_annotation is (
        DiscoverySnapshot
    )


def test_snapshot_exposes_generic_evidence_without_semantic_projections(
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
    assert all(not hasattr(DiscoverySnapshot, name) for name in projection_names)
    rendered = snapshot.render()
    assert all(f".{name}(" not in rendered for name in projection_names)
    assert "selected columns: order_id" in rendered
    assert ".profiles" in rendered
    assert ".retained_values" in rendered
    assert "reacquire boundary:" in rendered
    assert ".contract()" in rendered

    contract = snapshot.contract()
    assert contract.columns == ("order_id",)
    assert contract.retained_row_count == 1
    assert contract.value_evidence_state == "value_evidence_unavailable"
    assert contract.retained_values_shape == "dict_rows"
    assert ".retained_values" not in contract.available_reads
    assert "persist_values=True" in contract.render()


def test_persisted_snapshot_retained_values_are_named_rows(
    inspection: SourceInspection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1, timeout_seconds=30),
        columns=("order_id", "amount"),
        persist_values=True,
        refresh=True,
    )

    assert snapshot.retained_values == (({"order_id": "o-1", "amount": 10.0}),)
    assert snapshot.retained_values[0]["order_id"] == "o-1"
    contract = snapshot.contract()
    assert isinstance(contract, AgentResult)
    assert "DiscoverySnapshotContract" in repr(contract)
    rendered = contract.render()
    assert len(rendered.encode("utf-8")) <= 8192
    contract.show()
    assert capsys.readouterr().out == rendered + "\n"
    assert ".retained_values" in contract.available_reads


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
    assert snapshot.coverage.pushed_predicate == (("eq", "dt", "2026-07-10"),)
    assert snapshot.coverage.observed_row_count == 2
    assert snapshot.coverage.retained_row_count == 2
    assert snapshot.coverage.scope_exhaustion == "exhaustive"
    assert snapshot.coverage.scope_exactness == "scope_exact"

    by_name = {profile.name: profile for profile in snapshot.profiles}
    assert by_name["order_id"].sample_distinct_count == 2
    assert by_name["order_id"].scope_distinct_count == 2
    assert by_name["amount"].zero_count == 1
    assert by_name["amount"].negative_count == 0


def test_projected_sample_executes_generated_relation_once_with_outer_scope(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    projected = _projected_orders_inspection(inspection)

    snapshot = projected.sample(
        scope=md.partition(
            {"event_day": "2026-07-10"},
            max_rows=2,
            timeout_seconds=30,
        ),
        columns=("order_key", "value"),
        refresh=True,
    )

    assert query_spy.user_data_queries == 1
    sql = query_spy.user_data_sql[0]
    inner_projection = (
        'SELECT "dt" AS "event_day", "order_id" AS "order_key", "amount" AS "value" FROM "orders"'
    )
    assert inner_projection in sql
    assert sql.index(inner_projection) < sql.index(" WHERE ")
    assert '"event_day" = ' in sql
    assert "LIMIT 3" in sql.upper()
    assert snapshot.columns == ("order_key", "value")
    assert snapshot.coverage.pushed_predicate == (("eq", "event_day", "2026-07-10"),)
    assert snapshot.coverage.observed_row_count == 2


def test_projected_sample_missing_sql_capability_is_structured_before_execution(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LookupOnlyBackend:
        name = "duckdb"

        def __init__(self) -> None:
            self.disconnected = False

        def table(self, name: str, **_kwargs: object) -> object:
            return ibis.table({"order_id": "string"}, name=name)

        def disconnect(self) -> None:
            self.disconnected = True

    backend = LookupOnlyBackend()
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: backend,
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        _projected_orders_inspection(inspection).sample(
            scope=md.partition(
                {"event_day": "2026-07-10"},
                max_rows=2,
                timeout_seconds=30,
            ),
            columns=("order_key",),
            refresh=True,
        )

    error = exc_info.value
    assert error.code == "acquisition_source_failed"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert error.received == "DatasourceSourceCapabilityError"
    assert backend.disconnected is True


def test_projected_sample_unknown_physical_identifier_is_execution_failure(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    projected = replace(
        inspection,
        source=md.table(
            "orders",
            columns={"missing_alias": md.source_column("catalog.hidden", data_type="string")},
        ),
        schema=(ColumnMetadata("missing_alias", "string", None, None, 1),),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        projected.sample(
            scope=md.unpruned(max_rows=2, timeout_seconds=30),
            columns=("missing_alias",),
            refresh=True,
        )

    error = exc_info.value
    assert error.code == "acquisition_execution_failed"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is True
    assert query_spy.user_data_queries == 1


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


def test_time_range_allows_transformed_temporal_partition_and_pushes_half_open_filter(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    transformed = replace(
        inspection,
        partitioning=replace(
            inspection.partitioning,
            state="known",
            fields=(PartitionMetadata(name="ts", type="timestamp", transform="toYYYYMMDD"),),
        ),
    )

    snapshot = transformed.sample(
        scope=md.time_range(
            "ts",
            start="2026-07-10T00:00:00",
            end="2026-07-11T00:00:00",
            max_rows=10,
            timeout_seconds=30,
        ),
        columns=("order_id",),
        refresh=True,
    )

    sql = query_spy.user_data_sql[0]
    assert '"ts" >= ' in sql
    assert '"ts" < ' in sql
    assert sql.index("WHERE") < sql.index("LIMIT")
    assert snapshot.coverage.pushed_predicate == (
        ("time_range", "ts", "2026-07-10T00:00:00", "2026-07-11T00:00:00"),
    )
    assert snapshot.coverage.observed_row_count == 2


@pytest.mark.parametrize(
    "clickhouse_type",
    [
        "DateTime64(3)",
        "DateTime64(3, 'UTC')",
        "Nullable(DateTime64(3))",
    ],
)
def test_time_range_accepts_raw_clickhouse_temporal_type_spellings(
    inspection: SourceInspection,
    clickhouse_type: str,
) -> None:
    transformed = replace(
        inspection,
        partitioning=replace(
            inspection.partitioning,
            state="known",
            fields=(
                PartitionMetadata(
                    name="ts",
                    type=clickhouse_type,
                    transform="toYYYYMMDD",
                ),
            ),
        ),
        schema=tuple(
            replace(column, type=clickhouse_type) if column.name == "ts" else column
            for column in inspection.schema
        ),
    )

    snapshot = transformed.sample(
        scope=md.time_range(
            "ts",
            start="2026-07-10T00:00:00",
            end="2026-07-11T00:00:00",
            max_rows=10,
            timeout_seconds=30,
        ),
        columns=("order_id",),
        refresh=True,
    )

    assert snapshot.coverage.observed_row_count == 2


def test_time_range_uses_projected_temporal_alias(
    query_spy: _QuerySpy,
    inspection: SourceInspection,
) -> None:
    projected = md.inspect(
        inspection.datasource,
        md.table(
            "orders",
            columns={
                "event_time": md.source_column("ts", data_type="timestamp"),
                "order_key": md.source_column("order_id", data_type="string"),
            },
        ),
    )

    snapshot = projected.sample(
        scope=md.time_range(
            "event_time",
            start="2026-07-10T00:00:00",
            end="2026-07-11T00:00:00",
            max_rows=10,
            timeout_seconds=30,
        ),
        columns=("order_key",),
        refresh=True,
    )

    sql = query_spy.user_data_sql[0]
    assert 'SELECT "ts" AS "event_time", "order_id" AS "order_key" FROM "orders"' in sql
    assert '"event_time" >= ' in sql
    assert '"event_time" < ' in sql
    assert snapshot.coverage.observed_row_count == 2


def test_time_range_rejects_non_temporal_or_unexposed_column_before_connection(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    for column in ("dt", "missing"):
        with pytest.raises(DatasourceAuthoringError) as exc_info:
            inspection.sample(
                scope=md.time_range(
                    column,
                    start="2026-07-10",
                    end="2026-07-11",
                    max_rows=10,
                    timeout_seconds=30,
                ),
                columns=("order_id",),
            )
        assert exc_info.value.code == "unknown_source_column"
        assert exc_info.value.effect_observed is not None
        assert exc_info.value.effect_observed.query_executed is False


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


def test_backend_open_failure_is_structured_and_redacted(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BackendOpenError(RuntimeError):
        code = 115

    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BackendOpenError(
                "Code: 115. Unknown setting access_mode (UNKNOWN_SETTING); password=super-secret"
            )
        ),
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    error = exc_info.value
    assert error.code == "acquisition_connection_failed"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert error.received == "BackendOpenError code=115 name=UNKNOWN_SETTING"
    assert "super-secret" not in str(error)
    assert error.repair is not None
    assert error.repair.kind == "reconnect"
    assert error.repair.help_target.canonical_id == "test"
    assert error.repair.preserves_evidence is True


def test_source_resolution_failure_is_structured_and_disconnects(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Backend:
        disconnected = False

        def table(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("missing table password=super-secret")

        def disconnect(self) -> None:
            self.disconnected = True

    backend = Backend()
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: backend,
    )

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    error = exc_info.value
    assert error.code == "acquisition_source_failed"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert "super-secret" not in str(error)
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert error.repair.help_target.canonical_id == "inspect"
    assert error.repair.preserves_evidence is True
    assert backend.disconnected is True


def test_execution_failure_is_structured_redacted_and_disconnects(
    inspection: SourceInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ibis.backends.duckdb import Backend

    class ExecutionError(RuntimeError):
        code = 107
        name = "FILE_DOESNT_EXIST"

    disconnected = 0
    original_disconnect = Backend.disconnect

    def fail_execute(self: Backend, *_args: object, **_kwargs: object) -> object:
        raise ExecutionError(
            "Code: 107. Storage file missing (FILE_DOESNT_EXIST); token=super-secret"
        )

    def tracked_disconnect(self: Backend) -> None:
        nonlocal disconnected
        disconnected += 1
        original_disconnect(self)

    monkeypatch.setattr(Backend, "execute", fail_execute)
    monkeypatch.setattr(Backend, "disconnect", tracked_disconnect)

    with pytest.raises(DatasourceAuthoringError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=1),
            columns=("order_id",),
        )

    error = exc_info.value
    assert error.code == "acquisition_execution_failed"
    assert error.stage == "acquire"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is True
    assert error.received == "ExecutionError code=107 name=FILE_DOESNT_EXIST"
    assert "super-secret" not in str(error)
    assert "Code: acquisition_execution_failed" in str(error)
    assert "Stage: acquire" in str(error)
    assert error.repair is not None
    assert error.repair.kind == "reacquire"
    assert "at most once" in error.repair.action
    assert "stop and report" in error.repair.action
    assert disconnected == 1


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
        ms.ref.datasource("warehouse"),
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
    inspection = md.inspect(ms.ref.datasource("events"), md.table("events"))

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
    inspection = md.inspect(ms.ref.datasource("warehouse"), source)

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=("order_id",),
    )

    assert tuple(column.name for column in inspection.schema) == ("order_id", "amount")
    assert snapshot.columns == ("order_id",)
    assert query_spy.user_data_queries == 1
    assert "ignored" not in query_spy.user_data_sql[0]
