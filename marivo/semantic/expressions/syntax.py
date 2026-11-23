"""Extract the admitted return form of a field declaration."""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable


def _return_expr(
    fn: Callable[..., object],
    function: ast.FunctionDef | None = None,
) -> ast.AST | None:
    if function is None:
        try:
            source = textwrap.dedent(inspect.getsource(fn))
            tree = ast.parse(source)
        except (OSError, TypeError, IndentationError, SyntaxError):
            return None
        function = next(
            (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)), None
        )
    if function is None:
        return None
    locals_by_name = {
        stmt.targets[0].id: stmt.value
        for stmt in function.body
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    }
    value = next((stmt.value for stmt in function.body if isinstance(stmt, ast.Return)), None)
    seen: set[str] = set()
    while isinstance(value, ast.Name) and value.id in locals_by_name and value.id not in seen:
        seen.add(value.id)
        value = locals_by_name[value.id]
    return value
