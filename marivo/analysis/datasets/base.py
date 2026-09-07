"""Private immutable Dataset values and state-matched construction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, SupportsIndex

from marivo._compat import Never
from marivo.analysis.datasets.contract import DatasetContract, make_contract
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetRowContract,
    DatasetRowSetContract,
    DatasetSchema,
    _KeyedCardinality,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
    _SingletonCardinality,
    _StaticRowBound,
    _validate_realized_schema,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.fields import DatasetFields, make_fields
from marivo.analysis.datasets.handles import (
    BoundedLineage,
    CanonicalValue,
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    MaterializedInputToken,
    MaterializedScanLeafHandle,
    RealizationRequirement,
    _check_label,
    _check_tuple,
    _LogicalNodePayload,
    _make_lineage,
    _make_logical_root,
    _validate_logical_root,
)
from marivo.analysis.datasets.state import (
    LogicalDatasetState,
    MaterializedDatasetState,
    _logical_state,
    _validate_materialized_state,
)

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.datasets.registry import DatasetFamilyRegistration, DatasetFamilyRegistry
    from marivo.analysis.evidence.artifact_reads import Finding, FindingPage
    from marivo.analysis.evidence.types import ArtifactDigest
    from marivo.semantic.runtime_metric import RuntimeMetricExpr


def _construction_error(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Reconstruct the Dataset through the registered private family factory.",
        location="dataset.construction",
    )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class DatasetOwner:
    """Already-admitted identity facts; no Session implementation or live lookup."""

    session_id: str
    store_id: str
    catalog_identity: object | None = None
    runtime_metric_bindings: tuple[tuple[RuntimeMetricExpr, str], ...] = ()

    def __post_init__(self) -> None:
        _check_label(self.session_id)
        _check_label(self.store_id)
        if not isinstance(self.runtime_metric_bindings, tuple):
            raise _construction_error(
                "immutable retained runtime Metric bindings", "mutable bindings"
            )

        for binding in self.runtime_metric_bindings:
            _check_tuple(binding)
            if len(binding) != 2:
                raise _construction_error(
                    "expression and exact field-id binding", "invalid retained binding"
                )
            _check_label(binding[1])

    def __repr__(self) -> str:
        return "<private Dataset owner>"


class Dataset(ABC):
    """Immutable analytical value with complete row meaning before execution."""

    __slots__ = (
        "_definition_fingerprint",
        "_lineage",
        "_owner",
        "_registration",
        "_registry",
        "_root",
        "_row_contract",
        "_row_set_contract",
        "_state",
    )
    _declared_family: ClassVar[str] = ""
    _owner: DatasetOwner
    _registration: DatasetFamilyRegistration
    _registry: DatasetFamilyRegistry
    _row_contract: DatasetRowContract
    _row_set_contract: DatasetRowSetContract
    _state: LogicalDatasetState | MaterializedDatasetState
    _root: LogicalRootHandle | MaterializedScanLeafHandle
    _definition_fingerprint: str
    _lineage: BoundedLineage
    # Object identity equality intentionally has no matching public hash operation.
    __hash__: ClassVar[None] = None  # type: ignore[assignment]

    def __init_subclass__(
        cls, *, _token: object = None, family_id: str = "", **kwargs: object
    ) -> None:
        if _token is not _CORE_TOKEN:
            raise _construction_error(
                "sealed private family registration", "external Dataset subclass"
            )
        super().__init_subclass__()
        if "__slots__" not in cls.__dict__:
            raise _construction_error("slotted immutable family class", "mutable subclass layout")
        cls._declared_family = family_id

    def __init__(
        self,
        *,
        _token: object,
        owner: DatasetOwner,
        registration: DatasetFamilyRegistration,
        registry: DatasetFamilyRegistry,
        row_contract: DatasetRowContract,
        row_set_contract: DatasetRowSetContract,
        state: LogicalDatasetState | MaterializedDatasetState,
        root: LogicalRootHandle | MaterializedScanLeafHandle,
        definition_fingerprint: str,
        lineage: BoundedLineage,
    ) -> None:
        if _token is not _CORE_TOKEN:
            raise _construction_error("registered private factory", "direct Dataset construction")
        registry.require_frozen()
        if registry.get(row_contract.shape_id.family_id) is not registration:
            raise _construction_error(
                "the registry's exact family registration", "foreign registration"
            )
        registration.validate(row_contract, row_set_contract)
        logical = isinstance(self, LogicalDataset)
        if logical:
            if (
                type(self) is not registration.logical_type
                or type(state) is not LogicalDatasetState
                or type(root) is not LogicalRootHandle
            ):
                raise _construction_error(
                    "registered Logical class/state/root pair", "corrupt Dataset pair"
                )
            _validate_logical_root(root)
            registration.validate_payload(root.payload)
            if state.kind != "logical":
                raise _construction_error("logical state discriminator", "corrupt state kind")
            if root.definition_fingerprint != definition_fingerprint:
                raise _construction_error(
                    "root-bound definition fingerprint", "fingerprint mismatch"
                )
        else:
            if (
                type(self) is not registration.materialized_type
                or type(state) is not MaterializedDatasetState
                or type(root) is not MaterializedScanLeafHandle
            ):
                raise _construction_error(
                    "registered Materialized class/state/leaf pair", "corrupt Dataset pair"
                )
            _validate_materialized_state(state, ids=registration.ids)
            if (
                root.artifact_ref != state.artifact_ref
                or root.content_authority_digest != state.content_authority_digest
                or root.artifact_session_id != state.artifact_session_ref
            ):
                raise _construction_error(
                    "exact Artifact content and owning Session", "mismatched scan authority"
                )
            _validate_realized_schema(
                row_contract.schema, state.realized_schema, ids=registration.ids
            )
            cardinality = row_set_contract.cardinality
            if isinstance(cardinality, _SingletonCardinality) and state.realized_row_count != 1:
                raise _construction_error(
                    "exactly one realized row for a singleton", "row count mismatch"
                )
            if (
                isinstance(cardinality, _KeyedCardinality)
                and isinstance(cardinality.row_bound, _StaticRowBound)
                and state.realized_row_count > cardinality.row_bound.max_rows
            ):
                raise _construction_error(
                    "realized rows within the static row bound", "row bound exceeded"
                )
        if (
            root.session_id != owner.session_id
            or root.store_id != owner.store_id
            or root.shape_id != row_contract.shape_id
            or root.row_contract_fingerprint != _row_contract_fingerprint(row_contract)
            or root.row_set_contract_fingerprint != _row_set_contract_fingerprint(row_set_contract)
        ):
            raise _construction_error(
                "matching owner, shape and complete contracts", "root contract mismatch"
            )
        if (
            not definition_fingerprint.startswith("ds_")
            or len(definition_fingerprint) != 67
            or any(character not in "0123456789abcdef" for character in definition_fingerprint[3:])
        ):
            raise _construction_error(
                "canonical definition fingerprint", "invalid definition identity"
            )
        for name, value in (
            ("_owner", owner),
            ("_registration", registration),
            ("_registry", registry),
            ("_row_contract", row_contract),
            ("_row_set_contract", row_set_contract),
            ("_state", state),
            ("_root", root),
            ("_definition_fingerprint", definition_fingerprint),
            ("_lineage", lineage),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: object) -> None:
        raise _construction_error("immutable Dataset value", "attribute mutation")

    def __delattr__(self, name: str) -> None:
        raise _construction_error("immutable Dataset value", "attribute deletion")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _construction_error(
            "definition reconstruction or runtime Artifact recovery", "Dataset serialization"
        )

    def __copy__(self) -> Dataset:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Dataset:
        return self

    @property
    def kind(self) -> str:
        return self._row_contract.shape_id.family_id

    @property
    def row_contract(self) -> DatasetRowContract:
        return self._row_contract

    @property
    def row_set_contract(self) -> DatasetRowSetContract:
        return self._row_set_contract

    @property
    def schema(self) -> DatasetSchema:
        return self._row_contract.schema

    @property
    def state(self) -> LogicalDatasetState | MaterializedDatasetState:
        return self._state

    @property
    def definition_fingerprint(self) -> str:
        return self._definition_fingerprint

    @property
    def fields(self) -> DatasetFields:
        return make_fields(self)

    def contract(self) -> DatasetContract:
        """Return bounded, non-executing contract facts for this Dataset.

        Returns:
            A terminal DatasetContract; no parameters are required.

        Example:
            ``dataset.contract().show()`` inspects admitted continuations.

        Constraints:
            Reads already-bound facts only and never executes or revalidates rows.
        """
        return make_contract(self)

    def __repr__(self) -> str:
        rendered = self._registration.repr_renderer(self)
        return rendered.replace("\n", " ").replace("\r", " ")[:256]


class LogicalDataset(Dataset, _token=_CORE_TOKEN):
    __slots__ = ()

    @property
    def state(self) -> LogicalDatasetState:
        state = self._state
        if not isinstance(state, LogicalDatasetState):
            raise _construction_error("Logical state", "corrupt state")
        return state

    @abstractmethod
    def execute(self) -> MaterializedDataset:
        """Produce or recover the paired committed Dataset.

        Returns: The same family's Materialized Dataset.
        Example: ``materialized = logical.execute()``.
        Constraints: No parameters; the materialization owner implements admission.
        """


class MaterializedDataset(Dataset, _token=_CORE_TOKEN):
    __slots__ = ()

    @property
    def state(self) -> MaterializedDatasetState:
        state = self._state
        if not isinstance(state, MaterializedDatasetState):
            raise _construction_error("Materialized state", "corrupt state")
        return state

    @property
    @abstractmethod
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Artifact digest; runtime owns this retained read."""

    @abstractmethod
    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read a bounded Finding page using limit and an optional opaque cursor."""

    @abstractmethod
    def finding(self, finding_id: str) -> Finding:
        """Read the exact retained Finding identified by finding_id."""

    @abstractmethod
    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print a bounded committed preview, optionally tightening its byte budget."""

    @abstractmethod
    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete DataFrame under the runtime's read guards."""


