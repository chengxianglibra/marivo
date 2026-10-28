"""Consumed physical fields of derived Ibis expressions."""

import ibis.expr.operations as ops
import ibis.expr.types as ir


def physical_fields(value: ir.Value) -> tuple[ops.Field, ...]:
    """Follow only consumed projection expressions through aliases to source fields."""
    found: dict[ops.Field, None] = {}

    def visit(node: ops.Value) -> None:
        if isinstance(node, ops.Field):
            definition = node.rel.values.get(node.name)
            if definition is not None:
                visit(definition)
            elif isinstance(node.rel, ops.Reference):
                visit(ops.Field(node.rel.parent, node.name))
            else:
                found[node] = None
        else:
            for child in node.args:
                if isinstance(child, ops.Value):
                    visit(child)
                elif isinstance(child, tuple):
                    for item in child:
                        if isinstance(item, ops.Value):
                            visit(item)

    visit(value.op())
    return tuple(found)
