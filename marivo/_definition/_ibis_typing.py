"""Selected Ibis extension contracts used to define Marivo operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ibis.expr.operations as ops
    import ibis.expr.schema as sch
    import ibis.expr.types as ir

    class ValueOperation:
        def to_expr(self) -> ir.Value: ...
        def copy(self, **overrides: object) -> ops.Value: ...

    class PhysicalTableOperation:
        name: str
        schema: sch.Schema

        def to_expr(self) -> ir.Table: ...
else:
    from ibis.expr.operations import PhysicalTable as PhysicalTable
    from ibis.expr.operations import Value as Value

    PhysicalTableOperation = PhysicalTable
    ValueOperation = Value
