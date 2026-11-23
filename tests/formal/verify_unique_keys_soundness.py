"""Search for two distinct output occurrences with the same inferred key."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pytest

from marivo.analysis.compiler.unique_keys import relation_unique_keys
from marivo.analysis.model.unique_keys import UniqueKeys
from tests.formal._smt import prove_unsat

_Key = tuple[str, ...]
_Keys = tuple[_Key, ...]
_Carrier = Literal["ordinary", "string", "float"]
_Equality = Literal["completed", "sql", "stored"]
_Schema = Mapping[str, str]
_Seeds = Mapping[ops.Relation, UniqueKeys]

_NEGATIVE_ZERO = 2**63
_POSITIVE_INFINITY = 0x7FF0000000000000
_NEGATIVE_INFINITY = 0xFFF0000000000000
_CANONICAL_NAN = 0x7FF8000000000000


def _and(*terms: str) -> str:
    return f"(and {' '.join(terms)})" if terms else "true"


def _not(term: str) -> str:
    return f"(not {term})"


def _eq(lhs: str, rhs: str) -> str:
    return f"(= {lhs} {rhs})"


def _implies(condition: str, conclusion: str) -> str:
    return f"(=> {condition} {conclusion})"


@dataclass(frozen=True)
class _Cell:
    value: str
    null: str
    carrier: _Carrier

    def exact_value(self) -> str:
        if self.carrier != "float":
            return self.value
        # Completion identifies signed zeros and all valid NaN payloads.
        return (
            f"(ite (= {self.value} {_NEGATIVE_ZERO}) 0 "
            f"(ite (or (and (< {_POSITIVE_INFINITY} {self.value}) "
            f"(< {self.value} {_NEGATIVE_ZERO})) (< {_NEGATIVE_INFINITY} {self.value})) "
            f"{_CANONICAL_NAN} {self.value}))"
        )

    def sql_value(self) -> str:
        # An arbitrary folding map permits coarser collation classes, without
        # assuming which distinct returned strings share a class.
        return f"(fold {self.value})" if self.carrier == "string" else self.value

    def compare_value(self, equality: _Equality) -> str:
        if equality == "completed":
            return self.exact_value()
        return self.sql_value() if equality == "sql" else self.value

    def same(self, other: _Cell, *, equality: _Equality = "completed") -> str:
        lhs = self.compare_value(equality)
        rhs = other.compare_value(equality)
        return _and(_eq(self.null, other.null), f"(or {self.null} {_eq(lhs, rhs)})")

    def matched(self, other: _Cell, *, null_safe: bool) -> str:
        return _and("true" if null_safe else _not(self.null), self.same(other, equality="sql"))

    def when_present(self, present: str) -> _Cell:
        return _Cell(self.value, f"(or {_not(present)} {self.null})", self.carrier)


_Row = dict[str, _Cell]


@dataclass(frozen=True)
class _Rows:
    first: _Row
    second: _Row

    def same(self, key: _Key, *, equality: _Equality = "completed") -> str:
        return _and(*(self.first[k].same(self.second[k], equality=equality) for k in key))

    def select(self, **sources: str) -> _Rows:
        return _Rows(
            *(
                {out: source[name] for out, name in sources.items()}
                for source in (self.first, self.second)
            )
        )

    def with_remainder(self, name: str) -> _Rows:
        def transform(cell: _Cell) -> _Cell:
            return _Cell(f"(mod {cell.value} 2)", cell.null, cell.carrier)

        return _Rows(
            {**self.first, name: transform(self.first[name])},
            {**self.second, name: transform(self.second[name])},
        )


@dataclass
class _Proof:
    declarations: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    witness: list[str] = field(default_factory=list)

    def variable(self, name: str, sort: str) -> str:
        self.declarations.append(f"(declare-const {name} {sort})")
        self.witness.append(name)
        return name

    def assume(self, *terms: str) -> None:
        self.assertions.extend(f"(assert {term})" for term in terms)

    def rows(self, schema: _Schema, *, name: str = "row") -> _Rows:
        def row(prefix: str) -> _Row:
            cells: _Row = {}
            for slot, dtype in schema.items():
                kind = dtype.lstrip("!")
                carrier: _Carrier = (
                    "string" if kind == "string" else "float" if kind == "float64" else "ordinary"
                )
                cell = f"{prefix}_{slot}"
                value = self.variable(cell, "Int")
                if carrier == "float":
                    self.assume(f"(and (<= 0 {value}) (< {value} {2**64}))")
                cells[slot] = _Cell(
                    value,
                    "false" if dtype.startswith("!") else self.variable(f"{cell}_null", "Bool"),
                    carrier,
                )
                if carrier == "string":
                    self.witness.append(cells[slot].sql_value())
            return cells

        return _Rows(row(f"{name}1"), row(f"{name}2"))

    def pair_ids(self, name: str) -> tuple[str, str]:
        return self.variable(f"{name}1", "Int"), self.variable(f"{name}2", "Int")

    def verify(
        self, claim: str, table: ir.Table, rows: _Rows, *, expected: _Keys, seeds: _Seeds
    ) -> None:
        facts = relation_unique_keys(table, seeds)
        # Prove each *actual* candidate before checking coverage. An unsound
        # mutation must produce a SAT witness, even for a normally keyless case.
        assert facts.slots == tuple(rows.first) == tuple(rows.second)
        for key in facts.keys:
            query = "\n".join(
                (
                    "(declare-fun fold (Int) Int)",
                    *self.declarations,
                    *self.assertions,
                    f"(assert {rows.same(key)})",
                )
            )
            prove_unsat(query, claim=f"{claim}, key {key}", witness=tuple(self.witness))
        # A conservative implementation could otherwise pass by reporting no facts.
        assert facts.keys == expected


def _table(schema: _Schema, name: str) -> ir.Table:
    return ibis.table(dict(schema), name=name)


def _seed(table: ir.Table, key: _Key) -> UniqueKeys:
    return UniqueKeys(tuple(table.columns), (key,))


@pytest.mark.parametrize("carrier", ["scalar", "integer", "string", "floating"])
def test_aggregate_respects_completed_group_identity(carrier: str) -> None:
    dtype = {"scalar": "int64", "integer": "int64", "string": "string", "floating": "float64"}
    table = _table({"key": dtype[carrier]}, "source")
    query = (
        table.aggregate(n=table.count())
        if carrier == "scalar"
        else (table.group_by("key").aggregate(n=table.count()))
    )
    groups = () if carrier == "scalar" else ("key",)
    schema = {"key": dtype[carrier], "n": "!int64"} if groups else {"n": "!int64"}
    proof = _Proof()
    rows = proof.rows(schema)
    # A grouped result has one occurrence per SQL grouping class, even NULL.
    proof.assume(_not(rows.same(groups, equality="sql")))
    proof.verify(
        f"Aggregate/{carrier}",
        query,
        rows,
        expected=() if carrier == "floating" else (groups,),
        seeds={},
    )


@pytest.mark.parametrize("carrier", ["integer", "string", "floating"])
@pytest.mark.parametrize("seeded", [False, True])
def test_distinct_keeps_input_keys_and_establishes_only_exact_output_keys(
    carrier: str,
    seeded: bool,
) -> None:
    dtype = {"integer": "int64", "string": "string", "floating": "float64"}
    schema = {"key": "int64", "value": dtype[carrier]}
    table = _table(schema, "source")
    proof = _Proof()
    rows = proof.rows(schema)
    proof.assume(_not(rows.same(("key", "value"), equality="sql")))
    if seeded:
        proof.assume(_not(rows.same(("key",))))
    expected = (("key",),) if seeded else (() if carrier == "floating" else (("key", "value"),))
    proof.verify(
        f"Distinct/{carrier}",
        table.distinct(),
        rows,
        expected=expected,
        seeds={table.op(): _seed(table, ("key",))} if seeded else {},
    )


@pytest.mark.parametrize(
    "operation",
    ["alias", "drop", "replace", "filter", "sort", "dropnull", "limit0", "limit1", "limit2"],
)
def test_subset_and_projection_keep_only_keys_from_surviving_origins(operation: str) -> None:
    schema = {"tenant": "int64", "key": "int64", "value": "int64"}
    table = _table(schema, "source")
    proof = _Proof()
    source = proof.rows(schema)
    proof.assume(_not(source.same(("tenant", "key"))))
    limit = int(operation[-1]) if operation.startswith("limit") else None
    if limit is not None:
        # LIMIT picks distinct row positions numbered in [0, limit).
        a, b = proof.pair_ids("position")
        proof.assume(_not(_eq(a, b)), *(f"(and (<= 0 {i}) (< {i} {limit}))" for i in (a, b)))
    query, rows, expected = {
        "alias": (
            table.select(account=table.tenant, code=table.key),
            source.select(account="tenant", code="key"),
            (("account", "code"),),
        ),
        "drop": (
            table.select("tenant", "value"),
            source.select(tenant="tenant", value="value"),
            (),
        ),
        "replace": (table.mutate(key=table.key % 2), source.with_remainder("key"), ()),
        "filter": (table.filter(table.value > 0), source, (("tenant", "key"),)),
        "sort": (table.order_by("value"), source, (("tenant", "key"),)),
        "dropnull": (table.drop_null("value"), source, (("tenant", "key"),)),
        "limit0": (table.limit(0), source, ((),)),
        "limit1": (table.limit(1), source, ((),)),
        "limit2": (table.limit(2), source, (("tenant", "key"),)),
    }[operation]
    # Filter, Sort and DropNull can choose/reorder any distinct source
    # occurrences without weakening this invariant. Selection needs no predicates.
    proof.verify(
        operation,
        query,
        rows,
        expected=expected,
        seeds={
            table.op(): _seed(table, ("tenant", "key")),
        },
    )


@pytest.mark.parametrize("seeded", [False, True])
def test_negative_limit_retains_distinct_tail_occurrences(seeded: bool) -> None:
    table = ibis.range(5).unnest().name("key").as_table()
    proof = _Proof()
    rows = proof.rows({"key": "!int64"})
    # ClickHouse LIMIT -3 takes the final three occurrences of range(5).
    tail = (rows.first["key"].value, rows.second["key"].value)
    proof.assume(_not(rows.same(("key",))), *(f"(and (<= 2 {i}) (<= {i} 4))" for i in tail))
    proof.verify(
        "Limit(-3) on a five-row range",
        table.limit(-3),
        rows,
        expected=(("key",),) if seeded else (),
        seeds={table.op(): _seed(table, ("key",))} if seeded else {},
    )


@pytest.mark.parametrize("operation", ["union", "intersect", "difference"])
@pytest.mark.parametrize("distinct", [False, True], ids=["all", "distinct"])
@pytest.mark.parametrize("carrier", ["integer", "string", "floating"])
@pytest.mark.parametrize("seeded", [False, True])
def test_set_operations_preserve_only_uniqueness_from_valid_origins(
    operation: str,
    distinct: bool,
    carrier: str,
    seeded: bool,
) -> None:
    dtype = {"integer": "int64", "string": "string", "floating": "float64"}
    schema = {"key": dtype[carrier], "value": "!int64"}
    left = _table(schema, "set_left")
    right = _table(schema, "set_right")
    query = {"union": left.union, "intersect": left.intersect, "difference": left.difference}[
        operation
    ](right, distinct=distinct)
    proof = _Proof()
    rows = proof.rows(schema)
    expected: _Keys = ()
    if distinct:
        proof.assume(_not(rows.same(("key", "value"), equality="sql")))
        if carrier != "floating":
            expected = (("key", "value"),)
    # EXCEPT and INTERSECT emit a subset of the left occurrences even in
    # their bag variants. UNION may instead select either source.
    if seeded and operation == "union":
        left_first = proof.variable("from_left1", "Bool")
        left_second = proof.variable("from_left2", "Bool")
        proof.assume(_implies(_and(left_first, left_second), _not(rows.same(("key",)))))
    elif seeded:
        proof.assume(_not(rows.same(("key",))))
        expected = (("key",),)
    proof.verify(
        f"{operation}/{distinct}/{carrier}",
        query,
        rows,
        expected=expected,
        seeds={left.op(): _seed(left, ("key",))} if seeded else {},
    )


_JoinKind = Literal["inner", "left", "semi", "anti", "cross"]


def _join_input(
    proof: _Proof,
    name: str,
    dtype: str,
) -> tuple[ir.Table, _Rows, _Key, str]:
    schema = {f"{name}_key": dtype, f"{name}_tenant": "int64", f"{name}_payload": "int64"}
    table = _table(schema, name)
    rows = proof.rows(schema, name=name)
    key = (f"{name}_key", f"{name}_tenant")
    first, second = proof.pair_ids(f"{name}_ordinal")
    same_source = _eq(first, second)
    proof.assume(
        _implies(rows.same(key), same_source),
        _implies(same_source, rows.same(tuple(schema), equality="stored")),
    )
    return table, rows, key, same_source


def _matches(left: _Row, right: _Row, *, null_safe: bool) -> str:
    return _and(
        *(
            left[f"left_{slot}"].matched(right[f"right_{slot}"], null_safe=null_safe)
            for slot in ("key", "tenant")
        )
    )


def _joined_rows(
    proof: _Proof,
    left: _Rows,
    right: _Rows,
    left_same: str,
    right_same: str,
    how: _JoinKind,
    *,
    null_safe: bool,
) -> _Rows:
    if how in ("semi", "anti"):
        # These are existence/nonexistence selections of left occurrences.
        proof.assume(_not(left_same))
        return left
    if how == "left":
        present1, present2 = (
            proof.variable("matched1", "Bool"),
            proof.variable("matched2", "Bool"),
        )
        # An unmatched left row has no match, including either right witness
        # used by a matched output. Other right rows need not be modeled.
        for source, present in ((left.first, present1), (left.second, present2)):
            for other, other_present in ((right.first, present1), (right.second, present2)):
                proof.assume(
                    _implies(
                        _and(_not(present), other_present),
                        _not(_matches(source, other, null_safe=null_safe)),
                    )
                )
        proof.assume(
            _implies(present1, _matches(left.first, right.first, null_safe=null_safe)),
            _implies(present2, _matches(left.second, right.second, null_safe=null_safe)),
            _not(_and(left_same, _eq(present1, present2), f"(or (not {present1}) {right_same})")),
        )
        return _Rows(
            {**left.first, **{k: c.when_present(present1) for k, c in right.first.items()}},
            {**left.second, **{k: c.when_present(present2) for k, c in right.second.items()}},
        )
    if how == "inner":
        proof.assume(
            _matches(left.first, right.first, null_safe=null_safe),
            _matches(left.second, right.second, null_safe=null_safe),
        )
    proof.assume(_not(_and(left_same, right_same)))
    return _Rows({**left.first, **right.first}, {**left.second, **right.second})


@pytest.mark.parametrize("null_safe", [False, True], ids=["equals", "identical-to"])
@pytest.mark.parametrize("how", ["inner", "left", "semi", "anti", "cross"])
@pytest.mark.parametrize(
    ("left_type", "right_type"),
    [
        ("!int64", "!int64"),
        ("int64", "int64"),
        ("!int64", "int64"),
        ("!string", "!string"),
        ("string", "string"),
    ],
    ids=["integer", "nullable-integer", "mixed-nullability", "string", "nullable-string"],
)
def test_join_only_transfers_keys_when_matches_are_unique(
    how: _JoinKind,
    null_safe: bool,
    left_type: str,
    right_type: str,
) -> None:
    proof = _Proof()
    left, lhs, left_key, left_same = _join_input(proof, "left", left_type)
    right, rhs, right_key, right_same = _join_input(proof, "right", right_type)
    lkey, ltenant = (left[name] for name in left_key)
    rkey, rtenant = (right[name] for name in right_key)
    # Reverse the second binding to exercise both operand orders.
    condition = (
        lkey.identical_to(rkey) & rtenant.identical_to(ltenant)
        if null_safe
        else (lkey == rkey) & (rtenant == ltenant)
    )
    # A Cartesian join has no predicate; both comparison cases use the
    # predicate-free constructor and the Cartesian occurrence identity.
    query = left.cross_join(right) if how == "cross" else left.join(right, condition, how=how)
    rows = _joined_rows(proof, lhs, rhs, left_same, right_same, how, null_safe=null_safe)
    expected: dict[_JoinKind, _Keys] = {
        "inner": () if "string" in left_type else (left_key, right_key),
        "left": () if "string" in left_type else (left_key,),
        "semi": (left_key,),
        "anti": (left_key,),
        "cross": ((*left_key, *right_key),),
    }
    proof.verify(
        f"JoinChain/{how}/{null_safe}/{left_type}/{right_type}",
        query,
        rows,
        expected=expected[how],
        seeds={
            left.op(): _seed(left, left_key),
            right.op(): _seed(right, right_key),
        },
    )
