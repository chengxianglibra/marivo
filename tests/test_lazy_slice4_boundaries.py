"""Actual backend widening and independent source-domain acceptance boundaries."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import duckdb
import ibis
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import SourceStep, place, source_binding
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.backends import BuiltDatasourceBackend, EffectiveDatasourceKwargs
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot, statistics, versions
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def test_independent_equal_argument_sources_keep_exact_registered_unary_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _manifest()
    registry, sidecar = make_execution_registry(Path(":memory:"))
    runtime = DatasetRuntime.create(tmp_path, "independent-connections")
    first = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    second = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    revenue = ref.metric("sales.revenue")
    first_source = first.observe(revenue).where(gt(revenue, 0))
    second_source = second.observe(revenue).where(gt(revenue, 1))
    assert not source_binding(first_source).same_domain(source_binding(second_source))
    assert all(
        len(graph.steps) == 1 and isinstance(graph.steps[0], SourceStep)
        for graph in (place(first_source), place(second_source))
    )
    backends = [ibis.duckdb.connect(":memory:") for _ in range(2)]
    identities = [id(backend.con) for backend in backends]
    assert len(set(identities)) == 2
    calls: list[dict[str, object]] = []
    try:
        for backend, amount in zip(backends, (11, 29), strict=True):
            backend.raw_sql(
                "CREATE TABLE orders (id BIGINT, tenant VARCHAR, customer_id BIGINT, "
                "order_id BIGINT, amount DOUBLE, weight DOUBLE, region VARCHAR, "
                'channel VARCHAR, day DATE, start DATE, "end" DATE)'
            )
            backend.con.execute("INSERT INTO orders (id, amount) VALUES (1, ?)", [amount])

        def supplied(
            datasource: DatasourceIR, effective: EffectiveDatasourceKwargs, *, read_only: bool
        ) -> BuiltDatasourceBackend:
            assert datasource.fields == {"path": ":memory:"}
            assert effective.kwargs == {"path": ":memory:"} and read_only
            selected: Backend = backends[len(calls)]
            calls.append({"path": ":memory:", "connection_identity": id(selected.con)})
            return BuiltDatasourceBackend(selected, ())

        monkeypatch.setattr(admission, "_build_backend_from_effective", supplied)
        source_results = [first_source.execute(), second_source.execute()]
        assert len(calls) == 2
        branch_evidence: list[dict[str, object]] = []
        for checkpoint, expected in zip(source_results, (11, 29), strict=True):
            output = checkpoint.where(gt(revenue, 10)).aggregate().execute()
            assert output.to_pandas()["revenue"].tolist() == [expected]
            assert runtime.statistics.primary_queries == 1
            assert runtime.statistics.worker_pid is None
            branch_evidence.append(
                {
                    "input": str(checkpoint.state.artifact_ref),
                    "output": str(output.state.artifact_ref),
                    "value": expected,
                    "worker_pid": runtime.statistics.worker_pid,
                    "statistics": statistics(runtime),
                }
            )
        before_rejection = snapshot(runtime)
        mixed = second.observe(revenue, population=first.population(ref.entity("sales.customers")))
        with pytest.raises(DatasetCompilationError, match="source-required"):
            mixed.execute()
        assert runtime.last_run_ref is None and snapshot(runtime) == before_rejection
        assert len(calls) == 2
        assert candidate == _manifest()
        record = {
            "schema": "marivo.slice4.source-boundary/v1",
            "pid": os.getpid(),
            "connection_arguments": [call["path"] for call in calls],
            "connection_identities": identities,
            "branches": branch_evidence,
            "source_required_combination": "rejected_before_admission",
            "local_scope": "independent registered unary Metric continuations",
            "after": snapshot(runtime),
            "candidate_before": candidate,
            "candidate_after": _manifest(),
            "versions": versions(),
        }
        retained = os.environ.get("MARIVO_SLICE4D_EVIDENCE_DIR")
        if retained:
            directory = Path(retained)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "slice-4-independent-source-boundaries.json").write_text(
                json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
            )
    finally:
        for backend in backends:
            backend.disconnect()


@pytest.mark.parametrize("overflow", [False, True])
def test_actual_integer_sum_widening_is_exact_or_atomically_failed(
    tmp_path: Path, overflow: bool
) -> None:
    candidate = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    values = (2**63 - 1, 1) if overflow else (2**31 - 1, 1)
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("ALTER TABLE orders ALTER COLUMN amount TYPE BIGINT")
        connection.executemany(
            "INSERT INTO orders (id, customer_id, amount) VALUES (?, 1, ?)",
            ((1, values[0]), (2, values[1])),
        )
        actual = connection.execute("SELECT SUM(amount) AS total FROM orders").to_arrow_table()
    assert actual.schema.field("total").type == pa.decimal128(38, 0)
    assert actual["total"].to_pylist() == [Decimal(sum(values))]
    registry, sidecar = make_execution_registry(database)
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities = dict(registry.entities)
    entities[entity.semantic_id] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type="int64") if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "integer-sum")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.revenue")).aggregate()
    backend = ibis.duckdb.connect(str(database))
    try:
        expression = backend.table("orders")
        ibis_sum = expression.aggregate(total=expression.amount.sum())
        assert str(ibis_sum.schema()["total"]) == "int64"
        if overflow:
            with pytest.raises(pa.ArrowInvalid, match="out of bounds"):
                backend.to_pyarrow(ibis_sum)
        else:
            converted = backend.to_pyarrow(ibis_sum)
            assert converted.schema.field("total").type == pa.int64()
            assert converted["total"].to_pylist() == [sum(values)]
    finally:
        backend.disconnect()
    if overflow:
        with pytest.raises(MaterializationError) as caught:
            logical.execute()
        assert caught.value.run_ref == runtime.last_run_ref
        assert runtime.last_run_ref is not None
        failed = runtime.store.run(runtime.last_run_ref)
        assert failed is not None and failed.lifecycle == "failed" and failed.failure is not None
        assert failed.failure.phase == "storage_staging"
        assert failed.output_artifact_ref is None
        state = snapshot(runtime)
        counts = state["counts"]
        assert isinstance(counts, dict)
        assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
        assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 1
        assert counts["action_resource_journal"] == 0
        outcome = "failed_without_publication"
    else:
        result = logical.execute()
        assert result.to_pandas()["revenue"].tolist() == [sum(values)]
        assert runtime.last_run_ref is not None
        succeeded = runtime.store.run(runtime.last_run_ref)
        assert succeeded is not None and succeeded.lifecycle == "succeeded"
        outcome = "exact_int64_result"
    assert runtime.statistics.primary_queries == 1
    assert candidate == _manifest()
    record = {
        "schema": "marivo.slice4.numeric-boundary/v1",
        "pid": os.getpid(),
        "kind": "overflow" if overflow else "widening",
        "native_arrow_type": str(actual.schema.field("total").type),
        "native_sum": str(actual["total"][0].as_py()),
        "ibis_logical_type": "int64",
        "outcome": outcome,
        "runtime": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
        "candidate_before": candidate,
        "candidate_after": _manifest(),
    }
    retained = os.environ.get("MARIVO_SLICE4D_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"slice-4-integer-{'overflow' if overflow else 'widening'}.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
