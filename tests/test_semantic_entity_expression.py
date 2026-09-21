"""Entity expression declaration tests: authoring forms, body shape, identity.

Covers the decorator form of ``ms.entity`` (omitted ``name``), the shared
Ref family with direct declarations, body-shape validation for a
Table-valued single-expression body, and normalized body identity.

Materialization of the compiled body is out of scope here (Phase 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import ExpressionBody
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, SemanticLoadError
from marivo.semantic.loader import _LOADER_CTX, LoaderContext


def _enter_ctx(**kwargs: object) -> LoaderContext:
    ctx = LoaderContext(**kwargs)  # type: ignore[arg-type]
    _LOADER_CTX.set(ctx)
    return ctx


def _exit_ctx() -> None:
    _LOADER_CTX.set(None)


@pytest.fixture(autouse=True)
def _clean_ctx():
    _exit_ctx()
    yield
    _exit_ctx()


def _pending_entity_bodies(ctx: LoaderContext) -> dict[str, ExpressionBody]:
    return {
        pending.definition.name: pending.expression_body
        for pending in ctx.pending_definitions
        if pending.expression_body is not None
        and type(pending.ref) is ms.Ref
        and pending.ref.kind is ms.SemanticKind.ENTITY
    }


def _compile_entity_body(fn: object, ref: object = None) -> ExpressionBody:
    """Compile one Entity body through the shared expression compiler."""
    from marivo.semantic._expression_binding import compile_expression_body

    entity_ref = ref if ref is not None else ref_factory.entity("sales.t")
    return compile_expression_body(  # type: ignore[arg-type]
        fn,
        owning_ref=entity_ref,
        body_kind="entity_table",
    )


def _direct_entity(name: str = "orders") -> object:
    return ms.entity(
        name=name,
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
    )


def daily_orders(raw: object) -> object:
    """Identity body used for decorator-form registration tests."""
    return raw


# ---------------------------------------------------------------------------
# Decorator form and dispatch
# ---------------------------------------------------------------------------


def test_entity_without_name_returns_decorator_that_registers_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.entity(
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )(daily_orders)
        assert type(ref) is ms.Ref and ref.kind is ms.SemanticKind.ENTITY
        assert ref.path == "sales.daily_orders"
        ir, body = [
            (pending.definition, pending.expression_body)
            for pending in ctx.pending_definitions
            if getattr(pending.definition, "name", None) == "daily_orders"
        ][-1]
        assert body is not None
        assert body.parameter_count == 1
    finally:
        _exit_ctx()


def test_entity_decorator_derives_name_from_function() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )(daily_orders)
        ir = next(
            pending.definition
            for pending in ctx.pending_definitions
            if getattr(pending.definition, "name", None) == "daily_orders"
        )
        assert ir.name == "daily_orders"
        assert ir.python_symbol == "daily_orders"
        assert ir.semantic_id == "sales.daily_orders"
    finally:
        _exit_ctx()


def test_direct_entity_still_returns_ref_immediately_and_has_no_body() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = _direct_entity()
        assert type(ref) is ms.Ref and ref.kind is ms.SemanticKind.ENTITY
        pending = ctx.pending_definitions[-1]
        assert pending.expression_body is None
        assert pending.definition.name == "orders"
        assert pending.definition.python_symbol == "orders"
    finally:
        _exit_ctx()


def test_entity_duplicate_name_via_decorator_collides_with_direct() -> None:
    _enter_ctx(default_domain="sales")
    try:
        decorator = ms.entity(
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )
        _direct_entity(name="daily_orders")
        with pytest.raises(SemanticDecoratorError) as exc_info:
            decorator(daily_orders)
        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Body shape validation
# ---------------------------------------------------------------------------


def test_entity_body_accepts_docstring_and_single_return() -> None:
    def body(raw: object) -> object:
        """One latest order per day."""
        return raw.filter(raw.is_deleted == False)  # noqa: E712

    compiled = _compile_entity_body(body)
    assert compiled.parameter_count == 1
    assert compiled.body_ast_hash.startswith("sha256:")


def test_entity_body_rejects_two_positional_parameters() -> None:
    def body(raw: object, other: object) -> object:
        return raw

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


def test_entity_body_rejects_defaulted_parameter() -> None:
    def body(raw: object = None) -> object:
        return raw

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


def test_entity_body_rejects_assignment() -> None:
    def body(raw: object) -> object:
        filtered = raw.filter(raw.is_deleted == False)  # noqa: E712
        return filtered

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_entity_body_rejects_nested_function() -> None:
    def body(raw: object) -> object:
        def helper(t: object) -> object:
            return t

        return helper(raw)

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind in (
        ErrorKind.INVALID_COMPONENT_BODY,
        ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
    )


def test_entity_body_rejects_execution_attribute() -> None:
    def body(raw: object) -> object:
        return raw.filter(raw.amount > 0).execute()  # type: ignore[attr-defined]

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH


def test_entity_body_rejects_ms_bind() -> None:
    from marivo.semantic import _expression_binding

    def body(raw: object) -> object:
        return _expression_binding.bind(ref_factory.dimension("sales.orders.region"), raw)

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_entity_body_rejects_clock_and_random_symbols() -> None:
    def body_now(raw: object) -> object:
        return raw.mutate(t=ibis.now())

    def body_random(raw: object) -> object:
        return raw.mutate(r=ibis.random())

    for body in (body_now, body_random):
        with pytest.raises(SemanticLoadError) as exc_info:
            _compile_entity_body(body)
        assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


def test_entity_body_rejects_clock_and_random_import_aliases() -> None:
    from ibis import now as sort_clock
    from ibis import random as pick_random

    def body_alias_clock(raw: object) -> object:
        return raw.mutate(t=sort_clock())

    def body_alias_random(raw: object) -> object:
        return raw.mutate(r=pick_random())

    for body in (body_alias_clock, body_alias_random):
        with pytest.raises(SemanticLoadError) as exc_info:
            _compile_entity_body(body)
        assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


def test_entity_body_rejects_named_expression() -> None:
    def body(raw: object) -> object:
        return (raw := raw.filter(raw.amount > 0))

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_entity_body_rejects_comprehensions() -> None:
    def body_listcomp(raw: object) -> object:
        return [raw for _ in [1]][0]  # type: ignore[index]  # noqa: RUF015

    def body_genexp(raw: object) -> object:
        return next(t for t in [raw])

    for body in (body_listcomp, body_genexp):
        with pytest.raises(SemanticLoadError) as exc_info:
            _compile_entity_body(body)
        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_entity_body_rejects_captured_table() -> None:
    captured = ibis.memtable({"a": [1, 2, 3]})
    _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )
        with pytest.raises((SemanticLoadError, SemanticDecoratorError)):
            ms.entity(
                datasource=ms.ref.datasource("warehouse"),
                source=md.table("orders"),
            )(captured_branch)
    finally:
        _exit_ctx()


def captured_branch(raw: object) -> object:
    return captured.join(raw)  # type: ignore[name-defined] # noqa: F821


def test_entity_body_rejects_scalar_return_ast_classified_at_runtime() -> None:
    def body(raw: object) -> object:
        return raw.amount.sum()

    compiled = _compile_entity_body(body)
    assert compiled.body_ast_hash.startswith("sha256:")


def test_entity_body_binds_injected_parameter_by_position() -> None:
    def body(orders_table: object) -> object:
        return orders_table.filter(orders_table["amount"] > 0)

    compiled = _compile_entity_body(body)
    assert compiled.source_columns == ("amount",)


def test_entity_body_rejects_variadic_parameter() -> None:
    def body(*raw: object) -> object:
        return raw[0]  # type: ignore[index]

    with pytest.raises(SemanticLoadError) as exc_info:
        _compile_entity_body(body)
    assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


# ---------------------------------------------------------------------------
# Identity: body hash normalization
# ---------------------------------------------------------------------------


def test_entity_body_hash_ignores_parameter_name_and_docstring() -> None:
    def body_a(raw: object) -> object:
        """Docstring one."""
        return raw.filter(raw.amount > 0)

    def body_b(table: object) -> object:
        """Docstring two."""
        return table.filter(table.amount > 0)

    compiled_a = _compile_entity_body(body_a)
    compiled_b = _compile_entity_body(body_b)
    assert compiled_a.body_ast_hash == compiled_b.body_ast_hash


def test_entity_body_hash_changes_with_expression_change() -> None:
    def body_a(raw: object) -> object:
        return raw.filter(raw.amount > 0)

    def body_b(raw: object) -> object:
        return raw.filter(raw.amount > 1)

    assert _compile_entity_body(body_a).body_ast_hash != _compile_entity_body(body_b).body_ast_hash


def test_entity_body_hash_differs_from_field_expression_hash() -> None:
    def entity_body(raw: object) -> object:
        return raw

    def field_body(orders: object) -> object:
        return orders.amount

    entity_body_hash = _compile_entity_body(entity_body).body_ast_hash
    field_body_hash = _compile_entity_body(field_body)
    assert entity_body_hash != field_body_hash.body_ast_hash


# ---------------------------------------------------------------------------
# Ref family parity
# ---------------------------------------------------------------------------


def test_decorator_entity_ref_joins_same_ref_family() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        decorator_ref = ms.entity(
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )(daily_orders)
        direct_ref = ref_factory.entity("sales.daily_orders")
        assert type(decorator_ref) is type(direct_ref)
        assert decorator_ref.kind is direct_ref.kind
        assert decorator_ref.path == direct_ref.path
        assert decorator_ref.key == direct_ref.key
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Definition identity: body digests in dependency fingerprints
# ---------------------------------------------------------------------------


_IDENTITY_DIRECT = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("wh")
orders = ms.entity(name="orders", datasource=wh, source=md.table("orders"))
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity="additive", unit="CNY"
)
"""


