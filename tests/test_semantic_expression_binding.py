"""Tier-2 semantic expression binding compilation and runtime tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import ibis
import pytest

from marivo.refs import Ref, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    ExpressionBody,
    bind,
    compile_expression_body,
    evaluate_expression_body,
)
from marivo.semantic.errors import ErrorKind, SemanticLoadError, SemanticRuntimeError

ORDERS = ref_factory.entity("sales.orders")
USERS = ref_factory.entity("sales.users")
AMOUNT = ref_factory.measure("sales.orders.amount")
AMOUNT_ALIAS = AMOUNT
NET_AMOUNT = ref_factory.measure("sales.orders.net_amount")
COUNTRY = ref_factory.dimension("sales.orders.country")
REVENUE = ref_factory.metric("sales.revenue")


def _revenue(orders):
    return bind(AMOUNT, orders).sum()


def _renamed_revenue(rows):
    return bind(AMOUNT_ALIAS, rows).sum()


def _net_amount(orders):
    return bind(AMOUNT, orders) * 0.9


def _net_revenue(orders):
    return bind(NET_AMOUNT, orders).sum()


def _wrong_alias(orders):
    return bind(AMOUNT, orders.filter(orders.amount > 0)).sum()


def _two_entities(orders, users):
    return bind(AMOUNT, orders).sum()


def _second_entity(orders, users):
    return bind(AMOUNT, users).sum()


def _identity_metric(orders):
    return bind(AMOUNT, orders)


def _recursive_amount(orders):
    return bind(AMOUNT, orders)


def _metric_ref_call(orders):
    return bind(REVENUE, orders)  # type: ignore[arg-type]


def _legacy_field_call(orders):
    return AMOUNT(orders).sum()  # type: ignore[operator]


def _sidecar(
    *,
    amount_body: ExpressionBody | None = None,
    extra_bodies: dict | None = None,
) -> CompiledExpressionSidecar:
    bodies = {AMOUNT: amount_body or ExpressionBody.for_column("amount")}
    if extra_bodies:
        bodies.update(extra_bodies)
    owners = {AMOUNT: ORDERS, NET_AMOUNT: ORDERS}
    return CompiledExpressionSidecar(
        bodies=bodies,
        field_owners=owners,
        catalog_refs=frozenset({ORDERS, AMOUNT, NET_AMOUNT, REVENUE}),
    )


def test_compile_captures_exact_field_ref_and_entity_position() -> None:
    body = compile_expression_body(
        _revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    assert body.parameter_count == 1
    assert len(body.bindings) == 1
    binding = body.bindings[0]
    assert binding.field_ref.kind.value == "measure"
    assert binding.field_ref.path == "sales.orders.amount"
    assert binding.entity_position == 0


def test_variable_parameter_and_function_renames_preserve_body_identity() -> None:
    first = compile_expression_body(
        _revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    renamed = compile_expression_body(
        _renamed_revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    assert first.body_ast_hash == renamed.body_ast_hash
    assert first.bindings == renamed.bindings


def test_entity_position_changes_body_identity_and_binding() -> None:
    first = compile_expression_body(
        _two_entities,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS, USERS),
    )
    second = compile_expression_body(
        _second_entity,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS, USERS),
    )
    assert first.body_ast_hash != second.body_ast_hash
    assert first.bindings[0].entity_position == 0
    assert second.bindings[0].entity_position == 1


def test_compile_rejects_transformed_or_indirect_alias() -> None:
    with pytest.raises(SemanticLoadError) as exc_info:
        compile_expression_body(
            _wrong_alias,
            owning_ref=REVENUE,
            ordered_entity_refs=(ORDERS,),
        )
    assert exc_info.value.kind == ErrorKind.BINDING_ALIAS_NOT_DIRECT


def test_compile_rejects_metric_ref_call_as_invalid_binding() -> None:
    with pytest.raises(SemanticLoadError) as exc_info:
        compile_expression_body(
            _metric_ref_call,
            owning_ref=REVENUE,
            ordered_entity_refs=(ORDERS,),
        )
    assert exc_info.value.kind == ErrorKind.INVALID_BINDING_REF
    assert exc_info.value.semantic_refs == (REVENUE.key, REVENUE.key)


def test_compile_rejects_legacy_callable_ref_with_bind_repair() -> None:
    with pytest.raises(SemanticLoadError) as exc_info:
        compile_expression_body(
            _legacy_field_call,
            owning_ref=REVENUE,
            ordered_entity_refs=(ORDERS,),
        )
    assert exc_info.value.kind == ErrorKind.INVALID_BINDING_REF
    assert exc_info.value.expected == "ms.bind(field_ref, entity_parameter)"


def test_root_and_nested_evaluation_use_compiled_sidecar() -> None:
    amount_body = ExpressionBody.for_column("amount")
    net_body = compile_expression_body(
        _net_amount,
        owning_ref=NET_AMOUNT,
        ordered_entity_refs=(ORDERS,),
    )
    metric_body = compile_expression_body(
        _net_revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    sidecar = _sidecar(
        amount_body=amount_body,
        extra_bodies={NET_AMOUNT: net_body},
    )
    table = ibis.table({"amount": "float64"}, name="orders")
    result = evaluate_expression_body(
        catalog_definition_fingerprint="sha256:test",
        expression_sidecar=sidecar,
        owning_ref=REVENUE,
        body=metric_body,
        entity_refs=(ORDERS,),
        aliases=(table,),
    )
    assert result.type().is_floating()
    assert "amount" in str(result)


def test_context_is_cleaned_after_success_and_body_failure() -> None:
    metric_body = compile_expression_body(
        _revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    table = ibis.table({"amount": "float64"}, name="orders")
    evaluate_expression_body(
        catalog_definition_fingerprint="sha256:test",
        expression_sidecar=_sidecar(),
        owning_ref=REVENUE,
        body=metric_body,
        entity_refs=(ORDERS,),
        aliases=(table,),
    )
    with pytest.raises(SemanticRuntimeError) as success_cleanup:
        bind(AMOUNT, table)
    assert success_cleanup.value.kind == ErrorKind.BINDING_CONTEXT_MISSING

    def fails(_orders):
        raise RuntimeError("boom")

    failing = ExpressionBody(
        callable=fails,
        body_ast_hash="sha256:failure",
        parameter_count=1,
        bindings=(),
    )
    with pytest.raises(SemanticRuntimeError):
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(),
            owning_ref=REVENUE,
            body=failing,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    with pytest.raises(SemanticRuntimeError) as failure_cleanup:
        bind(AMOUNT, table)
    assert failure_cleanup.value.kind == ErrorKind.BINDING_CONTEXT_MISSING


def test_same_alias_at_multiple_positions_is_rejected_by_identity() -> None:
    body = compile_expression_body(
        _two_entities,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS, USERS),
    )
    table = ibis.table({"amount": "float64"}, name="orders")
    with pytest.raises(SemanticRuntimeError) as exc_info:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(),
            owning_ref=REVENUE,
            body=body,
            entity_refs=(ORDERS, USERS),
            aliases=(table, table),
        )
    assert exc_info.value.kind == ErrorKind.BINDING_ALIAS_AMBIGUOUS


def test_wrong_entity_binding_fails_with_structured_repair() -> None:
    body = compile_expression_body(
        _second_entity,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS, USERS),
    )
    orders = ibis.table({"amount": "float64"}, name="orders")
    users = ibis.table({"amount": "float64"}, name="users")
    with pytest.raises(SemanticRuntimeError) as exc_info:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(),
            owning_ref=REVENUE,
            body=body,
            entity_refs=(ORDERS, USERS),
            aliases=(orders, users),
        )
    assert exc_info.value.kind == ErrorKind.BINDING_ENTITY_MISMATCH
    assert "ms.bind(field_ref, entity_alias)" in str(exc_info.value)


def test_undeclared_and_missing_binding_targets_fail_structurally() -> None:
    table = ibis.table({"amount": "float64"}, name="orders")
    declared = compile_expression_body(
        _revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    undeclared = ExpressionBody(
        callable=_revenue,
        body_ast_hash=declared.body_ast_hash,
        parameter_count=1,
        bindings=(),
    )
    with pytest.raises(SemanticRuntimeError) as undeclared_error:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(),
            owning_ref=REVENUE,
            body=undeclared,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    assert undeclared_error.value.kind == ErrorKind.BINDING_NOT_DECLARED

    missing_catalog_ref = CompiledExpressionSidecar(
        bodies={AMOUNT: ExpressionBody.for_column("amount")},
        field_owners={AMOUNT: ORDERS},
        catalog_refs=frozenset({ORDERS, REVENUE}),
    )
    with pytest.raises(SemanticRuntimeError) as missing_ref_error:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=missing_catalog_ref,
            owning_ref=REVENUE,
            body=declared,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    assert missing_ref_error.value.kind == ErrorKind.BINDING_TARGET_MISSING

    missing_body = CompiledExpressionSidecar(
        bodies={},
        field_owners={AMOUNT: ORDERS},
        catalog_refs=frozenset({ORDERS, AMOUNT, REVENUE}),
    )
    with pytest.raises(SemanticRuntimeError) as missing_body_error:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=missing_body,
            owning_ref=REVENUE,
            body=declared,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    assert missing_body_error.value.kind == ErrorKind.BINDING_TARGET_MISSING


def test_cycle_and_invalid_nested_result_reset_every_frame() -> None:
    table = ibis.table({"amount": "float64"}, name="orders")
    metric_body = compile_expression_body(
        _revenue,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    recursive = compile_expression_body(
        _recursive_amount,
        owning_ref=AMOUNT,
        ordered_entity_refs=(ORDERS,),
    )
    with pytest.raises(SemanticRuntimeError) as cycle_error:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(amount_body=recursive),
            owning_ref=REVENUE,
            body=metric_body,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    assert cycle_error.value.kind == ErrorKind.BINDING_CYCLE
    with pytest.raises(SemanticRuntimeError) as cycle_cleanup:
        bind(AMOUNT, table)
    assert cycle_cleanup.value.kind == ErrorKind.BINDING_CONTEXT_MISSING

    invalid = ExpressionBody(
        callable=lambda _orders: 1,
        body_ast_hash="sha256:invalid-result",
        parameter_count=1,
        bindings=(),
    )
    with pytest.raises(SemanticRuntimeError) as result_error:
        evaluate_expression_body(
            catalog_definition_fingerprint="sha256:test",
            expression_sidecar=_sidecar(amount_body=invalid),
            owning_ref=REVENUE,
            body=metric_body,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
    assert result_error.value.kind == ErrorKind.BINDING_RESULT_INVALID
    with pytest.raises(SemanticRuntimeError) as result_cleanup:
        bind(AMOUNT, table)
    assert result_cleanup.value.kind == ErrorKind.BINDING_CONTEXT_MISSING


def test_runtime_rejects_non_field_and_non_exact_receivers_structurally() -> None:
    table = ibis.table({"amount": "float64"}, name="orders")
    with pytest.raises(SemanticRuntimeError) as metric_error:
        bind(REVENUE, table)  # type: ignore[arg-type]
    assert metric_error.value.kind == ErrorKind.INVALID_BINDING_REF
    with pytest.raises(SemanticRuntimeError) as exact_error:
        bind(object(), table)  # type: ignore[arg-type]
    assert exact_error.value.kind == ErrorKind.INVALID_BINDING_REF


def test_equal_refs_resolve_independently_in_concurrent_catalog_contexts() -> None:
    metric_body = compile_expression_body(
        _identity_metric,
        owning_ref=REVENUE,
        ordered_entity_refs=(ORDERS,),
    )
    table = ibis.table(
        {"amount_a": "float64", "amount_b": "float64"},
        name="orders",
    )

    def evaluate(column: str) -> str:
        result = evaluate_expression_body(
            catalog_definition_fingerprint=f"sha256:{column}",
            expression_sidecar=_sidecar(amount_body=ExpressionBody.for_column(column)),
            owning_ref=REVENUE,
            body=metric_body,
            entity_refs=(ORDERS,),
            aliases=(table,),
        )
        return result.get_name()

    with ThreadPoolExecutor(max_workers=2) as pool:
        names = set(pool.map(evaluate, ("amount_a", "amount_b")))
    assert names == {"amount_a", "amount_b"}


def test_sidecar_copies_input_mappings_and_is_immutable() -> None:
    bodies: dict[Ref[SemanticKindTag], ExpressionBody] = {
        AMOUNT: ExpressionBody.for_column("amount")
    }
    sidecar = CompiledExpressionSidecar(
        bodies=bodies,
        field_owners={AMOUNT: ORDERS},
        catalog_refs=frozenset({ORDERS, AMOUNT}),
    )
    bodies.clear()
    assert AMOUNT in sidecar.bodies
    with pytest.raises(TypeError):
        sidecar.bodies[AMOUNT] = ExpressionBody.for_column("other")  # type: ignore[index]
