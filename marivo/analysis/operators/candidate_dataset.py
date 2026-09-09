"""Private paired Candidate states and exact retained row continuations."""

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
from marivo.analysis.operators.errors import discovery_error

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


class LogicalCandidateDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="candidate"):
    """Ordered logical analytical leads; execution belongs to the runtime."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalCandidateDataset:
        """Select rows by predicates and return a new Logical Candidate.

        Example: ``candidate.where(gt(candidate.fields.get('score'), 0))``.
        Constraints: Current selectors are required; Retained coordinates and generated scalar values are filterable.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalCandidateDataset:
        """Rank by a current numeric selector and return Logical Candidate.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``candidate.rank(candidate.fields.get('score')).limit(10)``.
        Constraints: Non-singleton rows and an exact current selector are required.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalCandidateDataset:
        """Return a Logical Candidate retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; uses the current authored or ranked order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def execute(self) -> MaterializedCandidateDataset:
        """Commit candidate rows through the runtime; no parameters.

        Returns: Materialized Candidate. Example: ``candidate.execute()``.
        Constraints: The search must have an evaluable series and remain within runtime budgets.
        """
        return owner_of(self).action_port.execute_candidate(self)


class MaterializedCandidateDataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="candidate"):
    """Committed immutable candidate rows with original Evidence and Findings."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalCandidateDataset:
        """Select retained rows by predicates and return Logical Candidate.

        Example: ``candidate.where(gt(candidate.fields.get('score'), 0))``.
        Constraints: Current selectors are required; Retained coordinates and generated scalar values are filterable.
        """
        return _where(self, predicates)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalCandidateDataset:
        """Rank retained rows by a numeric selector and return Logical Candidate.

        Args: by: Numeric field. order: Direction. ties: Tie rule. partition_by: Key coordinates.
        Example: ``candidate.rank(candidate.fields.get('score'))``. Constraints: Current numeric selectors are required.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalCandidateDataset:
        """Return a Logical Candidate retaining at most count ordered rows.

        Example: ``ranked.limit(10)``. Constraints: Exact count in [1, 100000]; uses the current authored or ranked order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print a bounded committed preview, optionally limited by max_output_bytes.

        Returns: None. Example: ``candidate.show()``. Constraints: Uses only committed rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete DataFrame; no parameters.

        Example: ``frame = candidate.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest; no new execution occurs."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read committed Findings with limit and opaque cursor; return FindingPage.

        Example: ``candidate.findings(limit=10)``. Constraints: Pages never infer new Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Return the committed Finding named by finding_id.

        Example: ``candidate.finding('finding-id')``. Constraints: Exact retained id required.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def _checked(dataset: Dataset) -> LogicalCandidateDataset:
    if not isinstance(dataset, LogicalCandidateDataset):
        raise discovery_error("paired Logical Candidate", "invalid family registration")
    return dataset


def _where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalCandidateDataset:
    def resolve(operand: PredicateField) -> DatasetField:
        if isinstance(operand, DatasetFieldRef):
            selected = validate_field_ref(dataset, operand)
            from marivo.analysis.operators.discovery import candidate_filterable_field

            if not candidate_filterable_field(selected):
                raise discovery_error(
                    "retained coordinate or generated Candidate scalar",
                    "non-scalar Candidate field",
                )
            return selected
        return retained_field(dataset, operand)

    bound = bind_predicates(predicates, resolve)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="candidate.where",
            contract_versions=producer_contract("candidate.where").versions,
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            payload=RetainedRowsPayload(_token=_CORE_TOKEN, predicate=bound),
        )
    )