def _dataset_repr(dataset: Dataset) -> str:
    shape = dataset.row_contract.shape_id.local_shape_id[:48]
    name = type(dataset).__name__[:64]
    if isinstance(dataset, LogicalDataset):
        return f"<{name} shape={shape} fp={dataset.definition_fingerprint[:15]}; use .execute()>"
    if isinstance(dataset, MaterializedDataset):
        state = dataset.state
        ref = state.artifact_ref.ref[:48].replace("\n", " ")
        return f"<{name} shape={shape} ref={ref} rows={state.realized_row_count}; use .show()>"
    raise _construction_error("registered paired state class", "unpaired Dataset")


def _validate_input_ownership(owner: DatasetOwner, inputs: tuple[Dataset, ...]) -> None:
    for item in inputs:
        if not isinstance(item, Dataset):
            raise _construction_error("registered Dataset input", type(item).__name__)
        if item._owner.store_id != owner.store_id or (
            isinstance(item, LogicalDataset) and item._owner.session_id != owner.session_id
        ):
            raise DatasetOwnershipError(
                expected="Logical inputs in the consuming Session; Materialized inputs in the same Store",
                received="foreign input authority",
                repair="Reconstruct Logical inputs in this Session or recover the exact same-Store Artifact.",
                location="dataset.inputs",
            )


