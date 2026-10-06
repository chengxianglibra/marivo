"""Tests for the shared fuzzy-match helper."""

from marivo.introspection._fuzzy import did_you_mean


def test_returns_matching_suggestions():
    candidates = ["region", "country", "revenue", "order_count"]
    result = did_you_mean("regn", candidates)
    assert "region" in result


def test_returns_empty_when_no_match():
    candidates = ["region", "country", "revenue"]
    result = did_you_mean("zzzzzzz", candidates)
    assert result == []


def test_respects_n_parameter():
    candidates = ["region", "regions", "regional", "country"]
    result = did_you_mean("region", candidates, n=2)
    assert len(result) <= 2


def test_handles_empty_candidate_list():
    result = did_you_mean("something", [])
    assert result == []


def test_qualified_field_id_matches():
    candidates = ["sales.region", "sales.revenue", "orders.amount"]
    result = did_you_mean("sales.regin", candidates)
    assert "sales.region" in result
