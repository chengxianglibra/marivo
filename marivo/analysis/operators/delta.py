"""Private paired Delta states and exact retained row continuations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, DatasetField
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.funnel import FunnelLossRate
from marivo.analysis.observation.contracts import (
    DimensionInput,
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
from marivo.analysis.operators.errors import comparison_error

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
    from marivo.analysis.operators.attribution import LogicalAttributionDataset
    from marivo.analysis.operators.discovery import DeltaDiscovery


class LogicalDeltaDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="delta"):
    """Complete logical comparison rows; execution belongs to the runtime."""

    __slots__ = ()

    @property
    def discover(self) -> DeltaDiscovery:
        """Return the non-callable namespace for time discovery on this Dataset.

        Example: ``dataset.discover.period_shifts()``.
        Constraints: A time-bearing Delta of one Metric is required; construction performs no data work.
        """
        from marivo.analysis.operators.discovery import DeltaDiscovery

        return DeltaDiscovery(self)

    @overload
    def attribute(
        self,
        *,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionDataset: ...

    @overload
    def attribute(
        self,
        *,
        target: FunnelLossRate,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionDataset: ...

    def attribute(
        self,
        *,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
        target: FunnelLossRate | None = None,
    ) -> LogicalAttributionDataset:
        """Decompose this Delta into exact scoped contributions.

        Args:
            axes: Ordered governed Dimensions.
            mode: Joint or authored prefixes.
            top_k: Optional number of retained members per mapped parent.
            target: Required non-initial FunnelLossRate for Event Delta; omitted for Metric.

        Returns: Logical Attribution. Example: ``delta.attribute(axes=(region,))``.
        Constraints: Requires exact additive partitions and retained components.
        """
        from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics

        if isinstance(self.row_contract.family_semantics, FunnelDeltaSemantics):
            from marivo.analysis.domains.event_attribution import attribute as funnel_attribute

            if target is None:
                raise comparison_error("a FunnelLossRate target", "missing Event target")
            return funnel_attribute(self, target=target, axes=axes, mode=mode, top_k=top_k)
        if target is not None:
            raise comparison_error("Metric attribution without an Event target", "foreign target")
        from marivo.analysis.operators.attribute import attribute

        return attribute(self, axes=axes, mode=mode, top_k=top_k)

    def where(self, *predicates: AnalysisPredicate) -> LogicalDeltaDataset:
        """Select rows by predicates and return a new Logical Delta.

        Example: ``delta.where(gt(delta.fields.get('delta'), 0))``.
        Constraints: Scalar comparison rejects row filtering.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalDeltaDataset:
        """Rank by a current numeric selector and return Logical Delta.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``delta.rank(delta.fields.get('delta')).limit(10)``.
        Constraints: Non-singleton rows and an exact current selector are required.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalDeltaDataset:
        """Return a Logical Delta retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; requires rank order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def execute(self) -> MaterializedDeltaDataset:
        """Commit comparison rows through the runtime; no parameters.

        Returns: Materialized Delta. Example: ``delta.execute()``.
        Constraints: Both inputs must pass complete guarded validation.
        """
        return owner_of(self).action_port.execute_delta(self)


class MaterializedDeltaDataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="delta"):
    """Committed immutable comparison rows with original Evidence and Findings."""

    __slots__ = ()

    @property
    def discover(self) -> DeltaDiscovery:
        """Return the non-callable namespace for time discovery on this Dataset.

        Example: ``dataset.discover.period_shifts()``.
        Constraints: A time-bearing Delta of one Metric is required; construction performs no data work.
        """
        from marivo.analysis.operators.discovery import DeltaDiscovery

        return DeltaDiscovery(self)

    @overload
    def attribute(
        self,
        *,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionDataset: ...

    @overload
    def attribute(
        self,
        *,
        target: FunnelLossRate,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionDataset: ...

    def attribute(
        self,
        *,
        axes: tuple[DimensionInput, ...] | list[DimensionInput],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
        target: FunnelLossRate | None = None,
    ) -> LogicalAttributionDataset:
        """Decompose this Delta into exact scoped contributions.

        Args:
            axes: Ordered governed Dimensions.
            mode: Joint or authored prefixes.
            top_k: Optional number of retained members per mapped parent.
            target: Required non-initial FunnelLossRate for Event Delta; omitted for Metric.

        Returns: Logical Attribution. Example: ``delta.attribute(axes=(region,))``.
        Constraints: Requires exact additive partitions and retained components.
        """
        from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics

        if isinstance(self.row_contract.family_semantics, FunnelDeltaSemantics):
            from marivo.analysis.domains.event_attribution import attribute as funnel_attribute

            if target is None:
                raise comparison_error("a FunnelLossRate target", "missing Event target")
            return funnel_attribute(self, target=target, axes=axes, mode=mode, top_k=top_k)
        if target is not None:
            raise comparison_error("Metric attribution without an Event target", "foreign target")
        from marivo.analysis.operators.attribute import attribute

        return attribute(self, axes=axes, mode=mode, top_k=top_k)

    def where(self, *predicates: AnalysisPredicate) -> LogicalDeltaDataset:
        """Select retained rows by predicates and return Logical Delta.

        Example: ``delta.where(gt(delta.fields.get('delta'), 0))``.
        Constraints: Scalar comparison rejects row filtering.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalDeltaDataset:
        """Rank retained rows by a numeric selector and return Logical Delta.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``delta.rank(delta.fields.get('delta'))``. Constraints: Non-singleton rows only.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalDeltaDataset:
        """Return a Logical Delta retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; requires rank order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print a bounded committed preview, optionally limited by max_output_bytes.

        Returns: None. Example: ``delta.show()``. Constraints: Uses only committed rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete DataFrame; no parameters.

        Example: ``frame = delta.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest; no new execution occurs."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read committed Findings with limit and opaque cursor; return FindingPage.

        Example: ``delta.findings(limit=10)``. Constraints: Pages never infer new Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Return the committed Finding named by finding_id.

        Example: ``delta.finding('finding-id')``. Constraints: Exact retained id required.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def _checked(dataset: Dataset) -> LogicalDeltaDataset:
    if not isinstance(dataset, LogicalDeltaDataset):
        raise comparison_error("paired Logical Delta", "invalid family registration")
    return dataset


def _where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalDeltaDataset:
    if dataset.row_contract.shape_id.local_shape_id == "scalar":
        raise comparison_error("non-singleton rows for filtering", "scalar singleton")

    def resolve(operand: PredicateField) -> DatasetField:
        if isinstance(operand, DatasetFieldRef):
            return validate_field_ref(dataset, operand)
        return retained_field(dataset, operand)

    bound = bind_predicates(predicates, resolve)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="delta.where",
            contract_versions=producer_contract("delta.where").versions,
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            payload=RetainedRowsPayload(_token=_CORE_TOKEN, predicate=bound),
        )
    )
