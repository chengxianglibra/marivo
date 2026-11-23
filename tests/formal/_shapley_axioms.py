"""Independent exact coalition oracle and observations at the Shapley sampler boundary."""

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import permutations
from types import SimpleNamespace

import pytest

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import MetricNode
from marivo._kernel.refs import ExactRef
from marivo.analysis.binding.attribute_game import AttributeGame, build_game
from marivo.analysis.core.attribute_game import CoalitionValue, GameSample
from marivo.analysis.dsl import frame_attribute_binding
from marivo.analysis.materialization import attribute_native_execution
from marivo.analysis.methods import attribute_game_sampling, frame_attribute_shapley
from marivo.analysis.model.frozen_keys import FrozenKey


@dataclass
class SamplingObservation:
    players: tuple[str, ...]
    orders: list[tuple[int, ...]] = field(default_factory=list)
    coalitions: dict[int, Fraction] = field(default_factory=dict)
    marginals: list[tuple[float, ...]] = field(default_factory=list)


def _group(key: FrozenKey) -> str:
    ((_, label),) = key
    assert isinstance(label, str)
    return label


class _ExhaustiveStream:
    def __init__(self, observation: SamplingObservation) -> None:
        self._observation = observation
        n = len(observation.players)
        self._orders: Iterator[tuple[int, ...]] = (
            iter(((0,), (0,))) if n == 1 else permutations(range(n))
        )

    def shuffle(self, order: list[int]) -> None:
        selected = next(self._orders)
        self._observation.orders.append(selected)
        order[:] = selected


def observe_exhaustive_sampling(monkeypatch: pytest.MonkeyPatch) -> list[SamplingObservation]:
    """Control which orders are drawn and observe real evaluator and marginal values."""
    observations: list[SamplingObservation] = []
    # Each route supplies its own sampler options. This hook forwards those options verbatim.
    sample: Callable[..., GameSample] = attribute_game_sampling.sample_game
    moments = attribute_game_sampling._sample_moments

    def observe_sample(
        parent: FrozenKey,
        players: tuple[FrozenKey, ...],
        permutations: int,
        seed: int,
        evaluate: Callable[[int], CoalitionValue],
        **options: bool,
    ) -> GameSample:
        observed = SamplingObservation(tuple(map(_group, players)))
        observations.append(observed)

        def evaluate_and_record(mask: int) -> CoalitionValue:
            value = evaluate(mask)
            exact = Fraction(value.value)
            assert observed.coalitions.setdefault(mask, exact) == exact
            return value

        def record_moments(values: list[float]) -> tuple[float, float, float]:
            observed.marginals.append(tuple(values))
            return moments(values)

        def order_stream(_seed: int) -> _ExhaustiveStream:
            return _ExhaustiveStream(observed)

        with monkeypatch.context() as sampling:
            sampling.setattr(
                attribute_game_sampling, "random", SimpleNamespace(Random=order_stream)
            )
            sampling.setattr(attribute_game_sampling, "_sample_moments", record_moments)
            return sample(parent, players, permutations, seed, evaluate_and_record, **options)

    monkeypatch.setattr(frame_attribute_shapley, "sample_game", observe_sample)
    monkeypatch.setattr(attribute_native_execution, "sample_game", observe_sample)
    return observations


def force_native_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the compiler's native route with the original formulas and source."""

    def mark_ineligible(
        node: MetricNode,
        declared: Mapping[ExactRef, MetricNode],
        *,
        metric: str,
        by: tuple[ComparisonAxis, ...] = (),
    ) -> AttributeGame:
        game = build_game(node, declared, metric=metric, by=by)
        assert all(leaf.state_eligible for leaf in game.leaves)
        return replace(
            game,
            leaves=tuple(replace(leaf, state_eligible=False) for leaf in game.leaves),
        )

    monkeypatch.setattr(frame_attribute_binding, "build_game", mark_ineligible)


def subset_shapley(values: dict[int, Fraction], n: int) -> tuple[Fraction, ...]:
    """Weight each independent predecessor subset by its ordering multiplicity."""
    denominator = math.factorial(n)
    return tuple(
        sum(
            (
                Fraction(
                    math.factorial(mask.bit_count()) * math.factorial(n - mask.bit_count() - 1),
                    denominator,
                )
                * (values[mask | bit] - values[mask])
                for mask in values
                if not mask & bit
            ),
            Fraction(0),
        )
        for bit in (1 << index for index in range(n))
    )


def permutation_marginals(
    values: dict[int, Fraction], orders: list[tuple[int, ...]], n: int
) -> tuple[tuple[Fraction, ...], ...]:
    by_player: list[list[Fraction]] = [[] for _ in range(n)]
    for order in orders:
        for position, player in enumerate(order):
            predecessor = sum(2**member for member in order[:position])
            successor = predecessor + 2**player
            by_player[player].append(values[successor] - values[predecessor])
    return tuple(tuple(draws) for draws in by_player)


def standard_error(draws: tuple[Fraction, ...], mean: Fraction) -> float:
    variance = sum((draw - mean) ** 2 for draw in draws) / (len(draws) * (len(draws) - 1))
    return math.sqrt(variance)
