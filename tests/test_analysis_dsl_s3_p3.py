"""J4 source numerical route, batch invariance and route failure boundaries."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis.materialization.dsl_j4_source as j4_source
from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.dsl_j4_source import (
    capture_j4_endpoints,
    choose_j4_source_route,
    run_j4_source,
    run_j4_source_numeric,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.ibis_batches import IbisBatchStream
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.operators.dsl_j1_contracts import J4_SPEARMAN
from marivo.analysis.operators.errors import CorrelationError
from marivo.analysis.operators.registry import MethodRegistration
from tests.shared_fixtures import DslCaseFactory, DslScenario
from tests.test_analysis_dsl_s3_p1 import _association, _source


@pytest.mark.parametrize("scenario", ["j4", "j4_ties"])
def test_j4_source_numeric_matches_python_owner_and_publishes(
    analysis_dsl_case_factory: DslCaseFactory, scenario: DslScenario
) -> None:
    case = analysis_dsl_case_factory(scenario)
    association = _association(case)
    with _source(case) as (backend, tables):
        python = run_j4_source(association, backend, tables)
        numeric = run_j4_source_numeric(association, backend, tables)
    assert numeric.metric_key_a == python.metric_key_a
    assert numeric.metric_key_b == python.metric_key_b
    assert numeric.status == python.status
    assert numeric.coefficient == pytest.approx(python.coefficient)
    assert (
        numeric.input_observation_count,
        numeric.matched_observation_count,
        numeric.null_pair_count,
        numeric.complete_pair_count,
    ) == (
        python.input_observation_count,
        python.matched_observation_count,
        python.null_pair_count,
        python.complete_pair_count,
    )
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
    saved = runtime.execute_j1(
        association, source=lambda: _source(case), source_route="source_numeric"
    )
    assert saved.to_pandas()["coefficient"].tolist() == pytest.approx([python.coefficient])


def test_j4_source_numeric_counts_nulls_and_rejects_no_valid_candidate(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute('DELETE FROM "order" WHERE customer_id = ?', ["D"])
    finally:
        connection.close()
    with _source(case) as (backend, tables):
        result = run_j4_source_numeric(association, backend, tables)
    assert (result.null_pair_count, result.complete_pair_count) == (1, 3)
    assert result.coefficient == pytest.approx(-0.5)

    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute('DELETE FROM "order"')
    finally:
        connection.close()
    with (
        _source(case) as (backend, tables),
        pytest.raises(CorrelationError, match="insufficient_pairs"),
    ):
        run_j4_source_numeric(association, backend, tables)


@pytest.mark.parametrize("change", ["unknown", "nonfinite"])
def test_j4_source_numeric_rejects_invalid_cells_before_publication(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    with _source(case) as (backend, tables):
        left, right = capture_j4_endpoints(association, backend, tables)
        table = left.primary
        if change == "unknown":
            table = table.set_column(
                table.schema.get_field_index("value"),
                "value",
                pa.array([None, 2.0, 4.0, 8.0], type=pa.float64()),
            )
            table = table.set_column(
                table.schema.get_field_index("cell_tag"),
                "cell_tag",
                pa.array(["unknown", "defined", "defined", "defined"]),
            )
            table = table.set_column(
                table.schema.get_field_index("cell_reason"),
                "cell_reason",
                pa.array(["coverage_unknown", None, None, None]),
            )
        else:
            table = table.set_column(
                table.schema.get_field_index("value"),
                "value",
                pa.array([float("inf"), 2.0, 4.0, 8.0], type=pa.float64()),
            )
        monkeypatch.setattr(
            j4_source,
            "_prepare_j4_plans",
            lambda *_args, **_kwargs: (
                J1SourcePlan(ibis.memtable(table)),
                J1SourcePlan(ibis.memtable(right.primary)),
                None,
            ),
        )
        with pytest.raises(MaterializationError, match="invalid Spearman endpoint Cell"):
            run_j4_source_numeric(association, backend, tables)


class _RebatchedBackend:
    name = "duckdb"

    def __init__(self, native: ibis.BaseBackend, batch_size: int) -> None:
        self.native = native
        self.batch_size = batch_size
        self.batch_count = 0

    def compile(self, expression: ir.Table) -> str:
        return self.native.compile(expression)

    def to_pyarrow_batches(self, expression: ir.Table, *, chunk_size: int) -> pa.RecordBatchReader:
        reader = self.native.to_pyarrow_batches(expression, chunk_size=chunk_size)
        try:
            table = pa.Table.from_batches(
                tuple(batch.cast(reader.schema, safe=True) for batch in reader),
                schema=reader.schema,
            )
        finally:
            reader.close()
        batches = table.to_batches(max_chunksize=self.batch_size)
        self.batch_count += len(batches)
        return pa.RecordBatchReader.from_batches(table.schema, batches)


@pytest.mark.parametrize("batch_size", [1, 2, 3, 32])
def test_j4_spearman_ranks_over_complete_input_across_batches(
    analysis_dsl_case_factory: DslCaseFactory, batch_size: int
) -> None:
    case = analysis_dsl_case_factory("j4_ties")
    association = _association(case)
    with _source(case) as (backend, tables):
        split = _RebatchedBackend(backend, batch_size)
        python = run_j4_source(association, split, tables)
        numeric = run_j4_source_numeric(association, split, tables)
    assert split.batch_count > 0
    assert python.coefficient == pytest.approx(7 / 9)
    assert numeric.coefficient == pytest.approx(7 / 9)
    assert python.complete_pair_count == numeric.complete_pair_count == 4


def test_j4_source_execution_failure_does_not_retry_python(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected source failure")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source failure retried in Python")

    monkeypatch.setattr("marivo.analysis.materialization.dsl_j4_source.run_j4_source_numeric", fail)
    monkeypatch.setattr("marivo.analysis.materialization.dsl_j4_source.run_j4_source", forbidden)
    with pytest.raises(Exception):
        runtime.execute_j1(association, source=lambda: _source(case))
    assert runtime.last_run_ref is not None


def test_empty_zero_column_ibis_reader_keeps_declared_schema() -> None:
    schema = pa.schema([])
    native = pa.RecordBatchReader.from_batches(schema, ())
    stream = IbisBatchStream(native, schema)
    try:
        assert stream.schema.equals(schema)
        assert list(stream) == []
    finally:
        stream.close()


def test_j4_capability_registration_selects_only_qualified_routes() -> None:
    assert choose_j4_source_route(J4_SPEARMAN, "duckdb") == "source_numeric"
    prepared = replace(
        J4_SPEARMAN,
        implementations=tuple(
            item for item in J4_SPEARMAN.implementations if item.route != "source_numeric"
        ),
    )
    assert choose_j4_source_route(prepared, "duckdb") == "source"
    none = replace(
        J4_SPEARMAN,
        implementations=tuple(
            item for item in J4_SPEARMAN.implementations if item.route == "local"
        ),
    )
    with pytest.raises(MaterializationError, match="registered source Spearman"):
        choose_j4_source_route(none, "duckdb")
    with pytest.raises(MaterializationError, match="registered source Spearman"):
        choose_j4_source_route(J4_SPEARMAN, "postgres")
    numeric = next(item for item in J4_SPEARMAN.implementations if item.route == "source_numeric")
    with pytest.raises(DatasetRegistrationError, match="missing check"):
        MethodRegistration(
            J4_SPEARMAN.contract,
            (replace(numeric, supported_checks=("complete_pairing",)),),
        )
