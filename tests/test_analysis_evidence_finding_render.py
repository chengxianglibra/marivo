"""Readable bilingual rendering for all canonical Finding variants."""

from __future__ import annotations

from datetime import datetime

import pytest

from marivo._compat import UTC
from marivo.analysis.evidence.finding_render import render_finding
from marivo.analysis.evidence.types import (
    AnomalyCandidateFindingValue,
    AssociationFindingValue,
    ContributionFindingValue,
    DeltaFindingValue,
    DerivationRule,
    EventFunnelObservationValue,
    EventJourneyObservationValue,
    EventTimeToEventObservationValue,
    Finding,
    FindingPage,
    ForecastPointFindingValue,
    FunnelAttributionObservationValue,
    FunnelDeltaObservationValue,
    LifecycleDistributionObservationValue,
    LifecycleDwellObservationValue,
    LifecycleHistoryObservationValue,
    LifecycleTransitionsObservationValue,
    LifecycleViolationsObservationValue,
    MetricValueFindingValue,
    ObservationFindingValue,
    ObservationSegmentValue,
    PanelObservationValue,
    QualityCheckFindingValue,
    ScalarObservationValue,
    SegmentedObservationValue,
    SubjectSetObservationValue,
    TestFindingValue,
    TimeSeriesObservationValue,
    TimeWindow,
)
from marivo.render import AgentResult
from tests.shared_fixtures import make_test_analysis_scope, make_test_subject, rendered_help

_EPISTEMIC = {
    "observation": "observed",
    "metric_value": "observed",
    "delta": "algebraic",
    "decomposition_item": "algebraic",
    "anomaly_candidate": "candidate",
    "correlation_result": "estimated",
    "test_result": "tested",
    "forecast_point": "predicted",
    "quality_check": "tested",
}


def _finding(value, *, suffix: str = "1") -> Finding:
    return Finding(
        finding_id=f"fnd_{value.kind}_{suffix}",
        finding_type=value.kind,
        epistemic_kind=_EPISTEMIC[value.kind],
        artifact_id="art_render",
        session_id="sess_render",
        subject=make_test_subject(metric_id="sales.revenue", analysis_axis="change"),
        canonical_item_key=suffix,
        value=value,
        derivation=DerivationRule(
            rule_id=f"extract.{value.kind}",
            rule_version="v1",
            operator="test",
            source_fields=("value",),
            source_finding_refs=(),
        ),
        committed_at=datetime(2026, 8, 27, tzinfo=UTC),
    )


def _nine_findings() -> tuple[Finding, ...]:
    scope = make_test_analysis_scope("sales.revenue")
    return (
        _finding(
            ObservationFindingValue(
                row_count=1, value=ScalarObservationValue(value=173_758, unit="USD")
            )
        ),
        _finding(
            MetricValueFindingValue(
                value=173_758,
                unit="USD",
                dimension_keys={"region": "west"},
                bucket="2026-08-24",
            )
        ),
        _finding(
            DeltaFindingValue(
                delta_kind="segmented_delta",
                current=173_758,
                baseline=219_095,
                magnitude=-45_337,
                relative_delta=-0.206926,
                direction="decrease",
                unit="USD",
                dimension_keys={"region": "west"},
                current_window=TimeWindow(field="created_at", start="2026-08-24", end="2026-08-28"),
                baseline_window=TimeWindow(
                    field="created_at", start="2026-08-17", end="2026-08-21"
                ),
            )
        ),
        _finding(
            ContributionFindingValue(
                dimension="region",
                dimension_keys={"region": "west"},
                contribution_value=-12_500,
                contribution_share=0.275,
                contribution_rank=1,
                direction="decrease",
                decomposition_method="algebraic_decomposition",
                scope_delta_ref="art_delta",
            )
        ),
        _finding(
            AnomalyCandidateFindingValue(
                candidate_ref="region=west",
                score=3.5,
                detector="robust_z",
                threshold=3,
                rank=1,
                current_value=173_758,
                baseline_value=219_095,
                deviation_absolute=-45_337,
                deviation_relative=-0.206926,
            )
        ),
        _finding(
            AssociationFindingValue(
                left_ref="sales.revenue",
                right_ref="sales.orders",
                method="pearson",
                coefficient=0.812345,
                p_value=0.012345,
                confidence_interval=(0.5, 0.9),
                sample_size=120,
                join_basis="bucket_start",
                lag=0,
            )
        ),
        _finding(
            TestFindingValue(
                null_predicate="current_minus_baseline_equals_zero",
                alternative="two_sided",
                method="welch_t",
                alpha=0.05,
                statistic=-2.4,
                p_value=0.018,
                effect_estimate=-45_337,
                confidence_interval=(-80_000, -10_000),
                reject_null=True,
                sample_size=120,
            )
        ),
        _finding(
            ForecastPointFindingValue(
                bucket_start="2026-09-01",
                bucket_end="2026-09-08",
                predicted_value=180_000,
                prediction_interval=(160_000, 200_000),
                horizon_index=1,
                model="linear",
                training_scope=scope,
                evaluation_scope=scope,
                observed_actual=175_000,
                accuracy_metric=-5_000,
            )
        ),
        _finding(
            QualityCheckFindingValue(
                check_id="null_rate",
                measured_value=0.01,
                expectation_predicate="less_than_or_equal",
                expectation_parameters={"max": 0.05},
                expectation_condition_passed=True,
                evaluated_scope=scope,
            )
        ),
    )


