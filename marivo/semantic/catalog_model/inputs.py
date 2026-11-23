"""Normalize captured Catalog inputs in the receiving definition scope."""

from __future__ import annotations

from collections.abc import Sequence
from typing import (
    TYPE_CHECKING,
    NoReturn,
    Protocol,
    TypeAlias,
)

from ibis.common.deferred import Deferred

from marivo._definition.deferred import root_declaration
from marivo._kernel.paths import relationship_binding
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.refs import (
    ref as ref_factory,
)
from marivo._kernel.symbolic import (
    native_symbol_ref,
)
from marivo._kernel.validation import input_sequence, is_instance
from marivo.semantic._deferred_semantics import consume_field, consume_row
from marivo.semantic.catalog_model.index import _CatalogIndex
from marivo.semantic.catalog_model.objects import (
    _ENTRY_TYPE_BY_KIND,
    CatalogLookup,
    SemanticObject,
    _SemanticObject,
    entry_reference,
    entry_workspace,
    lookup_state,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticExpressionError,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.expressions.objects import Dimension as _Dimension
from marivo.semantic.expressions.objects import Measure as _Measure
from marivo.semantic.expressions.objects import (
    TimeField,
    _Entity,
    _Relationship,
    entity_exact,
)

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringRepair


_SemanticInput: TypeAlias = "SemanticObject"

_SEMANTIC_INPUT_CANDIDATE_LIMIT = 12

_ALL_SEMANTIC_KINDS = frozenset(SemanticKind)


def _same_kind_candidates(
    catalog: CatalogInputLookup,
    allowed_kinds: frozenset[SemanticKind],
) -> tuple[Ref[SemanticKindTag], ...]:
    return tuple(ref for ref in require_catalog_index(catalog).refs if ref.kind in allowed_kinds)[
        :_SEMANTIC_INPUT_CANDIDATE_LIMIT
    ]


def _semantic_input_expected(allowed_kinds: frozenset[SemanticKind]) -> str:
    kinds = ", ".join(sorted(kind.value for kind in allowed_kinds))
    return f"a named semantic object with kind in {{{kinds}}}"


def _physical_dependencies(
    catalog: CatalogInputLookup, ref: Ref[SemanticKindTag]
) -> tuple[Ref[SemanticKindTag], ...]:
    """Find physical check inputs in the already compiled dependency closure."""
    pending = [ref]
    seen: set[Ref[SemanticKindTag]] = set()
    physical: set[Ref[SemanticKindTag]] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        declared = current.kind is SemanticKind.METRIC or (
            current.kind is SemanticKind.MEASURE
            and current.path in lookup_state(catalog).registry.declared_measures
        )
        if declared:
            pending.extend(lookup_state(catalog).dependencies.get(current, ()))
        else:
            physical.add(current)
    return tuple(sorted(physical, key=lambda item: item.key))


def _value_lookup_ref(value: object) -> Ref[SemanticKindTag] | None:
    """The catalog ref a resolved expression value names; None for anything else."""
    if isinstance(value, Deferred):
        declaration = root_declaration(value)
        if declaration is not None:
            if declaration.kind == "measure":
                return ref_factory.measure(f"{consume_row(value).root.ref.path}.{declaration.name}")
            if declaration.domain is not None:
                return ref_factory.metric(f"{declaration.domain.path}.{declaration.name}")
            return None
        try:
            field = consume_field(value)
        except SemanticExpressionError:
            return None
        return field.reference.ref
    if isinstance(value, TimeField) and value.path:
        return None
    if isinstance(value, _Entity):
        return entity_exact(value).ref
    if isinstance(value, _Relationship):
        return relationship_binding(value).ref.ref
    if isinstance(value, (_Dimension, TimeField, _Measure)):
        return value.ref.ref
    return None


def _semantic_input_ref(value: object) -> Ref[SemanticKindTag] | None:
    symbolic = native_symbol_ref(value)
    if symbolic is not None:
        return symbolic
    if isinstance(value, _SemanticObject):
        return entry_reference(value)
    return _value_lookup_ref(value)


def _semantic_input_received(value: object) -> str:
    exact_ref = _semantic_input_ref(value)
    if exact_ref is not None:
        if type(value) is Ref:
            return f"Ref[{exact_ref.kind.value}]({exact_ref.key})"
        return f"{type(value).__name__}({exact_ref.key})"
    if isinstance(value, _SemanticObject):
        return f"{type(value).__name__}(invalid identity)"
    return type(value).__name__


def _raise_invalid_semantic_input(
    *,
    catalog: CatalogInputLookup,
    value: object,
    allowed_kinds: frozenset[SemanticKind],
    location: str,
    message: str,
    kind: ErrorKind = ErrorKind.INVALID_REF,
    repair_value: AuthoringRepair | None = None,
    candidate_kinds: frozenset[SemanticKind] | None = None,
) -> NoReturn:
    candidates = _same_kind_candidates(
        catalog,
        candidate_kinds if candidate_kinds is not None else allowed_kinds,
    )
    received_ref = _semantic_input_ref(value)
    operation = location.split(".", 1)[1].split("(", 1)[0]
    _raise(
        kind,
        f"{location}: {message}",
        cls=SemanticRuntimeError,
        refs=(received_ref.key,) if received_ref is not None else (),
        expected=_semantic_input_expected(allowed_kinds),
        received=_semantic_input_received(value),
        constraint_id=ConstraintId.REF_SHAPE,
        hint=f'Use a named object from catalog.get(id) or search(); run marivo.help("semantic.{operation}") for the accepted kinds.',
        repair_value=repair_value
        or repair(
            kind="inspect",
            canonical_id=operation,
            action="Choose an allowed named object directly from the receiving project's Catalog.",
            snippet=f"subject = catalog.get({received_ref.key!r})"
            if received_ref is not None
            and received_ref in require_catalog_index(catalog).refs
            and received_ref.kind in allowed_kinds
            else None,
            candidates=tuple(candidate.key for candidate in candidates),
        ),
        details={
            "operation": location,
            "parameter": location,
            "query_executed": False,
            "allowed_kinds": tuple(sorted(kind.value for kind in allowed_kinds)),
            "received_type": type(value).__name__,
            "catalog_definition_fingerprint": catalog.definition_fingerprint,
            "candidates": tuple(candidate.key for candidate in candidates),
        },
    )


def _normalize_semantic_input(
    catalog: CatalogInputLookup,
    value: _SemanticInput,
    *,
    allowed_kinds: frozenset[SemanticKind],
    location: str,
    metric_preview: bool = False,
    declaration_closure: bool = False,
) -> Ref[SemanticKindTag]:
    """Resolve a named object's identity in the explicit receiving project scope."""
    index = require_catalog_index(catalog)
    if not isinstance(value, _SemanticObject):
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="expected a named semantic object obtained from this project's Catalog.get(id) or search().",
        )
    exact_ref = entry_reference(value)
    if type(value) is not _ENTRY_TYPE_BY_KIND.get(exact_ref.kind):
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="expected a concrete named semantic object produced by a Catalog.",
        )
    if entry_workspace(value).resolve() != catalog.workspace_dir.resolve():
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=(
                f"input belongs to project {entry_workspace(value)}; "
                f"the receiving Catalog belongs to {catalog.workspace_dir}."
            ),
        )
    if exact_ref.kind not in allowed_kinds:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=f"received semantic kind {exact_ref.kind.value}; select an allowed named object.",
        )
    if exact_ref not in index:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            kind=ErrorKind.NOT_FOUND,
            message="the named identity is outside the receiving Catalog scope.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    registry = lookup_state(catalog).registry
    if not declaration_closure and (
        (exact_ref.kind is SemanticKind.METRIC and not metric_preview)
        or (exact_ref.kind is SemanticKind.MEASURE and exact_ref.path in registry.declared_measures)
    ):
        physical = _physical_dependencies(catalog, exact_ref)
        shown = physical[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]
        physical_snippet: str | None = None
        if location == "catalog.readiness(refs=...)":
            physical_snippet = (
                "catalog.readiness(refs=["
                + ", ".join(f"catalog.get({ref.key!r})" for ref in shown)
                + "])"
                if shown and len(shown) == len(physical)
                else f"catalog.get({exact_ref.key!r}).show()"
            )
        _raise(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{location} requires physical inputs; {exact_ref.key} is a published "
            "expression declaration.",
            cls=SemanticRuntimeError,
            refs=(exact_ref.key,),
            expected="a physical Entity, field, Relationship, or authored temporal object",
            received=f"published {exact_ref.kind.value} {exact_ref.key}",
            hint='Run marivo.help("semantic.readiness") to inspect physical input requirements.',
            repair_value=repair(
                kind="inspect",
                canonical_id="readiness",
                action=(
                    "Inspect its definition with catalog.get(identity).show(), then "
                    "check the physical fields and Entities it reads instead."
                ),
                snippet=physical_snippet,
                candidates=tuple(ref.key for ref in shown),
            ),
        )
    return exact_ref


