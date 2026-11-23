"""Population matching, sample completion and finite window slices."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from itertools import product
from typing import TypeAlias

from tests.formal.reference.evaluator import observe, reduce_bag
from tests.formal.reference.model import (
    Bag,
    Branch,
    Key,
    Obligation,
    Occurrence,
    Read,
    Reducer,
    Result,
    Scope,
    key_rank,
)

Relation: TypeAlias = Mapping[Key, tuple[Key, ...]]
Instant: TypeAlias = int | datetime


def _image(values: Iterable[Key]) -> tuple[Key, ...]:
    return tuple({key_rank(key): key for key in values}.values())


def matching_keys(identity: Key, independent_relations: tuple[Relation, ...]) -> tuple[Key, ...]:
    """Take products across independent relations, preserving each joint match."""
    return _image(
        tuple(value for part in matched for value in part)
        for matched in product(*(relation.get(identity, ()) for relation in independent_relations))
    )


def in_population(
    bag: Bag, population: tuple[Key, ...], independent_relations: tuple[Relation, ...]
) -> Bag:
    allowed = {key_rank(key) for key in population}
    return tuple(
        row
        for row in bag
        if any(
            key_rank(matched) in allowed
            for matched in matching_keys(row.identity, independent_relations)
        )
    )


def membership(bag: Bag, independent_relations: tuple[Relation, ...]) -> dict[Key, Bag]:
    groups: dict[Key, list[Occurrence]] = {}
    representatives: dict[tuple[tuple[int, object], ...], Key] = {}
    for row in bag:
        for matched in matching_keys(row.identity, independent_relations):
            key = representatives.setdefault(key_rank(matched), matched)
            groups.setdefault(key, []).append(row)
    return {key: tuple(rows) for key, rows in groups.items()}


def preimage(domain: Iterable[Key], relation: Relation, selected: Iterable[Key]) -> tuple[Key, ...]:
    target = {key_rank(key) for key in selected}
    return tuple(
        key for key in domain if any(key_rank(value) in target for value in relation.get(key, ()))
    )


def compose(left: Relation, right: Relation) -> dict[Key, tuple[Key, ...]]:
    return {
        key: _image(value for step in steps for value in right.get(step, ()))
        for key, steps in left.items()
    }


def classify(
    keys: Iterable[Key], relation: Relation, *, width: int, identity: bool
) -> tuple[tuple[Key, ...], frozenset[Obligation]]:
    output: list[Key] = []
    errors: set[Obligation] = set()
    for key in keys:
        candidates = _image(relation.get(key, ())) or ((None,) * width,)
        for target in candidates:
            if identity and None in target:
                errors.add(Obligation("missing_identity", key))
            else:
                output.append((*key, *target))
    return _image(output), frozenset(errors)


def per(
    groups: Mapping[Key, Mapping[Key, Bag]],
    reducer: Reducer,
    outer: Reducer,
    domain: Mapping[Key, tuple[Key, ...]] | None = None,
) -> Result:
    """Complete each admitted sample before reducing its resulting value."""
    errors: set[Obligation] = set()
    completed: dict[Key, Bag] = {}
    selected = (
        {key: tuple(samples) for key, samples in groups.items()} if domain is None else domain
    )
    for key, samples in selected.items():
        finished = []
        for sample in samples:
            value, issue = reduce_bag(reducer, groups.get(key, {}).get(sample, ()))
            if issue is not None:
                errors.add(Obligation(issue, (*key, *sample)))
            finished.append(Occurrence(sample, value))
        completed[key] = tuple(finished)
    result = observe(
        {"sample": Branch(outer, completed)},
        {"value": Read("sample")},
        Scope("explicit", tuple(selected)),
    )
    return replace(result, obligations=result.obligations | errors)


def window_slices(
    bag: Bag, endpoints: Mapping[Key, tuple[Instant | None, Instant]]
) -> dict[Key, Bag]:
    return {
        key: tuple(
            row
            for row in bag
            if row.time is not None
            and (start is None or key_rank((start,)) <= key_rank((row.time,)))
            and key_rank((row.time,)) < key_rank((end,))
        )
        for key, (start, end) in endpoints.items()
    }