_NINE_EXPECTED = (
    (
        "sales.revenue: observed 173,758 USD across 1 rows.",
        "sales.revenue：观测值为 173,758 USD，共 1 行。",
    ),
    (
        "sales.revenue [region=west]: observed 173,758 USD, bucket 2026-08-24.",
        "sales.revenue [region=west]：观测值为 173,758 USD，时间桶 2026-08-24。",
    ),
    (
        "sales.revenue [region=west]: current 173,758 USD, baseline 219,095 USD; "
        "decreased by 45,337 USD (20.7%); current window [2026-08-24, 2026-08-28), "
        "baseline window [2026-08-17, 2026-08-21).",
        "sales.revenue [region=west]：本期 173,758 USD、基线 219,095 USD，下降 "
        "45,337 USD（20.7%）；本期区间为 [2026-08-24, 2026-08-28)，"
        "基线区间为 [2026-08-17, 2026-08-21)。",
    ),
    (
        "sales.revenue [region=west]: region contributed −12,500 algebraically "
        "(27.5%, rank 1) using algebraic_decomposition; this contribution is not causal.",
        "sales.revenue [region=west]：维度 region 的代数贡献为 −12,500"
        "（占比 27.5%，排名 1），方法为 algebraic_decomposition；该贡献不表示因果。",
    ),
    (
        "sales.revenue: candidate region=west ranks 1; robust_z score 3.5 against threshold 3, "
        "current 173,758, baseline 219,095, deviation −45,337 (−20.7%); this is a review "
        "candidate, not a confirmed anomaly.",
        "sales.revenue：候选 region=west 排名 1，robust_z 得分 3.5、阈值 3，本期 "
        "173,758、基线 219,095、偏差 −45,337（−20.7%）；这是待复核候选，不是已确认异常。",
    ),
    (
        "sales.revenue: pearson association between sales.revenue and sales.orders has "
        "coefficient 0.812345, p=0.012345, interval [0.5, 0.9], n=120, and lag=0; "
        "association does not imply causation.",
        "sales.revenue：sales.revenue 与 sales.orders 的 pearson 相关系数为 0.812345，"
        "p=0.012345，区间 [0.5, 0.9]，样本量 120，lag=0；相关不表示因果。",
    ),
    (
        "sales.revenue: welch_t rejected the null “current_minus_baseline_equals_zero”; "
        "p=0.018, alpha=0.05, statistic −2.4, effect −45,337, interval "
        "[−80,000, −10,000], n=120.",
        "sales.revenue：welch_t 检验对原假设“current_minus_baseline_equals_zero”的结论为拒绝，"
        "p=0.018、alpha=0.05、统计量 −2.4、效应 −45,337、区间 "
        "[−80,000, −10,000]、样本量 120。",
    ),
    (
        "sales.revenue: predicted 180,000 for 2026-09-01 to 2026-09-08, interval "
        "[160,000, 200,000], horizon 1, model linear; actual 175,000, error −5,000.",
        "sales.revenue：2026-09-01 至 2026-09-08 的预测值为 180,000，预测区间 "
        "[160,000, 200,000]，horizon 1，模型 linear；实际值 175,000、误差 −5,000。",
    ),
    (
        "sales.revenue: quality check null_rate passed; measured 0.01 against less_than_or_equal.",
        "sales.revenue：质量检查 null_rate 通过；测量值 0.01，期望谓词 less_than_or_equal。",
    ),
)


