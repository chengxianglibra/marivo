"""Public allocation with raw-fact expectations and fixed continuations."""

from fractions import Fraction
from typing import Literal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT

pytestmark = pytest.mark.runtime


def test_additive_source_and_fixed(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)
    current = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            ms.ref.entity("sales.customer"),
            *axes,
        ),
    )
    baseline = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            ms.ref.entity("sales.customer"),
            *axes,
        ),
    )
    current = current.group_by(ms.ref.entity("sales.customer")).rollup()
    baseline = baseline.group_by(ms.ref.entity("sales.customer")).rollup()
    change = current.compare(baseline)
    facts = analysis_dsl_rows("j2")
    expected = {}
    for _, member, channel, _, day, _ in facts.orders:
        if str(day).startswith(("2026-07", "2026-08")):
            key = (member, channel)
            expected[key] = expected.get(key, 0) + (1 if str(day).startswith("2026-08") else -1)
    for input_change in (change, change.execute()):
        result = input_change.attribute(axes=axes).execute()
        frame = result.contribution.to_pandas()
        assert {(r.group, r.coord_1): r.value for r in frame.itertuples()} == expected
        selected = result.where(result.contribution.value.gt(0)).execute()
        assert selected.current.to_pandas().shape == selected.baseline.to_pandas().shape
        assert dict(selected.contract()._facts)["complete_partition"] == "False"
        restored = case.session.artifact(result.state.artifact_ref)
        assert isinstance(restored, mv.MaterializedAttributionResult)
        assert restored.contribution.to_pandas().equals(frame)


