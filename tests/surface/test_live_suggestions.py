"""Suggestion scoring stays byte-for-byte compatible with the original lexical policy."""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Callable
from types import MappingProxyType

import pytest

from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.resolve import (
    LiveSuggestionIndex,
    _normalize_token,
    _tokenize,
    suggestions_for,
)


def _index(targets: tuple[tuple[str, str], ...]) -> LiveSuggestionIndex:
    return LiveSuggestionIndex(
        _all_targets=tuple(target for target, _ in targets),
        _target_tokens=MappingProxyType(
            {target: _tokenize(target) | _tokenize(summary) for target, summary in targets}
        ),
        _normalized_targets=MappingProxyType(
            {target: _normalize_token(target) for target, _ in targets}
        ),
    )


def _legacy_suggestions_for(query: str, index: LiveSuggestionIndex) -> tuple[str, ...]:
    """Return deterministic bounded suggestions for a lexical query."""
    if not query or not query.strip():
        return ()
    query_tokens = _tokenize(query)
    normalized_query = _normalize_token(query)
    if not query_tokens and not normalized_query:
        return ()
    scored = [
        (score, target)
        for target in index._all_targets
        if (score := _legacy_score_target(target, query_tokens, normalized_query, index)) > 0.0
    ]
    # A typo within an exact namespace should lead to that namespace's closest
    # leaf before a shorter family/root name that shares more generic tokens.
    parent, separator, leaf = query.rpartition(".")
    if separator:
        scored = [
            (
                score
                + (1000.0 if parent == target.rpartition(".")[0] else 0.0)
                + (
                    200.0 * ratio
                    if parent == target.rpartition(".")[0]
                    and (
                        ratio := difflib.SequenceMatcher(
                            None, leaf, target.rpartition(".")[2]
                        ).ratio()
                    )
                    >= 0.7
                    else 0.0
                ),
                target,
            )
            for score, target in scored
        ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(target for _, target in scored[: SURFACE_LIMITS.help_suggestion_limit])


def _legacy_score_target(
    target: str,
    query_tokens: frozenset[str],
    normalized_query: str,
    index: LiveSuggestionIndex,
) -> float:
    target_tokens = index._target_tokens[target]
    normalized_target = index._normalized_targets[target]
    score = 100.0 * len(query_tokens & target_tokens)
    if normalized_query and len(normalized_query) >= 3:
        ratios = (
            difflib.SequenceMatcher(None, normalized_query, token).ratio()
            for token in target_tokens
            if len(token) >= 3 and token != normalized_query
        )
        best_ratio = max(ratios, default=0.0)
        if best_ratio >= 0.7:
            score += best_ratio * 100.0
    if normalized_query and normalized_target:
        if normalized_query == normalized_target:
            # An exact canonical-id match always outranks fuzzy/prefix matches.
            score += 1000.0
        elif normalized_query in normalized_target:
            position = normalized_target.find(normalized_query)
            score += 50.0 + max(0.0, 50.0 - position * 2.0)
        elif normalized_target in normalized_query:
            score += 30.0
    overlap = query_tokens & target_tokens
    if overlap:
        score += 20.0 * len(overlap) / len(query_tokens | target_tokens)
    if score == 0.0 and normalized_query and normalized_target:
        full_ratio = difflib.SequenceMatcher(None, normalized_query, normalized_target).ratio()
        if full_ratio > 0.6:
            score += full_ratio * 10.0
    return score


@pytest.mark.parametrize(
    "query",
    (
        "",
        "  ",
        "!!!",
        "catalog.gett",
        "catalog",
        "get",
        "get member",
        "GET_MEMBER",
        "other.catalog.get",
        "zzzzzzzzz",
        "分析",
        "catalog.search",
        "serch",
    ),
)
def test_suggestions_match_original_scoring_and_order(query: str) -> None:
    index = _index(
        (
            ("catalog.get", "Get one catalog member."),
            ("catalog.search", "Search catalog members by name."),
            ("catalog.get_member", "Get one catalog member."),
            ("catalog", "Catalog member discovery."),
            ("other.get", "Get one catalog member."),
            ("other.search", "Search catalog members by name."),
            ("search", "Search by name."),
        )
    )
    assert suggestions_for(query, index) == _legacy_suggestions_for(query, index)


def test_suggestions_compare_each_word_pair_once_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        tuple(
            (f"catalog.member_{suffix}", "Search the catalog for a member by name.")
            for suffix in ("one", "two", "three", "four")
        )
    )
    expected = _legacy_suggestions_for("catalog.membr", index)
    pairs: Counter[tuple[str, str]] = Counter()
    matcher = difflib.SequenceMatcher

    def record(
        isjunk: Callable[[str], bool] | None = None,
        a: str = "",
        b: str = "",
        autojunk: bool = True,
    ) -> difflib.SequenceMatcher[str]:
        pairs[(a, b)] += 1
        return matcher(isjunk, a, b, autojunk)

    monkeypatch.setattr(difflib, "SequenceMatcher", record)
    assert suggestions_for("catalog.membr", index) == expected
    assert pairs and max(pairs.values()) == 1