@pytest.mark.parametrize(
    ("finding", "expected_english", "expected_chinese"),
    tuple(
        pytest.param(finding, english, chinese, id=finding.finding_type)
        for finding, (english, chinese) in zip(_nine_findings(), _NINE_EXPECTED, strict=True)
    ),
)
def test_all_nine_finding_variants_match_exact_bilingual_output(
    finding: Finding,
    expected_english: str,
    expected_chinese: str,
) -> None:
    assert finding.render() == expected_english
    assert finding.render(language="en") == expected_english
    assert finding.render(language="zh") == expected_chinese
    assert finding.finding_id not in expected_english
    assert finding.finding_id not in expected_chinese


def _observation_values() -> tuple[object, ...]:
    return (
        ScalarObservationValue(value=10, unit="USD"),
        TimeSeriesObservationValue(
            bucket_count=2,
            first_bucket="2026-08-01",
            last_bucket="2026-08-02",
            first_value=10,
            last_value=12,
            min_value=10,
            max_value=12,
            mean_value=11,
            endpoint_change_direction="increase",
        ),
        SegmentedObservationValue(
            segment_count=1,
            total_value=10,
            top_segments=(ObservationSegmentValue(keys={"region": "west"}, value=10, share=1),),
        ),
        PanelObservationValue(
            bucket_count=2,
            segment_count=1,
            total_value=22,
            top_segments=(ObservationSegmentValue(keys={"region": "west"}, value=22, share=1),),
        ),
        EventJourneyObservationValue(
            attempt_count=3,
            complete_count=1,
            incomplete_count=1,
            coverage_censored_count=1,
            unused_event_count=2,
        ),
        EventFunnelObservationValue(
            cohort_count=3,
            step_count=2,
            axis_tuple_count=1,
            source_unused_event_count=2,
            grouped=False,
            reconciliation_passed=True,
        ),
        EventTimeToEventObservationValue(
            qualifying_count=3,
            complete_count=1,
            incomplete_count=1,
            coverage_censored_count=1,
            source_unused_end_count=2,
            duration_count=1,
            min_duration_seconds=10,
            median_duration_seconds=20,
            max_duration_seconds=30,
        ),
        LifecycleHistoryObservationValue(
            population_count=3,
            seeded_subject_count=1,
            coverage_censored_subject_count=1,
            interval_count=3,
            completed_interval_count=1,
            right_censored_interval_count=1,
            coverage_censored_interval_count=1,
            violation_count=0,
        ),
        LifecycleDistributionObservationValue(
            instant_count=2,
            state_count=3,
            row_count=6,
            grouped=False,
            reconciliation_passed=True,
        ),
        LifecycleTransitionsObservationValue(
            modeled_pair_count=4,
            transition_count=3,
            nonzero_pair_count=2,
        ),
        LifecycleDwellObservationValue(
            state_count=2,
            interval_count=3,
            completed_count=1,
            right_censored_count=1,
            coverage_censored_count=1,
        ),
        LifecycleViolationsObservationValue(
            violation_count=2,
            illegal_transition_count=1,
            transition_from_terminal_count=1,
        ),
        SubjectSetObservationValue(
            selected_count=3,
            excluded_coverage_censored_count=1,
            coverage_status="coverage_censored",
        ),
        FunnelDeltaObservationValue(
            step_count=3,
            axis_count=1,
            zero_filled_tuple_count=2,
            current_coverage_basis="complete",
            baseline_coverage_basis="complete",
        ),
        FunnelAttributionObservationValue(
            target_step_key="payment",
            contribution_count=2,
            positive_pool=10,
            negative_pool=-4,
            residual=0,
            reconciliation_status="reconciled",
        ),
    )


