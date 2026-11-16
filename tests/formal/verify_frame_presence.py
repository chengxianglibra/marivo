"""Exhaust small real Frame joins against a complete-key, row-presence model."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from math import isnan
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value

_Key = tuple[tuple[str, int], str | None]
_KEYS: tuple[_Key, ...] = (
    (("a", 1), None),
    (("a", 2), None),
    (("b", 1), None),
    (("a", 1), "west"),
)
_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
items = ms.entity(name="items", datasource=warehouse, source=md.table("items"),
    primary_key=["tenant", "id"])
bucket = ms.dimension_column(name="bucket", entity=items, column="bucket")
point = ms.time_dimension_column(name="point", entity=items, column="point", parse=ms.timestamp())
amount = ms.measure_column(name="amount", entity=items, column="amount", unit="USD",
    additivity=ms.additive_all())
"""


@dataclass(frozen=True)
class _Frames:
    session: mv.Session
    other: mv.Session

    def values(
        self, rows: Mapping[_Key, int | None], *, reverse_axes: bool = False, foreign: bool = False
    ) -> mv.AnalysisFrame:
        session = self.other if foreign else self.session
        identity = mv.identity(catalog_value(session.catalog, "sales.items", kind="entity"))
        bucket = catalog_value(session.catalog, "sales.items.bucket", kind="dimension")
        raw = session.values(
            by=(bucket, identity) if reverse_axes else (identity, bucket),
            columns={"numerator": "USD", "denominator": None},
            rows=tuple(
                (
                    (key[1], key[0]) if reverse_axes else key,
                    {"numerator": amount or 0, "denominator": 0 if amount is None else 1},
                )
                for key, amount in (rows or {_KEYS[0]: 0}).items()
            ),
        )
        if not rows:
            raw = raw.where(raw["denominator"] < 0)
        return raw.select({"q": raw["numerator"] / raw["denominator"]}).compute()

    def days(self, start: str, end: str) -> mv.AnalysisFrame:
        session = self.session
        amount = catalog_value(session.catalog, "sales.items.amount", kind="measure")
        point = catalog_value(session.catalog, "sales.items.point", kind="time_dimension")
        calendar = session.time_scope(start=start, end=end).by(mv.grain("day"))
        domain = mv.product(calendar, mv.coordinates(by=(), keys=((),)))
        return session.observe({"q": amount.sum(time=point)}, by=domain).compute()


