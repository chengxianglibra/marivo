"""Private paired Forecast states and exact retained row continuations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, DatasetField
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.observation.contracts import (
    RetainedRowsPayload,
    owner_of,
    producer_contract,
    retained_field,
)
from marivo.analysis.observation.predicates import (
    AnalysisPredicate,
    PredicateField,
    bind_predicates,
)
from marivo.analysis.operators.errors import forecast_error

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


class LogicalForecastDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="forecast"):
    """Complete logical forecast rows; execution belongs to the runtime."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalForecastDataset:
        """Select rows by predicates and return a new Logical Forecast.

        Example: ``forecast.where(gt(forecast.fields.get('forecast_value'), 0))``.
        Constraints: Current selectors are required; Retained Dimensions and generated Forecast values are filterable.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalForecastDataset:
        """Rank by a current numeric selector and return Logical Forecast.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``forecast.rank(forecast.fields.get('forecast_value')).limit(10)``.
        Constraints: Non-singleton rows and an exact current selector are required.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalForecastDataset:
        """Return a Logical Forecast retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; uses the current authored or ranked order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def execute(self) -> MaterializedForecastDataset:
        """Commit forecast rows through the runtime; no parameters.

        Returns: Materialized Forecast. Example: ``forecast.execute()``.
        Constraints: Every series must have complete certified history and finite predictions.
        """
        return owner_of(self).action_port.execute_forecast(self)


class MaterializedForecastDataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="forecast"):
    """Committed immutable forecast rows with original Evidence and Findings."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalForecastDataset:
        """Select retained rows by predicates and return Logical Forecast.

        Example: ``forecast.where(gt(forecast.fields.get('forecast_value'), 0))``.
        Constraints: Current selectors are required; Retained Dimensions and generated Forecast values are filterable.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalForecastDataset:
        """Rank retained rows by a numeric selector and return Logical Forecast.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``forecast.rank(forecast.fields.get('forecast_value'))``. Constraints: Current numeric selectors are required.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalForecastDataset:
        """Return a Logical Forecast retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; uses the current authored or ranked order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print a bounded committed preview, optionally limited by max_output_bytes.

        Returns: None. Example: ``forecast.show()``. Constraints: Uses only committed rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete DataFrame; no parameters.

        Example: ``frame = forecast.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest; no new execution occurs."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read committed Findings with limit and opaque cursor; return FindingPage.

        Example: ``forecast.findings(limit=10)``. Constraints: Pages never infer new Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Return the committed Finding named by finding_id.

        Example: ``forecast.finding('finding-id')``. Constraints: Exact retained id required.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def _checked(dataset: Dataset) -> LogicalForecastDataset:
    if not isinstance(dataset, LogicalForecastDataset):
        raise forecast_error("paired Logical Forecast", "invalid family registration")
    return dataset


def _where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalForecastDataset:
    def resolve(operand: PredicateField) -> DatasetField:
        if isinstance(operand, DatasetFieldRef):
            selected = validate_field_ref(dataset, operand)
            if selected.role_id == "metric_identity":
                raise forecast_error(
                    "filterable generated value or Dimension", "Metric identity predicate"
                )
            return selected
        return retained_field(dataset, operand)

    bound = bind_predicates(predicates, resolve)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="forecast.where",
            contract_versions=producer_contract("forecast.where").versions,
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            payload=RetainedRowsPayload(_token=_CORE_TOKEN, predicate=bound),
        )
    )