_OBSERVATION_EXPECTED = (
    (
        "sales.revenue: observed 10 USD across 3 rows.",
        "sales.revenue：观测值为 10 USD，共 3 行。",
    ),
    (
        "sales.revenue: observed 2 time buckets from 2026-08-01 to 2026-08-02; "
        "first 10, last 12, min 10, max 12, mean 11; the endpoint increased.",
        "sales.revenue：观测到 2 个时间桶（2026-08-01 至 2026-08-02），首值 10、末值 12、"
        "最小值 10、最大值 12、均值 11，端点上升。",
    ),
    (
        "sales.revenue: observed 1 segments totaling 10; top segments: region=west: 10 (100.0%).",
        "sales.revenue：观测到 1 个分群、合计 10；主要分群为 region=west: 10 (100.0%)。",
    ),
    (
        "sales.revenue: observed 2 time buckets and 1 segments totaling 22; "
        "top segments: region=west: 22 (100.0%).",
        "sales.revenue：观测到 2 个时间桶、1 个分群，合计 22；"
        "主要分群为 region=west: 22 (100.0%)。",
    ),
    (
        "sales.revenue: 3 journeys; 1 complete, 1 incomplete, 1 coverage-censored, "
        "and 2 unused events.",
        "sales.revenue：共 3 次 journey，完成 1、未完成 1、覆盖删失 1，未使用事件 2。",
    ),
    (
        "sales.revenue: funnel cohort 3 across 2 steps and 1 axis tuples; grouped=false, "
        "reconciled=true, with 2 unused source events.",
        "sales.revenue：漏斗 cohort 3、步骤 2、轴组合 1，分组=false、对账通过，源未使用事件 2。",
    ),
    (
        "sales.revenue: 3 qualifying cases; 1 complete, 1 incomplete, 1 coverage-censored; "
        "median duration 20 seconds and 2 unused source end events.",
        "sales.revenue：符合条件 3，完成 1、未完成 1、覆盖删失 1，中位耗时 20 秒，"
        "源未使用结束事件 2。",
    ),
    (
        "sales.revenue: lifecycle population 3, 1 seeded, 3 intervals, "
        "1 coverage-censored, and 0 violations.",
        "sales.revenue：生命周期 population 3、seeded 1，区间 3、覆盖删失 1、违规 0。",
    ),
    (
        "sales.revenue: lifecycle distribution across 2 instants, 3 states, and 6 rows; "
        "grouped=false, reconciled=true.",
        "sales.revenue：生命周期分布包含 2 个时点、3 个状态、6 行，分组=false、对账=true。",
    ),
    (
        "sales.revenue: lifecycle transitions cover 4 modeled pairs and 3 transitions, "
        "with 2 nonzero pairs.",
        "sales.revenue：生命周期转移包含 4 个建模状态对、3 次转移，其中 2 个非零状态对。",
    ),
    (
        "sales.revenue: lifecycle dwell covers 2 states and 3 intervals; 1 complete, "
        "1 right-censored, and 1 coverage-censored.",
        "sales.revenue：生命周期停留覆盖 2 个状态、3 个区间，完成 1、右删失 1、覆盖删失 1。",
    ),
    (
        "sales.revenue: found 2 lifecycle violations: 1 illegal transitions and "
        "1 transitions from terminal states.",
        "sales.revenue：发现 2 个生命周期违规，其中非法转移 1、终态后转移 1。",
    ),
    (
        "sales.revenue: selected 3 subjects, excluded 1 coverage-censored subjects, "
        "with coverage status coverage_censored.",
        "sales.revenue：选中 3 个主体，排除覆盖删失主体 1，覆盖状态为 coverage_censored。",
    ),
    (
        "sales.revenue: funnel comparison covers 3 steps and 1 axes with 2 zero-filled tuples; "
        "current coverage complete, baseline coverage complete.",
        "sales.revenue：漏斗比较覆盖 3 个步骤、1 个轴，零填充 2 个组合；"
        "本期覆盖=complete、基线覆盖=complete。",
    ),
    (
        "sales.revenue: funnel attribution for payment has 2 contributions, positive pool 10, "
        "negative pool −4, residual 0, and status reconciled.",
        "sales.revenue：漏斗归因目标 payment，贡献项 2，正向池 10、负向池 −4、"
        "残差 0，状态为 reconciled。",
    ),
)


@pytest.mark.parametrize(
    ("value", "expected_english", "expected_chinese"),
    tuple(
        pytest.param(value, english, chinese, id=value.shape)
        for value, (english, chinese) in zip(
            _observation_values(), _OBSERVATION_EXPECTED, strict=True
        )
    ),
)
def test_every_observation_shape_matches_exact_bilingual_output(
    value: object,
    expected_english: str,
    expected_chinese: str,
) -> None:
    finding = _finding(ObservationFindingValue(row_count=3, value=value))

    assert finding.render() == expected_english
    assert finding.render(language="zh") == expected_chinese
    assert "\n" not in expected_english
    assert "\n" not in expected_chinese


def test_finding_render_validates_language_bounds_and_show(
    capsys: pytest.CaptureFixture[str],
) -> None:
    finding = _finding(
        AnomalyCandidateFindingValue(
            candidate_ref="candidate-" + "宽" * 200,
            detector="robust_z",
            rank=1,
        )
    )

    with pytest.raises(ValueError, match="language must be"):
        render_finding(finding, language="fr")
    bounded = finding.render(max_output_bytes=160)
    assert len(bounded.encode("utf-8")) <= 160
    assert "output truncated" in bounded
    assert "\n" not in bounded
    assert "candidate-" + "宽" * 200 in finding.render(max_output_bytes=None)
    with pytest.raises(ValueError, match="max_output_bytes is too small"):
        finding.render(max_output_bytes=1)

    finding.show(language="zh")
    assert capsys.readouterr().out == finding.render(language="zh") + "\n"