def _require_readiness_input(
    catalog: CatalogInputLookup,
    value: _SemanticInput,
) -> Ref[SemanticKindTag]:
    return _normalize_semantic_input(
        catalog,
        value,
        allowed_kinds=_ALL_SEMANTIC_KINDS,
        location="catalog.readiness(refs)",
        declaration_closure=True,
    )


def _freeze_semantic_inputs(
    values: Sequence[_SemanticInput],
    *,
    operation: str,
) -> tuple[_SemanticInput, ...]:
    if not input_sequence(values) or not values:
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{operation}(refs) requires a non-empty sequence of named semantic objects.",
            cls=SemanticRuntimeError,
            expected="a non-empty sequence of named semantic objects",
            received=(
                f"an empty {type(values).__name__}"
                if is_instance(values, Sequence) and not values
                else type(values).__name__
            ),
            repair_value=repair(
                kind="inspect",
                canonical_id=operation,
                action=f"Select at least one object and call catalog.{operation}([object]).",
            ),
            details={"parameter": "refs", "query_executed": False},
        )
    return tuple(values)


class CatalogInputLookup(CatalogLookup, Protocol):
    def _require_index(self) -> _CatalogIndex: ...

    def _entry_dependencies(
        self, ref: Ref[SemanticKindTag]
    ) -> tuple[Ref[SemanticKindTag], ...]: ...


def require_catalog_index(catalog: CatalogInputLookup) -> _CatalogIndex:
    return catalog._require_index()  # noqa: SLF001 - The Catalog checks its loaded state.


def catalog_entry_dependencies(
    catalog: CatalogInputLookup, ref: Ref[SemanticKindTag]
) -> tuple[Ref[SemanticKindTag], ...]:
    return catalog._entry_dependencies(ref)  # noqa: SLF001 - Captured closure of the owning Catalog.
