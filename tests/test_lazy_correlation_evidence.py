"""Independent disclosure, selection and signed-lag contract regressions."""

import json
from dataclasses import replace

import pytest

from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.materialization.association_codec import (
    AssociationEvidenceSummary,
    decode_evidence,
    decode_semantics,
    evidence_payload,
    semantics_payload,
)
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.operators.association_contracts import (
    SELECTION_RULE_ID,
    STATUSES,
    candidate_count,
    pair_approximation_bindings,
    selection_key,
)
from tests.lazy_correlation_fixtures import association_spec


def test_evidence_explicitly_binds_selection_pairs_and_truncation() -> None:
    meaning = replace(
        association_spec("pearson").semantics,
        _token=_CORE_TOKEN,
        approximations=("sampled_population", "semantic_percentile"),
    )
    summary = AssociationEvidenceSummary(
        row_count=1001,
        status_counts=tuple((status, 1001 if status == "valid" else 0) for status in STATUSES),
        emitted_finding_count=1000,
        finding_set_digest="0" * 64,
        searched_pair_count=1,
        searched_lag_count=1,
        searched_series_count=1001,
        original_candidate_count=1001,
        complete_pair_range=(2, 2),
        null_pair_range=(0, 0),
        selection_rule_id=SELECTION_RULE_ID,
        pair_approximation_bindings=pair_approximation_bindings(meaning),
        eligible_finding_count=1001,
        finding_truncated=True,
    )
    body = json.loads(canonical_json(evidence_payload(summary)))
    assert (
        body["selection_rule_id"] == "association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1"
    )
    assert body["pair_approximation_bindings"] == [
        {
            "metric_key_a": "metric:sales.revenue",
            "metric_key_b": "metric:sales.order_count",
            "approximation_a": "sampled_population",
            "approximation_b": "semantic_percentile",
        }
    ]
    assert decode_evidence(body) == summary
    for field, invalid in (
        ("selection_rule_id", "unregistered@v1"),
        ("pair_approximation_bindings", []),
        ("finding_truncated", False),
        ("eligible_finding_count", 1000),
        ("emitted_finding_count", 1001),
    ):
        with pytest.raises(IntegrityError):
            decode_evidence({**body, field: invalid})


@pytest.mark.parametrize("bad", [True, False, 1.0, "1"])
def test_signed_lag_codec_rejects_non_integer_values(bad: object) -> None:
    body = json.loads(
        canonical_json(semantics_payload(association_spec("pearson", shape="time").semantics))
    )
    with pytest.raises(IntegrityError):
        decode_semantics({**body, "lag_offsets": [bad]})
    body["lag_offsets"] = [-1, 0, 1]
    assert decode_semantics(body).lag_offsets == (-1, 0, 1)


def test_selection_policy_preserves_large_signed_offsets_and_count_formula() -> None:
    assert sorted(
        [(0.5, 0), (-0.8, 3), (0.8, -3), (0.8, 1)], key=lambda pair: selection_key(*pair)
    ) == [(0.8, 1), (0.8, -3), (-0.8, 3), (0.5, 0)]
    assert selection_key(1.0, 2**62) < selection_key(1.0, 2**62 + 1)
    assert candidate_count(16, 34) == 4080
    assert candidate_count(2, 1, 4096) == 4096


def test_approximation_bindings_keep_each_requested_pair() -> None:
    from marivo.analysis.operators.association_contracts import AssociationSemantics
    from marivo.refs import ref
    from tests.lazy_observation_fixtures import make_sources

    metrics = ("sales.revenue", "sales.weighted_amount", "sales.mean_amount")
    value = make_sources().observe([ref.metric(name) for name in metrics]).correlate()
    meaning = value.row_contract.family_semantics
    assert isinstance(meaning, AssociationSemantics)
    meaning = replace(
        meaning,
        _token=_CORE_TOKEN,
        approximations=("exact", "sampled_population", "semantic_percentile"),
    )
    bindings = pair_approximation_bindings(meaning)
    assert [
        (b.metric_key_a, b.metric_key_b, b.approximation_a, b.approximation_b) for b in bindings
    ] == [
        ("metric:sales.revenue", "metric:sales.weighted_amount", "exact", "sampled_population"),
        ("metric:sales.revenue", "metric:sales.mean_amount", "exact", "semantic_percentile"),
        (
            "metric:sales.weighted_amount",
            "metric:sales.mean_amount",
            "sampled_population",
            "semantic_percentile",
        ),
    ]
