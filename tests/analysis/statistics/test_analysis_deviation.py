"""Deviation public execution and independent exact-fact regression oracles."""

import subprocess
import sys
from decimal import Decimal, localcontext
from fractions import Fraction

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods import deviation_numeric as numeric
from marivo.analysis.methods.physical import DecimalType, ScalarType
from tests.analysis.statistics.deviation_oracle import decimal_finish, expected
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.parametrize("method", ["zscore", "mad"])
def test_signed_independent_integer_oracle(method: numeric.DeviationMethod) -> None:
    values = (2**63 - 5, 2**63 - 4, 2**63 - 1)
    raw = tuple(Fraction(value) for value in values)
    fitted = numeric.fit(raw, method)
    center = sum(raw, Fraction()) / 3 if method == "zscore" else raw[1]
    with localcontext() as ctx:
        ctx.prec = 150
        scale = (
            (sum(((x - center) * (x - center) for x in raw), Fraction()) / 3)
            if method == "zscore"
            else Fraction(7413, 5000)
        )
        divisor = Decimal(scale.numerator) / Decimal(scale.denominator)
        if method == "zscore":
            divisor = divisor.sqrt()
        expected = tuple(
            float((Decimal((x - center).numerator) / Decimal((x - center).denominator)) / divisor)
            for x in raw
        )
    assert fitted.center is not None and fitted.center.value() == center
    assert tuple(numeric.score(x - center, fitted) for x in raw) == expected
    assert expected[0] < 0 < expected[-1]


@pytest.mark.parametrize("precision,scale", [(p, s) for p in range(1, 39) for s in range(p + 1)])
def test_every_decimal_profile_preserves_original_and_half_even(precision: int, scale: int) -> None:
    typ = DecimalType(precision, scale)
    value = Decimal((0, (1,), -scale))
    assert numeric.exact(value, typ) == Fraction(1, 10**scale)
    assert numeric.finish(Fraction(5, 2) / 10**scale, typ) == Decimal((0, (2,), -scale))
    assert numeric.finish(Fraction(7, 2) / 10**scale, typ) == Decimal((0, (4,), -scale))
    values = tuple(Decimal((0, (x,), -scale)) for x in (1, 2, 7))
    for method in ("zscore", "mad"):
        center, raw_scale, branch, scores = expected(values, method)
        fitted = numeric.fit(tuple(Fraction(x) for x in values), method)
        assert fitted.center is not None and fitted.raw_scale is not None
        assert (fitted.center.value(), fitted.raw_scale.value(), fitted.branch) == (
            center,
            raw_scale,
            branch,
        )
        output = numeric.unit_type(typ)
        assert isinstance(output, DecimalType)
        assert numeric.finish(center, output) == decimal_finish(center, output.scale)
        for value, score in zip(values, scores, strict=True):
            delta = Fraction(value) - center
            assert numeric.finish(delta, output) == decimal_finish(delta, output.scale)
            certified = numeric.certified_score(delta, fitted)
            assert certified.value == score
            assert numeric.verify_score(delta, fitted, score, certified.root)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True])
