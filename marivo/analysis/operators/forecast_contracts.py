"""Closed private forecast values, invocation and retained model authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.forecast_models import (
    ASSUMPTIONS as ASSUMPTIONS,
)
from marivo.analysis.forecast_models import (
    INTERVAL_METHOD as INTERVAL_METHOD,
)
from marivo.analysis.forecast_models import (
    ForecastHorizon as ForecastHorizon,
)
from marivo.analysis.forecast_models import (
    ForecastModel as ForecastModel,
)
from marivo.analysis.forecast_models import (
    ModelId as ModelId,
)
from marivo.analysis.forecast_models import (
    drift as drift,
)
from marivo.analysis.forecast_models import (
    naive as naive,
)
from marivo.analysis.forecast_models import (
    periods as periods,
)
from marivo.analysis.forecast_models import (
    seasonal_naive as seasonal_naive,
)
from marivo.analysis.operators.errors import forecast_error


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
