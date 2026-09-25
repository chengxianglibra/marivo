"""Private S2 P3 multi-root ratio and original-component reduction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import duckdb
import ibis
import ibis.expr.types as ir
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.source_stage import run_j1_source
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context, J3Observed, j3_route, j3_routes
from marivo.semantic.errors import SemanticLoadFailed
from tests.shared_fixtures import DSL_NAMES, DslCase, DslCaseFactory


@contextmanager
def _source(case: DslCase) -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        n = case.names
        yield (
            backend,
            {
                f"{n.domain}.{name}": backend.table(name)
                for name in (n.customer, n.order, n.order_line)
            },
        )
    finally:
        backend.disconnect()


def _observed(
    case: DslCase, store: SessionStore, *, coordinates: tuple[str, ...] = ()
) -> J3Observed:
    n = case.names
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    members = context.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    return members.observe(
        ms.ref.metric(f"{n.domain}.{n.aov}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=j3_routes(
            j3_route(
                ms.ref.entity(f"{n.domain}.{n.order_line}"),
                through=(
                    ms.ref.relationship(f"{n.domain}.{n.line_order}"),
                    ms.ref.relationship(f"{n.domain}.{n.buyer}"),
                ),
            ),
            j3_route(
                ms.ref.entity(f"{n.domain}.{n.order}"),
                through=(ms.ref.relationship(f"{n.domain}.{n.buyer}"),),
            ),
        ),
        coordinates=tuple(ms.ref.dimension(f"{n.domain}.{n.order}.{name}") for name in coordinates),
    )


def test_p3_source_ratio_and_original_state_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    store = SessionStore(case.root)
    store.create_session("p3-source", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel,))
    assert isinstance(observed, J3Observed)
    with _source(case) as (backend, tables):
        result = run_j1_source(observed.context, observed.root, backend, tables)
        rows = {(row["member"], row["coord_0"]): row for row in result.primary.to_pylist()}
        assert {key: row["value"] for key, row in rows.items()} == {
            ("A", "web"): 100.0,
            ("A", "mobile"): 0.0,
            ("B", "web"): 30.0,
        }
        assert rows[("A", "web")]["numerator_row_count"] == 2
        assert rows[("A", "web")]["denominator_count"] == 1
        grouped = observed.group_by(
            ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}")
        ).rollup()
        by_channel = run_j1_source(observed.context, grouped.root, backend, tables)
        values = {row["group"]: row["value"] for row in by_channel.primary.to_pylist()}
        assert values["web"] == pytest.approx(160 / 3)
        assert values["mobile"] == 0.0
        overall = run_j1_source(observed.context, observed.rollup().root, backend, tables)
        assert overall.primary.to_pylist()[0]["value"] == 40.0
        mean = run_j1_source(observed.context, observed.summarize("mean").root, backend, tables)
        assert mean.primary.to_pylist()[0]["value"] == pytest.approx(130 / 3)


def test_p3_weighted_rollup_differs_from_current_row_mean(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    store = SessionStore(case.root)
    store.create_session("p3-weighting", session_ref="session")
    observed = _observed(case, store)
    with _source(case) as (backend, tables):
        rows = run_j1_source(observed.context, observed.root, backend, tables).primary.to_pylist()
        assert {row["member"]: row["value"] for row in rows} == {"A": 1.0, "B": 100.0}
        mean = run_j1_source(observed.context, observed.summarize("mean").root, backend, tables)
        assert mean.primary.to_pylist()[0]["value"] == 50.5
        overall = run_j1_source(observed.context, observed.rollup().root, backend, tables)
        assert overall.primary.to_pylist()[0]["value"] == pytest.approx(200 / 101)


def test_p3_full_coordinate_tuple_union_and_zero_denominator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("tuple_union")
    store = SessionStore(case.root)
    store.create_session("p3-tuples", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel, case.names.status))
    with _source(case) as (backend, tables):
        rows = run_j1_source(observed.context, observed.root, backend, tables).primary.to_pylist()
    assert {(row["member"], row["coord_0"], row["coord_1"]): row["value"] for row in rows} == {
        ("A", "web", "paid"): 40.0,
        ("A", "mobile", "cancelled"): 0.0,
    }
    empty_case = analysis_dsl_case_factory("zero_denominator")
    empty_store = SessionStore(empty_case.root)
    empty_store.create_session("p3-zero", session_ref="session")
    empty_observed = _observed(empty_case, empty_store)
    with _source(empty_case) as (backend, tables):
        row = run_j1_source(
            empty_observed.context, empty_observed.root, backend, tables
        ).primary.to_pylist()[0]
    assert (row["value"], row["cell_tag"], row["cell_reason"], row["denominator_count"]) == (
        None,
        "undefined",
        "zero_denominator",
        0,
    )


def test_p3_complete_empty_group_keeps_zero_numerator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("empty_group")
    store = SessionStore(case.root)
    store.create_session("p3-empty-group", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel,))
    with _source(case) as (backend, tables):
        row = run_j1_source(observed.context, observed.root, backend, tables).primary.to_pylist()[0]
    assert (
        row["member"],
        row["coord_0"],
        row["numerator_sum"],
        row["denominator_count"],
        row["value"],
    ) == (
        "A",
        "web",
        0,
        1,
        0.0,
    )


def test_p3_semantic_load_rejects_discontinuous_time_path(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    models = case.root / "models" / "semantic" / case.names.domain / "models.py"
    source = models.read_text()
    assert "time_via=(line_order,)" in source
    models.write_text(source.replace("time_via=(line_order,)", "time_via=(buyer,)", 1))
    with pytest.raises(SemanticLoadFailed, match="invalid event time dimension"):
        ms.load(workspace_dir=case.root)


def test_p3_rejects_missing_path_coverage(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("missing_key")
    store = SessionStore(case.root)
    store.create_session("p3-missing", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel,))
    with _source(case) as (backend, tables), pytest.raises(MaterializationError):
        run_j1_source(observed.context, observed.root, backend, tables)


def test_p3_rejects_unmapped_component_path(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    store = SessionStore(case.root)
    store.create_session("p3-bad-route", session_ref="session")
    n = case.names
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    members = context.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    with pytest.raises(DatasetConstructionError, match="functional component path"):
        members.observe(
            ms.ref.metric(f"{n.domain}.{n.aov}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=j3_routes(
                j3_route(
                    ms.ref.entity(f"{n.domain}.{n.order_line}"),
                    through=(ms.ref.relationship(f"{n.domain}.{n.buyer}"),),
                ),
                j3_route(
                    ms.ref.entity(f"{n.domain}.{n.order}"),
                    through=(ms.ref.relationship(f"{n.domain}.{n.buyer}"),),
                ),
            ),
        )


def test_p3_rejects_duplicate_component_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            f'INSERT INTO "{case.names.order}" SELECT * FROM "{case.names.order}" '
            f"WHERE {case.names.order_id} = 'j3_aw'"
        )
    store = SessionStore(case.root)
    store.create_session("p3-duplicate", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel,))
    with (
        _source(case) as (backend, tables),
        pytest.raises(MaterializationError, match="contribution_partition"),
    ):
        run_j1_source(observed.context, observed.root, backend, tables)


def test_p3_uses_declared_names_after_renaming(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    names = replace(
        DSL_NAMES,
        domain="commerce",
        customer="client",
        order="activity",
        order_line="activity_entry",
        customer_id="client_key",
        order_id="activity_key",
        line_id="entry_key",
        region="territory",
        channel="medium",
        status="phase",
        ordered_at="occurred_at",
        amount="base_value",
        line_amount="entry_value",
        buyer="activity_client",
        line_order="entry_activity",
        revenue="gross_value",
        order_count="activity_count",
        line_revenue="entry_total",
        aov="entry_average",
    )
    case = analysis_dsl_case_factory("j3", names=names)
    store = SessionStore(case.root)
    store.create_session("p3-renamed", session_ref="session")
    observed = _observed(case, store, coordinates=(names.channel,))
    with _source(case) as (backend, tables):
        overall = run_j1_source(observed.context, observed.rollup().root, backend, tables)
    assert overall.primary.to_pylist()[0]["value"] == 40.0


@pytest.mark.runtime
def test_p3_runtime_publishes_ratio_and_local_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j3")
    store = SessionStore(case.root)
    store.create_session("p3-runtime", session_ref="session")
    observed = _observed(case, store, coordinates=(case.names.channel,))
    runtime = DatasetRuntime(store, "session")
    saved = runtime.execute_j1(observed, source=lambda: _source(case))
    assert set(saved.to_pandas()["value"].tolist()) == {0.0, 100.0, 30.0}
    record = store.artifact(saved.state.artifact_ref.ref)
    assert record is not None and record.descriptor.j1_exchange is not None
    assert len(record.descriptor.retained_parts) == 5

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixed ratio continuation opened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    rolled = observed.rollup()
    fixed = runtime.execute_j1(
        rolled, input_node=observed, input_artifact_ref=saved.state.artifact_ref.ref
    )
    assert fixed.to_pandas()["value"].tolist() == [40.0]
    grouped = observed.group_by(
        ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}")
    ).rollup()
    fixed_groups = runtime.execute_j1(
        grouped, input_node=observed, input_artifact_ref=saved.state.artifact_ref.ref
    )
    fixed_frame = fixed_groups.to_pandas()
    assert dict(zip(fixed_frame["group"], fixed_frame["value"], strict=True)) == {
        "mobile": 0.0,
        "web": pytest.approx(160 / 3),
    }
    part = record.descriptor.retained_parts[0]
    with (case.root / part.storage_receipt.project_relative_path / "data.parquet").open(
        "ab"
    ) as output:
        output.write(b"corrupt")
    with pytest.raises(MaterializationError):
        load_j1_artifact(
            SessionStore.open_existing(case.root),
            "session",
            saved.state.artifact_ref.ref,
            observed,
            input_binding=record.descriptor.j1_exchange.input_binding,
        )
