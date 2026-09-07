"""Independent canonical typing and real source SQL three-valued predicate acceptance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.compiler.predicates import lower_bound_predicate, predicate_leaves
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.observation.errors import ObservationPredicateError
from marivo.analysis.observation.predicates import (
    AnalysisPredicate,
    BoundPredicate,
    all_of,
    any_of,
    bind_predicates,
    eq,
    gt,
    gte,
    is_in,
    is_not_null,
    is_null,
    lt,
    lte,
    not_,
    not_eq,
)
from marivo.refs import ref

VALUE = ref.metric("sales.value")


def _field(
    logical: str = "float64", *, nullable: bool = True, role: str = "metric"
) -> d.DatasetField:
    field_id = d._make_field_id("generated.rank@v1" if role == "rank" else "test.value")
    return d.DatasetField(
        _token=d._CORE_TOKEN,
        field_id=field_id,
        name="value",
        role_id=role,
        identity=d._generated_identity(field_id),
        derivation_identity="test.literal_binding",
        logical_type_id=logical,
        physical_type_state=d._ResolvedPhysicalType(_token=d._CORE_TOKEN, physical_type_id=logical),
        nullable=nullable,
    )


def _bind(*predicates: AnalysisPredicate, field: d.DatasetField | None = None) -> BoundPredicate:
    resolved = _field() if field is None else field
    return bind_predicates(predicates, lambda _operand: resolved)


def test_all_twelve_predicates_obey_sql_three_valued_logic() -> None:
    cases = (
        (eq(VALUE, 1), [False, False, True, False, None], [2]),
        (not_eq(VALUE, 1), [True, True, False, True, None], [0, 1, 3]),
        (lt(VALUE, 1), [True, True, False, False, None], [0, 1]),
        (lte(VALUE, 1), [True, True, True, False, None], [0, 1, 2]),
        (gt(VALUE, 1), [False, False, False, True, None], [3]),
        (gte(VALUE, 1), [False, False, True, True, None], [2, 3]),
        (is_in(VALUE, [0, 2]), [False, True, False, True, None], [1, 3]),
        (is_null(VALUE), [False, False, False, False, True], [4]),
        (is_not_null(VALUE), [True, True, True, True, False], [0, 1, 2, 3]),
        (all_of(gte(VALUE, 0), lte(VALUE, 1)), [False, True, True, False, None], [1, 2]),
        (any_of(eq(VALUE, 1), is_null(VALUE)), [False, False, True, False, True], [2, 4]),
        (not_(eq(VALUE, 1)), [True, True, False, True, None], [0, 1, 3]),
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "predicate_values",
            pa.table({"row_id": [0, 1, 2, 3, 4], "value": [-1.0, 0.0, 1.0, 2.0, None]}),
        )
        for predicate, booleans, selected in cases:
            lowered = lower_bound_predicate(table, _bind(predicate))
            assert table.select(result=lowered).to_pyarrow()["result"].to_pylist() == booleans
            assert (
                table.filter(lowered).order_by("row_id").to_pyarrow()["row_id"].to_pylist()
                == selected
            )
    finally:
        backend.disconnect()


def test_boolean_normalization_preserves_scopes_and_authored_paths() -> None:
    first, second, third = eq(VALUE, 1), eq(VALUE, 2), is_null(VALUE)
    unbound = any_of(first, any_of(second, first))
    assert len(unbound.children) == 3
    tree = _bind(unbound)
    equivalent = _bind(any_of(second, first))
    assert tree.identity_payload() == equivalent.identity_payload()
    leaves = tuple(predicate_leaves(tree))
    one = next(item for item in leaves if item.literal == ("floating", 1.0))
    assert one.occurrence_paths == ((0, 0), (0, 1, 1))
    scoped = _bind(all_of(any_of(first, second), not_(third)))
    assert scoped.kind == "all_of"
    assert {child.kind for child in scoped.children} == {"any_of", "not_"}
    assert len(tuple(predicate_leaves(scoped))) == 3
    # No distribution, De Morgan rewrite, or null-check negation rewrite.
    assert _bind(not_(is_null(VALUE))).kind == "not_"
    assert _bind(not_(not_(first))).children[0].kind == "not_"


def test_is_in_copies_and_normalizes_the_authored_list() -> None:
    values = [1, 2, 1]
    predicate = is_in(VALUE, values)
    values.append(3)
    first = _bind(predicate)
    assert first.identity_payload() == _bind(is_in(VALUE, (2.0, 1.0))).identity_payload()
    assert len(first.literal) == 2
    with pytest.raises(ObservationPredicateError):
        _bind(is_in(VALUE, [True, 1]))


@pytest.mark.parametrize("values", [[], (), {1, 2}, iter([1]), "1", [None], [float("nan")]])
def test_is_in_rejects_invalid_containers_and_members(values: object) -> None:
    with pytest.raises(ObservationPredicateError):
        is_in(VALUE, values)


@pytest.mark.parametrize("helper", [eq, not_eq, lt, lte, gt, gte])
def test_null_comparisons_have_a_null_helper_repair(
    helper: Callable[..., AnalysisPredicate],
) -> None:
    with pytest.raises(ObservationPredicateError) as caught:
        helper(VALUE, None)
    assert "is_null" in caught.value.hint or "is_not_null" in caught.value.hint


@pytest.mark.parametrize("helper", [is_null, is_not_null])
def test_null_checks_require_nullable_fields(helper: Callable[..., AnalysisPredicate]) -> None:
    with pytest.raises(ObservationPredicateError, match="non-nullable"):
        _bind(helper(VALUE), field=_field(nullable=False))
    assert _bind(helper(VALUE), field=_field("int64", role="rank")).kind == helper.__name__


@pytest.mark.parametrize(
    "logical,value", [("float32", 2**24 + 1), ("float32", 0.1), ("float64", 2**53 + 1)]
)
def test_floating_literals_must_be_exact_for_the_governed_width(
    logical: str, value: int | float
) -> None:
    with pytest.raises(ObservationPredicateError, match="lossy"):
        _bind(eq(VALUE, value), field=_field(logical))


def test_float32_exact_literals_zero_and_large_integer_roundtrip() -> None:
    field = _field("float32")
    assert _bind(eq(VALUE, 2**24), field=field).literal == ("floating", float(2**24))
    assert _bind(eq(VALUE, 0.5), field=field).literal == ("floating", 0.5)
    assert (
        _bind(eq(VALUE, -0.0), field=field).identity_payload()
        == _bind(eq(VALUE, 0), field=field).identity_payload()
    )
    assert _bind(eq(VALUE, 2**200), field=_field("int64")).literal == ("integer", 2**200)
    assert _bind(eq(VALUE, 2), field=_field("int64", role="rank")).kind == "eq"


def test_decimal_normalization_never_uses_ambient_rounding_context() -> None:
    value = Decimal("123456789012345678901234567890.123456789000")
    with localcontext() as context:
        context.prec = 4
        bound = _bind(eq(VALUE, value), field=_field("decimal(38, 9)"))
    assert bound.literal == ("decimal", "123456789012345678901234567890.123456789")
    assert _bind(is_in(VALUE, [Decimal("1.00"), 1]), field=_field("decimal")).literal == (
        ("decimal", "1"),
    )
    with pytest.raises(ObservationPredicateError):
        _bind(eq(VALUE, 1.0), field=_field("decimal"))


def test_temporal_literals_bind_exact_authority_or_fail_closed() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    same = now.astimezone(timezone(timedelta(hours=8)))
    assert (
        _bind(eq(VALUE, now), field=_field("instant")).identity_payload()
        == _bind(eq(VALUE, same), field=_field("instant")).identity_payload()
    )
    assert _bind(eq(VALUE, same), field=_field("timestamp('UTC')")).literal == (
        "instant",
        now.isoformat(),
    )
    for logical in ("datetime", "timestamp", "localizable_datetime"):
        with pytest.raises(ObservationPredicateError, match=r"read.timezone"):
            _bind(eq(VALUE, now), field=_field(logical))
    with pytest.raises(ObservationPredicateError, match="naive"):
        eq(VALUE, datetime(2026, 9, 1))
    assert _bind(lte(VALUE, date(2026, 9, 1)), field=_field("date")).literal == (
        "date",
        "2026-09-01",
    )
    with pytest.raises(ObservationPredicateError):
        _bind(eq(VALUE, now), field=_field("date"))


@pytest.mark.parametrize("helper", [lt, lte, gt, gte])
def test_string_and_boolean_have_no_ungoverned_value_order(
    helper: Callable[..., AnalysisPredicate],
) -> None:
    with pytest.raises(ObservationPredicateError, match="collation"):
        _bind(helper(VALUE, "private-string"), field=_field("string"))
    with pytest.raises(ObservationPredicateError):
        _bind(helper(VALUE, True), field=_field("bool"))
    assert _bind(not_eq(VALUE, "exact Unicode 🦆"), field=_field("string")).kind == "not_eq"


@pytest.mark.parametrize(
    "predicates",
    [
        (eq(VALUE, 1), not_eq(VALUE, 1)),
        (eq(VALUE, 1), is_in(VALUE, [2, 3])),
        (is_in(VALUE, [1, 2]), is_in(VALUE, [3, 4])),
        (is_in(VALUE, [1, 2]), gt(VALUE, 2)),
        (gt(VALUE, 1), lte(VALUE, 1)),
        (gte(VALUE, 2), lt(VALUE, 1)),
        (is_null(VALUE), is_not_null(VALUE)),
        (is_null(VALUE), eq(VALUE, 1)),
        (eq(VALUE, 1), not_(eq(VALUE, 1))),
    ],
)
def test_only_proven_contradictions_fail_within_one_conjunction(
    predicates: tuple[AnalysisPredicate, ...],
) -> None:
    with pytest.raises(ObservationPredicateError, match="contradictory"):
        _bind(*predicates)
    # The same leaves under OR are valid and must not become a conjunction.
    assert _bind(any_of(*predicates)).kind == "any_of"


def test_sensitive_payloads_never_enter_errors_repr_or_identity_projection() -> None:
    canary = "private-predicate-secret-a42e"
    predicate = any_of(eq(VALUE, canary), not_eq(VALUE, "other"))
    bound = _bind(predicate, field=_field("string"))
    assert canary not in repr(predicate) + repr(bound) + repr(bound.identity_payload())
    with pytest.raises(ObservationPredicateError) as caught:
        _bind(eq(VALUE, canary), field=_field("int64"))
    assert canary not in str(caught.value)
    with pytest.raises(ObservationPredicateError):
        eq(VALUE, "bad\ud800scalar")
    # Field binding identity remains exact even though literal display is redacted.
    changed = replace(_field("string"), _token=d._CORE_TOKEN, derivation_identity="other.binding")
    assert bound.identity_payload() != _bind(predicate, field=changed).identity_payload()


def test_binding_errors_keep_the_exact_authored_occurrence_without_literals() -> None:
    invalid = any_of(eq(VALUE, 1), any_of(eq(VALUE, 2), eq(VALUE, "secret-type-error")))
    with pytest.raises(ObservationPredicateError) as caught:
        _bind(invalid)
    assert caught.value.location == "observation.where(0, 1, 1)"
    assert "secret-type-error" not in str(caught.value)
    with pytest.raises(ObservationPredicateError) as contradiction:
        _bind(gt(VALUE, 2), lte(VALUE, 1))
    assert contradiction.value.location == "observation.where((0,), (1,))"


def test_scalar_lowering_keeps_decimal_integer_unicode_and_instant_values_exact() -> None:
    instant = datetime(2026, 9, 1, 2, 3, 4, 123456, tzinfo=timezone.utc)
    decimal = Decimal("12345678901234567890.123456789")
    cases = (
        ("int64", pa.int64(), [2**53, 2**53 + 1, None], 2**53 + 1),
        ("float32", pa.float32(), [0.25, 0.5, None], 0.5),
        ("decimal(38, 9)", pa.decimal128(38, 9), [Decimal("1.0"), decimal, None], decimal),
        ("string", pa.string(), ["e\u0301", "é", None], "é"),
        ("bool", pa.bool_(), [False, True, None], True),
        ("date", pa.date32(), [date(2026, 8, 31), date(2026, 9, 1), None], date(2026, 9, 1)),
        (
            "timestamp('UTC')",
            pa.timestamp("us", tz="UTC"),
            [instant - timedelta(microseconds=1), instant, None],
            instant.astimezone(timezone(timedelta(hours=8))),
        ),
    )
    backend = ibis.duckdb.connect()
    try:
        for index, (logical, physical, values, literal) in enumerate(cases):
            table = backend.create_table(
                f"scalar_case_{index}", pa.table({"value": pa.array(values, type=physical)})
            )
            bound = _bind(eq(VALUE, literal), field=_field(logical))
            result = table.select(matched=lower_bound_predicate(table, bound)).to_pyarrow()
            assert result["matched"].to_pylist() == [False, True, None]
    finally:
        backend.disconnect()