def test_nonfinite_or_wrong_original_carrier_rejects(value: float | bool) -> None:
    with pytest.raises(ValueError):
        numeric.exact(value, ScalarType("float64"))


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["zscore", "mad"])
def test_public_source_fixed_views_and_selection(
    analysis_dsl_case_factory: DslCaseFactory, method: numeric.DeviationMethod
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales." + case.names.buyer)
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=via,
        by=(ms.ref.entity("sales.customer"),),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=via,
        by=(ms.ref.entity("sales.customer"),),
    )
    change = current.compare(baseline)
    source = change.deviation(method=method)
    fixed = source.execute()
    observed = fixed.observed.to_pandas()
    reference = fixed.reference.to_pandas()
    deviations = fixed.deviation.to_pandas()
    scores = fixed.score.to_pandas()
    assert (
        list(observed.member)
        == list(reference.member)
        == list(deviations.member)
        == list(scores.member)
    )
    assert observed.value.tolist() == change.execute().to_pandas().value.tolist()
    values = tuple(Fraction(float(x)) for x in observed.value)
    center = (
        sum(values) / len(values)
        if method == "zscore"
        else (sorted(values)[(len(values) - 1) // 2] + sorted(values)[len(values) // 2]) / 2
    )
    assert reference.value.tolist() == [float(center)] * len(values)
    assert deviations.value.tolist() == [float(x - center) for x in values]
    selected = fixed.where(fixed.score.value.is_defined())
    positive = selected.where(selected.score.value.gt(0)).execute()
    assert (
        positive.observed.to_pandas().member.tolist()
        == scores.loc[scores.value > 0, "member"].tolist()
    )
    again = change.execute().deviation(method=method).execute()
    assert again.score.to_pandas().value.tolist() == scores.value.tolist()
    ranking = fixed.score.rank(order="descending", ties="dense").limit(2).execute()
    assert (
        ranking.values.to_pandas().value.tolist() == sorted(scores.value.tolist(), reverse=True)[:2]
    )
    table = mv.table(
        observed=fixed.observed,
        reference=fixed.reference,
        deviation=fixed.deviation,
        score=fixed.score,
    ).execute()
    assert len(table.to_pandas()) == len(scores)


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["zscore", "mad"])
@pytest.mark.parametrize("grid_mode", [False, True])
@pytest.mark.parametrize(
    "profile",
    (
        "BIGINT",
        "DOUBLE",
        "DECIMAL(9,2)",
        "DECIMAL(18,6)",
        "DECIMAL(38,0)",
        "DECIMAL(38,6)",
        "DECIMAL(38,18)",
        "DECIMAL(38,38)",
    ),
)
def test_f11_complete_source_chain(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    grid_mode: bool,
    profile: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        factor = "0.001" if profile == "DECIMAL(38,38)" else "1"
        db.execute(
            f'ALTER TABLE "order" ALTER amount TYPE {profile} USING CAST(amount * {factor} AS {profile})'
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales." + case.names.buyer)
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=via,
        by=(ms.ref.entity("sales.customer"),),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=via,
        by=(ms.ref.entity("sales.customer"),),
    )
    deviation = current.compare(baseline).deviation(method=method)
    defined = deviation.where(deviation.score.value.is_defined())
    positive = defined.where(defined.score.value.gt(0))
    selected = positive.observed.members()
    assert isinstance(selected, mv.LogicalAnalysisDomain)
    if grid_mode:
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
        )
        followup = selected.observe(
            metric, during=grid, via=via, by=(ms.ref.entity("sales.customer"),)
        )
    else:
        followup = selected.observe(
            metric,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
    trace: list[str] = []
    source_batches, original_fit = SourceSession.batches, numeric.fit

    def batches(self: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        trace.append("source")
        return source_batches(self, read, chunk_size=chunk_size)

    def fitting(xs: tuple[Fraction, ...], algorithm: numeric.DeviationMethod) -> numeric.Fit:
        trace.append("local")
        return original_fit(xs, algorithm)

    monkeypatch.setattr(SourceSession, "batches", batches)
    monkeypatch.setattr(numeric, "fit", fitting)
    assert followup.summarize(method=mv.count_defined()).execute().to_pandas().value.tolist() == [2]
    assert trace.count("local") == 1
    assert max(i for i, event in enumerate(trace) if event == "source") < trace.index("local")
    trace.clear()
    values = followup.execute().to_pandas()
    assert trace.count("local") == 1
    assert max(i for i, event in enumerate(trace) if event == "source") < trace.index("local")
    assert values.loc[values.cell_tag == "defined", "value"].tolist() == (
        [Decimal("0.12"), Decimal("0")] if profile == "DECIMAL(38,38)" else [120, 0]
    )
    if grid_mode:
        assert len(values) == 62
        assert values.cell_tag.value_counts().to_dict() == {"null": 60, "defined": 2}


PROFILES = (
    "BIGINT",
    "DOUBLE",
    "DECIMAL(9,2)",
    "DECIMAL(18,6)",
    "DECIMAL(38,0)",
    "DECIMAL(38,6)",
    "DECIMAL(38,18)",
    "DECIMAL(38,38)",
)


@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize(
    "values",
    (
        (-sys.float_info.max, 0.0, sys.float_info.max),
        (sys.float_info.max, float.fromhex("0x1.ffffffffffffep+1023")),
        (-float.fromhex("0x0.0000000000001p-1022"), 0.0, float.fromhex("0x0.0000000000001p-1022")),
        (1e100, 1e100 + 1e85, 1e100 - 1e85),
    ),
)
def test_binary64_extremes_and_ambient_context(
    method: numeric.DeviationMethod, values: tuple[float, ...]
) -> None:
    center, raw_scale, branch, scores = expected(values, method)
    with localcontext() as context:
        context.prec = 2
        fitted = numeric.fit(tuple(Fraction(x) for x in values), method)
        assert fitted.center is not None and fitted.raw_scale is not None
        assert (fitted.center.value(), fitted.raw_scale.value(), fitted.branch) == (
            center,
            raw_scale,
            branch,
        )
        actual = tuple(numeric.score(Fraction(x) - center, fitted) for x in values)
    assert actual == scores


def test_finite_input_unrepresentable_final_field_rejects() -> None:
    maximum = Decimal("99999999999999999999999999999999999999")
    typ = DecimalType(38, 0)
    raw = numeric.exact(maximum, typ)
    with pytest.raises(OverflowError):
        numeric.finish(raw, numeric.unit_type(typ))
    fitted = numeric.fit(
        (
            Fraction(0),
            Fraction(float.fromhex("0x0.0000000000001p-1022")),
            Fraction(sys.float_info.max),
        ),
        "mad",
    )
    assert fitted.center is not None
    with pytest.raises(OverflowError):
        numeric.score(Fraction(sys.float_info.max) - fitted.center.value(), fitted)


@pytest.mark.parametrize("expire", (False, True))
def test_root_certificate_refines_a_rounding_boundary_under_shared_deadline(expire: bool) -> None:
    from marivo.analysis.core.domain_captures import DomainPreparationError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline, check

    # This primitive witness fixture is not a source or Runtime qualification.
    variance = Fraction(1) + Fraction(1, 2**2000)
    delta = Fraction(2**53 + 1, 2**53)
    fitted = numeric.Fit(
        "zscore",
        3,
        numeric.RationalFact.capture(Fraction()),
        numeric.RationalFact.capture(variance),
        "population_stddev",
        None,
        None,
    )
    now, steps = [0.0], [0]

    def checkpoint() -> None:
        steps[0] += 1
        if expire and steps[0] == 3:
            now[0] = 600.001
        check()

    token = CURRENT.set(ExecuteDeadline(0.0, lambda: now[0]))
    try:
        if expire:
            with pytest.raises(DomainPreparationError, match="execute_timeout"):
                numeric.certified_score(delta, fitted, checkpoint=checkpoint)
            assert steps[0] == 3
        else:
            with localcontext() as context:
                context.prec = 800
                independent = float(
                    (Decimal(delta.numerator) / Decimal(delta.denominator))
                    / (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
                )
            scored = numeric.certified_score(delta, fitted, checkpoint=checkpoint)
            assert scored.value == independent == 1.0
            assert scored.root is not None and scored.root.precision >= 640
            assert (scored.root.precision - 120) % 40 == 0
            assert numeric.verify_score(delta, fitted, scored.value, scored.root)
    finally:
        CURRENT.reset(token)


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_null_partitions_selection_boundary_and_shared_fit(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: numeric.DeviationMethod,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET channel=NULL WHERE amount=0')
    members = case.session.members(ms.ref.entity("sales.order"))
    values = members.read(ms.ref.measure("sales.order.amount"))
    categories = members.read(ms.ref.dimension("sales.order.channel"))
    assert isinstance(categories, mv.LogicalCategoryRelation)
    calls: list[tuple[Fraction, ...]] = []
    original_fit = numeric.fit

    def fitting(xs: tuple[Fraction, ...], algorithm: numeric.DeviationMethod) -> numeric.Fit:
        calls.append(xs)
        return original_fit(xs, algorithm)

    monkeypatch.setattr(numeric, "fit", fitting)
    fitted = values.deviation(method=method, partition_by=(categories,))
    result = mv.table(
        observed=fitted.observed, reference=fitted.reference, score=fitted.score
    ).execute()
    frame = result.to_pandas()
    assert len(calls) == 2
    assert sorted(len(xs) for xs in calls) == [4, 7]
    assert len(frame) == 11
    first = values.deviation(method=method)
    second = values.deviation(method=method)
    calls.clear()
    mv.table(first=first.score, second=second.score).execute()
    assert len(calls) == 2
    calls.clear()
    selected = values.where(values.value.gt(50))
    new_fit = selected.deviation(method=method).execute()
    assert len(calls) == 1 and len(calls[0]) == 5
    all_fit = values.deviation(method=method).execute()
    retained = all_fit.where(all_fit.observed.value.gt(50)).execute()
    assert dict(retained.contract()._facts)["original_count"] == "11"
    assert dict(new_fit.contract()._facts)["original_count"] == "5"


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("empty", (False, True))
def test_selected_time_score_can_establish_a_new_fixed_fit(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    empty: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.deviation_oracle import forbidden

    case = analysis_dsl_case_factory("j2")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    original = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales." + case.names.buyer),
            by=(ms.ref.entity("sales.customer"),),
        )
        .deviation(method=method)
        .execute()
    )
    defined = original.where(original.score.value.is_defined())
    values = defined.score
    selected = values.where(values.value.gt(10 if empty else 0))
    input_column = selected.execute().to_pandas().value.tolist()
    assert all(isinstance(value, float) for value in input_column)
    input_values = tuple(value for value in input_column if isinstance(value, float))
    assert bool(input_values) is not empty
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(ms, "load", forbidden)
    logical = selected.deviation(method=method)
    result = logical.execute()
    assert dict(result.contract()._facts)["original_count"] == str(len(input_values))
    assert result.observed.to_pandas().value.tolist() == list(input_values)
    actual = result.score.to_pandas()
    if not input_values:
        assert actual.empty
    elif len(input_values) == 1:
        assert actual.cell_reason.tolist() == ["insufficient_samples"]
    elif len(set(input_values)) == 1:
        assert actual.cell_reason.tolist() == ["zero_scale"] * len(input_values)
    else:
        _, _, _, wanted = expected(input_values, method)
        assert actual.value.tolist() == list(wanted)
    restored = case.session.artifact(result.evidence_digest().artifact_ref)
    assert isinstance(restored, mv.MaterializedDeviationResult)
    assert restored.score.to_pandas().equals(actual)
    runs = case.session.runs().items
    assert logical.execute().evidence_digest().artifact_ref == result.evidence_digest().artifact_ref
    assert case.session.runs().items == runs


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_observed_nullable_large_int64_dataframe_preserves_original_values(
    analysis_dsl_case_factory: DslCaseFactory, method: numeric.DeviationMethod
) -> None:
    import pandas as pd
    import pyarrow as pa

    case = analysis_dsl_case_factory("j2")
    values = (2**63 - 5, 2**63 - 4, 2**63 - 1)
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE BIGINT')
        for key, value in zip(("a", "b", "c", "null"), (*values, None), strict=True):
            db.execute('INSERT INTO "order" (order_id, amount) VALUES (?, ?)', [key, value])
    result = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method=method)
        .execute()
    )
    observed = result.observed.to_pandas().sort_values("member")
    assert observed.value.iloc[:3].tolist() == list(values)
    assert observed.value.iloc[3] is pd.NA
    assert observed.value.dtype == pd.ArrowDtype(pa.int64())
    assert observed.cell_tag.tolist() == ["defined", "defined", "defined", "null"]
    restored = case.session.artifact(result.evidence_digest().artifact_ref)
    assert isinstance(restored, mv.MaterializedDeviationResult)
    assert restored.observed.to_pandas().sort_values("member").equals(observed)


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_extreme_binary64_fit_card_preserves_bounded_continuations(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        for channel in ("a", "b", "c"):
            for index, value in enumerate(
                (sys.float_info.max, float.fromhex("0x0.0000000000001p-1022"))
            ):
                db.execute(
                    'INSERT INTO "order" (order_id, channel, amount) VALUES (?, ?, ?)',
                    [channel + str(index), channel, value],
                )
    members = case.session.members(ms.ref.entity("sales.order"))
    values = members.read(ms.ref.measure("sales.order.amount"))
    category = members.read(ms.ref.dimension("sales.order.channel"))
    assert isinstance(category, mv.LogicalCategoryRelation)
    assert isinstance(category, mv.LogicalCategoryRelation)
    result = values.deviation(method=method, partition_by=(category,)).execute()
    contract = result.contract()
    assert "excerpt" in dict(contract._facts)["fit_partition_0"]
    contract.show()
    text = capsys.readouterr().out
    assert len(text.encode()) <= 8192
    assert contract.actions and contract.actions[-1].help_target in text
    _, _, _, scores = expected(
        (sys.float_info.max, float.fromhex("0x0.0000000000001p-1022")), method
    )
    assert result.score.to_pandas().value.tolist().count(scores[0]) == 3


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_construction_mode_identity_and_zero_business_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: numeric.DeviationMethod,
) -> None:
    import marivo
    from marivo.analysis.errors import StatisticalRelationError
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.order"))
    values = members.read(ms.ref.measure("sales.order.amount"))
    category = members.read(ms.ref.dimension("sales.order.channel"))
    assert isinstance(category, mv.LogicalCategoryRelation)
    foreign_case = analysis_dsl_case_factory("j2")
    foreign = foreign_case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.dimension("sales.order.channel")
    )
    assert isinstance(foreign, mv.LogicalCategoryRelation)
    fixed = values.execute()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction read business rows")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    logical = values.deviation(method=method, partition_by=(category,))
    assert isinstance(logical, mv.LogicalDeviationResult)
    assert "fit_scope" in dict(logical.contract()._facts)
    help_api = marivo.help
    assert callable(help_api)
    help_api("analysis.dsl.NumericComparison.deviation")
    with pytest.raises(StatisticalRelationError) as raised:
        values.deviation(method=method, partition_by=(foreign,))
    assert raised.value.code == "r8.input_identity"
    with pytest.raises(StatisticalRelationError):
        fixed.deviation(method=method, partition_by=(category,))


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize(
    "phase", ("fit_complete", "graph_admitted", "graph_receipts_verified", "before_commit")
)
def test_deviation_deadline_failure_is_atomic(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: numeric.DeviationMethod,
    phase: str,
) -> None:
    from marivo.analysis.core.domain_captures import DomainPreparationError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    case = analysis_dsl_case_factory("j2")
    logical = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method=method)
    )
    runtime = case.session._runtime
    now = [0.0]
    original_event, original_fit = runtime._event, numeric.fit

    def event(point: str) -> None:
        original_event(point)
        if point == phase:
            now[0] = 600.001

    def fitting(xs: tuple[Fraction, ...], algorithm: numeric.DeviationMethod) -> numeric.Fit:
        result = original_fit(xs, algorithm)
        if phase == "fit_complete":
            now[0] = 600.001
        return result

    monkeypatch.setattr(runtime, "_event", event)
    monkeypatch.setattr(numeric, "fit", fitting)
    token = CURRENT.set(ExecuteDeadline(0.0, lambda: now[0]))
    try:
        with pytest.raises(DomainPreparationError, match="execute_timeout"):
            logical.execute()
        assert runtime.last_run_ref is not None
        failed_run = runtime.store._graph_run(runtime.last_run_ref)
        assert failed_run is not None and failed_run.lifecycle == "failed"
        with runtime.store._connection() as connection:
            for table in ("dataset_artifacts", "dataset_evidence", "findings"):
                assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert runtime.store.resources(runtime.session_ref) == ()
    finally:
        CURRENT.reset(token)


@pytest.mark.runtime
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("form", ("table", "parquet"))
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_eight_profiles_separate_producer_fixed_cold(
    analysis_dsl_case_factory: DslCaseFactory,
    profile: str,
    form: str,
    method: numeric.DeviationMethod,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        factor = "0.001" if profile == "DECIMAL(38,38)" else "1"
        db.execute(
            f'ALTER TABLE "order" ALTER amount TYPE {profile} USING CAST(amount * {factor} AS {profile})'
        )
    if form == "parquet":
        export_dsl_parquet_models(case, case.root)
    for phase in ("produce", "fixed", "cold"):
        if phase == "fixed":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            for source in (case.root / "source_files").glob("*.parquet"):
                source.rename(source.with_suffix(".offline"))
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.deviation_worker",
                str(case.root),
                phase,
                method,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert f'"accepted": "{phase}"' in process.stdout
