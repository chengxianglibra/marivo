"""Exhaust finite Population domains through public construction and execution."""

from collections.abc import Callable, Iterator, Set
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path
from typing import TypeVar

import duckdb
import pandas as pd
import pytest
import sqlglot
from ibis.common.deferred import Deferred
from sqlglot import exp

import marivo.analysis as mv
from marivo._definition.types import CoordinateValue
from marivo.analysis.session.core import Session
from marivo.semantic.catalog import Dimension, Entity, Relationship
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
items = ms.entity(name="items", datasource=warehouse, source=md.table("items"), primary_key=["tenant", "code"])
category = ms.dimension_column(name="category", entity=items, column="category")
partition = ms.dimension_column(name="partition", entity=items, column="partition")
target_id = ms.dimension_column(name="target_id", entity=items, column="target_id")
targets = ms.entity(name="targets", datasource=warehouse, source=md.table("targets"), primary_key=["id"])
target_key = ms.dimension_column(name="id", entity=targets, column="id")
target = ms.relationship(name="target", from_entity=items, to_entity=targets, keys=[ms.join_on(target_id, target_key)])
"""

_Identity = tuple[int, str]
_Row = tuple[_Identity, str | None]
_UNIVERSE: tuple[_Row, ...] = (
    ((1, "A"), None),
    ((1, "B"), None),
    ((1, "A"), "east"),
    ((2, "A"), "east"),
)
_IDENTITY = ("identity", ("tenant", "code"))
_CATEGORY = ("field", ("sets.items.category",))
_TARGET = ("identity", ("id",))
_T = TypeVar("_T")


def _subsets(values: tuple[_T, ...]) -> Iterator[tuple[_T, ...]]:
    for size in range(len(values) + 1):
        yield from combinations(values, size)


_DOMAINS = tuple(_subsets(_UNIVERSE))


@dataclass(frozen=True)
class _Case:
    session: Session
    items: Entity
    category: Dimension
    partition: Dimension
    targets: Entity
    target: Relationship

    def literal(self, rows: tuple[_Row, ...], *, reverse: bool = False) -> mv.Population:
        identity = mv.identity(self.items)
        if reverse:
            return mv.coordinates(
                by=(self.category, identity), keys=tuple((category, key) for key, category in rows)
            )
        return mv.coordinates(by=(identity, self.category), keys=rows)

    def fixed(self, rows: tuple[_Row, ...], *, reverse: bool = False) -> mv.Population:
        source = self.session.frame(self.literal(rows, reverse=reverse)).compute()
        targets = (self.category, self.items) if reverse else (self.items, self.category)
        return self.session.population_from(
            source, on=tuple(zip(source.axes, targets, strict=True))
        )


@pytest.fixture
def case(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Case:
    database = tmp_path / "sets.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            """
            CREATE TABLE items(
                tenant BIGINT, code VARCHAR, category VARCHAR, partition BIGINT, target_id BIGINT
            );
            INSERT INTO items VALUES (1, 'A', NULL, 1, 7),
                                     (1, 'B', NULL, 0, 7),
                                     (2, 'A', 'east', 2, 8);
            CREATE TABLE targets(id BIGINT);
            INSERT INTO targets VALUES (7);
            """
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
            ),
            "sets/_domain.py": "import marivo.semantic as ms\nms.domain(name='sets', owner='Test')",
            "sets/model.py": _MODEL,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("population-formal", timezone="UTC")
    catalog = session.catalog
    return _Case(
        session,
        catalog_value(catalog, "sets.items", kind="entity"),
        catalog_value(catalog, "sets.items.category", kind="dimension"),
        catalog_value(catalog, "sets.items.partition", kind="dimension"),
        catalog_value(catalog, "sets.targets", kind="entity"),
        catalog_value(catalog, "sets.target", kind="relationship"),
    )


def _check(
    case: _Case,
    domain: mv.Population | Deferred,
    expected: Set[tuple[CoordinateValue, ...]],
    axes: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    context: tuple[object, ...],
) -> None:
    assert isinstance(domain, mv.Population), context
    frame = case.session.frame(domain).compute()
    actual_axes = tuple((axis.role, axis.key_names) for axis in frame.axes)
    table = frame.to_pandas()
    raw_rows = table.itertuples(index=False, name=None)
    actual = (
        [tuple(None if value is pd.NA else value for value in row) for row in raw_rows]
        if axes
        else [()] * len(table.index)
    )
    message = (
        f"{context!r}\nExpected {expected!r} on {axes!r}\nActual {actual!r} on {actual_axes!r}"
    )
    assert actual_axes == axes, message
    assert table.shape[1] == len(axes), message
    assert len(actual) == len(set(actual)) and set(actual) == expected, message


def _set_cases(
    left: mv.Population, right: mv.Population, lhs: tuple[_Row, ...], rhs: tuple[_Row, ...]
) -> Iterator[tuple[str, mv.Population | Deferred, set[tuple[CoordinateValue, ...]]]]:
    a: set[tuple[CoordinateValue, ...]] = set(lhs)
    b: set[tuple[CoordinateValue, ...]] = set(rhs)
    yield "union", left.union(right), a | b
    yield "intersection", left.intersection(right), a & b
    yield "difference", left.difference(right), a - b


@pytest.mark.parametrize("lhs", _DOMAINS)
def test_all_literal_set_pairs(case: _Case, lhs: tuple[_Row, ...]) -> None:
    left = case.literal(lhs)
    for rhs in _DOMAINS:
        right = case.literal(rhs + rhs[:1], reverse=True)
        for operation, domain, expected in _set_cases(left, right, lhs, rhs):
            _check(
                case,
                domain,
                expected,
                (_IDENTITY, _CATEGORY),
                context=("literal", operation, lhs, rhs),
            )


def test_all_literal_projections_and_zero_axis_sets(case: _Case) -> None:
    axes = (_IDENTITY, _CATEGORY)
    for rows in _DOMAINS:
        domain = case.literal(rows)
        for positions in ((), (0,), (1,), (0, 1), (1, 0)):
            _check(
                case,
                domain.project(by=tuple(domain.axes[i] for i in positions)),
                {tuple(row[i] for i in positions) for row in rows},
                tuple(axes[i] for i in positions),
                context=("literal", "project", rows, positions),
            )

    for lkeys, rkeys in product(((), ((),)), repeat=2):
        left = mv.coordinates(by=(), keys=lkeys)
        right = mv.coordinates(by=(), keys=rkeys)
        for name, result, expected in (
            ("union", left.union(right), set(lkeys) | set(rkeys)),
            ("intersection", left.intersection(right), set(lkeys) & set(rkeys)),
            ("difference", left.difference(right), set(lkeys) - set(rkeys)),
        ):
            _check(case, result, expected, (), context=("zero axes", name, lkeys, rkeys))


def test_all_literal_products(case: _Case) -> None:
    identities = tuple(dict.fromkeys(key for key, _ in _UNIVERSE))
    labels = (None, "east")
    for keys, values in product(_subsets(identities), _subsets(labels)):
        members = mv.coordinates(by=(mv.identity(case.items),), keys=tuple((key,) for key in keys))
        categories = mv.coordinates(by=(case.category,), keys=tuple((value,) for value in values))
        _check(
            case,
            mv.product(members, categories),
            set(product(keys, values)),
            (_IDENTITY, _CATEGORY),
            context=("literal", "product", keys, values),
        )


def test_fixed_frame_set_operations(case: _Case, source_trace: SourceTrace) -> None:
    for lhs, rhs in (
        (_UNIVERSE[:3], (_UNIVERSE[0], _UNIVERSE[3])),
        ((), _UNIVERSE),
        (_UNIVERSE, ()),
    ):
        left = case.fixed(lhs)
        right = case.fixed(rhs, reverse=True)
        for operation, domain, expected in _set_cases(left, right, lhs, rhs):
            before = len(source_trace.native_sql)
            _check(
                case,
                domain,
                expected,
                (_IDENTITY, _CATEGORY),
                context=("fixed Frame", operation, lhs, rhs),
            )
            assert len(source_trace.native_sql) == before, ("fixed Frame", operation, lhs, rhs)


def _submitted_set_operation(sqls: list[str], operation: str) -> bool:
    """Recognize native and fused representations without matching SQL text."""
    node_type = {"union": exp.Union, "intersection": exp.Intersect, "difference": exp.Except}
    join_kind = {"intersection": "SEMI", "difference": "ANTI"}.get(operation)
    for sql in sqls:
        query = sqlglot.parse_one(sql, read="duckdb")
        if query.find(node_type[operation]) is not None:
            return True
        if join_kind and any(join.kind == join_kind for join in query.find_all(exp.Join)):
            return True
    return False


@pytest.mark.parametrize("right_is_source", (False, True), ids=("mixed", "same-source"))
def test_duckdb_set_operations(
    case: _Case, source_trace: SourceTrace, right_is_source: bool
) -> None:
    lhs = _UNIVERSE[:2]
    rhs = (_UNIVERSE[0], _UNIVERSE[3])
    left = case.session.members(case.items, where=case.partition <= 1).classify(by=(case.category,))
    right = (
        case.session.members(case.items, where=case.partition >= 1).classify(by=(case.category,))
        if right_is_source
        else case.literal(rhs, reverse=True)
    )
    for operation, domain, expected in _set_cases(left, right, lhs, rhs):
        context = ("DuckDB", "same-source" if right_is_source else "mixed", operation, lhs, rhs)
        before = len(source_trace.native_sql)
        _check(case, domain, expected, (_IDENTITY, _CATEGORY), context=context)
        submitted = source_trace.native_sql[before:]
        assert _submitted_set_operation(submitted, operation), (context, submitted)


def test_relationship_image_and_preimage(case: _Case) -> None:
    # The independent relation includes the dangling target (8) and no edge
    # from the requested identity (2, "B"), which has no source record.
    edges = {((1, "A"), 7), ((1, "B"), 7), ((2, "A"), 8)}
    absent = ((2, "B"), None)
    via = mv.path(case.target)
    for rows in _subsets((*_UNIVERSE, absent)):
        source = case.literal(rows)
        reached = {(target,) for origin, target in edges if any(key == origin for key, _ in rows)}
        _check(
            case,
            source.image(via=via),
            reached,
            (_TARGET,),
            context=("DuckDB", "image", rows),
        )
        for selected in _subsets((7, 8)):
            targets = mv.coordinates(
                by=(mv.identity(case.targets),), keys=tuple(((key,),) for key in selected)
            )
            _check(
                case,
                source.preimage(targets, via=via),
                {row for row in rows if any((row[0], key) in edges for key in selected)},
                (_IDENTITY, _CATEGORY),
                context=("DuckDB", "preimage", rows, selected),
            )
