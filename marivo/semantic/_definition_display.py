"""Non-executing display of captured syntax, independent of semantic decomposition."""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping

from typing_extensions import override

from marivo._kernel.refs import EntityKind, FieldKind, Ref, SemanticKindTag
from marivo.semantic._definition_expression_types import _ExpressionDisplay


class _Display(ast.NodeTransformer):
    def __init__(
        self,
        entities: Mapping[str, Ref[EntityKind]],
        bindings: Mapping[int, tuple[Ref[FieldKind], Ref[EntityKind]]],
    ) -> None:
        self.entities = entities
        self.bindings = bindings
        self.aliases: dict[str, tuple[str, Ref[SemanticKindTag]]] = {}
        self.count = 0
        self.depth = 0

    def alias(self, ref: Ref[SemanticKindTag], prefix: str) -> ast.Name:
        if ref.key not in self.aliases:
            self.aliases[ref.key] = (f"{prefix}{len(self.aliases) + 1}", ref)
        return ast.Name(id=self.aliases[ref.key][0], ctx=ast.Load())

    @override
    def visit(self, node: ast.AST) -> ast.AST:
        self.count += 1
        self.depth += 1
        if self.count > 1024 or self.depth > 64:
            raise OverflowError
        try:
            binding = self.bindings.get(id(node))
            if binding is not None:
                return self.alias(binding[0], "f")
            # The compiler owns the original tree; never mutate it.
            clone = copy.copy(node)
            for field, value in ast.iter_fields(node):
                if isinstance(value, list):
                    setattr(clone, field, list(value))
            result = super().visit(clone)
            assert isinstance(result, ast.AST)
            return result
        finally:
            self.depth -= 1

    @override
    def visit_Name(self, node: ast.Name) -> ast.expr:
        entity = self.entities.get(node.id)
        return self.alias(entity, "t") if entity is not None else node

    def expression(self, node: ast.expr) -> ast.expr:
        result = self.visit(node)
        assert isinstance(result, ast.expr)
        return result

    @override
    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if isinstance(node.value, ast.Name) and node.value.id in self.entities:
            return ast.Subscript(
                value=self.alias(self.entities[node.value.id], "t"),
                slice=ast.Constant(value=node.attr),
                ctx=ast.Load(),
            )
        return self.generic_visit(node)

    @override
    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in self.entities
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            return ast.Subscript(
                value=self.alias(self.entities[node.value.id], "t"),
                slice=ast.Constant(value=node.slice.value),
                ctx=ast.Load(),
            )
        return self.generic_visit(node)

    @override
    def visit_Call(self, node: ast.Call) -> ast.AST:
        if isinstance(node.func, ast.Attribute):
            # A method name is not a column access (including table.count()).
            return ast.Call(
                func=ast.Attribute(
                    value=self.expression(node.func.value), attr=node.func.attr, ctx=ast.Load()
                ),
                args=[self.expression(arg) for arg in node.args],
                keywords=[
                    ast.keyword(arg=kw.arg, value=self.expression(kw.value)) for kw in node.keywords
                ],
            )
        return self.generic_visit(node)


def describe_display(
    expression: ast.expr,
    *,
    entities: Mapping[str, Ref[EntityKind]],
    bindings: Mapping[int, tuple[Ref[FieldKind], Ref[EntityKind]]],
) -> _ExpressionDisplay:
    """Normalize an existing expression AST; preserve constants and bind aliases."""
    display = _Display(entities, bindings)
    text = ast.unparse(ast.fix_missing_locations(display.visit(expression)))
    if len(text) > 16384:
        raise OverflowError
    return _ExpressionDisplay(text, tuple(display.aliases.values()))
