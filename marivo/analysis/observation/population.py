"""Private paired Population family and governed membership source construction."""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, TypeGuard

from marivo._temporal import TimeScope
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import (
    Dataset,
    LogicalDataset,
    MaterializedDataset,
    _make_logical_dataset,
)
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, _EntityFieldIdentity
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.observation.contracts import (
    DimensionInput,
    EntityInput,
    ObservationOwner,
    PopulationPayload,
    TimeDimensionInput,
    additional_captures,
    construction_error,
    dimension_field,
    owner_of,
    path_dependency_fingerprint,
    population_contracts,
)
from marivo.analysis.observation.coordinates import (
    functional_path,
    normalize_dimension_input,
    path_entities,
    resolve_time_axis,
)
from marivo.analysis.observation.predicates import (
    AnalysisPredicate,
    PredicateField,
    bind_predicates,
)
from marivo.refs import Ref, SemanticKind
from marivo.semantic.catalog import DimensionEntry, EntityEntry
from marivo.semantic.validator import normalize_target_entity, normalize_target_version_selection

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.datasets.descriptors import DatasetField
    from marivo.analysis.evidence.artifact_reads import Finding, FindingPage
    from marivo.analysis.evidence.types import ArtifactDigest


def _is_membership_dimension(operand: PredicateField) -> TypeGuard[DimensionInput]:
    return type(operand) is DimensionEntry or (
        type(operand) is Ref and operand.kind is SemanticKind.DIMENSION
    )


class LogicalPopulationDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="population"):
    """Logical governed Entity membership with no implicit row reads."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalPopulationDataset:
        """Narrow membership using stable Dimensions; predicates are combined with AND.

        Returns: New Logical Population. Example: ``population.where(eq(region, 'EU'))``.
        Constraints: Predicates must have one atemporal value per Entity.
        """
        return _where(self, predicates)

    def execute(self) -> MaterializedPopulationDataset:
        """Delegate this definition to its required runtime owner.

        Returns: Committed same-family Dataset. Example: ``population.execute()``.
        Constraints: The injected runtime owns admission and publication; no parameters.
        """
        return owner_of(self).action_port.execute_population(self)


class MaterializedPopulationDataset(
    MaterializedDataset, _token=_CORE_TOKEN, family_id="population"
):
    """Exact retained identity rows, never an executable membership origin."""

    __slots__ = ()

    def where(self, *predicates: AnalysisPredicate) -> LogicalPopulationDataset:
        """Filter retained membership through a current stable Dimension path.

        Args: predicates: Immutable predicate descriptions.
        Returns: Logical Population. Example: ``population.where(eq(region, 'EU'))``.
        Constraints: Consumes the exact Artifact leaf and does not reselect its origin.
        """
        return _where(self, predicates)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print committed rows within max_output_bytes; returns None.

        Example: ``population.show()``. Constraints: Only the runtime reads rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete retained DataFrame with no parameters.

        Example: ``population.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest through the runtime read owner."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read a bounded retained Finding page using limit and opaque cursor.

        Returns: FindingPage. Example: ``population.findings(limit=10)``.
        Constraints: No new Findings are inferred from retained rows.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Read the exact retained Finding identified by finding_id.

        Returns: Finding. Example: ``population.finding('finding-id')``.
        Constraints: Missing IDs are handled by the owning runtime.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def make_population(
    owner: ObservationOwner,
    registry: DatasetFamilyRegistry,
    entity: EntityInput,
    *,
    time_scope: TimeScope | None = None,
    time_dimension: TimeDimensionInput | None = None,
) -> LogicalPopulationDataset:
    """Normalize one current Entity into a complete private membership definition."""
    if isinstance(entity, EntityEntry):
        if type(entity) is not EntityEntry or entity._catalog is not owner.catalog_identity:
            raise construction_error("current exact Entity entry", "foreign or stale entry")
        reference = entity.ref
    elif type(entity) is Ref and entity.kind is SemanticKind.ENTITY:
        reference = entity
    else:
        raise construction_error("exact Entity ref or current entry", type(entity).__name__)
    normalized = normalize_target_entity(owner.semantic_registry, reference.path)
    if not normalized.identity_signature:
        raise construction_error("non-empty governed Entity identity", "source-only Entity")
    if normalized.version is not None and time_scope is None:
        raise construction_error(
            "explicit finite membership scope for a versioned Entity",
            "unscoped versioned Population",
            repair="Call population(entity, time_scope=...) explicitly; observation scope does not select membership versions.",
        )
    axis = resolve_time_axis(
        owner, (reference.path,), time_scope, time_dimension, entity_membership=True
    )
    selection = None
    if normalized.version is not None and time_scope is not None:
        boundary = time_scope.end
        if not isinstance(boundary, datetime):
            boundary = datetime.combine(boundary, time.min)
        selection = normalize_target_version_selection(
            normalized, boundary=boundary, interpretation="before_endpoint"
        )
    path = (
        ()
        if axis is None
        else functional_path(owner.semantic_registry, reference.path, axis.entity_ref.path)
    )
    captures = owner.binding_scopes.capture(
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(owner.semantic_registry, reference.path, (path,))
        )
    )
    row, row_set = population_contracts(normalized, registry.get("population").ids)
    result = _make_logical_dataset(
        owner=owner,
        registry=registry,
        family_id="population",
        row_contract=row,
        row_set_contract=row_set,
        operator_id="session.population",
        payload=PopulationPayload(
            _token=_CORE_TOKEN,
            entity=normalized,
            time_scope=time_scope,
            reference_axis=axis,
            version_selection=selection,
            captures=captures,
            dependency_fingerprint=path_dependency_fingerprint(owner, reference.path, (path,)),
        ),
        requirements=tuple(f"population.{item.kind}@v1" for item in normalized.obligations),
        dependency_facts=(f"entity:{reference.path}",),
        contract_versions=(("observation", "v1"),),
    )
    if not isinstance(result, LogicalPopulationDataset):
        raise construction_error("paired Logical Population", "invalid family registration")
    return result


def _where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalPopulationDataset:
    owner = owner_of(dataset)
    identity = dataset.schema.columns[0].identity
    if not isinstance(identity, _EntityFieldIdentity):
        raise construction_error("exact governed membership identity", "invalid identity")
    entity = normalize_target_entity(owner.semantic_registry, identity.entity_ref.path)
    if entity.identity_signature != identity.identity_signature:
        raise construction_error(
            "the retained Entity identity signature",
            "changed identity contract",
            repair="Reconstruct membership under the current exact identity signature before enriching it.",
        )
    paths: list[tuple[str, ...]] = []

    def resolve(operand: PredicateField) -> DatasetField:
        if not _is_membership_dimension(operand):
            raise construction_error(
                "membership-stable non-time Dimension", "unsupported Population predicate operand"
            )
        dimension = normalize_dimension_input(owner, operand, time=False)
        path = functional_path(owner.semantic_registry, entity.ref.path, dimension.entity_ref.path)
        if any(
            normalize_target_entity(owner.semantic_registry, name).version is not None
            for name in path_entities(owner.semantic_registry, entity.ref.path, (path,))
        ):
            raise construction_error(
                "unversioned atemporal membership path", "versioned Dimension path"
            )
        paths.append(path)
        return dimension_field(
            dimension,
            dataset._registration.ids,
            dependency_fingerprint=path_dependency_fingerprint(owner, entity.ref.path, (path,)),
        )

    bound = bind_predicates(predicates, resolve)
    from marivo.analysis.datasets.handles import LogicalRootHandle

    root = dataset._root
    previous = (
        root.payload
        if isinstance(root, LogicalRootHandle) and isinstance(root.payload, PopulationPayload)
        else None
    )
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(owner.semantic_registry, entity.ref.path, tuple(paths))
        ),
    )
    payload = PopulationPayload(
        _token=_CORE_TOKEN,
        entity=entity,
        time_scope=None if previous is None else previous.time_scope,
        reference_axis=None if previous is None else previous.reference_axis,
        version_selection=None if previous is None else previous.version_selection,
        captures=captures,
        predicate=bound,
        dependency_fingerprint=path_dependency_fingerprint(owner, entity.ref.path, tuple(paths)),
    )
    result = construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="population.where",
        inputs=(dataset,),
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        payload=payload,
    )
    if not isinstance(result, LogicalPopulationDataset):
        raise construction_error("paired Logical Population", "invalid family registration")
    return result
