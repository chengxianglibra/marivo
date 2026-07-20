"""Internal semantic resolver backed by Materializer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import ibis
import ibis.expr.types as ir

from marivo.datasource.source import AuthoringScope
from marivo.refs import (
    EntityKind,
    FieldKind,
    MeasureKind,
    MetricKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import AggKind
from marivo.semantic.materializer import Materializer


def _require_kind[KindT: SemanticKindTag](
    catalog: SemanticCatalog,
    value: Ref[KindT],
    *,
    expected: tuple[SemanticKind, ...],
) -> str:
    if type(value) is not Ref:
        _raise(
            ErrorKind.INVALID_REF,
            f"Resolver requires an exact Ref; received {type(value).__name__}. "
            "Pass entry.ref or construct ms.Ref.<kind>(path).",
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
    return catalog.require(ref).path


@dataclass
class SemanticResolver:
    """Internal semantic-to-Ibis resolver for analysis and catalog previews."""

    catalog: SemanticCatalog
    connections: Any
    sample_size: int | None = None
    entity_scopes: Mapping[str, AuthoringScope] | None = None

    def __post_init__(self) -> None:
        self._materializer = Materializer(
            self.catalog._project,
            self.connections.session_backend,
            sample_size=self.sample_size,
            entity_scopes=self.entity_scopes,
        )

    def entity(self, entity_ref: Ref[EntityKind]) -> ibis.Table:
        ref = _require_kind(self.catalog, entity_ref, expected=(SemanticKind.ENTITY,))
        return self._materializer.entity(ref)

    def table(self, entity_ref: Ref[EntityKind]) -> ibis.Table:
        """Private compatibility spelling for tabular preview internals."""
        return self.entity(entity_ref)

    def dimension(self, ref_value: Ref[FieldKind]) -> ir.Value:
        ref = _require_kind(
            self.catalog,
            ref_value,
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        )
        return self._materializer.dimension(ref)

    def metric(self, ref_value: Ref[MetricKind]) -> ir.Value:
        ref = _require_kind(self.catalog, ref_value, expected=(SemanticKind.METRIC,))
        return self._materializer.metric(ref)

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

    def aggregate_measure_on(
        self,
        ref_value: Ref[MeasureKind],
        table: ibis.Table,
        agg: AggKind,
    ) -> ir.Value:
        """Apply one registered aggregate to a governed measure on ``table``."""
        ref = _require_kind(self.catalog, ref_value, expected=(SemanticKind.MEASURE,))
        return self._materializer.aggregate_measure_on(ref, table, agg)

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

    def metric_on(
        self,
        ref_value: Ref[MetricKind],
        *tables: ibis.Table,
    ) -> ir.Value:
        ref = _require_kind(self.catalog, ref_value, expected=(SemanticKind.METRIC,))
        return self._materializer.metric_on(ref, *tables)
