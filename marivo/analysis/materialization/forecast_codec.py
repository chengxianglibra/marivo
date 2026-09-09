"""Closed Forecast descriptor and bounded training Evidence codecs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization.contracts import (
    FINDING_CAP,
    _array,
    _hash,
    _int,
    _obj,
    _text,
    invalid,
)
from marivo.analysis.operators.forecast_contracts import ForecastSemantics, ForecastTrainingSummary


@dataclass(frozen=True, slots=True)
class ForecastEvidenceSummary:
    row_count: int
    emitted_finding_count: int
    finding_set_digest: str
    training: ForecastTrainingSummary


def semantics_payload(s: ForecastSemantics) -> dict[str, object]:
    return {
        name: getattr(s, name)
        for name in (
            "kind",
            "metric_key",
            "metric_unit",
            "approximation",
            "fold_authority",
            "model_id",
            "season_length",
            "horizon",
            "interval_level",
            "interval_method",
            "assumption_contract",
        )
    }


def _finite(value: object) -> float:
    if type(value) is not int and type(value) is not float:
        raise invalid("non-finite Forecast value")
    if not math.isfinite(value):
        raise invalid("non-finite Forecast value")
    return float(value)


def decode_semantics(value: object) -> ForecastSemantics:
    obj = _obj(
        value,
        "kind metric_key metric_unit approximation fold_authority model_id season_length horizon interval_level interval_method assumption_contract",
    )
    model = obj["model_id"]
    if obj["kind"] != "forecast/metric@v1" or model not in (
        "naive@v1",
        "drift@v1",
        "seasonal_naive@v1",
    ):
        raise invalid("invalid Forecast model")
    return ForecastSemantics(
        _token=d._CORE_TOKEN,
        metric_key=_text(obj["metric_key"]),
        metric_unit=None if obj["metric_unit"] is None else _text(obj["metric_unit"]),
        approximation=_text(obj["approximation"]),
        fold_authority=_text(obj["fold_authority"]),
        model_id="naive@v1"
        if model == "naive@v1"
        else "drift@v1"
        if model == "drift@v1"
        else "seasonal_naive@v1",
        season_length=None
        if obj["season_length"] is None
        else _int(obj["season_length"], minimum=2),
        horizon=_int(obj["horizon"], minimum=1),
        interval_level=_finite(obj["interval_level"]),
        interval_method=_text(obj["interval_method"]),
        assumption_contract=_text(obj["assumption_contract"]),
    )


def evidence_payload(value: ForecastEvidenceSummary | None) -> object:
    return None if value is None else {"schema": "marivo.forecast_evidence/v1", **asdict(value)}


def decode_evidence(value: object) -> ForecastEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(value, "schema row_count emitted_finding_count finding_set_digest training")
    if obj["schema"] != "marivo.forecast_evidence/v1":
        raise invalid("invalid Forecast Evidence schema")
    t = _obj(
        obj["training"],
        "series_count training_row_count residual_count residual_df zero_residual_series_count variance_range history_start history_end future_coordinates",
    )
    variances = tuple(_finite(v) for v in _array(t["variance_range"]))
    if len(variances) != 2 or not 0 <= variances[0] <= variances[1]:
        raise invalid("invalid Forecast variance range")
    summary = ForecastTrainingSummary(
        _int(t["series_count"], minimum=1),
        _int(t["training_row_count"], minimum=2),
        _int(t["residual_count"], minimum=1),
        _int(t["residual_df"], minimum=1),
        _int(t["zero_residual_series_count"]),
        (variances[0], variances[1]),
        _text(t["history_start"]),
        _text(t["history_end"]),
        tuple(_text(v) for v in _array(t["future_coordinates"])),
    )
    result = ForecastEvidenceSummary(
        _int(obj["row_count"]),
        _int(obj["emitted_finding_count"]),
        _text(obj["finding_set_digest"]),
        summary,
    )
    _hash(result.finding_set_digest)
    if (
        result.emitted_finding_count != min(FINDING_CAP, result.row_count)
        or not 0 <= summary.zero_residual_series_count <= summary.series_count
        or len(summary.future_coordinates) not in range(1, 1001)
    ):
        raise invalid("inconsistent Forecast Evidence counts")
    return result
