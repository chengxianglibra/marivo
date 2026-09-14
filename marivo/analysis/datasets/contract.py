"""Bounded terminal audit snapshots for private Dataset values."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, SupportsIndex, final

from marivo._compat import Never, Self
from marivo.analysis.datasets.descriptors import (
    DatasetFieldId,
    DatasetFieldIdentity,
    DatasetPhysicalTypeState,
    _CatalogFieldIdentity,
    _DeferredPhysicalType,
    _EntityFieldIdentity,
    _ExactByteCount,
    _GeneratedFieldIdentity,
    _KeyedCardinality,
    _OrderedOrdering,
    _ResolvedPhysicalType,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
    _RuntimeMetricFieldIdentity,
    _RuntimePolicyRowBound,
    _StaticRowBound,
    _UnavailableByteCount,
)
from marivo.analysis.datasets.state import MaterializedDatasetState
from marivo.render import Card, result_repr

if TYPE_CHECKING:
    from marivo.analysis.datasets.base import Dataset

_MAX_FIELDS = 12
_MAX_FACTS = 12
_MAX_CONSUMERS = 12
_MAX_CONSUMER_BYTES = 1024

# The composition root installs a read-only projection of native descriptors.
# Core must not import Help assembly or executing analysis implementations.
_continuation_reader: Callable[[Dataset, str], tuple[str, str, tuple[str, ...]] | None] | None = (
    None
)


def _install_continuation_reader(
    reader: Callable[[Dataset, str], tuple[str, str, tuple[str, ...]] | None],
) -> None:
    global _continuation_reader
    _continuation_reader = reader


@final
class DatasetContract:
    """Immutable non-executing terminal snapshot, produced by ``dataset.contract()``."""

    __slots__ = (
        "_column_count",
        "_columns",
        "_consumers",
        "_facts",
        "_identity",
        "_lineage",
        "_requirements",
    )

    _identity: str
    _facts: tuple[tuple[str, str], ...]
    _columns: tuple[tuple[str, ...], ...]
    _column_count: int
    _consumers: tuple[str, ...]
    _lineage: tuple[str, ...]
    _requirements: tuple[str, ...]

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise TypeError("DatasetContract has no public constructor; use dataset.contract().")

    def __init__(self, _sealed: Never, /) -> None:
        raise AssertionError("DatasetContract initialization is unreachable")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("DatasetContract is sealed and cannot be subclassed.")

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("DatasetContract instances are immutable")

    def __delattr__(self, name: str) -> Never:
        del name
        raise AttributeError("DatasetContract instances are immutable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        del protocol
        raise TypeError("DatasetContract cannot be pickled; call dataset.contract() again.")

    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Render this bounded audit snapshot without executing or validating data.

        Args:
            max_output_bytes: Additional UTF-8 output bound, or ``None`` to keep
                only the snapshot's fixed structural bounds.
        Returns:
            A deterministic plain-text audit card.
        Example:
            ``text = dataset.contract().render()``
        Constraints:
            No rows, datasource reads, or current catalog lookups are performed.
        """
        card = Card(identity=self._identity, available=(".show()",))
        for label, value in self._facts:
            card.field(label, value)
        omitted_columns = self._column_count - len(self._columns)
        if omitted_columns:
            card.field(
                "schema_inventory",
                f"{self._column_count} fields; {omitted_columns} omitted; inspect dataset.schema.columns",
            )
        card.table(
            (
                "field_id",
                "name",
                "role",
                "identity",
                "derivation",
                "logical_type",
                "physical_type",
                "nullable",
            ),
            self._columns,
            label="schema",
            row_count=self._column_count,
            show_omission_counts=True,
            recovery="inspect dataset.schema.columns for the complete field inventory",
        )
        card.listing("operators", self._consumers or ("(none admitted)",))
        card.listing("action_requirements", self._requirements or ("(none retained)",))
        card.listing("lineage", self._lineage or ("(none retained)",))
        return card.render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print exactly this snapshot's rendered card and one newline.

        Args:
            max_output_bytes: Additional UTF-8 output bound, or ``None``.
        Returns:
            ``None`` after printing the non-executing card.
        Example:
            ``dataset.contract().show()``
        Constraints:
            This is an audit read; it never renders Dataset rows.
        """
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        return result_repr(self._identity)


def _bounded_facts(facts: tuple[str, ...], *, omitted: int = 0) -> tuple[str, ...]:
    visible = tuple(fact[:240].replace("\n", " ").replace("\r", " ") for fact in facts[:_MAX_FACTS])
    omitted += max(0, len(facts) - _MAX_FACTS)
    return (*visible, f"({omitted} additional facts omitted)") if omitted else visible


def _field_ids(field_ids: tuple[DatasetFieldId, ...]) -> str:
    value = ", ".join(field_id.value for field_id in field_ids[:_MAX_FIELDS]) or "(none)"
    omitted = len(field_ids) - _MAX_FIELDS
    return f"{value}; {omitted} additional fields omitted" if omitted > 0 else value


def _identity_text(identity: DatasetFieldIdentity) -> str:
    if isinstance(identity, _EntityFieldIdentity):
        signature = ",".join(
            f"{name}:{logical_type}" for name, logical_type in identity.identity_signature[:12]
        )
        omitted = len(identity.identity_signature) - 12
        if omitted > 0:
            signature += f",+{omitted} components"
        return f"entity_identity:{identity.entity_ref.key}({signature})"
    if isinstance(identity, _CatalogFieldIdentity):
        return f"catalog_ref:{identity.identity_id}"
    if isinstance(identity, _RuntimeMetricFieldIdentity):
        return f"runtime_metric:{identity.expression_fingerprint}"
    if isinstance(identity, _GeneratedFieldIdentity):
        return f"generated:{identity.producer_field_id.value}"
    raise AssertionError("Dataset field identity was not validated at construction")


def _physical_type_text(state: DatasetPhysicalTypeState) -> str:
    if isinstance(state, _ResolvedPhysicalType):
        return f"resolved:{state.physical_type_id}"
    if isinstance(state, _DeferredPhysicalType):
        return f"deferred:{state.admitted_type_class_id}"
    raise AssertionError("Dataset physical type was not validated at construction")


def make_contract(dataset: Dataset) -> DatasetContract:
    """Snapshot only admitted local facts and the sole registry's continuations."""
    row = dataset.row_contract
    row_set = dataset.row_set_contract
    state = dataset.state
    shape = row.shape_id
    identity = (
        f"DatasetContract family={dataset.kind[:48]} "
        f"state={state.kind} fp={dataset.definition_fingerprint[:20]}"
    )
    facts: list[tuple[str, str]] = [
        ("shape", f"{shape.family_id}/{shape.local_shape_id}@v{shape.semantic_version}"),
        ("session", dataset._owner.session_id),
        ("row_contract", f"v{row.schema_version} fp={_row_contract_fingerprint(row)}"),
        (
            "row_set_contract",
            f"v{row_set.schema_version} fp={_row_set_contract_fingerprint(row_set)}",
        ),
        ("family_semantics", repr(row.family_semantics)),
        ("coordinates", _field_ids(row.coordinate_field_ids)),
        ("row_key", _field_ids(row.key_field_ids)),
    ]
    cardinality = row_set.cardinality
    if isinstance(cardinality, _KeyedCardinality):
        row_bound = cardinality.row_bound
        if isinstance(row_bound, _StaticRowBound):
            bound = f"static max_rows={row_bound.max_rows}"
        elif isinstance(row_bound, _RuntimePolicyRowBound):
            bound = f"runtime_policy={row_bound.policy_id}"
        else:
            bound = "unknown"
        facts.append(("cardinality", f"keyed; row_bound={bound}"))
    else:
        facts.append(("cardinality", "singleton"))
    ordering = row_set.ordering
    if isinstance(ordering, _OrderedOrdering):
        terms = "; ".join(
            f"{term.field_id.value} {term.direction} nulls={term.nulls} value_order={term.value_order_contract_id}"
            for term in ordering.terms[:_MAX_FIELDS]
        )
        omitted_terms = len(ordering.terms) - _MAX_FIELDS
        if omitted_terms > 0:
            terms += f"; {omitted_terms} additional terms omitted"
        facts.append(("ordering", terms))
    else:
        facts.append(("ordering", "unordered"))
    if isinstance(state, MaterializedDatasetState):
        byte_count = state.realized_byte_count
        if isinstance(byte_count, _ExactByteCount):
            bytes_text = f"exact {byte_count.byte_count}"
        elif isinstance(byte_count, _UnavailableByteCount):
            bytes_text = f"unavailable: {byte_count.reason_id}"
        else:
            raise AssertionError("Dataset byte count was not validated at construction")
        facts.extend(
            (
                ("artifact", str(state.artifact_ref)),
                ("artifact_session", state.artifact_session_ref),
                ("storage_kind", state.storage_kind_id),
                ("rows", str(state.realized_row_count)),
                ("bytes", bytes_text),
                ("producing_run", state.producing_run_ref),
                ("content_authority", state.content_authority_digest),
                ("quality_authority", state.quality_authority_digest),
                ("evidence_authority", state.evidence_authority_digest),
                (
                    "terminal_boundary",
                    "show() and to_pandas() read the committed Artifact; no logical-origin replay",
                ),
            )
        )
    else:
        facts.append(
            (
                "terminal_boundary",
                "execute() produces the paired MaterializedDataset; logical values have no row reads",
            )
        )
    for label, value in dataset._registration.facts_for(dataset)[:_MAX_FACTS]:
        facts.append((label[:80], value[:240].replace("\n", " ").replace("\r", " ")))
    consumers = dataset._registry.consumers_for(dataset)
    visible_consumers: list[str] = []
    omitted_consumers = max(0, len(consumers) - _MAX_CONSUMERS)
    for consumer in consumers[:_MAX_CONSUMERS]:
        disclosure = (
            None if _continuation_reader is None else _continuation_reader(dataset, consumer.id)
        )
        if disclosure is not None and consumer.id not in disclosure[2]:
            continue
        entry = (
            f"{consumer.id}: input_roles=({', '.join(consumer.input_roles)}) "
            f"-> {consumer.output_family}; requirements=({', '.join(consumer.requirements)})"
        )
        if disclosure is not None:
            public_call, target, _ = disclosure
            entry += f"; call={public_call}; marivo.help('analysis.{target}')"
        if len(entry.encode("utf-8")) > _MAX_CONSUMER_BYTES:
            omitted_consumers += 1
        else:
            visible_consumers.append(entry)
    if omitted_consumers:
        visible_consumers.append(
            f"({omitted_consumers} additional operators omitted; use marivo.help(type(dataset)) for family navigation)"
        )
    action = "show" if isinstance(state, MaterializedDatasetState) else "execute"
    visible_consumers.append(f"dataset.{action}(); marivo.help('analysis.actions.{action}')")
    contract = object.__new__(DatasetContract)
    object.__setattr__(contract, "_identity", identity)
    object.__setattr__(contract, "_facts", tuple(facts))
    object.__setattr__(
        contract,
        "_columns",
        tuple(
            (
                field.field_id.value,
                field.name,
                field.role_id,
                _identity_text(field.identity),
                field.derivation_identity,
                field.logical_type_id,
                _physical_type_text(field.physical_type_state),
                str(field.nullable).lower(),
            )
            for field in row.schema.columns[:_MAX_FIELDS]
        ),
    )
    object.__setattr__(contract, "_column_count", len(row.schema.columns))
    object.__setattr__(contract, "_consumers", tuple(visible_consumers))
    object.__setattr__(contract, "_requirements", _bounded_facts(dataset._root.requirements))
    object.__setattr__(
        contract,
        "_lineage",
        _bounded_facts(dataset._lineage.facts, omitted=dataset._lineage.omitted_count),
    )
    return contract
