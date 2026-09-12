"""Immutable quality facts persisted with Dataset Artifact descriptors."""

from pydantic import BaseModel, ConfigDict, Field


class QualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage: float | None = None
    null_rate: float | None = None
    sample_size: int | None = None
    sample_coverage_min: float | None = None
    sample_coverage_avg: float | None = None
    sample_coverage_partial_buckets: int | None = None
    zero_denominator_rows: int | None = None
    evaluated_check_count: int | None = Field(default=None, ge=0)
    failed_check_count: int | None = Field(default=None, ge=0)
    warning_check_count: int | None = Field(default=None, ge=0)
