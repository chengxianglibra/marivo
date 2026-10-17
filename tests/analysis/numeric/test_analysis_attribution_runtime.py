"""Explicit allocation recipes over typed, complete terminal fact exports."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.numeric.attribution_workflows import (
    Method,
    allocation,
    basis,
    basis_query,
)
from tests.analysis.statistics.pandas_workflows import plain, receipt_paths
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import capture_source


def _inputs(case: DslCase, *, weight: bool = False) -> tuple[mv.Artifact, mv.Artifact]:
    return basis_query(case, 8, weight=weight).execute(), basis_query(
        case, 7, weight=weight
    ).execute()


def _count(case: DslCase, month: int, *, drill: bool = False) -> mv.Query:
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        during=session.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
        by=(customer,) if drill else (customer, channel),
        members=session.members(ms.ref.entity("sales.customer")),
    )
    return values.drill(add=(channel,)) if drill else values


def _expected_counts() -> dict[tuple[str | None, str | None], int]:
    expected: dict[tuple[str | None, str | None], int] = {}
    for _, member, channel, _, day, _ in analysis_dsl_rows("j2").orders:
        if day.startswith(("2026-07", "2026-08")):
            key = member, channel
            expected[key] = expected.get(key, 0) + (1 if day.startswith("2026-08") else -1)
    return expected


def _run_worker(root: Path, phase: str) -> Json:
    output = root / ("allocation-" + phase + ".json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.numeric.attribution_carrier_worker",
            str(root),
            phase,
            str(output),
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return read(output)


def _save_inputs(
    case: DslCase,
    inputs: tuple[mv.Artifact, mv.Artifact],
    *,
    methods: tuple[Method, ...],
    top_k: int | None = None,
) -> None:
    recipes: dict[str, Json] = {}
    for method in methods:
        current, baseline = (basis(value) for value in inputs)
        if method == "ratio":
            current, baseline = (frame.assign(weight=frame.amount) for frame in (current, baseline))
        recipes[method] = allocation(current, baseline, method=method, top_k=top_k).to_json(
            orient="split", date_format="iso", double_precision=15
        )
    state: dict[str, Json] = {
        "session": case.session.id,
        "inputs": [{"artifact": value.id, "snapshot": snapshot(value)} for value in inputs],
        "methods": list(methods),
        "top_k": top_k,
        "recipe_outputs": recipes,
    }
    (case.root / "r94-attribution-carrier.json").write_bytes(encode(state))


def _remove_sources(case: DslCase) -> None:
    shutil.rmtree(case.root / "models")
    case.database_path.unlink()
    if (case.root / "source_files").exists():
        shutil.rmtree(case.root / "source_files")


def test_additive_source_and_fixed(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    inputs = _inputs(case)
    frame = allocation(*(basis(value) for value in inputs), method="count")
    assert {(r.member, r.channel): r.contribution for r in frame.itertuples()} == _expected_counts()
    # The source drill remains a real positive capability, before publication.
    for month, artifact in zip((8, 7), inputs, strict=True):
        direct = plain(_count(case, month).execute())
        drilled = plain(_count(case, month, drill=True).execute())
        assert direct.equals(drilled)
        expected = basis(artifact).groupby(["member", "channel"]).size().to_dict()
        assert {(r.key_0, r.key_1): r.value for r in drilled.itertuples()} == expected
    selected = frame.loc[frame.contribution > 0]
    assert selected[["current"]].shape == selected[["baseline"]].shape
    before = len(case.session.runs().items)
    restored = tuple(case.session.artifact(value.id) for value in inputs)
    pd.testing.assert_frame_equal(
        allocation(*(basis(value) for value in restored), method="count"), frame
    )
    assert len(case.session.runs().items) == before
    assert all(not hasattr(value, "attribute") for value in restored)


@pytest.mark.slow
@pytest.mark.parametrize(
    "family,parquet", [("int64", False), ("float64", False), ("decimal", False), ("duration", True)]
)
def test_numeric_source_fixed_and_axis_expansion(
    analysis_dsl_case_factory: DslCaseFactory,
    family: str,
    parquet: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    facts = [(8, "web", 12, 3), (8, "app", 6, 1), (7, "web", 4, 2), (7, "Other", 2, 2)]
    dtype = {
        "int64": "BIGINT",
        "float64": "DOUBLE",
        "decimal": "DECIMAL(30,6)",
        "duration": "BIGINT",
    }[family]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {dtype}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN weight {dtype}')
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                [str(i), "A", channel, "paid", f"2026-{month:02d}-15", amount, weight]
                for i, (month, channel, amount, weight) in enumerate(facts)
            ],
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "finalized = ms.finalize()",
            "weight = ms.dimension_column(name='weight', entity=orders, column='weight')\nfinalized = ms.finalize()",
        )
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        path = case.root / "source_files/order.parquet"
        data = pq.read_table(path)
        pq.write_table(
            data.set_column(
                data.schema.get_field_index("amount"),
                "amount",
                data["amount"].cast(pa.duration("ns")),
            ),
            path,
        )
        assert pq.read_schema(path).field("amount").type == pa.duration("ns")
    ms.load(workspace_dir=case.root)
    trace = capture_source(monkeypatch)
    inputs = _inputs(case, weight=True)
    if family == "duration":
        assert isinstance(basis(inputs[0])["amount"].iloc[0], pd.Timedelta), (
            basis(inputs[0]).dtypes,
            basis(inputs[0]).amount.tolist(),
        )
    methods: tuple[Method, ...] = (
        ("sum", "count", "linear", "mean", "weighted", "ratio")
        if family == "int64"
        else ("sum",)
        if family == "duration"
        else ("mean",)
    )
    expected = {
        "sum": {"web": Fraction(8), "app": Fraction(6), "Other": Fraction(-2)},
        "count": {"web": Fraction(0), "app": Fraction(1), "Other": Fraction(-1)},
        "linear": {"web": Fraction(16), "app": Fraction(12), "Other": Fraction(-4)},
        "mean": {"web": Fraction(4), "app": Fraction(3), "Other": Fraction(-1)},
        "weighted": {"web": Fraction(7), "app": Fraction(3, 2), "Other": Fraction(-1)},
        "ratio": {"web": Fraction(0), "app": Fraction(1, 3), "Other": Fraction(-1, 3)},
    }
    for method in methods:
        current, baseline = (basis(value) for value in inputs)
        if method == "ratio":
            current, baseline = (frame.assign(weight=frame.amount) for frame in (current, baseline))
        result = allocation(current, baseline, method=method)
        actual = dict(zip(result.channel, result.contribution, strict=True))
        if family == "duration":
            assert {key: value.value for key, value in actual.items()} == {
                key: int(value) for key, value in expected[method].items()
            }
        else:
            assert {key: float(value) for key, value in actual.items()} == pytest.approx(
                {key: float(value) for key, value in expected[method].items()}
            )
        assert result.current.shape == result.baseline.shape == result.contribution.shape
    assert trace.native_sql and trace.owners and all(owner._closed for owner in trace.owners)
    _save_inputs(case, inputs, methods=methods)
    _remove_sources(case)
    reports = [
        _run_worker(case.root, phase)
        for phase in (("offline", "cold") if family == "int64" else ("cold",))
    ]
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 1 + len(reports)


@pytest.mark.parametrize(("mode", "fixed_followup"), [("joint", False), ("hierarchy", True)])
def test_common_other_mapping_each_parent_and_resolution(
    analysis_dsl_case_factory: DslCaseFactory,
    mode: Literal["joint", "hierarchy"],
    fixed_followup: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    facts = [
        (8, "Other", "x", 20),
        (7, "Other", "x", 30),
        (8, "app", "y", 10),
        (7, "web", "z", 25),
        (8, "web", "x", 2),
        (7, "app", "x", 1),
    ]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                [str(i), "A", channel, status, f"2026-{month:02d}-15", amount]
                for i, (month, channel, status, amount) in enumerate(facts)
            ],
        )
    inputs = _inputs(case)
    sources = (
        (inputs, tuple(case.session.artifact(value.id) for value in inputs))
        if fixed_followup
        else (inputs,)
    )
    for source in sources:
        result = allocation(
            *(basis(value) for value in source), axes=("channel", "status"), mode=mode, top_k=1
        )
        assert ((result.channel == "Other") & (result.other_flags == 0)).any()
        assert (result.channel.isna() & (result.other_flags & 1).astype(bool)).any()
        for level in (1, 2) if mode == "hierarchy" else (2,):
            assert result.loc[result.level == level, "contribution"].sum() == -24


@pytest.mark.slow
def test_offline_attribution_cold_path(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    inputs = _inputs(case)
    _save_inputs(case, inputs, methods=("count",), top_k=1)
    _remove_sources(case)
    reports = [_run_worker(case.root, phase) for phase in ("offline", "cold")]
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    for path in receipt_paths(case.root, inputs[0])[:1]:
        contents = path.read_bytes()
        try:
            path.write_bytes(b"corrupt allocation basis")
            with pytest.raises(AnalysisError):
                basis(inputs[0])
        finally:
            path.write_bytes(contents)


def test_static_refusals_and_shared_compiled_submissions(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j2")
    issued: list[str] = []
    execute = SourceSession.execute

    def audited(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        assert read.sql == owner._backend.compile(
            read.expression.as_table(), params=dict(read.params), limit=None
        )
        issued.append(read.sql)
        frame = execute(owner, read)
        assert owner.submissions[-1].sql == read.sql
        return frame

    monkeypatch.setattr(SourceSession, "execute", audited)
    inputs = _inputs(case)
    current, baseline = (basis(value) for value in inputs)
    before = (len(issued), len(case.session.runs().items))
    for bad in (True, False, 0, -1, 1001, 1.5):
        with pytest.raises(ValueError, match="top_k"):
            allocation(current, baseline, top_k=bad)  # type: ignore[arg-type]  # Runtime validation of explicit pandas parameters.
    for axes in ((), ("channel", "channel"), ("missing",)):
        with pytest.raises(ValueError, match="axes"):
            allocation(current, baseline, axes=axes)
    with pytest.raises(ValueError, match="hierarchy"):
        allocation(current, baseline, mode="hierarchy")
    result = allocation(current, baseline, method="count")
    views = result[["current", "baseline", "contribution"]]
    assert views.shape == (4, 3)
    assert issued and before == (len(issued), len(case.session.runs().items))
    assert all("reconciled AS" not in sql and "attributed AS" not in sql for sql in issued)
    query = _count(case, 8)
    for add in ((), (case.session.catalog.fields.get("sales.order.channel"),)):
        with pytest.raises(AnalysisError):
            query.drill(add=add)
    assert before == (len(issued), len(case.session.runs().items))


@pytest.mark.parametrize(
    "coarsen", [False, pytest.param(True, marks=pytest.mark.slow, id="coarsened")]
)
def test_period_buckets_and_retained_coarsened_partition(
    analysis_dsl_case_factory: DslCaseFactory, coarsen: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")

    def endpoint(month: int) -> mv.Query:
        scope = session.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01")
        calendar = scope.by(mv.grain("day" if coarsen else "month"))
        values = session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")},
            during=calendar,
            by=(customer, channel),
            members=session.members(ms.ref.entity("sales.customer")),
        )
        return (
            values.rollup(by=(customer, scope.by(mv.grain("month")), channel))
            if coarsen
            else values
        )

    current, baseline = endpoint(8), endpoint(7)
    result = current.compare(baseline, match=mv.ordinal()).execute()
    compared = plain(result)
    assert len(compared) == 4
    assert compared["value.absolute"].tolist() == [0, 0, 0, 0]
    assert compared["value.current"].tolist() == compared["value.baseline"].tolist() == [1, 1, 1, 1]
    if coarsen:
        assert compared.key_0.tolist() == ["A", "B", "C", "D"]
        assert compared.key_1.tolist() == ["(0, 0)"] * 4
    else:
        assert compared.key_0.tolist() == ["(0, 0)"] * 4
        assert compared.key_1.tolist() == ["A", "B", "C", "D"]
    assert compared.key_2.tolist() == ["web"] * 4
    bounds = {
        tuple(value.isoformat() for value in bound)
        for projection in result._manifest.data.coordinate_projections
        for bound in projection.bounds
    }
    assert bounds == {
        ("2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00") * 2,
        ("2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00") * 2,
    }
    allocated = allocation(*(basis(value) for value in _inputs(case)), method="count")
    assert {
        (r.member, r.channel): r.contribution for r in allocated.itertuples()
    } == _expected_counts()


def test_zero_component_basis_cannot_hide_under_other(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT')
        db.execute('DELETE FROM "order"')
        db.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                [str(i), "A", channel, "paid", f"2026-{month:02d}-15", amount, weight]
                for i, (month, channel, amount, weight) in enumerate(
                    [(8, "web", 1, 0), (8, "app", 0, 1), (7, "app", 1, 1)]
                )
            ],
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            "weight = ms.dimension_column(name='weight', entity=orders, column='weight')\nfinalized = ms.finalize()",
        )
    )
    ms.load(workspace_dir=case.root)
    inputs = _inputs(case, weight=True)
    before = len(case.session.runs().items)
    with pytest.raises(ValueError, match="nonzero category numerator"):
        allocation(*(basis(value) for value in inputs), method="ratio", top_k=1)
    assert len(case.session.runs().items) == before
    assert basis(inputs[0]).amount.tolist() == [1, 0]


def test_new_source_evaluation_preserves_fixed_basis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    queries = basis_query(case, 8), basis_query(case, 7)
    fixed = queries[0].execute(), queries[1].execute()
    before = allocation(*(basis(value) for value in fixed), method="count")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["new-attribution", "A", "web", "paid", "2026-08-15", 1],
        )
    fresh = queries[0].execute(), queries[1].execute()
    assert fresh[0].id != fixed[0].id
    assert (
        allocation(*(basis(value) for value in fresh), method="count").contribution.sum()
        == before.contribution.sum() + 1
    )
    pd.testing.assert_frame_equal(
        allocation(*(basis(case.session.artifact(value.id)) for value in fixed), method="count"),
        before,
    )


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
                for i in range(3)
            ],
        )
    ms.load(workspace_dir=case.root)
    inputs = _inputs(case)
    assert basis(inputs[0]).amount.tolist() == [Decimal("1.000000")] * 3
    before = case.session.runs().items
    # Three separately rounded 1/3 contributions total .999999, not the parent's 1.
    with pytest.raises(ValueError, match="reconcile"):
        allocation(*(basis(value) for value in inputs), method="mean")
    assert case.session.runs().items == before
    assert basis(inputs[0]).amount.tolist() == [Decimal("1.000000")] * 3


def test_float_view_arithmetic_retains_allocation_error_bounds(
    analysis_dsl_case_factory: DslCaseFactory,
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
    ms.load(workspace_dir=case.root)
    result = allocation(*(basis(value) for value in _inputs(case)), method="mean")
    exact = {
        channel: Fraction(current) / Fraction(baseline)
        for (_, channel, current), (_, _, baseline) in zip(facts[:2], facts[2:], strict=True)
    }
    for row in result.itertuples():
        assert isinstance(row.current, float) and isinstance(row.baseline, float)
        assert isinstance(row.channel, str)
        ratio = row.current / row.baseline
        # Explicit pandas arithmetic has ordinary IEEE rounding, independently bounded.
        bound = 4 * math.ulp(ratio)
        assert bound > 0 and abs(Fraction(ratio) - exact[row.channel]) <= Fraction(bound)
        assert ratio == pytest.approx(10)


def test_numeric_view_rank_uses_typed_other_order(
    analysis_dsl_case_factory: DslCaseFactory,
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
    result = allocation(*(basis(value) for value in _inputs(case)), method="count", top_k=2)
    result = result.sort_values(
        ["contribution", "other_flags", "channel"],
        ascending=[False, True, True],
        na_position="last",
    )
    result["rank"] = range(1, len(result) + 1)
    assert result["rank"].tolist() == [1, 2, 3]
    assert result.channel.tolist()[:2] == ["A", "Other"]
    assert result.other_flags.tolist() == [0, 0, 1]
    assert pd.isna(result.channel.tolist()[2])


@pytest.mark.slow
def test_attribution_publication_cleans_only_failed_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = basis_query(case, 8)
    fixed = query.execute()
    expected = fixed.to_pandas()
    for point in ("terminal_primary_written", "terminal_files_published"):
        before = set((case.root / ".marivo").rglob("*.parquet"))
        succeeded = case.session.runs(status="succeeded").items

        def fail(event: str, stage: str = point) -> None:
            if event == stage:
                raise RuntimeError("injected basis publication failure")

        case.session._runtime._hook = fail
        try:
            with pytest.raises((AnalysisError, RuntimeError)):
                query.execute()
        finally:
            case.session._runtime._hook = None
        assert set((case.root / ".marivo").rglob("*.parquet")) == before
        assert case.session.runs(status="succeeded").items == succeeded
        pd.testing.assert_frame_equal(fixed.to_pandas(), expected)
        with case.session._runtime.store._read() as conn:
            assert conn.execute("SELECT count(*) FROM action_resource_journal").fetchone()[0] == 0
    fresh = query.execute()
    baseline = basis_query(case, 7).execute()
    assert allocation(basis(fresh), basis(baseline), method="count").contribution.sum() == 0