def _input_binding(item: Dataset, role: str) -> DefinitionInput:
    if isinstance(item, LogicalDataset):
        token: LogicalInputToken | MaterializedInputToken = LogicalInputToken(
            item.definition_fingerprint
        )
    elif isinstance(item, MaterializedDataset):
        token = MaterializedInputToken(item.state.artifact_ref)
    else:
        raise _construction_error("closed paired Dataset input", "unpaired Dataset")
    return DefinitionInput(role=role, token=token, root=item._root)


def _make_logical_dataset(
    *,
    owner: DatasetOwner,
    registry: DatasetFamilyRegistry,
    family_id: str,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    operator_id: str,
    inputs: tuple[Dataset, ...] = (),
    parameters: CanonicalValue = (),
    realizations: tuple[RealizationRequirement, ...] = (),
    requirements: tuple[str, ...] = (),
    dependency_facts: tuple[str, ...] = (),
    contract_versions: tuple[tuple[str, str], ...] = (),
    input_roles: tuple[str, ...] = (),
    payload: _LogicalNodePayload | None = None,
) -> LogicalDataset:
    registry.require_frozen()
    _validate_input_ownership(owner, inputs)
    registration = registry.get(family_id)
    registration.validate(row_contract, row_set_contract)
    registration.validate_payload(payload)
    roles = input_roles or tuple(f"input_{index}" for index in range(len(inputs)))
    if len(roles) != len(inputs):
        raise _construction_error("one role per ordered input", "input role count mismatch")
    bindings = tuple(_input_binding(item, role) for role, item in zip(roles, inputs, strict=True))
    root = _make_logical_root(
        session_id=owner.session_id,
        store_id=owner.store_id,
        shape_id=row_contract.shape_id,
        row_contract_fingerprint=_row_contract_fingerprint(row_contract),
        row_set_contract_fingerprint=_row_set_contract_fingerprint(row_set_contract),
        operator_id=operator_id,
        inputs=bindings,
        parameters=parameters,
        realizations=realizations,
        requirements=requirements,
        dependency_facts=dependency_facts,
        contract_versions=contract_versions,
        payload=payload,
    )
    return registration.logical_type(
        _token=_CORE_TOKEN,
        owner=owner,
        registration=registration,
        registry=registry,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        state=_logical_state(),
        root=root,
        definition_fingerprint=root.definition_fingerprint,
        lineage=_make_lineage(
            operator_id,
            tuple(item._lineage for item in inputs),
            dependency_facts,
            definition_fingerprint=root.definition_fingerprint,
            row_contract_fingerprint=root.row_contract_fingerprint,
            row_set_contract_fingerprint=root.row_set_contract_fingerprint,
        ),
    )


def _make_materialized_dataset(
    *,
    owner: DatasetOwner,
    registry: DatasetFamilyRegistry,
    family_id: str,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    state: MaterializedDatasetState,
    definition_fingerprint: str,
) -> MaterializedDataset:
    registry.require_frozen()
    registration = registry.get(family_id)
    registration.validate(row_contract, row_set_contract)
    root = MaterializedScanLeafHandle(
        _token=_CORE_TOKEN,
        session_id=owner.session_id,
        store_id=owner.store_id,
        artifact_session_id=state.artifact_session_ref,
        shape_id=row_contract.shape_id,
        artifact_ref=state.artifact_ref,
        content_authority_digest=state.content_authority_digest,
        row_contract_fingerprint=_row_contract_fingerprint(row_contract),
        row_set_contract_fingerprint=_row_set_contract_fingerprint(row_set_contract),
    )
    return registration.materialized_type(
        _token=_CORE_TOKEN,
        owner=owner,
        registration=registration,
        registry=registry,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        state=state,
        root=root,
        definition_fingerprint=definition_fingerprint,
        lineage=BoundedLineage(facts=(f"artifact:{state.artifact_ref.ref[:96]}",), omitted_count=0),
    )
