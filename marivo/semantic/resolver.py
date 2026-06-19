"""Internal semantic resolver backed by Materializer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ibis
import ibis.expr.types as ir

from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic.catalog import SemanticKind, SemanticObject
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.materializer import Materializer
from marivo.semantic.refs import as_ref


def _ref_and_kind(value: SemanticRef | SemanticObject | str) -> tuple[str, SymbolKind | None]:
    ref = as_ref(value)
    if ref is not None:
        return ref.id, ref.kind
    return str(value), None


def _require_kind(
    value: SemanticRef | SemanticObject | str,
    *,
    expected: tuple[SemanticKind, ...],
) -> str:
    ref, kind = _ref_and_kind(value)
    if kind is not None and kind not in expected:
        expected_text = " or ".join(str(item) for item in expected)
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Semantic ref {ref!r} has kind {kind}; expected {expected_text}.",
            cls=SemanticRuntimeError,
            refs=(ref,),
            details={"expected_kind": expected_text, "actual_kind": str(kind)},
        )
    return ref


@dataclass
class SemanticResolver:
    """Internal semantic-to-Ibis resolver for analysis and catalog previews."""

    catalog: Any
    connections: Any
    sample_size: int | None = None

    def __post_init__(self) -> None:
        self._materializer = Materializer(
            self.catalog._project,
            self.connections.session_backend,
            sample_size=self.sample_size,
        )

    def table(self, entity_ref: SemanticRef | SemanticObject | str) -> ibis.Table:
        ref = _require_kind(entity_ref, expected=(SemanticKind.ENTITY,))
        return self._materializer.entity(ref)

    def dimension(self, ref_value: SemanticRef | SemanticObject | str) -> ir.Value:
        ref = _require_kind(
            ref_value,
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        )
        return self._materializer.dimension(ref)

    def metric(self, ref_value: SemanticRef | SemanticObject | str) -> ir.Value:
        ref = _require_kind(ref_value, expected=(SemanticKind.METRIC,))
        return self._materializer.metric(ref)

    def measure(self, ref_value: SemanticRef | SemanticObject | str) -> ir.Value:
        ref = _require_kind(ref_value, expected=(SemanticKind.MEASURE,))
        return self._materializer.measure(ref)

    def dimension_on(
        self,
        ref_value: SemanticRef | SemanticObject | str,
        table: ibis.Table,
    ) -> ir.Value:
        ref = _require_kind(
            ref_value,
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        )
        return self._materializer.dimension_on(ref, table)

    def metric_on(
        self,
        ref_value: SemanticRef | SemanticObject | str,
        *tables: ibis.Table,
    ) -> ir.Value:
        ref = _require_kind(ref_value, expected=(SemanticKind.METRIC,))
        return self._materializer.metric_on(ref, *tables)