@pytest.mark.parametrize(
    "family,parquet",
    [
        (family, parquet)
        for family in ("int64", "float64", "decimal", "duration")
        for parquet in (False, True)
    ]
    + [("duration_" + unit, True) for unit in ("s", "ms", "us")],
)
def test_numeric_source_fixed_and_axis_expansion(
    analysis_dsl_case_factory: DslCaseFactory,
    family: str,
    parquet: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from marivo.analysis.public_dsl import MetricInputValue
    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.analysis.numeric.attribution_carrier_worker import parts
    from tests.support.json import Json, checked, encode, read
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j1")
    facts = [(8, "web", 12, 3), (8, "app", 6, 1), (7, "web", 4, 2), (7, "Other", 2, 2)]
    dtype = {
        "int64": "BIGINT",
        "float64": "DOUBLE",
        "decimal": "DECIMAL(30,6)",
        "duration": "BIGINT",
    }["duration" if family.startswith("duration") else family]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {dtype}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN weight {dtype}')
        db.execute('DELETE FROM "order"')
        for index, (month, channel, amount, weight) in enumerate(facts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [str(index), "A", channel, "paid", f"2026-{month:02d}-15", amount, weight],
            )
        if family.startswith("duration") and not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if family.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            data = pq.read_table(path)
            pq.write_table(
                data.set_column(
                    data.schema.get_field_index("amount"),
                    "amount",
                    data["amount"].cast(pa.duration(family.partition("_")[2] or "ns")),
                ),
                path,
            )
    ms.load(workspace_dir=case.root)
    trace = capture_source(monkeypatch)
    methods = (
        ("sum", "linear")
        if family.startswith("duration")
        else ("sum", "count", "linear", "mean", "weighted", "ratio")
    )
    originals: dict[str, Json] = {}
    original_parts: dict[str, Json] = {}
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(
        month: int, retain: bool, metric: MetricInputValue
    ) -> mv.LogicalRolledNumericRelation | mv.LogicalRolledRatioRelation:
        return members.observe(
            metric,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes if retain else (),
        ).rollup()

    for kind in methods:
        measure = ms.ref.measure("sales.order.amount")
        base = mv.runtime_metric.aggregate(measure, agg="sum", label="sum")
        expression = (
            base
            if kind == "sum"
            else ms.ref.metric("sales.order_count")
            if kind == "count"
            else mv.runtime_metric.linear(add=[base, base], label="linear")
            if kind == "linear"
            else mv.runtime_metric.aggregate(measure, agg="mean", label="mean")
            if kind == "mean"
            else mv.runtime_metric.weighted_mean(
                measure, ms.ref.measure("sales.order.weight"), label="weighted"
            )
            if kind == "weighted"
            else mv.runtime_metric.ratio(base, base, label="ratio")
        )
        retained = endpoint(8, True, expression).compare(endpoint(7, True, expression))
        expanded = endpoint(8, False, expression).compare(endpoint(7, False, expression))
        by_side = []
        for month in (8, 7):
            denominators = sum(
                w if kind == "weighted" else a if kind == "ratio" else 1
                for m, _, a, w in facts
                if m == month
            )
            side = {}
            for m, channel, amount, weight in facts:
                if m == month:
                    side[channel] = Fraction(
                        1
                        if kind == "count"
                        else amount
                        * (2 if kind == "linear" else weight if kind == "weighted" else 1),
                        denominators if kind in ("mean", "weighted", "ratio") else 1,
                    )
            by_side.append(side)
        fixed_difference = retained.execute()
        originals[kind + ":difference"], original_parts[kind + ":difference"] = (
            snapshot(fixed_difference),
            parts(fixed_difference),
        )
        for label, change in (("allocation", retained), ("expanded", expanded)):
            result = change.attribute(axes=axes).execute()
            assert result._dataset is not None
            originals[kind + ":" + label], original_parts[kind + ":" + label] = (
                snapshot(result),
                parts(result),
            )
            verified = result._dataset.verified()
            allocation = next(p.table for p in verified.parts if p.role == "allocation")
            if family.startswith("duration"):
                allocation = allocation.set_column(
                    allocation.schema.get_field_index("allocation__contribution"),
                    "allocation__contribution",
                    allocation["allocation__contribution"].cast(pa.int64()),
                )
            actual = {r["key_1"]: r["allocation__contribution"] for r in allocation.to_pylist()}
            expected = {
                key: by_side[0].get(key, Fraction()) - by_side[1].get(key, Fraction())
                for key in set(by_side[0]) | set(by_side[1])
            }
            if family == "decimal" and kind != "count":
                assert all(
                    abs(Fraction(actual[k]) - v) <= Fraction(1, 10**6) for k, v in expected.items()
                )
            elif family == "float64" or kind in ("mean", "weighted", "ratio"):
                assert actual == pytest.approx(
                    {k: float(v) for k, v in expected.items()}, rel=1e-12, abs=1e-12
                )
            else:
                assert actual == {k: int(v) for k, v in expected.items()}
            assert (
                result.current.to_pandas().shape
                == result.baseline.to_pandas().shape
                == result.contribution.to_pandas().shape
            )
        with pytest.raises(AnalysisError):
            expanded.execute().attribute(axes=axes)

    state: dict[str, Json] = {
        "phase": "produce",
        "pid": os.getpid(),
        "session": case.session.id,
        "family": family,
        "form": "parquet" if parquet else "table",
        "methods": list(methods),
        "originals": originals,
        "parts": original_parts,
        "native_sql": [*trace.native_sql],
        "source_closed": all(owner._closed for owner in trace.owners),
        "expanded_fixed_axes_refused": True,
    }
    assert trace.native_sql and trace.owners and state["source_closed"] is True
    (case.root / "r94-attribution-carrier.json").write_bytes(encode(state))
    shutil.rmtree(case.root / "models")
    case.database_path.unlink()
    if (case.root / "source_files").exists():
        shutil.rmtree(case.root / "source_files")
    repository = PROJECT_ROOT
    reports: list[Json] = [state]
    for phase in ("fixed", "cold"):
        output = case.root / ("attribution-carrier-" + phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.numeric.attribution_carrier_worker",
                str(case.root),
                phase,
                str(output),
            ],
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(repository)},
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        name = "attribution-carrier-" + family + ("-parquet" if parquet else "-table") + ".json"
        Path(directory, name).write_bytes(encode({"reports": checked(reports)}))


