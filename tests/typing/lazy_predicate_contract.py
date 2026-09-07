"""Exact first-cutover predicate helper and compiler typing."""

import ibis.expr.types as ir
from typing_extensions import assert_type

from marivo.analysis.compiler.predicates import lower_bound_predicate
from marivo.analysis.observation.predicates import (
    AnalysisPredicate,
    BoundPredicate,
    PredicateField,
    all_of,
    any_of,
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


def predicates(field: PredicateField, table: ir.Table, bound: BoundPredicate) -> None:
    assert_type(eq(field, 1), AnalysisPredicate)
    assert_type(not_eq(field, 1), AnalysisPredicate)
    assert_type(lt(field, 1), AnalysisPredicate)
    assert_type(lte(field, 1), AnalysisPredicate)
    assert_type(gt(field, 1), AnalysisPredicate)
    assert_type(gte(field, 1), AnalysisPredicate)
    assert_type(is_in(field, [1, 2]), AnalysisPredicate)
    assert_type(is_null(field), AnalysisPredicate)
    assert_type(is_not_null(field), AnalysisPredicate)
    assert_type(all_of(eq(field, 1), is_not_null(field)), AnalysisPredicate)
    assert_type(any_of(eq(field, 1), is_null(field)), AnalysisPredicate)
    assert_type(not_(eq(field, 1)), AnalysisPredicate)
    assert_type(lower_bound_predicate(table, bound), ir.BooleanValue)
