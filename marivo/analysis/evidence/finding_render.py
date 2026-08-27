"""Pure bilingual rendering for canonical typed findings."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Literal, cast

from marivo.analysis.evidence.summary import render_evidence_subject
from marivo.analysis.evidence.types import (
    AnomalyCandidateFindingValue,
    AssociationFindingValue,
    ContributionFindingValue,
    DeltaFindingValue,
    EventFunnelObservationValue,
    EventJourneyObservationValue,
    EventTimeToEventObservationValue,
    EvidenceSubject,
    Finding,
    ForecastPointFindingValue,
    FunnelAttributionObservationValue,
    FunnelDeltaObservationValue,
    JsonScalar,
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
)

FindingLanguage = Literal["en", "zh"]


def _validate_language(language: str) -> FindingLanguage:
    if language not in {"en", "zh"}:
        raise ValueError("language must be 'en' or 'zh'")
    return cast("FindingLanguage", language)


def _clean_text(value: object) -> str:
    raw = str(value)
    return "".join(
        json.dumps(character, ensure_ascii=True)[1:-1]
        if ord(character) < 32
        or ord(character) == 127
        or character in {"\u0085", "\u2028", "\u2029"}
        else character
        for character in raw
    )


def _number(value: float | int | None, language: FindingLanguage) -> str:
    missing = "not computed" if language == "en" else "未计算"
    if value is None or isinstance(value, bool):
        return missing
    numeric = float(value)
    if not math.isfinite(numeric):
        return missing
    if numeric == 0:
        return "0"
    if numeric.is_integer():
        return format(numeric, ",.0f").replace("-", "−")
    magnitude = math.floor(math.log10(abs(numeric)))
    decimal_places = 5 - magnitude
    if decimal_places < 0:
        rendered = format(round(numeric, decimal_places), ",.0f")
    elif decimal_places <= 12:
        rendered = format(numeric, f",.{decimal_places}f").rstrip("0").rstrip(".")
    else:
        rendered = format(numeric, ".6g")
    return rendered.replace("-", "−")


def _percent(value: float | None, language: FindingLanguage) -> str:
    if value is None or not math.isfinite(value):
        return "not computed" if language == "en" else "未计算"
    return format(value, ".1%").replace("-", "−")


def _interval(value: tuple[float, float] | None, language: FindingLanguage) -> str:
    if value is None:
        return "not computed" if language == "en" else "未计算"
    return f"[{_number(value[0], language)}, {_number(value[1], language)}]"


def _scalar(value: JsonScalar) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _coordinate_token(value: str) -> str:
    if value and all(not character.isspace() and character not in ',=[]"\\' for character in value):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _coordinate_scalar(value: JsonScalar) -> str:
    if isinstance(value, str):
        return _coordinate_token(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _coordinates(values: Mapping[str, JsonScalar]) -> str:
    return ", ".join(
        f"{_coordinate_token(key)}={_coordinate_scalar(values[key])}" for key in sorted(values)
    )


def _target(
    subject: EvidenceSubject,
    coordinates: Mapping[str, JsonScalar] | None = None,
) -> str:
    rendered = render_evidence_subject(subject)
    coordinate_text = _coordinates(coordinates or {})
    return f"{rendered} [{coordinate_text}]" if coordinate_text else rendered


def _value_with_unit(
    value: float | None,
    unit: str | None,
    language: FindingLanguage,
) -> str:
    rendered = _number(value, language)
    return f"{rendered} {_clean_text(unit)}" if unit else rendered


def _direction(value: str, language: FindingLanguage) -> str:
    labels = {
        "en": {
            "increase": "increased",
            "decrease": "decreased",
            "flat": "was unchanged",
            "undefined": "had undefined change",
        },
        "zh": {
            "increase": "上升",
            "decrease": "下降",
            "flat": "持平",
            "undefined": "变化未定义",
        },
    }
    return labels[language][value]


def _segments(
    segments: tuple[ObservationSegmentValue, ...],
    language: FindingLanguage,
) -> str:
    if not segments:
        return "none" if language == "en" else "无"
    rendered: list[str] = []
    for segment in segments:
        keys = _coordinates(segment.keys) or ("all" if language == "en" else "全部")
        body = f"{keys}: {_number(segment.value, language)}"
        if segment.share is not None:
            body = f"{body} ({_percent(segment.share, language)})"
        rendered.append(body)
    return "; ".join(rendered)


def _render_observation(finding: Finding, language: FindingLanguage) -> str:
    wrapped = finding.value
    if not isinstance(wrapped, ObservationFindingValue):
        raise TypeError("observation finding requires ObservationFindingValue")
    value = wrapped.value
    target = _target(finding.subject)
    if isinstance(value, ScalarObservationValue):
        observed = _value_with_unit(value.value, value.unit, language)
        if language == "zh":
            return f"{target}：观测值为 {observed}，共 {wrapped.row_count:,} 行。"
        return f"{target}: observed {observed} across {wrapped.row_count:,} rows."
    if isinstance(value, TimeSeriesObservationValue):
        first_bucket = _clean_text(
            value.first_bucket or ("unknown" if language == "en" else "未知")
        )
        last_bucket = _clean_text(value.last_bucket or ("unknown" if language == "en" else "未知"))
        if language == "zh":
            tail = "，末桶不完整" if value.partial_tail_bucket else ""
            return (
                f"{target}：观测到 {value.bucket_count:,} 个时间桶（{first_bucket} 至 {last_bucket}），"
                f"首值 {_number(value.first_value, language)}、末值 {_number(value.last_value, language)}、"
                f"最小值 {_number(value.min_value, language)}、最大值 {_number(value.max_value, language)}、"
                f"均值 {_number(value.mean_value, language)}，端点{_direction(value.endpoint_change_direction, language)}{tail}。"
            )
        tail = "; the final bucket is partial" if value.partial_tail_bucket else ""
        return (
            f"{target}: observed {value.bucket_count:,} time buckets from {first_bucket} to {last_bucket}; "
            f"first {_number(value.first_value, language)}, last {_number(value.last_value, language)}, "
            f"min {_number(value.min_value, language)}, max {_number(value.max_value, language)}, "
            f"mean {_number(value.mean_value, language)}; the endpoint {_direction(value.endpoint_change_direction, language)}{tail}."
        )
    if isinstance(value, SegmentedObservationValue):
        total = _value_with_unit(value.total_value, value.unit, language)
        top = _segments(value.top_segments, language)
        if language == "zh":
            return (
                f"{target}：观测到 {value.segment_count:,} 个分群、合计 {total}；主要分群为 {top}。"
            )
        return f"{target}: observed {value.segment_count:,} segments totaling {total}; top segments: {top}."
    if isinstance(value, PanelObservationValue):
        total = _value_with_unit(value.total_value, value.unit, language)
        top = _segments(value.top_segments, language)
        if language == "zh":
            return (
                f"{target}：观测到 {value.bucket_count:,} 个时间桶、{value.segment_count:,} 个分群，"
                f"合计 {total}；主要分群为 {top}。"
            )
        return (
            f"{target}: observed {value.bucket_count:,} time buckets and {value.segment_count:,} "
            f"segments totaling {total}; top segments: {top}."
        )
    if isinstance(value, EventJourneyObservationValue):
        if language == "zh":
            return (
                f"{target}：共 {value.attempt_count:,} 次 journey，完成 {value.complete_count:,}、"
                f"未完成 {value.incomplete_count:,}、覆盖删失 {value.coverage_censored_count:,}，"
                f"未使用事件 {value.unused_event_count:,}。"
            )
        return (
            f"{target}: {value.attempt_count:,} journeys; {value.complete_count:,} complete, "
            f"{value.incomplete_count:,} incomplete, {value.coverage_censored_count:,} coverage-censored, "
            f"and {value.unused_event_count:,} unused events."
        )
    if isinstance(value, EventFunnelObservationValue):
        reconciled = "通过" if value.reconciliation_passed else "未通过"
        if language == "zh":
            return (
                f"{target}：漏斗 cohort {value.cohort_count:,}、步骤 {value.step_count:,}、"
                f"轴组合 {value.axis_tuple_count:,}，分组={str(value.grouped).lower()}、对账{reconciled}，"
                f"源未使用事件 {value.source_unused_event_count:,}。"
            )
        return (
            f"{target}: funnel cohort {value.cohort_count:,} across {value.step_count:,} steps and "
            f"{value.axis_tuple_count:,} axis tuples; grouped={str(value.grouped).lower()}, "
            f"reconciled={str(value.reconciliation_passed).lower()}, with "
            f"{value.source_unused_event_count:,} unused source events."
        )
    if isinstance(value, EventTimeToEventObservationValue):
        median = _number(value.median_duration_seconds, language)
        if language == "zh":
            return (
                f"{target}：符合条件 {value.qualifying_count:,}，完成 {value.complete_count:,}、"
                f"未完成 {value.incomplete_count:,}、覆盖删失 {value.coverage_censored_count:,}，"
                f"中位耗时 {median} 秒，源未使用结束事件 {value.source_unused_end_count:,}。"
            )
        return (
            f"{target}: {value.qualifying_count:,} qualifying cases; {value.complete_count:,} complete, "
            f"{value.incomplete_count:,} incomplete, {value.coverage_censored_count:,} coverage-censored; "
            f"median duration {median} seconds and {value.source_unused_end_count:,} unused source end events."
        )
    if isinstance(value, LifecycleHistoryObservationValue):
        if language == "zh":
            return (
                f"{target}：生命周期 population {value.population_count:,}、seeded {value.seeded_subject_count:,}，"
                f"区间 {value.interval_count:,}、覆盖删失 {value.coverage_censored_interval_count:,}、"
                f"违规 {value.violation_count:,}。"
            )
        return (
            f"{target}: lifecycle population {value.population_count:,}, {value.seeded_subject_count:,} seeded, "
            f"{value.interval_count:,} intervals, {value.coverage_censored_interval_count:,} coverage-censored, "
            f"and {value.violation_count:,} violations."
        )
    if isinstance(value, LifecycleDistributionObservationValue):
        if language == "zh":
            return (
                f"{target}：生命周期分布包含 {value.instant_count:,} 个时点、{value.state_count:,} 个状态、"
                f"{value.row_count:,} 行，分组={str(value.grouped).lower()}、"
                f"对账={str(value.reconciliation_passed).lower()}。"
            )
        return (
            f"{target}: lifecycle distribution across {value.instant_count:,} instants, "
            f"{value.state_count:,} states, and {value.row_count:,} rows; "
            f"grouped={str(value.grouped).lower()}, reconciled={str(value.reconciliation_passed).lower()}."
        )
    if isinstance(value, LifecycleTransitionsObservationValue):
        if language == "zh":
            return (
                f"{target}：生命周期转移包含 {value.modeled_pair_count:,} 个建模状态对、"
                f"{value.transition_count:,} 次转移，其中 {value.nonzero_pair_count:,} 个非零状态对。"
            )
        return (
            f"{target}: lifecycle transitions cover {value.modeled_pair_count:,} modeled pairs and "
            f"{value.transition_count:,} transitions, with {value.nonzero_pair_count:,} nonzero pairs."
        )
    if isinstance(value, LifecycleDwellObservationValue):
        if language == "zh":
            return (
                f"{target}：生命周期停留覆盖 {value.state_count:,} 个状态、{value.interval_count:,} 个区间，"
                f"完成 {value.completed_count:,}、右删失 {value.right_censored_count:,}、"
                f"覆盖删失 {value.coverage_censored_count:,}。"
            )
        return (
            f"{target}: lifecycle dwell covers {value.state_count:,} states and {value.interval_count:,} "
            f"intervals; {value.completed_count:,} complete, {value.right_censored_count:,} right-censored, "
            f"and {value.coverage_censored_count:,} coverage-censored."
        )
    if isinstance(value, LifecycleViolationsObservationValue):
        if language == "zh":
            return (
                f"{target}：发现 {value.violation_count:,} 个生命周期违规，其中非法转移 "
                f"{value.illegal_transition_count:,}、终态后转移 {value.transition_from_terminal_count:,}。"
            )
        return (
            f"{target}: found {value.violation_count:,} lifecycle violations: "
            f"{value.illegal_transition_count:,} illegal transitions and "
            f"{value.transition_from_terminal_count:,} transitions from terminal states."
        )
    if isinstance(value, SubjectSetObservationValue):
        if language == "zh":
            return (
                f"{target}：选中 {value.selected_count:,} 个主体，排除覆盖删失主体 "
                f"{value.excluded_coverage_censored_count:,}，覆盖状态为 {value.coverage_status}。"
            )
        return (
            f"{target}: selected {value.selected_count:,} subjects, excluded "
            f"{value.excluded_coverage_censored_count:,} coverage-censored subjects, "
            f"with coverage status {value.coverage_status}."
        )
    if isinstance(value, FunnelDeltaObservationValue):
        if language == "zh":
            return (
                f"{target}：漏斗比较覆盖 {value.step_count:,} 个步骤、{value.axis_count:,} 个轴，"
                f"零填充 {value.zero_filled_tuple_count:,} 个组合；本期覆盖={value.current_coverage_basis}、"
                f"基线覆盖={value.baseline_coverage_basis}。"
            )
        return (
            f"{target}: funnel comparison covers {value.step_count:,} steps and {value.axis_count:,} axes "
            f"with {value.zero_filled_tuple_count:,} zero-filled tuples; current coverage "
            f"{value.current_coverage_basis}, baseline coverage {value.baseline_coverage_basis}."
        )
    if isinstance(value, FunnelAttributionObservationValue):
        if language == "zh":
            return (
                f"{target}：漏斗归因目标 {value.target_step_key}，贡献项 {value.contribution_count:,}，"
                f"正向池 {_number(value.positive_pool, language)}、负向池 {_number(value.negative_pool, language)}、"
                f"残差 {_number(value.residual, language)}，状态为 {value.reconciliation_status}。"
            )
        return (
            f"{target}: funnel attribution for {value.target_step_key} has "
            f"{value.contribution_count:,} contributions, positive pool {_number(value.positive_pool, language)}, "
            f"negative pool {_number(value.negative_pool, language)}, residual {_number(value.residual, language)}, "
            f"and status {value.reconciliation_status}."
        )
    raise TypeError(f"unsupported observation value type {type(value).__name__}")


def _render_metric_value(
    finding: Finding, value: MetricValueFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject, value.dimension_keys)
    bucket = f"，时间桶 {value.bucket}" if language == "zh" and value.bucket else ""
    if language == "en":
        bucket = f", bucket {value.bucket}" if value.bucket else ""
        return f"{target}: observed {_value_with_unit(value.value, value.unit, language)}{bucket}."
    return f"{target}：观测值为 {_value_with_unit(value.value, value.unit, language)}{bucket}。"


def _render_delta(finding: Finding, value: DeltaFindingValue, language: FindingLanguage) -> str:
    target = _target(finding.subject, value.dimension_keys)
    bucket = f"，时间桶 {value.bucket}" if language == "zh" and value.bucket else ""
    if language == "en":
        bucket = f", bucket {value.bucket}" if value.bucket else ""
    current = _value_with_unit(value.current, value.unit, language)
    baseline = _value_with_unit(value.baseline, value.unit, language)
    magnitude = _value_with_unit(
        abs(value.magnitude) if value.magnitude is not None else None, value.unit, language
    )
    relative = _percent(
        abs(value.relative_delta) if value.relative_delta is not None else None, language
    )
    windows = ""
    if value.current_window is not None and value.baseline_window is not None:
        current_window = f"[{value.current_window.start}, {value.current_window.end})"
        baseline_window = f"[{value.baseline_window.start}, {value.baseline_window.end})"
        if language == "zh":
            windows = f"；本期区间为 {current_window}，基线区间为 {baseline_window}"
        else:
            windows = f"; current window {current_window}, baseline window {baseline_window}"
    if language == "zh":
        return (
            f"{target}{bucket}：本期 {current}、基线 {baseline}，{_direction(value.direction, language)} "
            f"{magnitude}（{relative}）{windows}。"
        )
    return (
        f"{target}{bucket}: current {current}, baseline {baseline}; {_direction(value.direction, language)} "
        f"by {magnitude} ({relative}){windows}."
    )


def _render_contribution(
    finding: Finding, value: ContributionFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject, value.dimension_keys)
    rank = _number(value.contribution_rank, language)
    if language == "zh":
        return (
            f"{target}：维度 {value.dimension} 的代数贡献为 {_number(value.contribution_value, language)}"
            f"（占比 {_percent(value.contribution_share, language)}，排名 {rank}），方法为 "
            f"{value.decomposition_method}；该贡献不表示因果。"
        )
    return (
        f"{target}: {value.dimension} contributed {_number(value.contribution_value, language)} "
        f"algebraically ({_percent(value.contribution_share, language)}, rank {rank}) using "
        f"{value.decomposition_method}; this contribution is not causal."
    )


def _render_anomaly(
    finding: Finding, value: AnomalyCandidateFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject)
    if language == "zh":
        return (
            f"{target}：候选 {value.candidate_ref} 排名 {value.rank:,}，{value.detector} 得分 "
            f"{_number(value.score, language)}、阈值 {_number(value.threshold, language)}，本期 "
            f"{_number(value.current_value, language)}、基线 {_number(value.baseline_value, language)}、"
            f"偏差 {_number(value.deviation_absolute, language)}（{_percent(value.deviation_relative, language)}）；"
            "这是待复核候选，不是已确认异常。"
        )
    return (
        f"{target}: candidate {value.candidate_ref} ranks {value.rank:,}; {value.detector} score "
        f"{_number(value.score, language)} against threshold {_number(value.threshold, language)}, current "
        f"{_number(value.current_value, language)}, baseline {_number(value.baseline_value, language)}, "
        f"deviation {_number(value.deviation_absolute, language)} ({_percent(value.deviation_relative, language)}); "
        "this is a review candidate, not a confirmed anomaly."
    )


def _render_association(
    finding: Finding, value: AssociationFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject)
    if language == "zh":
        return (
            f"{target}：{value.left_ref} 与 {value.right_ref} 的 {value.method} 相关系数为 "
            f"{_number(value.coefficient, language)}，p={_number(value.p_value, language)}，"
            f"区间 {_interval(value.confidence_interval, language)}，样本量 {_number(value.sample_size, language)}，"
            f"lag={_number(value.lag, language)}；相关不表示因果。"
        )
    return (
        f"{target}: {value.method} association between {value.left_ref} and {value.right_ref} has coefficient "
        f"{_number(value.coefficient, language)}, p={_number(value.p_value, language)}, interval "
        f"{_interval(value.confidence_interval, language)}, n={_number(value.sample_size, language)}, "
        f"and lag={_number(value.lag, language)}; association does not imply causation."
    )


def _render_test(finding: Finding, value: TestFindingValue, language: FindingLanguage) -> str:
    target = _target(finding.subject)
    decision_en = (
        "rejected"
        if value.reject_null is True
        else "not rejected"
        if value.reject_null is False
        else "not computed"
    )
    decision_zh = (
        "拒绝"
        if value.reject_null is True
        else "不拒绝"
        if value.reject_null is False
        else "未计算"
    )
    if language == "zh":
        return (
            f"{target}：{value.method} 检验对原假设“{_clean_text(value.null_predicate)}”的结论为{decision_zh}，"
            f"p={_number(value.p_value, language)}、alpha={_number(value.alpha, language)}、统计量 "
            f"{_number(value.statistic, language)}、效应 {_number(value.effect_estimate, language)}、"
            f"区间 {_interval(value.confidence_interval, language)}、样本量 {_number(value.sample_size, language)}。"
        )
    return (
        f"{target}: {value.method} {decision_en} the null “{_clean_text(value.null_predicate)}”; "
        f"p={_number(value.p_value, language)}, alpha={_number(value.alpha, language)}, statistic "
        f"{_number(value.statistic, language)}, effect {_number(value.effect_estimate, language)}, interval "
        f"{_interval(value.confidence_interval, language)}, n={_number(value.sample_size, language)}."
    )


def _render_forecast(
    finding: Finding, value: ForecastPointFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject)
    actual = ""
    actual_parts: list[str] = []
    if value.observed_actual is not None:
        label = "实际值" if language == "zh" else "actual"
        actual_parts.append(f"{label} {_number(value.observed_actual, language)}")
    if value.accuracy_metric is not None:
        label = "误差" if language == "zh" else "error"
        actual_parts.append(f"{label} {_number(value.accuracy_metric, language)}")
    if actual_parts:
        separator = "、" if language == "zh" else ", "
        prefix = "；" if language == "zh" else "; "
        actual = prefix + separator.join(actual_parts)
    if language == "zh":
        return (
            f"{target}：{value.bucket_start} 至 {value.bucket_end} 的预测值为 "
            f"{_number(value.predicted_value, language)}，预测区间 {_interval(value.prediction_interval, language)}，"
            f"horizon {value.horizon_index:,}，模型 {value.model}{actual}。"
        )
    return (
        f"{target}: predicted {_number(value.predicted_value, language)} for {value.bucket_start} to "
        f"{value.bucket_end}, interval {_interval(value.prediction_interval, language)}, horizon "
        f"{value.horizon_index:,}, model {value.model}{actual}."
    )


def _render_quality(
    finding: Finding, value: QualityCheckFindingValue, language: FindingLanguage
) -> str:
    target = _target(finding.subject)
    if value.measured_value is None:
        measured = "not computed" if language == "en" else "未计算"
    elif isinstance(value.measured_value, (int, float)) and not isinstance(
        value.measured_value, bool
    ):
        measured = _number(value.measured_value, language)
    else:
        measured = _scalar(value.measured_value)
    if language == "zh":
        status = "通过" if value.expectation_condition_passed else "失败"
        return (
            f"{target}：质量检查 {value.check_id} {status}；测量值 {measured}，期望谓词 "
            f"{_clean_text(value.expectation_predicate)}。"
        )
    status = "passed" if value.expectation_condition_passed else "failed"
    return (
        f"{target}: quality check {value.check_id} {status}; measured {measured} against "
        f"{_clean_text(value.expectation_predicate)}."
    )


def _render_body(finding: Finding, language: FindingLanguage) -> str:
    value = finding.value
    if isinstance(value, ObservationFindingValue):
        return _render_observation(finding, language)
    if isinstance(value, MetricValueFindingValue):
        return _render_metric_value(finding, value, language)
    if isinstance(value, DeltaFindingValue):
        return _render_delta(finding, value, language)
    if isinstance(value, ContributionFindingValue):
        return _render_contribution(finding, value, language)
    if isinstance(value, AnomalyCandidateFindingValue):
        return _render_anomaly(finding, value, language)
    if isinstance(value, AssociationFindingValue):
        return _render_association(finding, value, language)
    if isinstance(value, TestFindingValue):
        return _render_test(finding, value, language)
    if isinstance(value, ForecastPointFindingValue):
        return _render_forecast(finding, value, language)
    if isinstance(value, QualityCheckFindingValue):
        return _render_quality(finding, value, language)
    raise TypeError(f"unsupported finding value type {type(value).__name__}")


def _bounded_single_line(text: str, max_output_bytes: int | None, language: FindingLanguage) -> str:
    text = _clean_text(text)
    if max_output_bytes is None or len(text.encode("utf-8")) <= max_output_bytes:
        return text
    marker = (
        " … (output truncated; pass max_output_bytes=None for full output)"
        if language == "en"
        else "……（输出已截断；传入 max_output_bytes=None 可查看完整内容）"
    )
    marker_bytes = marker.encode("utf-8")
    if max_output_bytes < len(marker_bytes) + 1:
        raise ValueError(
            "max_output_bytes is too small to preserve the Finding truncation marker; "
            "pass max_output_bytes=None for full output"
        )
    prefix = text.encode("utf-8")[: max_output_bytes - len(marker_bytes)]
    while prefix:
        try:
            rendered_prefix = prefix.decode("utf-8")
            break
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    else:
        rendered_prefix = ""
    return rendered_prefix.rstrip() + marker


def render_finding(
    finding: Finding,
    *,
    language: str = "en",
    max_output_bytes: int | None = 8192,
) -> str:
    """Render one canonical Finding as bounded English or Chinese evidence prose."""
    selected_language = _validate_language(language)
    return _bounded_single_line(
        _render_body(finding, selected_language),
        max_output_bytes,
        selected_language,
    )


__all__ = ["FindingLanguage", "render_finding"]
