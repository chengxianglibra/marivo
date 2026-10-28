"""Typed builtin UDF bindings and immutable list literals retain closed recipes."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

import ibis
import ibis.expr.types as ir
import pytest

from marivo._definition.deferred import inspect_deferred
from marivo.refs import ref
from marivo.semantic._expression_binding import compile_expression_body
from marivo.semantic.errors import SemanticLoadError
from tests.shared_fixtures import load_inline_semantic


def test_entity_builtin_udf_executes_backend_function_without_authored_body() -> None:
    def declaration(value: str) -> int:
        raise AssertionError("the authored builtin declaration must never execute")

    character_count: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
        declaration, name="length"
    )

    def body(raw: ir.Table) -> ir.Table:
        return raw.mutate(size=character_count(raw["payload"]))

    compiled = compile_expression_body(
        body, owning_ref=ref.entity("sales.events"), body_kind="entity_table"
    )
    result = compiled.callable(ibis.memtable({"payload": ["one", "seven", None]}))
    assert isinstance(result, ir.Table)
    sizes = result.execute()["size"]
    assert sizes.iloc[:2].tolist() == [3, 5]
    assert sizes.isna().iloc[2]
    assert compiled.source_columns == ("payload",)


@pytest.mark.parametrize("owner", ("entity", "dimension", "time_dimension"))
def test_builtin_udf_hash_captures_resolved_identity_and_not_alias_name(owner: str) -> None:
    def declaration(value: str) -> str:
        raise AssertionError("a builtin declaration is metadata")

    lower: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
        declaration, name="lower", database="text", catalog="warehouse"
    )
    first = lower
    alias = lower

    def first_body(raw: ir.Table) -> ir.Table | ir.Value:
        return raw.mutate(text=first(raw["payload"]))

    def alias_body(raw: ir.Table) -> ir.Table | ir.Value:
        return raw.mutate(text=alias(raw["payload"]))

    def first_field(raw: ir.Table) -> ir.Value:
        return first(raw["payload"])

    def alias_field(raw: ir.Table) -> ir.Value:
        return alias(raw["payload"])

    def compile_body(body: Callable[[ir.Table], ir.Table | ir.Value]) -> str:
        return compile_expression_body(
            body,
            owning_ref=ref.entity("sales.events")
            if owner == "entity"
            else ref.dimension("sales.events.text")
            if owner == "dimension"
            else ref.time_dimension("sales.events.time"),
            ordered_entity_refs=() if owner == "entity" else (ref.entity("sales.events"),),
            body_kind="entity_table"
            if owner == "entity"
            else "dimension"
            if owner == "dimension"
            else "time_dimension",
        ).body_ast_hash

    first_body = first_body if owner == "entity" else first_field
    alias_body = alias_body if owner == "entity" else alias_field
    assert compile_body(first_body) == compile_body(alias_body)

    upper: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
        declaration, name="upper", database="text", catalog="warehouse"
    )

    before = compile_body(first_body)
    first = upper
    assert before != compile_body(first_body)


@pytest.mark.parametrize(
    "declaration",
    (
        "@ibis.udf.scalar.builtin(name='length', database='text')\ndef builtin(value: str) -> int: ...",
        "@ibis.udf.scalar.builtin(name='length', catalog='warehouse')\ndef builtin(value: str) -> int: ...",
        "@ibis.udf.scalar.builtin(name='length')\ndef builtin(value: bytes) -> int: ...",
        "@ibis.udf.scalar.builtin(name='length')\ndef builtin(value: str) -> float: ...",
        "@ibis.udf.scalar.builtin(name='length')\ndef builtin(value: str = 'empty') -> int: ...",
    ),
)
def test_builtin_udf_signature_and_namespace_change_definition_identity(declaration: str) -> None:
    baseline = "@ibis.udf.scalar.builtin(name='length')\ndef builtin(value: str) -> int: ..."

    def fingerprint(declared: str) -> str:
        source = f"""import ibis
import marivo.datasource as md
import marivo.semantic as ms
{declared}
@ms.entity(datasource=ms.ref.datasource('wh'), source=md.table('events'))
def events(raw):
    return raw.mutate(size=builtin(raw['payload']))
"""
        with load_inline_semantic(source) as result:
            assert result.compiled_state is not None, result.errors
            return result.compiled_state.definition_fingerprint

    assert fingerprint(baseline) != fingerprint(declaration)


def test_entity_rejects_helper_disguised_as_builtin_without_invoking_it() -> None:
    def declaration(value: str) -> int:
        raise AssertionError("a builtin declaration is metadata")

    builtin: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(declaration, name="length")

    @wraps(builtin)
    def helper(value: object) -> object:
        raise AssertionError("the arbitrary helper must not run")

    def body(raw: ir.Table) -> ir.Table:
        return raw.mutate(size=helper(raw["payload"]))

    with pytest.raises(SemanticLoadError, match="external value") as error:
        compile_expression_body(
            body, owning_ref=ref.entity("sales.events"), body_kind="entity_table"
        )
    assert "typed scalar builtin UDFs" in str(error.value.expected)


@pytest.mark.parametrize("mode", ("python", "aggregate", "untyped", "zero_argument"))
def test_entity_only_admits_typed_scalar_builtin_udfs(mode: str) -> None:
    declarations = {
        "python": "@ibis.udf.scalar.python\ndef builtin(value: str) -> int: return len(value)",
        "aggregate": "@ibis.udf.agg.builtin(name='count')\ndef builtin(value: str) -> int: ...",
        "untyped": "@ibis.udf.scalar.builtin(name='length')\ndef builtin(value) -> int: ...",
        "zero_argument": "@ibis.udf.scalar.builtin(name='random')\ndef builtin() -> float: ...",
    }
    call = "builtin()" if mode == "zero_argument" else "builtin(raw['payload'])"
    source = f"""import ibis
import marivo.datasource as md
import marivo.semantic as ms
{declarations[mode]}
@ms.entity(datasource=ms.ref.datasource('wh'), source=md.table('events'))
def events(raw):
    return raw.mutate(size={call})
"""
    with load_inline_semantic(source) as result:
        assert result.compiled_state is None
        assert "external value 'builtin'" in str(result.errors)
        assert "at least one argument" in str(result.errors[0].expected)
        assert "concrete argument and return types" in str(result.errors[0].expected)


@pytest.mark.parametrize("stage", ("measure", "metric"))
def test_declaration_list_literals_capture_the_same_frozen_recipe_as_tuples(stage: str) -> None:
    def fingerprint(values: str) -> str:
        expression = (
            f"ibis.ifelse(status.isin({values}), amount, 0)"
            if stage == "measure"
            else f"amount.sum().where(status.isin({values}))"
        )
        source = f"""import ibis
import marivo.datasource as md
import marivo.semantic as ms
events = ms.entity(name='events', datasource=ms.ref.datasource('wh'), source=md.table('events'))
amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())
status = ms.dimension_column(name='status', entity=events, column='status')
@ms.{stage}()
def selected():
    return {expression}
"""
        with load_inline_semantic(source) as result:
            assert result.compiled_state is not None, result.errors
            registry = result.compiled_state.registry
            value = (
                registry.declared_measures["test.events.selected"]
                if stage == "measure"
                else registry.declared_metrics["test.selected"]
            )
            return inspect_deferred(value).fingerprint

    assert fingerprint("['paid', 'settled']") == fingerprint("('paid', 'settled')")