@pytest.fixture
def frames(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Frames:
    database = tmp_path / "frame-presence.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            """CREATE TABLE items(
                tenant VARCHAR, id BIGINT, bucket VARCHAR, point TIMESTAMPTZ, amount BIGINT
            );
            INSERT INTO items VALUES
                ('a', 1, NULL, '2026-02-01T08:00:00Z', 0),
                ('a', 2, NULL, '2026-02-02T08:00:00Z', 2),
                ('a', 3, 'west', '2026-03-01T08:00:00Z', 3),
                ('a', 4, 'west', '2026-03-02T08:00:00Z', 4),
                ('a', 5, 'west', '2026-03-03T08:00:00Z', 5)"""
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test')",
            "sales/model.py": _MODEL,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return _Frames(
        mv.session.get_or_create("formal-frame", timezone="UTC"),
        mv.session.get_or_create("formal-peer", timezone="UTC"),
    )


def _normalize(value: object) -> object:
    return (
        None
        if value is pd.NA or value is pd.NaT or (isinstance(value, float) and isnan(value))
        else value
    )


def _export_keys(frame: mv.AnalysisFrame) -> list[_Key]:
    table = frame.to_pandas()
    keys: list[_Key] = []
    for identity, raw in table[["items", "bucket"]].itertuples(index=False, name=None):
        bucket = _normalize(raw)
        assert bucket is None or isinstance(bucket, str), bucket
        assert isinstance(identity, tuple) and len(identity) == 2, identity
        tenant, number = identity
        assert isinstance(tenant, str) and isinstance(number, int), identity
        keys.append(((tenant, number), bucket))
    return keys


def _check(
    frame: mv.AnalysisFrame,
    expected: Iterable[tuple[object, ...]],
    *,
    presence: tuple[str, ...],
    values: tuple[str, ...],
    context: tuple[object, ...],
    key_columns: tuple[str, ...] = ("items", "bucket"),
) -> None:
    assert frame.presence_names == presence, (context, frame.presence_names, presence)
    assert frame.output_names == values, (context, frame.output_names, values)
    schema = frame.schema
    assert all(not schema.presence[name].nullable for name in presence), context
    table = frame.to_pandas()
    assert tuple(table.columns) == (*key_columns, *values, *presence), (context, table.columns)
    assert all(pd.api.types.is_bool_dtype(table[name].dtype) for name in presence), context
    actual = [
        tuple(_normalize(value) for value in row)
        for row in table.itertuples(index=False, name=None)
    ]
    wanted = list(expected)
    assert actual == wanted, (context, "expected", wanted, "actual", actual)


def _subset(
    mask: int, keys: tuple[_Key, ...], amounts: tuple[int | None, ...]
) -> dict[_Key, int | None]:
    return {
        key: amount
        for index, (key, amount) in enumerate(zip(keys, amounts, strict=True))
        if mask & (1 << index)
    }


def _compare(x: int | None, y: int | None) -> tuple[int | None, float | None]:
    if x is None or y is None:
        return None, None
    change = x - y
    return change, change / abs(y) if y != 0 else None


@pytest.mark.parametrize("mask", range(16))
def test_exhaustive_full_key_align_and_compare(frames: _Frames, mask: int) -> None:
    lhs = _subset(mask, _KEYS, (None, 0, 6, -1))
    left = frames.values(lhs)
    left_keys = _export_keys(left)
    assert set(left_keys) == set(lhs)
    for other_mask in range(16):
        rhs = _subset(other_mask, _KEYS[::-1], (-2, 0, None, 2))
        right = frames.values(rhs, reverse_axes=True)
        right_keys = _export_keys(right)
        assert set(right_keys) == set(rhs)
        order = [*left_keys, *(key for key in right_keys if key not in lhs)]
        on = ((left.axes[1], right.axes[0]), (left.axes[0], right.axes[1]))
        _check(
            left.align(right, on=on).compute(),
            ((*key, lhs.get(key), rhs.get(key), key in lhs, key in rhs) for key in order),
            values=("left.q", "right.q"),
            presence=("left_present", "right_present"),
            context=("align", lhs, rhs),
        )
        _check(
            left.compare(right).compute(),
            (
                (
                    *key,
                    lhs.get(key),
                    rhs.get(key),
                    *_compare(lhs.get(key), rhs.get(key)),
                    key in lhs,
                    key in rhs,
                )
                for key in order
            ),
            values=("q", "reference.baseline.q", "reference.absolute.q", "reference.relative.q"),
            presence=("current.present", "reference.present"),
            context=("compare", lhs, rhs),
        )


_ALIGN_FLAGS = (
    "left_present",
    "right_present",
    "left.left_present",
    "left.right_present",
    "right.left_present",
    "right.right_present",
)
_COMPARE_FLAGS = (
    "current.present",
    "prior.present",
    "current.presence.current.present",
    "current.presence.reference.present",
    "prior.presence.current.present",
    "prior.presence.reference.present",
)


@pytest.mark.parametrize("comparison", (False, True), ids=("align", "compare"))
@pytest.mark.parametrize("a", range(3))
def test_exhaustive_nested_presence_and_projection(
    frames: _Frames, comparison: bool, a: int
) -> None:
    key = _KEYS[0]
    inputs = [frames.values({}), frames.values({key: None}), frames.values({key: 0})]
    flags = _COMPARE_FLAGS if comparison else _ALIGN_FLAGS
    for b, c, d in product(range(3), repeat=3):
        left, right = bool(a or b), bool(c or d)
        if comparison:
            one, two = inputs[a].compare(inputs[b]), inputs[c].compare(inputs[d])
            nested = one.compare({"prior": mv.baseline(two)})
        else:
            one, two = inputs[a].align(inputs[b]), inputs[c].align(inputs[d])
            nested = one.align(two)
        expected_flags = (left, right, bool(a), bool(b), bool(c), bool(d))
        rows = [(*key, *expected_flags)] if left or right else []
        fixed = nested.select({}).compute()
        context = ("nested compare" if comparison else "nested align", a, b, c, d)
        _check(fixed, rows, presence=flags, values=(), context=context)
        inherited_only = fixed.where(fixed[flags[3]] & ~fixed[flags[1]]).compute()
        _check(
            inherited_only,
            rows if b and not right else [],
            presence=flags,
            values=(),
            context=(*context, "where"),
        )


def test_presence_reduction_and_reopen(frames: _Frames) -> None:
    first = frames.values({_KEYS[0]: None, _KEYS[2]: 0})
    second = frames.values({_KEYS[1]: 3, _KEYS[2]: None})
    combined = first.align(second).compute()
    material = combined.select({"renamed": combined["left.q"]}).persist()
    reopened = frames.session.persisted_frame(material.artifact_id)
    (alignment,) = reopened.schema.alignments
    assert (alignment.left.values, alignment.left.presence) == (("renamed",), ("left_present",))
    assert alignment.right.values == () and alignment.right.presence == ("right_present",)
    flags = ("left_present", "right_present")
    _check(
        reopened,
        [
            (*_KEYS[0], None, True, False),
            (*_KEYS[2], 0, True, True),
            (*_KEYS[1], None, False, True),
        ],
        presence=flags,
        values=("renamed",),
        context=("published renamed value",),
    )
    reduced = reopened.reduce({"n": mv.row_count()}, by=reopened.axes).compute()
    assert reduced.schema.alignments == ()
    _check(
        reduced,
        [(*key, 1) for key in sorted((_KEYS[0], _KEYS[2], _KEYS[1]))],
        presence=(),
        values=("n",),
        context=("retain all grouping axes",),
    )
    _check(
        reopened.reduce({"n": mv.row_count()}).compute(),
        [(3,)],
        presence=(),
        values=("n",),
        key_columns=(),
        context=("scalar reduction",),
    )


def test_named_baselines_keep_independent_presence(frames: _Frames) -> None:
    current = frames.values({_KEYS[0]: None})
    prior = frames.values({_KEYS[1]: 0})
    budget = frames.values({_KEYS[2]: 7, _KEYS[1]: None})
    comparison = current.compare({"prior": mv.baseline(prior), "budget": mv.baseline(budget)})
    flags = ("current.present", "prior.present", "budget.present")
    for alias, fact in zip(("prior", "budget"), comparison.schema.comparisons, strict=True):
        assert fact.alias == alias and fact.presence is not None
        assert (fact.presence.current, fact.presence.baseline) == (
            "current.present",
            f"{alias}.present",
        )
    _check(
        comparison.select({}).compute(),
        [
            (*_KEYS[0], True, False, False),
            (*_KEYS[1], False, True, True),
            (*_KEYS[2], False, False, True),
        ],
        presence=flags,
        values=(),
        context=("independent baselines with overlapping and disjoint keys",),
    )


def test_exhaustive_axis_binding_and_exact_input_ownership(frames: _Frames) -> None:
    values = {_KEYS[0]: 1}
    left, right, sibling = (frames.values(values) for _ in range(3))
    foreign = frames.values(values, foreign=True)
    for left_a, right_a, left_b, right_b in product(range(2), repeat=4):
        pairs = ((left.axes[left_a], right.axes[right_a]), (left.axes[left_b], right.axes[right_b]))
        valid = {left_a, left_b} == {0, 1} and (left_a, left_b) == (right_a, right_b)
        for combine in (left.align, left.compare):
            if valid:
                combine(right, on=pairs)
            else:
                with pytest.raises(AnalysisError):
                    combine(right, on=pairs)
    for combine in (left.align, left.compare):
        with pytest.raises(AnalysisError):
            combine(right, on=((left.axes[0], right.axes[0]),))
        with pytest.raises(AnalysisError):
            combine(right, on=tuple(zip(sibling.axes, right.axes, strict=True)))
        with pytest.raises(AnalysisError):
            combine(foreign)
    for input_ in (sibling, foreign):
        with pytest.raises(AnalysisError):
            _ = left["q"] + input_["q"]
        with pytest.raises(AnalysisError):
            left.where(input_["q"] > 0)
        with pytest.raises(AnalysisError):
            left.select({"q": input_["q"]})
        with pytest.raises(AnalysisError):
            left.reduce({"q": input_["q"].sum()})


def test_ordinal_alignment_preserves_missing_endpoint_and_comparison_domain(
    frames: _Frames,
) -> None:
    left = frames.days("2026-02-01", "2026-02-03")
    right = frames.days("2026-03-01", "2026-03-04")
    paired = left.align(right, match=mv.ordinal()).compute()
    assert [axis.role for axis in paired.axes] == ["paired"]
    table = paired.to_pandas()
    assert paired.presence_names == ("left_present", "right_present")
    assert table.iloc[:, 0].dt.strftime("%Y-%m-%d").fillna("").tolist() == [
        "2026-02-01",
        "2026-02-02",
        "",
    ]
    assert table.iloc[:, 1].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-03-01",
        "2026-03-02",
        "2026-03-03",
    ]
    assert table[["left_present", "right_present"]].to_records(index=False).tolist() == [
        (True, True),
        (True, True),
        (False, True),
    ]
    assert [_normalize(value) for value in table["left.q"]] == [0, 2, None]
    with pytest.raises(AnalysisError):
        left.compare(right, match=mv.ordinal()).compute()
    compared = right.compare(left, match=mv.ordinal()).compute()
    rows = compared.to_pandas()
    assert [axis.role for axis in compared.axes] == ["calendar"]
    assert rows["current.present"].tolist() == [True, True, True]
    assert rows["reference.present"].tolist() == [True, True, False]
    assert [_normalize(value) for value in rows["reference.relative.q"]] == [None, 1, None]