def _identity_body_variant(body_expr: str) -> str:
    return (
        _IDENTITY_DIRECT
        + f"""

@ms.entity(datasource=wh, source=md.table("orders"))
def daily_orders(raw):
    return raw.{body_expr}
"""
    )


def test_expression_entity_body_change_invalidates_fingerprint() -> None:
    from tests.shared_fixtures import load_inline_semantic

    def fingerprint_for(body_expr: str) -> str:
        with load_inline_semantic(_identity_body_variant(body_expr)) as result:
            assert result.compiled_state is not None
            return result.compiled_state.definition_fingerprint

    assert fingerprint_for("filter(raw.amount > 0)") != fingerprint_for("filter(raw.amount > 1)")


def test_expression_entity_body_includes_transitive_metric_dependency() -> None:
    """A body-only edit invalidates a dependent metric's scoped digest."""
    from marivo.semantic._definition_identity import scoped_definition_fingerprint
    from tests.shared_fixtures import load_inline_semantic

    source = (
        _identity_body_variant("filter(raw.amount > 0)")
        + """
daily_amount = ms.measure_column(
    name="daily_amount",
    entity=daily_orders,
    column="amount",
    additivity="additive",
    unit="CNY",
)
revenue = ms.aggregate(name="revenue", measure=daily_amount, agg="sum")
"""
    )
    with load_inline_semantic(source) as result:
        assert result.compiled_state is not None
        state = result.compiled_state
        revenue_digest = scoped_definition_fingerprint(
            root=ref_factory.metric("test.revenue"),
            definitions=state.definitions,
            dependencies=state.dependencies,
            sidecar=state.sidecar,
        )
    assert revenue_digest.startswith("sha256:")


