"""Non-executing display of captured syntax, independent of semantic decomposition."""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping

from marivo.refs import EntityKind, FieldKind, Ref, SemanticKindTag
from marivo.semantic.definition import _ExpressionDisplay


def describe_display(
    expression: ast.expr,
    *,
    entities: Mapping[str, Ref[EntityKind]],
    bindings: Mapping[int, tuple[Ref[FieldKind], Ref[EntityKind]]],
) -> _ExpressionDisplay:
    """Normalize an existing expression AST; preserve constants and bind aliases."""
    aliases: dict[str, tuple[str, Ref[SemanticKindTag]]] = {}
    count = 0
    depth = 0

    def alias(ref: Ref[SemanticKindTag], prefix: str) -> ast.Name:
        if ref.key not in aliases:
            aliases[ref.key] = (f"{prefix}{len(aliases) + 1}", ref)
        return ast.Name(id=aliases[ref.key][0], ctx=ast.Load())

    class Display(ast.NodeTransformer):
        def visit(self, node: ast.AST) -> ast.AST:
            nonlocal count, depth
            count += 1
            depth += 1
            if count > 1024 or depth > 64:
                raise OverflowError
            try:
                binding = bindings.get(id(node))
                if binding is not None:
                    return alias(binding[0], "f")
                # The compiler owns the original tree; never mutate it.
                clone = copy.copy(node)
                for field, value in ast.iter_fields(node):
                    if isinstance(value, list):
                        setattr(clone, field, list(value))
                result = super().visit(clone)
                assert isinstance(result, ast.AST)
                return result
            finally:
                depth -= 1

        def visit_Name(self, node: ast.Name) -> ast.expr:
            entity = entities.get(node.id)
            return alias(entity, "t") if entity is not None else node

        def expression(self, node: ast.expr) -> ast.expr:
            result = self.visit(node)
            assert isinstance(result, ast.expr)
            return result

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            if isinstance(node.value, ast.Name) and node.value.id in entities:
                return ast.Subscript(
                    value=alias(entities[node.value.id], "t"),
                    slice=ast.Constant(value=node.attr),
                    ctx=ast.Load(),
                )
            return self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in entities
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                return ast.Subscript(
                    value=alias(entities[node.value.id], "t"),
                    slice=ast.Constant(value=node.slice.value),
                    ctx=ast.Load(),
                )
            return self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> ast.AST:
            if isinstance(node.func, ast.Attribute):
                # A method name is not a column access (including table.count()).
                return ast.Call(
                    func=ast.Attribute(
                        value=self.expression(node.func.value), attr=node.func.attr, ctx=ast.Load()
                    ),
                    args=[self.expression(arg) for arg in node.args],
                    keywords=[
                        ast.keyword(arg=kw.arg, value=self.expression(kw.value))
                        for kw in node.keywords
                    ],
                )
            return self.generic_visit(node)

    text = ast.unparse(ast.fix_missing_locations(Display().visit(expression)))
    if len(text) > 16384:
        raise OverflowError
    return _ExpressionDisplay(text, tuple(aliases.values()))
