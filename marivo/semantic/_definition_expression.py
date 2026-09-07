"""Bounded, non-evaluating projection of already validated expression syntax."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Literal

from marivo.refs import EntityKind, FieldKind, Ref
from marivo.semantic._definition_display import describe_display
from marivo.semantic.definition import (
    ExpressionDescription,
    ExpressionNode,
    _Binary,
    _Cast,
    _Column,
    _ExpressionDisplay,
    _Field,
    _IfElse,
    _Literal,
    _SupportedExpression,
    _Unary,
    _UnsupportedExpression,
)


class _UnsupportedError(Exception):
    pass


def describe_expression(
    function: ast.FunctionDef,
    *,
    entities: Mapping[str, Ref[EntityKind]],
    bindings: Mapping[int, tuple[Ref[FieldKind], Ref[EntityKind]]],
) -> ExpressionDescription:
    """Capture only known structural syntax; never resolve literal values or names."""
    count = 0

    def visit(node: ast.expr, depth: int = 0) -> ExpressionNode:
        nonlocal count
        count += 1
        if depth > 32 or count > 256:
            raise OverflowError

        def child(value: ast.expr) -> ExpressionNode:
            return visit(value, depth + 1)

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            entity = entities.get(node.value.id)
            if entity is not None:
                return _Column(entity, node.attr)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            entity = entities.get(node.value.id)
            if (
                entity is not None
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                return _Column(entity, node.slice.value)
        if isinstance(node, ast.Constant):
            value_type: Literal["str", "int", "float", "bool", "none"]
            if node.value is None:
                value_type = "none"
            elif type(node.value) is bool:
                value_type = "bool"
            elif type(node.value) is int:
                value_type = "int"
            elif type(node.value) is float:
                value_type = "float"
            elif type(node.value) is str:
                value_type = "str"
            else:
                raise _UnsupportedError
            return _Literal(value_type)
        if isinstance(node, ast.Call):
            binding = bindings.get(id(node))
            if binding is not None:
                return _Field(*binding)
            if isinstance(node.func, ast.Attribute) and not node.keywords:
                if node.func.attr == "cast" and len(node.args) == 1:
                    data_type = node.args[0]
                    # Only fixed type tokens are structural metadata.
                    if (
                        isinstance(data_type, ast.Constant)
                        and isinstance(data_type.value, str)
                        and data_type.value
                        in {
                            "int8",
                            "int16",
                            "int32",
                            "int64",
                            "uint8",
                            "uint16",
                            "uint32",
                            "uint64",
                            "float32",
                            "float64",
                            "string",
                            "boolean",
                            "date",
                            "timestamp",
                        }
                    ):
                        return _Cast(child(node.func.value), data_type.value)
                if node.func.attr == "ifelse" and len(node.args) == 2:
                    return _IfElse(child(node.func.value), child(node.args[0]), child(node.args[1]))
        if isinstance(node, ast.UnaryOp):
            unary: dict[type[ast.unaryop], Literal["positive", "negative", "invert"]] = {
                ast.UAdd: "positive",
                ast.USub: "negative",
                ast.Invert: "invert",
            }
            operator = unary.get(type(node.op))
            if operator is not None:
                return _Unary(operator, child(node.operand))
        binary: dict[
            type[ast.operator] | type[ast.cmpop],
            Literal[
                "+",
                "-",
                "*",
                "/",
                "eq",
                "ne",
                "lt",
                "le",
                "gt",
                "ge",
                "&",
                "|",
                "^",
            ],
        ] = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.BitAnd: "&",
            ast.BitOr: "|",
            ast.BitXor: "^",
            ast.Eq: "eq",
            ast.NotEq: "ne",
            ast.Lt: "lt",
            ast.LtE: "le",
            ast.Gt: "gt",
            ast.GtE: "ge",
        }
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return _Binary(binary[type(node.op)], child(node.left), child(node.right))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in binary:
            return _Binary(binary[type(node.ops[0])], child(node.left), child(node.comparators[0]))
        raise _UnsupportedError

    returns = [node for node in function.body if isinstance(node, ast.Return)]
    if len(returns) != 1 or returns[0].value is None:
        return _UnsupportedExpression("unsupported_syntax")
    try:
        display: _ExpressionDisplay | None = describe_display(
            returns[0].value, entities=entities, bindings=bindings
        )
    except (OverflowError, ValueError, RecursionError):
        display = None
    try:
        return _SupportedExpression(visit(returns[0].value), display=display)
    except _UnsupportedError:
        return _UnsupportedExpression("unsupported_syntax", display=display)
    except OverflowError:
        return _UnsupportedExpression("limit_exceeded", display=display)
