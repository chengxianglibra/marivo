"""Closed private forecast values, invocation and retained model authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, SupportsIndex

from marivo._compat import Never
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.operators.errors import forecast_error

ModelId = Literal["naive@v1", "drift@v1", "seasonal_naive@v1"]
INTERVAL_METHOD = "normal_residual@v1"
ASSUMPTIONS = "zero_mean_uncorrelated_homoskedastic_normal_innovations@v1"
_TOKEN = object()


@dataclass(frozen=True, slots=True, repr=False, init=False)
class ForecastHorizon:
    """Immutable helper-produced future-period count; use periods(count)."""

    count: int

    def __init__(self, count: int, *, _token: object = None) -> None:
        if _token is not _TOKEN or type(count) is not int or not 1 <= count <= 1000:
            raise forecast_error("periods(count) with an integer in [1, 1000]", "invalid horizon")
        object.__setattr__(self, "count", count)

    def __repr__(self) -> str:
        return f"ForecastHorizon(periods={self.count})"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise forecast_error("a helper-produced horizon", "generic serialization")

    def __init_subclass__(cls) -> None:
        raise TypeError("ForecastHorizon is sealed; use periods(count)")


@dataclass(frozen=True, slots=True, repr=False, init=False)
class ForecastModel:
    """Immutable named model; use naive(), drift() or seasonal_naive(periods=...)."""

    model_id: ModelId
    season_length: int | None

    def __init__(
        self, model_id: ModelId, season_length: int | None = None, *, _token: object = None
    ) -> None:
        if _token is not _TOKEN or model_id not in ("naive@v1", "drift@v1", "seasonal_naive@v1"):
            raise forecast_error("a helper-produced named ForecastModel", "invalid model")
        if (
            type(season_length) is not int or not season_length > 1
            if model_id == "seasonal_naive@v1"
            else season_length is not None
        ):
            raise forecast_error(
                "an explicit integer season length greater than one", "invalid season"
            )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "season_length", season_length)

    def __repr__(self) -> str:
        season = (
            str(self.season_length)
            if self.season_length is None or self.season_length.bit_length() <= 256
            else "<large integer>"
        )
        return f"ForecastModel(model={self.model_id}, periods={season})"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise forecast_error("a helper-produced named model", "generic serialization")

    def __init_subclass__(cls) -> None:
        raise TypeError("ForecastModel is sealed; use the named model helpers")


def periods(count: int) -> ForecastHorizon:
    """Return an immutable horizon of count future periods.

    Example: ``periods(14)``. Constraints: count must be an integer in [1, 1000].
    """
    return ForecastHorizon(count, _token=_TOKEN)


def naive() -> ForecastModel:
    """Return the last-value model; no parameters.

    Example: ``history.forecast(horizon=periods(2), model=naive())``.
    Constraints: At least two complete periods; intervals are nominal predictions.
    """
    return ForecastModel("naive@v1", _token=_TOKEN)


def drift() -> ForecastModel:
    """Return the mean-increment model; no parameters.

    Example: ``history.forecast(horizon=periods(2), model=drift())``.
    Constraints: At least three complete periods; includes increment-estimation variance.
    """
    return ForecastModel("drift@v1", _token=_TOKEN)


def seasonal_naive(*, periods: int) -> ForecastModel:
    """Return the seasonal last-value model with an explicit season of periods buckets.

    Example: ``seasonal_naive(periods=7)``. Constraints: periods > 1; history >= periods + 1.
    """
    return ForecastModel("seasonal_naive@v1", periods, _token=_TOKEN)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ForecastSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    metric_key: str
    metric_unit: str | None
    approximation: str
    fold_authority: str
    model_id: ModelId
    season_length: int | None
    horizon: int
    interval_level: float
    interval_method: str = INTERVAL_METHOD
    assumption_contract: str = ASSUMPTIONS
    kind: Literal["forecast/metric@v1"] = field(default="forecast/metric@v1", init=False)

    @property
    def minimum_history(self) -> int:
        return (
            (self.season_length or 0) + 1
            if self.model_id == "seasonal_naive@v1"
            else 3
            if self.model_id == "drift@v1"
            else 2
        )


@dataclass(frozen=True, slots=True, repr=False)
class ForecastSpecV1:
    input_row: d.DatasetRowContract
    input_rows: d.DatasetRowSetContract
    output_row: d.DatasetRowContract
    output_rows: d.DatasetRowSetContract

    @property
    def semantics(self) -> ForecastSemantics:
        result = self.output_row.family_semantics
        if not isinstance(result, ForecastSemantics):
            raise forecast_error("closed Forecast semantics", "invalid family")
        return result

    @property
    def metric_name(self) -> str:
        return next(f.name for f in self.input_row.schema.columns if f.role_id == "metric")

    @property
    def time_name(self) -> str:
        return next(f.name for f in self.input_row.schema.columns if f.role_id == "time_dimension")

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.input_row.schema.columns if f.role_id == "dimension")

    def identity_payload(self) -> CanonicalValue:
        return (
            "forecast@v1",
            *(
                d._descriptor_payload(v)
                for v in (self.input_row, self.input_rows, self.output_row, self.output_rows)
            ),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class ForecastPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    spec: ForecastSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


@dataclass(frozen=True, slots=True)
class ForecastTrainingSummary:
    """Bounded original fit facts preserved across selections and cold recovery."""

    series_count: int
    training_row_count: int
    residual_count: int
    residual_df: int
    zero_residual_series_count: int
    variance_range: tuple[float, float]
    history_start: str
    history_end: str
    future_coordinates: tuple[str, ...]


DEFAULT_MODEL = naive()