def test_number_rendering_uses_grouping_and_six_significant_digits() -> None:
    rounded_fraction = _finding(MetricValueFindingValue(value=1_234_567.8))
    exact_integer = _finding(MetricValueFindingValue(value=1_234_567))

    assert "1,234,570" in rounded_fraction.render()
    assert "1,234,567" in exact_integer.render()
    assert "1,234,570" not in exact_integer.render()


def test_coordinate_rendering_is_sorted_unambiguous_and_lossless() -> None:
    finding = _finding(
        MetricValueFindingValue(
            value=1,
            dimension_keys={
                "tier]": "gold",
                "line": "north\nsouth",
                "city": "Washington, D.C.",
                "channel": "paid=search",
            },
        )
    )

    expected_coordinates = (
        '[channel="paid=search", city="Washington, D.C.", line="north\\nsouth", "tier]"=gold]'
    )
    for language in ("en", "zh"):
        rendered = finding.render(language=language)
        assert expected_coordinates in rendered
        assert "\n" not in rendered


@pytest.mark.parametrize(
    ("observed_actual", "accuracy_metric", "english_clause", "chinese_clause"),
    (
        (10, None, "; actual 10.", "；实际值 10。"),
        (None, 2, "; error 2.", "；误差 2。"),
    ),
)
def test_forecast_renderer_omits_each_absent_optional_clause(
    observed_actual: float | None,
    accuracy_metric: float | None,
    english_clause: str,
    chinese_clause: str,
) -> None:
    scope = make_test_analysis_scope("sales.revenue")
    finding = _finding(
        ForecastPointFindingValue(
            bucket_start="2026-09-01",
            bucket_end="2026-09-08",
            predicted_value=9,
            horizon_index=1,
            model="linear",
            training_scope=scope,
            observed_actual=observed_actual,
            accuracy_metric=accuracy_metric,
        )
    )

    english = finding.render()
    chinese = finding.render(language="zh")
    assert english.endswith(english_clause)
    assert chinese.endswith(chinese_clause)
    if observed_actual is None:
        assert "actual" not in english
        assert "实际值" not in chinese
    if accuracy_metric is None:
        assert "error" not in english
        assert "误差" not in chinese


def test_finding_repr_and_page_render_preserve_stable_identity() -> None:
    findings = _nine_findings()[:2]
    page = FindingPage(items=findings, limit=2, has_more=True, next_cursor="cursor_1")

    assert isinstance(findings[0], AgentResult)
    assert isinstance(page, AgentResult)
    assert repr(findings[0]) == (
        "<Finding id=fnd_observation_1 type=observation; call .show() to inspect>"
    )
    english = page.render()
    chinese = page.render(language="zh")
    for finding in findings:
        assert f"{finding.finding_id}: {finding.render()}" in english
        assert f"{finding.finding_id}: {finding.render(language='zh')}" in chinese
    assert "next_cursor: cursor_1" in english
    assert "next_cursor: cursor_1" in chinese
    bounded = page.render(max_output_bytes=260)
    assert len(bounded.encode("utf-8")) <= 260
    assert "output truncated" in bounded

    empty_page = FindingPage(items=(), limit=1, has_more=False, next_cursor=None)
    with pytest.raises(ValueError, match="language must be"):
        empty_page.render(language="fr")


def test_focused_evidence_help_teaches_readable_rendering() -> None:
    finding_help = rendered_help("session.evidence.finding", owner="analysis")
    findings_help = rendered_help("session.evidence.findings", owner="analysis")

    assert "finding.show()" in finding_help
    assert 'finding.show(language="zh")' in finding_help
    assert "page.show()" in findings_help
    assert 'page.show(language="zh")' in findings_help


def test_delta_without_both_windows_omits_window_statement() -> None:
    finding = _finding(
        DeltaFindingValue(
            delta_kind="scalar_delta",
            current=2,
            baseline=1,
            magnitude=1,
            relative_delta=1,
            direction="increase",
            current_window=TimeWindow(field="time", start="2026-08-01", end="2026-08-02"),
        )
    )

    assert "window" not in finding.render()
    assert "区间" not in finding.render(language="zh")
