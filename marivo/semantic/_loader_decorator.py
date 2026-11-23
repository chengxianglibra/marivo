"""Classify the executed Entity constructor used as a decorator."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType

from marivo.semantic._model_source import _ModelSources
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticLoadError
from marivo.semantic.ir import SourceLocation


def _semantic_aliases(tree: ast.Module) -> tuple[set[str], set[str], bool]:
    module_aliases: set[str] = set()
    direct_aliases: set[str] = set()
    bare_module = False
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "marivo.semantic":
                    if alias.asname is None:
                        bare_module = True
                    else:
                        module_aliases.add(alias.asname)
        elif isinstance(statement, ast.ImportFrom):
            if statement.module == "marivo":
                module_aliases.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "semantic"
                )
            elif statement.module == "marivo.semantic":
                direct_aliases.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "entity"
                )
    return module_aliases, direct_aliases, bare_module


def _is_entity_target(
    target: ast.expr,
    semantic_module: ModuleType,
    entity_constructor: object,
    offending_scope: dict[str, object],
    module_aliases: set[str],
    direct_aliases: set[str],
    bare_module: bool,
) -> bool:
    direct = (
        isinstance(target, ast.Name)
        and target.id in direct_aliases
        and offending_scope.get(target.id) is entity_constructor
    )
    qualified = (
        isinstance(target, ast.Attribute)
        and target.attr == "entity"
        and isinstance(target.value, ast.Name)
        and target.value.id in module_aliases
        and offending_scope.get(target.value.id) is semantic_module
    )
    bare = (
        bare_module
        and isinstance(target, ast.Attribute)
        and target.attr == "entity"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "semantic"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "marivo"
        and getattr(offending_scope.get("marivo"), "semantic", None) is semantic_module
    )
    return direct or qualified or bare


def entity_constructor_decorator_error(
    filepath: Path, exc: Exception, sources: _ModelSources
) -> SemanticLoadError | None:
    """Classify only an executed @ms.entity(...) application that returned Ref."""
    if type(exc) is not TypeError or str(exc) != "'Ref' object is not callable":
        return None
    offending_line: int | None = None
    offending_scope: dict[str, object] | None = None
    traceback = exc.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename) == filepath:
            offending_line = traceback.tb_lineno
            offending_scope = {
                **traceback.tb_frame.f_globals,
                **traceback.tb_frame.f_locals,
            }
        traceback = traceback.tb_next
    if offending_line is None or offending_scope is None:
        return None
    semantic_module = sys.modules.get("marivo.semantic")
    if semantic_module is None:
        return None
    entity_constructor = getattr(semantic_module, "entity", None)
    source = sources.get(str(filepath))
    if source is None:
        return None

    module_aliases, direct_aliases, bare_module = _semantic_aliases(source.tree)

    for node in ast.walk(source.tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not decorator.lineno <= offending_line <= (decorator.end_lineno or decorator.lineno):
                continue
            if _is_entity_target(
                decorator.func,
                semantic_module,
                entity_constructor,
                offending_scope,
                module_aliases,
                direct_aliases,
                bare_module,
            ):
                return SemanticLoadError(
                    kind=ErrorKind.ENTITY_CONSTRUCTOR_AS_DECORATOR,
                    message="ms.entity(name=...) returns a native Entity symbol and cannot decorate a definition.",
                    location=SourceLocation(str(filepath), decorator.lineno),
                    expected="name = ms.entity(...) without a decorated function body",
                    received="@ms.entity(...) applied to a definition",
                    constraint_id=ConstraintId.ENTITY_CONSTRUCTOR_ASSIGNMENT,
                )
    return None
