"""Internal semantic resolver backed by Materializer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

import ibis
import ibis.expr.types as ir

from marivo.datasource.ir import QueryParamScalar, QueryParamScalarList
from marivo.datasource.source import AuthoringScope
from marivo.refs import (
    EntityKind,
    FieldKind,
    MeasureKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.materializer import Materializer

KindT = TypeVar("KindT", bound=SemanticKindTag)


def _require_kind(
    catalog: SemanticCatalog,
    value: Ref[KindT],
    *,
    expected: tuple[SemanticKind, ...],
) -> str:
    if type(value) is not Ref:
        _raise(
            ErrorKind.INVALID_REF,
            f"Resolver requires an exact Ref; received {type(value).__name__}. "
            "Pass a typed Ref or construct ms.ref.<kind>(path).",
            cls=SemanticRuntimeError,
        )
    ref = value
    if ref.kind not in expected:
        expected_text = " or ".join(str(item) for item in expected)
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Semantic ref {ref.key!r} has kind {ref.kind}; expected {expected_text}.",
            cls=SemanticRuntimeError,
            refs=(ref.key,),
            details={"expected_kind": expected_text, "actual_kind": str(ref.kind)},
        )
    catalog._require_ref(ref)
    return ref.path


@dataclass
class SemanticResolver:
    """Internal semantic-to-Ibis resolver for analysis and catalog previews."""

    catalog: SemanticCatalog
    connections: Any
    sample_size: int | None = None
    entity_scopes: Mapping[str, AuthoringScope] | None = None
    source_bindings: Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] | None = (
        None
    )

    def __post_init__(self) -> None:
        self._materializer = Materializer(
            self.catalog._project,
            self.connections.session_backend,
            sample_size=self.sample_size,
            entity_scopes=self.entity_scopes,
            source_bindings=self.source_bindings,
            source_binder=getattr(self.connections, "bind_source", None),
        )

    def entity(self, entity_ref: Ref[EntityKind]) -> ibis.Table:
        ref = _require_kind(self.catalog, entity_ref, expected=(SemanticKind.ENTITY,))
        return self._materializer.entity(ref)

    def dimension(self, ref_value: Ref[FieldKind]) -> ir.Value:
        ref = _require_kind(
            self.catalog,
            ref_value,
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        )
        return self._materializer.dimension(ref)

    def measure(self, ref_value: Ref[MeasureKind]) -> ir.Value:
        ref = _require_kind(self.catalog, ref_value, expected=(SemanticKind.MEASURE,))
        return self._materializer.measure(ref)

    def measure_on(
        self,
        ref_value: Ref[MeasureKind],
        table: ibis.Table,
    ) -> ir.Value:
        ref = _require_kind(self.catalog, ref_value, expected=(SemanticKind.MEASURE,))
        return self._materializer.measure_on(ref, table)

    def dimension_on(
        self,
        ref_value: Ref[FieldKind],
        table: ibis.Table,
    ) -> ir.Value:
        ref = _require_kind(
            self.catalog,
            ref_value,
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        )
        return self._materializer.dimension_on(ref, table)
