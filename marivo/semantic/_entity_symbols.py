"""Validate resolved symbol identities in captured Entity table expressions."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from types import ModuleType

import ibis

from marivo.semantic._ibis_builtin_udf import builtin_udf_token
from marivo.semantic.errors import ErrorKind, SemanticLoadError, repair

_ENTITY_NONDETERMINISTIC_IBIS_NAMES: frozenset[str] = frozenset(
    {
        "now",
        "today",
        "random",
        "uuid",
        "integer_range",
    }
)

_ENTITY_DEFERRED_IBIS_NAMES: frozenset[str] = frozenset({"_", "t"})

_ENTITY_IBIS_ADMITTED_NAMES: frozenset[str] = (
    frozenset(getattr(ibis, "__all__", ())) - _ENTITY_NONDETERMINISTIC_IBIS_NAMES
)


def _is_admitted_ibis_symbol(value: object) -> bool:
    """Check one resolved body name against the ibis module by identity.

    A name is admitted when it is the ``ibis`` module itself, the deferred
    ``_``/``t`` instances, or the exact attribute for one admitted public
    Ibis symbol (``ibis.__all__`` minus clock/random names). This covers
    author import aliases regardless of local spelling. Module
    ``__getattr__`` is bypassed so lazy backend imports are never triggered
    by validation. Captured Tables, helper callables, nondeterministic
    clock/random symbols, and unrelated objects are rejected even when
    their names or modules coincide with Ibis types.
    """
    if value is ibis:
        return True
    for name, attr in vars(ibis).items():
        if attr is value:
            return name in _ENTITY_IBIS_ADMITTED_NAMES or name in _ENTITY_DEFERRED_IBIS_NAMES
    return False


def _entity_body_symbol_name(value: object) -> str | None:
    """Return the canonical ``ibis.<name>`` for one admitted symbol, else None.

    Names resolve by identity against the ``ibis`` module attributes, so
    author import aliases and ``ibis.<attr>`` spellings map to one canonical
    identity. Deferred instances and the module itself admit no hash token.
    """
    if value is ibis:
        return None
    for name, attr in vars(ibis).items():
        if attr is value:
            return name if name in _ENTITY_IBIS_ADMITTED_NAMES else None
    return None


def _rejected_ibis_attribute_name(value: object) -> str | None:
    """Return the ``ibis.<name>`` for one resolved-but-rejected ibis attribute.

    Distinguishes a rejected Ibis module attribute (clock, random, internal
    surfaces) from a captured non-Ibis value so the error can name the
    banned symbol identity.
    """
    if value is ibis:
        return None
    for name, attr in vars(ibis).items():
        if attr is value and name not in _ENTITY_DEFERRED_IBIS_NAMES:
            return str(name)
    return None


def _validate_ibis_attributes(
    body_root: ast.AST,
    symbols: Mapping[str, object],
    owning_key: str,
    symbol_tokens: dict[str, str],
) -> None:
    for node in ast.walk(body_root):
        # Attribute access on the ibis module (ibis.now(), ibis.random()) must
        # resolve to one admitted expression symbol; alias spellings are checked
        # at their Name nodes below.
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and type(symbols.get(node.value.id)) is ModuleType
            and getattr(symbols.get(node.value.id), "__name__", None) == "ibis"
        ):
            attr_value = getattr(symbols[node.value.id], node.attr, None)
            canonical = _entity_body_symbol_name(attr_value)
            if canonical is None:
                rejected = _rejected_ibis_attribute_name(attr_value)
                raise SemanticLoadError(
                    kind=ErrorKind.COMPILE_ERROR,
                    message=(
                        f"Entity body {owning_key!r} uses Ibis symbol "
                        f"ibis.{rejected or node.attr}, which is not admitted. Clock "
                        "reads, random values, generated identifiers, and non-expression "
                        "module attributes are not allowed in an Entity body."
                    ),
                    refs=(owning_key,),
                    expected=(
                        "deterministic expression-building symbols such as ibis.literal, "
                        "ibis.window, or ibis.row_number"
                    ),
                    received=f"ibis.{node.attr}",
                    hint=(
                        "Move the nondeterministic value into an explicit authored column "
                        "or pass it as data, not as a body symbol."
                    ),
                )
            symbol_tokens.setdefault(f"{node.value.id}.{node.attr}", f"ibis:{canonical}")


def _reject_entity_bind_calls(
    function: ast.FunctionDef,
    symbols: Mapping[str, object],
    is_bind_target: Callable[[ast.expr, Mapping[str, object]], bool],
    owning_key: str,
) -> None:
    for node in ast.walk(entity_table_body_nodes(function)):
        if isinstance(node, ast.Call) and is_bind_target(node.func, symbols):
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=(
                    f"Entity body {owning_key!r} calls ms.bind, which requires fields "
                    "that do not exist as inputs to their own Entity relation."
                ),
                refs=(owning_key,),
                expected="one Ibis Table expression over the injected Table parameter",
                received=ast.dump(node.func, include_attributes=False),
                hint="Read source columns from the injected parameter instead of ms.bind.",
            )


def validate_entity_table_closed_symbols(
    function: ast.FunctionDef,
    *,
    symbols: Mapping[str, object],
    is_bind_target: Callable[[ast.expr, Mapping[str, object]], bool],
    parameter_names: frozenset[str],
    owning_key: str,
) -> dict[str, str]:
    """Require every body ``Name`` to be the parameter or an admitted Ibis builder.

    Parameter annotations are type metadata, not runtime expression values,
    and are excluded from the closed-name check. Returns the mapping of body
    local names to canonical identity tokens for admitted Ibis symbols and typed
    scalar builtin UDFs, used to normalize the body hash by resolved identity.
    """
    symbol_tokens: dict[str, str] = {}
    annotation_nodes: set[int] = {
        id(parameter.annotation)
        for parameter in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
        if parameter.annotation is not None
    }
    if function.returns is not None:
        annotation_nodes.add(id(function.returns))
    _reject_entity_bind_calls(function, symbols, is_bind_target, owning_key)

    body_root = entity_table_body_nodes(function)
    _validate_ibis_attributes(body_root, symbols, owning_key, symbol_tokens)
    for node in ast.walk(body_root):
        if not isinstance(node, ast.Name):
            continue
        if node.id in parameter_names or id(node) in annotation_nodes:
            continue
        value = symbols.get(node.id)
        udf_token = builtin_udf_token(value)
        if udf_token is not None:
            symbol_tokens[node.id] = udf_token
            continue
        if _is_admitted_ibis_symbol(value):
            # The ibis module may only be used for admitted attribute access;
            # each attribute spelling is checked at its Attribute node above.
            if value is ibis:
                continue
            canonical = _entity_body_symbol_name(value)
            if canonical is not None:
                symbol_tokens[node.id] = f"ibis:{canonical}"
            continue
        if value is None and node.id not in symbols:
            raise SemanticLoadError(
                kind=ErrorKind.COMPILE_ERROR,
                message=(f"Entity body {owning_key!r} uses unresolved external name {node.id!r}."),
                refs=(owning_key,),
                expected=("the injected Table parameter, ibis module symbols, or inline literals"),
                received=node.id,
                hint=(
                    "Derive the expression from the injected parameter and import "
                    "ibis symbols in the defining module."
                ),
            )
        rejected_ibis_name = _rejected_ibis_attribute_name(value)
        if rejected_ibis_name is not None:
            raise SemanticLoadError(
                kind=ErrorKind.COMPILE_ERROR,
                message=(
                    f"Entity body {owning_key!r} uses Ibis symbol ibis.{rejected_ibis_name} "
                    f"(bound as {node.id!r}), which is not admitted. Clock reads, random "
                    "values, generated identifiers, and non-expression module attributes "
                    "are not allowed in an Entity body."
                ),
                refs=(owning_key,),
                expected=(
                    "deterministic expression-building symbols such as ibis.literal, "
                    "ibis.window, or ibis.row_number"
                ),
                received=f"ibis.{rejected_ibis_name}",
                hint=(
                    "Move the nondeterministic value into an explicit authored column or "
                    "pass it as data, not as a body symbol."
                ),
            )
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=(
                f"Entity body {owning_key!r} uses external value {node.id!r} of type "
                f"{type(value).__name__}, which is not a supported Ibis expression symbol."
            ),
            refs=(owning_key,),
            expected=(
                "the injected Table parameter, ibis module symbols, inline literals, or "
                "typed scalar builtin UDFs with at least one argument and concrete argument "
                "and return types"
            ),
            received=f"{node.id}={type(value).__name__}",
            repair=repair(
                kind="reauthor",
                canonical_id="entity",
                action=f"Replace the external reference {node.id!r} with an inline expression, a deterministic ibis root symbol, or an @ibis.udf.scalar.builtin declaration with at least one argument and concrete argument and return types. For casts, use .cast('decimal(20,0)') for the full UInt64 range, or .cast('int64') only within signed int64; ibis.dtype('int64') is supported. Ibis submodules are not Entity body symbols.",
            ),
            hint=(
                "Use the injected Table, inline literals, or deterministic public symbols "
                "from the ibis root module, or @ibis.udf.scalar.builtin declarations with "
                "at least one argument and concrete argument and return types. "
                "Ibis submodules are not body symbols. "
                "For casts use raw['column'].cast('decimal(20,0)') for the full UInt64 "
                "range, or .cast('int64') only when values fit signed int64; "
                "ibis.dtype('int64') is also admitted. Read marivo.help('semantic.entity.define')."
            ),
        )
    return symbol_tokens


def entity_table_body_nodes(function: ast.FunctionDef) -> ast.AST:
    """Return the single return expression with decorators and docstring excluded.

    Falls back to the whole function when the return-count contract is not yet
    established; shape validation then reports the structural failure.
    """
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    if len(returns) == 1 and returns[0].value is not None:
        return returns[0].value
    return function