def test_direct_entity_fingerprint_excludes_absent_body() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_IDENTITY_DIRECT) as result:
        assert result.compiled_state is not None
        # The direct form has no body sidecar entry for the entity ref.
        entity_ref = ref_factory.entity("test.orders")
        assert entity_ref not in result.compiled_state.sidecar.bodies


def test_ibis_import_alias_identity_changes_entity_fingerprint() -> None:
    """Swapping the resolved Ibis symbol behind one alias invalidates identity.

    The review case: ``from ibis import asc as sort`` versus
    ``from ibis import desc as sort`` pick different rows for the same AST
    spelling, so the normalized body must encode the resolved symbol identity.
    """
    from tests.shared_fixtures import load_inline_semantic

    def source_for(import_line: str) -> str:
        return (
            "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            f"{import_line}\n"
            "\n"
            "wh = ms.ref.datasource('wh')\n"
            "\n"
            "@ms.entity(datasource=wh, source=md.table('orders'))\n"
            "def latest(raw):\n"
            "    return raw.order_by(sort(raw.amount)).limit(1)\n"
        )

    def fingerprint_for(import_line: str) -> str:
        with load_inline_semantic(source_for(import_line)) as result:
            assert result.compiled_state is not None
            return result.compiled_state.definition_fingerprint

    assert fingerprint_for("from ibis import asc as sort") != fingerprint_for(
        "from ibis import desc as sort"
    )


def test_entity_body_hash_resolves_alias_identity_within_compile() -> None:
    """Two aliases for the same Ibis symbol produce one body hash."""
    from ibis import asc as sort_first
    from ibis import asc as sort_second

    def body_first(raw: object) -> object:
        return raw.order_by(sort_first(raw.amount)).limit(1)

    def body_second(raw: object) -> object:
        return raw.order_by(sort_second(raw.amount)).limit(1)

    assert (
        _compile_entity_body(body_first).body_ast_hash
        == _compile_entity_body(body_second).body_ast_hash
    )


def test_entity_card_discloses_expression_form_with_normalized_body(
    semantic_project_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An expression Entity card shows the form and normalized body text.

    Reading the card never executes the body; details render from captured
    display syntax only.
    """
    from marivo.semantic.catalog import SemanticCatalog

    model = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "wh = ms.ref.datasource('wh')\n"
        "\n"
        "@ms.entity(datasource=wh, source=md.table('orders'))\n"
        "def daily_totals(raw):\n"
        "    '''One row per day.'''\n"
        "    return raw.group_by('day').aggregate(total=raw['amount'].sum())\n"
    )

    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/model.py": model,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    entry = catalog.require(ref_factory.entity("sales.daily_totals"))
    details = entry.details()
    assert details.definition_form == "expression"
    assert details.expression_display is not None
    text = details.expression_display.text
    assert "t1.group_by" in text
    assert "'day'" not in text  # literals are redacted
    rendered = entry.details().render()
    assert "definition_form: expression" in rendered
    assert "t1.group_by" in rendered
    assert "output schema is the body's returned Table schema" in rendered


def test_direct_entity_card_keeps_direct_form_disclosure(
    semantic_project_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A direct Entity card reports the direct form and source schema authority."""
    from marivo.semantic.catalog import SemanticCatalog

    model = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "wh = ms.ref.datasource('wh')\n"
        "orders = ms.entity(name='orders', datasource=wh, source=md.table('orders'))\n"
    )

    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/model.py": model,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    entry = catalog.require(ref_factory.entity("sales.orders"))
    details = entry.details()
    assert details.definition_form == "direct"
    assert details.expression_display is None
    rendered = entry.details().render()
    assert "definition_form: direct" in rendered
    assert "output schema is the source schema" in rendered