@pytest.mark.parametrize("mode", ["joint", "hierarchy"])
def test_common_other_mapping_each_parent_and_resolution(
    analysis_dsl_case_factory: DslCaseFactory,
    mode: Literal["joint", "hierarchy"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        facts = [
            (8, "Other", "x", 20),
            (7, "Other", "x", 30),
            (8, "app", "y", 10),
            (7, "web", "z", 25),
            (8, "web", "x", 2),
            (7, "app", "x", 1),
        ]
        for index, (month, channel, status, amount) in enumerate(facts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", channel, status, f"2026-{month:02d}-15", amount],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"), ms.ref.dimension("sales.order.status"))
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg="sum", label="amount"
    )

    def endpoint(month: int):
        return members.observe(
            metric,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    change = endpoint(8).compare(endpoint(7))
    for source in (change, change.execute()):
        result = source.attribute(axes=axes, mode=mode, top_k=1).execute()
        rows = cell_rows(result._dataset.verified().primary)
        assert any(r["key_1"] == "Other" and r["key_3"] == 0 for r in rows)
        assert any(r["key_1"] is None and r["key_3"] & 1 for r in rows)
        for level in (1, 2) if mode == "hierarchy" else (2,):
            assert sum(r["value"] for r in rows if r["key_0"] == level) == -24
        selected = result.where(result.contribution.value.is_defined()).execute()
        assert logical_table(selected._dataset.verified().primary).equals(
            logical_table(result._dataset.verified().primary)
        )
        assert dict(selected.contract()._facts)["complete_partition"] == "False"


@pytest.mark.parametrize("parquet", [False, True])
def test_offline_cold_parts_and_selected_views(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
) -> None:
    import os
    import subprocess
    import sys

    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        return members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    saved = endpoint(8).compare(endpoint(7)).execute()
    result = saved.attribute(axes=axes, top_k=1).execute()
    checked = result._dataset.verified()
    for part in checked.parts:
        with pytest.raises(AnalysisError):
            from_arrow(
                checked.primary,
                checked.contract,
                parts=tuple(p for p in checked.parts if p is not part),
                method_state=checked.method_state,
                completed_checks=checked.completed_checks,
            )
        if part.role in ("allocation", "reconciliation", "basis"):
            column = next(
                c
                for c in part.table.column_names
                if c.endswith(("__value", "__contribution", "__total"))
            )
            bad = part.table.set_column(
                part.table.schema.get_field_index(column),
                column,
                pa.array([99] * len(part.table), type=part.table[column].type),
            )
            with pytest.raises(AnalysisError):
                from_arrow(
                    checked.primary,
                    checked.contract,
                    parts=tuple(
                        ExchangePart(p.role, bad) if p is part else p for p in checked.parts
                    ),
                    method_state=checked.method_state,
                    completed_checks=checked.completed_checks,
                )
    path = case.root / "source_files" if parquet else case.database_path
    offline = path.with_name(path.name + ".offline")
    path.rename(offline)
    script = """
import sys
import duckdb
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
def unavailable(*args, **kwargs):
    raise AssertionError("fixed attribution touched source or Semantic")
duckdb.connect = ibis.duckdb.connect = ms.load = SourceSession.bind = SourceSession.batches = unavailable
session = mv.session.resume(sys.argv[1], by='id')
saved = session.artifact(sys.argv[2])
axes = (ms.ref.dimension('sales.order.channel'),)
if sys.argv[3] == 'continue':
    assert isinstance(saved, mv.MaterializedDifferenceRelation)
    saved = saved.attribute(axes=axes, top_k=1).execute()
    saved = saved.where(saved.contribution.value.is_defined()).execute()
    print(saved.state.artifact_ref.ref)
else:
    assert isinstance(saved, mv.MaterializedAttributionResult)
    assert dict(saved.contract()._facts)['complete_partition'] == 'False'
    assert saved.contribution.to_pandas().shape == saved.current.to_pandas().shape == saved.baseline.to_pandas().shape
    selected = saved.current.where(saved.current.value.is_defined()).execute()
    assert isinstance(selected, mv.MaterializedSelectedNumericRelation)
    assert mv.table(c=saved.contribution, a=saved.current, b=saved.baseline).execute().to_pandas().shape[0] == selected.to_pandas().shape[0]
"""
    try:
        artifact = saved.state.artifact_ref.ref
        for phase in ("continue", "recover"):
            completed = subprocess.run(
                [sys.executable, "-c", script, case.session.id, artifact, phase],
                cwd=case.root,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr
            if phase == "continue":
                artifact = completed.stdout.strip().splitlines()[-1]
    finally:
        offline.rename(path)


def test_static_refusals_and_shared_compiled_submissions(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from marivo.analysis.materialization import graph_attribution
    from marivo.datasource import adapters

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        return members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    current, baseline = endpoint(8), endpoint(7)
    change = current.compare(baseline)
    before = change._runtime.last_run_ref
    with monkeypatch.context() as patch:

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("invalid attribution read business rows")

        patch.setattr(adapters.SourceSession, "batches", forbidden)
        for bad in (True, False, 0, -1, 1001, 1.5):
            with pytest.raises(AnalysisError):
                change.attribute(axes=axes, top_k=bad)
        for bad in ((), axes * 2, list(axes), (ms.ref.entity("sales.order"),)):
            with pytest.raises(AnalysisError):
                change.attribute(axes=bad)
        with pytest.raises(AnalysisError):
            change.attribute(axes=axes, mode="hierarchy")
        with pytest.raises(AnalysisError):
            current.compare(baseline, value="relative_change").attribute(axes=axes)
        assert change._runtime.last_run_ref == before
    issued = []
    submitted = []
    original_batches = adapters.SourceSession.batches
    original_cursor = adapters._native_cursor

    def audited(self, read, *, chunk_size):
        assert read.sql == self._backend.compile(read.expression, params=dict(read.params))
        issued.append(read.sql)
        return original_batches(self, read, chunk_size=chunk_size)

    def cursor(backend, name, sql):
        submitted.append(sql)
        return original_cursor(backend, name, sql)

    monkeypatch.setattr(adapters.SourceSession, "batches", audited)
    monkeypatch.setattr(adapters, "_native_cursor", cursor)
    calls = []
    original_result = graph_attribution.result

    def counted(*args, **kwargs):
        if kwargs.get("verify", True):
            calls.append(args[0].domain.definition_id)
        return original_result(*args, **kwargs)

    monkeypatch.setattr(graph_attribution, "result", counted)
    allocation = change.attribute(axes=axes)
    mv.table(c=allocation.contribution, a=allocation.current, b=allocation.baseline).execute()
    assert len(calls) == 1
    assert issued and submitted == issued
    assert not any("reconciled AS" in sql or "attributed AS" in sql for sql in submitted)


@pytest.mark.parametrize("coarsen", [False, True])
def test_period_buckets_and_retained_coarsened_partition(
    analysis_dsl_case_factory: DslCaseFactory,
    coarsen: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        scope = mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01")
        grid = mv.time_grid(
            during=scope, grain=mv.grain("day" if coarsen else "month"), timezone="UTC"
        )
        values = members.observe(
            ms.ref.metric("sales.order_count"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"), *axes),
        )
        if coarsen:
            values = values.group_by(ms.ref.entity("sales.customer"), mv.grain("month")).rollup()
        else:
            values = values.group_by(ms.ref.entity("sales.customer"), grid).rollup()
        return values

    current, baseline = endpoint(8), endpoint(7)
    facts = analysis_dsl_rows("j2")
    expected = {}
    for _, member, channel, _, day, _ in facts.orders:
        if str(day).startswith(("2026-07", "2026-08")):
            key = member, channel
            expected[key] = expected.get(key, 0) + (1 if str(day).startswith("2026-08") else -1)
    for first, second in ((current, baseline), (current.execute(), baseline.execute())):
        change = first.compare(second, design=mv.PeriodChange(alignment=mv.window_bucket()))
        result = change.attribute(axes=axes).execute()
        actual = {
            (r["key_0"], r["key_3"]): r["value"]
            for r in cell_rows(result._dataset.verified().primary)
        }
        assert actual == expected


def test_zero_component_basis_cannot_hide_under_other(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT')
        db.execute('DELETE FROM "order"')
        for index, (month, channel, amount, weight) in enumerate(
            [(8, "web", 1, 0), (8, "app", 0, 1), (7, "app", 1, 1)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [str(index), "A", channel, "paid", f"2026-{month:02d}-15", amount, weight],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)
    expression = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(ms.ref.measure("sales.order.amount"), agg="sum", label="n"),
        mv.runtime_metric.aggregate(ms.ref.measure("sales.order.weight"), agg="sum", label="w"),
        label="ratio",
    )

    def endpoint(month: int):
        return members.observe(
            expression,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    change = endpoint(8).compare(endpoint(7))
    for current in (change, change.execute()):
        with pytest.raises(AnalysisError, match="contradictory"):
            current.attribute(axes=axes, top_k=1).execute()


@pytest.mark.parametrize("parquet", [False, True])
def test_new_source_evaluation_preserves_fixed_basis(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        return members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    difference = endpoint(8).compare(endpoint(7))
    logical = difference.attribute(axes=axes)
    first = logical.execute()
    fixed_expression = difference.execute().attribute(axes=axes)
    fixed = fixed_expression.execute()
    before = fixed.contribution.to_pandas()
    assert before.equals(first.contribution.to_pandas())
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["new-attribution", "A", "web", "paid", "2026-08-15", 1],
        )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    changed = logical.execute()
    assert changed.state.artifact_ref != first.state.artifact_ref
    assert changed.contribution.to_pandas().value.sum() == before.value.sum() + 1
    reused = fixed_expression.execute()
    assert reused.state.artifact_ref == fixed.state.artifact_ref
    assert reused.contribution.to_pandas().equals(before)


def test_public_decimal_small_partition_rounding_refuses_publication(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                [f"{month}-{i}", "A", str(i), "paid", f"2026-{month:02d}-15", amount]
                for month, amount in ((8, 1), (7, 0))
                for i in range(3000)
            ],
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)
    expression = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg="mean", label="mean"
    )

    def endpoint(month: int):
        return members.observe(
            expression,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    change = endpoint(8).compare(endpoint(7))
    # Each rounded side is 0.000333; 3000 contributions sum to 0.999,
    # exceeding the accepted threshold for an independently computed target of 1.
    for difference in (change, change.execute()):
        before = case.session.runs(status="succeeded").items
        with pytest.raises(AnalysisError, match="reconcile"):
            difference.attribute(axes=axes).execute()
        run = difference._runtime.last_run_ref
        assert run is not None and case.session.get_run(run).lifecycle == "failed"
        assert case.session.runs(status="succeeded").items == before


@pytest.mark.parametrize("parquet", [False, True])
def test_float_view_arithmetic_retains_allocation_error_bounds(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    facts = [(8, "web", 0.1), (8, "app", 0.2), (7, "web", 0.01), (7, "app", 0.02)]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                [str(i), "A", channel, "paid", f"2026-{month:02d}-15", amount]
                for i, (month, channel, amount) in enumerate(facts)
            ],
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)
    expression = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg="mean", label="mean"
    )

    def endpoint(month: int):
        return members.observe(
            expression,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    allocation = endpoint(8).compare(endpoint(7)).attribute(axes=axes)
    for result in (allocation, allocation.execute()):
        ratio = result.current.ratio(result.baseline).execute()
        checked = ratio._dataset.verified()
        correspondence = next(p.table for p in checked.parts if p.role == "correspondence")
        assert all(v > 0 for v in correspondence["correspondence__current_error_bound"].to_pylist())
        assert all(
            v > 0 for v in correspondence["correspondence__baseline_error_bound"].to_pylist()
        )
        exact = {
            channel: Fraction(current) / Fraction(baseline)
            for (_, channel, current), (_, _, baseline) in zip(facts[:2], facts[2:], strict=True)
        }
        bounds = {
            tuple(r[k] for k in checked.contract.key_fields): r[
                "correspondence__result_error_bound"
            ]
            for r in correspondence.to_pylist()
        }
        for row in cell_rows(checked.primary):
            key = tuple(row[k] for k in checked.contract.key_fields)
            assert abs(Fraction(row["value"]) - exact[row["key_1"]]) <= Fraction(bounds[key])


@pytest.mark.parametrize("parquet", [False, True])
def test_numeric_view_rank_uses_typed_other_order(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                [f"{month}-{channel}-{i}", "A", channel, "paid", f"2026-{month:02d}-15", 1]
                for month, count in ((8, 2), (7, 1))
                for channel in ("A", "Other", "Z")
                for i in range(count)
            ],
        )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        return members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    allocation = endpoint(8).compare(endpoint(7)).attribute(axes=axes, top_k=2)
    for result in (allocation, allocation.execute()):
        calls = {a.call for a in result.contribution.contract().actions}
        assert "relation.where(predicate)" in calls
        assert any(".ratio(" in call for call in calls)
        assert any(".summarize(" in call for call in calls)
        ranking = result.contribution.rank(order="descending", ties="ordinal").execute()
        assert isinstance(ranking, mv.MaterializedRankingResult)
        rows = ranking.ranks.to_pandas()
        assert rows.value.tolist() == [1, 2, 3]
        assert rows.coord_0.tolist()[:2] == ["A", "Other"]
        assert rows.coord_1.tolist() == [0, 0, 1]


@pytest.mark.parametrize(
    "point", ["graph_part_written", "graph_files_published", "graph_receipts_verified"]
)
def test_attribution_publication_cleans_only_failed_run(
    analysis_dsl_case_factory: DslCaseFactory,
    point: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    axes = (ms.ref.dimension("sales.order.channel"),)

    def endpoint(month: int):
        return members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=axes,
        ).rollup()

    change = endpoint(8).compare(endpoint(7))
    fixed = change.execute()
    expected = fixed.to_pandas()
    for difference in (change, fixed):
        before = set((case.root / ".marivo").rglob("*.parquet"))
        succeeded = case.session.runs(status="succeeded").items

        def fail(event: str) -> None:
            if event == point:
                raise RuntimeError("injected attribution publication failure")

        difference._runtime._hook = fail
        try:
            with pytest.raises((AnalysisError, RuntimeError)):
                difference.attribute(axes=axes).execute()
        finally:
            difference._runtime._hook = None
        assert set((case.root / ".marivo").rglob("*.parquet")) == before
        assert case.session.runs(status="succeeded").items == succeeded
        assert fixed.to_pandas().equals(expected)
        with difference._runtime.store._read() as conn:
            assert conn.execute("SELECT count(*) FROM action_resource_journal").fetchone()[0] == 0
        # A clean retry reaches the same registered method without replaying a failed publication.
        result = difference.attribute(axes=axes).execute()
        assert len(result._dataset.verified().parts) == 6
