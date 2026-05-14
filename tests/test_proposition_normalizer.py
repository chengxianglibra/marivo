"""Tests for proposition identity normalisation (Phase 4e-2).

Acceptance criteria:
- ``_canonical_decimal``: strips trailing zeros, no scientific notation.
- ``_canonical_json``: dict keys sorted, floats as canonical numbers, type
  isolation (int ≠ str, bool ≠ int).
- ``normalize_proposition_identity``: stable across repeated calls; changes
  when any identity field changes; does NOT change when non-identity fields
  (schema_version, template_version, unit, …) differ.
- Origin partitioning: ``system_seeded`` vs ``agent_authored`` produce
  different identity_keys even for identical judgment payloads.
- ``derivation_version`` bump → new identity_key.
- ``make_proposition_id``: prefix ``"prop_"`` + 24 hex chars.
"""

from __future__ import annotations

import unittest
from typing import Any

from marivo.core.evidence.proposition_normalizer import (
    _canonical_decimal,
    _canonical_json,
    make_proposition_id,
    normalize_proposition_identity,
)

# ---------------------------------------------------------------------------
# Canonical subject fixtures
# ---------------------------------------------------------------------------

_SUBJECT_CHANGE: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "change",
}

_SUBJECT_DECOMP: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "decomposition",
}

_SUBJECT_ANOMALY: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "anomaly",
}

_SUBJECT_CORR: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "correlation",
}

_SUBJECT_TEST: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": None,
    "analysis_axis": "test",
}

_SUBJECT_FORECAST: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "forecast",
}

# ---------------------------------------------------------------------------
# Canonical payload fixtures
# ---------------------------------------------------------------------------

_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}

_PAYLOAD_CHANGE: dict[str, Any] = {
    "change_kind": "scalar_change",
    "comparison_window": {"left": _LEFT_WIN, "right": _RIGHT_WIN},
    "direction_of_interest": "decrease",
    "dimension_keys": None,
    "comparison_basis": "left_vs_right",  # NOT identity
    "unit": "users",  # NOT identity
}

_PAYLOAD_DECOMP: dict[str, Any] = {
    "dimension": "country",
    "dimension_keys": {"country": "US"},
    "contribution_role": "primary_driver",
    "scope_delta_ref": {"session_id": "sess_4e2", "finding_id": "fnd_4e2_delta_001"},
    "comparison_window": {"left": _LEFT_WIN, "right": _RIGHT_WIN},  # NOT identity
}

_PAYLOAD_ANOMALY: dict[str, Any] = {
    "anomaly_kind": "candidate",  # NOT identity
    "candidate_ref": {
        "artifact_id": "art_4e2_001",
        "item_ref": {"collection": "candidates", "index": None, "key": "0"},
    },
    "expected_behavior_ref": None,  # NOT identity
    "observed_window": {
        "kind": "range",
        "start": "2024-01-08",
        "end": "2024-01-09",
    },  # NOT identity
    "validation_goal": "validate_candidate",
}

_LEFT_SUB_CORR: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "correlation",
}
_RIGHT_SUB_CORR: dict[str, Any] = {
    "metric": "revenue",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "correlation",
}

_PAYLOAD_CORR: dict[str, Any] = {
    "left_subject": _LEFT_SUB_CORR,
    "right_subject": _RIGHT_SUB_CORR,
    "method_family": "pearson",
    "relationship_of_interest": "positive_association",
    "join_basis": {"kind": "time_aligned", "grain": "day", "key_fields": ["date"]},
    "aligned_window": {"kind": "range", "start": "2024-01-01", "end": "2024-01-31"},
}

_LEFT_SUB_TEST: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {"country": "US"},
    "grain": None,
    "analysis_axis": "test",
}
_RIGHT_SUB_TEST: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {"country": "CN"},
    "grain": None,
    "analysis_axis": "test",
}

_PAYLOAD_TEST: dict[str, Any] = {
    "hypothesis_family": "difference",
    "alternative": "two_sided",
    "left_subject": _LEFT_SUB_TEST,
    "right_subject": _RIGHT_SUB_TEST,
    "method_family": "welch_t",
    "alpha": 0.05,
    "hypothesis_label": None,  # NOT identity
}

_PAYLOAD_FORECAST: dict[str, Any] = {
    "forecast_kind": "point_forecast",  # NOT identity
    "forecast_window": {"kind": "range", "start": "2024-02-01", "end": "2024-02-02"},
    "horizon_index": 3,
    "expectation_direction": "open",
    "forecast_basis_ref": None,  # NOT identity
}


def _norm(
    proposition_type: str,
    payload: dict[str, Any],
    *,
    session_id: str = "sess_4e2",
    origin_kind: str = "system_seeded",
    derivation_version: str = "seed.change_from_delta.identity.v1",
    subject: dict[str, Any] | None = None,
) -> str:
    if subject is None:
        subject = _SUBJECT_CHANGE
    return normalize_proposition_identity(
        session_id=session_id,
        origin_kind=origin_kind,
        proposition_type=proposition_type,
        derivation_version=derivation_version,
        subject=subject,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# _canonical_decimal
# ---------------------------------------------------------------------------


class TestCanonicalDecimal(unittest.TestCase):
    def test_strips_trailing_zeros(self) -> None:
        self.assertEqual(_canonical_decimal(0.050), "0.05")

    def test_no_scientific_notation_small(self) -> None:
        self.assertEqual(_canonical_decimal(1e-4), "0.0001")

    def test_no_scientific_notation_large(self) -> None:
        result = _canonical_decimal(1e10)
        self.assertNotIn("e", result.lower())
        self.assertNotIn("E", result)

    def test_integer_float(self) -> None:
        self.assertEqual(_canonical_decimal(1.0), "1")

    def test_zero(self) -> None:
        self.assertEqual(_canonical_decimal(0.0), "0")

    def test_common_alpha(self) -> None:
        self.assertEqual(_canonical_decimal(0.05), "0.05")

    def test_0_01(self) -> None:
        self.assertEqual(_canonical_decimal(0.01), "0.01")

    def test_0_050_equals_0_05(self) -> None:
        # Python float 0.050 is the same object as 0.05
        self.assertEqual(_canonical_decimal(0.050), _canonical_decimal(0.05))


# ---------------------------------------------------------------------------
# _canonical_json
# ---------------------------------------------------------------------------


class TestCanonicalJson(unittest.TestCase):
    def test_dict_keys_sorted(self) -> None:
        j = _canonical_json({"z": 1, "a": 2, "m": 3})
        self.assertEqual(j, '{"a":2,"m":3,"z":1}')

    def test_nested_dict_keys_sorted(self) -> None:
        j = _canonical_json({"b": {"z": 1, "a": 2}})
        self.assertEqual(j, '{"b":{"a":2,"z":1}}')

    def test_float_emitted_as_canonical_decimal(self) -> None:
        j = _canonical_json({"alpha": 0.05})
        # Must be a JSON number, not a quoted string
        self.assertIn('"alpha":0.05', j)
        self.assertNotIn('"0.05"', j)

    def test_float_trailing_zeros_stripped(self) -> None:
        j = _canonical_json({"alpha": 0.050})
        self.assertIn('"alpha":0.05', j)

    def test_bool_true(self) -> None:
        self.assertEqual(_canonical_json(True), "true")

    def test_bool_false(self) -> None:
        self.assertEqual(_canonical_json(False), "false")

    def test_bool_not_int(self) -> None:
        # bool is a subclass of int; ensure they serialize differently
        self.assertNotEqual(_canonical_json(True), _canonical_json(1))
        self.assertEqual(_canonical_json(True), "true")
        self.assertEqual(_canonical_json(1), "1")

    def test_int(self) -> None:
        self.assertEqual(_canonical_json(3), "3")

    def test_none(self) -> None:
        self.assertEqual(_canonical_json(None), "null")

    def test_string_with_special_chars(self) -> None:
        j = _canonical_json({"k": 'say "hi"'})
        self.assertIn('\\"hi\\"', j)

    def test_list(self) -> None:
        j = _canonical_json([3, 1, 2])
        self.assertEqual(j, "[3,1,2]")

    def test_float_in_list(self) -> None:
        j = _canonical_json([0.05, 0.1])
        self.assertNotIn('"0.05"', j)
        self.assertIn("0.05", j)

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            _canonical_json(object())


# ---------------------------------------------------------------------------
# normalize_proposition_identity — stability and isolation
# ---------------------------------------------------------------------------


class TestNormalizePropositionIdentityStability(unittest.TestCase):
    def test_stable_across_calls(self) -> None:
        k1 = _norm("change", _PAYLOAD_CHANGE, subject=_SUBJECT_CHANGE)
        k2 = _norm("change", _PAYLOAD_CHANGE, subject=_SUBJECT_CHANGE)
        self.assertEqual(k1, k2)

    def test_returns_64_char_hex(self) -> None:
        k = _norm("change", _PAYLOAD_CHANGE, subject=_SUBJECT_CHANGE)
        self.assertEqual(len(k), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in k))

    def test_different_session_id(self) -> None:
        k1 = _norm("change", _PAYLOAD_CHANGE, session_id="sess_A")
        k2 = _norm("change", _PAYLOAD_CHANGE, session_id="sess_B")
        self.assertNotEqual(k1, k2)

    def test_origin_kind_partition(self) -> None:
        """system_seeded and agent_authored never share an identity_key."""
        k1 = _norm("change", _PAYLOAD_CHANGE, origin_kind="system_seeded")
        k2 = _norm("change", _PAYLOAD_CHANGE, origin_kind="agent_authored")
        self.assertNotEqual(k1, k2)

    def test_different_derivation_version(self) -> None:
        k1 = _norm(
            "change", _PAYLOAD_CHANGE, derivation_version="seed.change_from_delta.identity.v1"
        )
        k2 = _norm(
            "change", _PAYLOAD_CHANGE, derivation_version="seed.change_from_delta.identity.v2"
        )
        self.assertNotEqual(k1, k2)

    def test_unknown_proposition_type_raises(self) -> None:
        with self.assertRaises(KeyError):
            _norm("no_such_type", {})

    def test_schema_version_not_in_identity(self) -> None:
        """schema_version must not affect identity."""
        k1 = _norm("change", _PAYLOAD_CHANGE)
        # Verify stability (schema_version is not an input to normalize_proposition_identity)
        self.assertEqual(k1, _norm("change", _PAYLOAD_CHANGE))


# ---------------------------------------------------------------------------
# normalize_proposition_identity — per-type field isolation
# ---------------------------------------------------------------------------


class TestChangeIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_CHANGE, **override}
        return _norm(
            "change",
            p,
            subject=_SUBJECT_CHANGE,
            derivation_version="seed.change_from_delta.identity.v1",
        )

    def test_different_change_kind(self) -> None:
        k1 = self._k(change_kind="scalar_change")
        k2 = self._k(change_kind="segment_change")
        self.assertNotEqual(k1, k2)

    def test_different_direction(self) -> None:
        k1 = self._k(direction_of_interest="increase")
        k2 = self._k(direction_of_interest="decrease")
        self.assertNotEqual(k1, k2)

    def test_different_comparison_window(self) -> None:
        k1 = self._k(comparison_window={"left": _LEFT_WIN, "right": _RIGHT_WIN})
        other_right = {"kind": "range", "start": "2024-01-15", "end": "2024-01-21"}
        k2 = self._k(comparison_window={"left": _LEFT_WIN, "right": other_right})
        self.assertNotEqual(k1, k2)

    def test_dimension_keys_none_vs_dict(self) -> None:
        k1 = self._k(dimension_keys=None)
        k2 = self._k(dimension_keys={"country": "US"})
        self.assertNotEqual(k1, k2)

    def test_comparison_basis_not_in_identity(self) -> None:
        """comparison_basis must not affect identity."""
        k1 = self._k(comparison_basis="left_vs_right")
        k2 = self._k(comparison_basis="current_vs_baseline")
        self.assertEqual(k1, k2)

    def test_unit_not_in_identity(self) -> None:
        k1 = self._k(unit="users")
        k2 = self._k(unit=None)
        self.assertEqual(k1, k2)

    def test_different_metric_in_subject(self) -> None:
        s1 = {**_SUBJECT_CHANGE, "metric": "dau"}
        s2 = {**_SUBJECT_CHANGE, "metric": "revenue"}
        k1 = normalize_proposition_identity(
            session_id="sess_4e2",
            origin_kind="system_seeded",
            proposition_type="change",
            derivation_version="seed.change_from_delta.identity.v1",
            subject=s1,
            payload=_PAYLOAD_CHANGE,
        )
        k2 = normalize_proposition_identity(
            session_id="sess_4e2",
            origin_kind="system_seeded",
            proposition_type="change",
            derivation_version="seed.change_from_delta.identity.v1",
            subject=s2,
            payload=_PAYLOAD_CHANGE,
        )
        self.assertNotEqual(k1, k2)


class TestDecompositionIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_DECOMP, **override}
        return _norm(
            "decomposition",
            p,
            subject=_SUBJECT_DECOMP,
            derivation_version="seed.decomposition_from_item.identity.v1",
        )

    def test_different_scope_delta_ref(self) -> None:
        k1 = self._k(scope_delta_ref={"session_id": "sess_4e2", "finding_id": "fnd_delta_001"})
        k2 = self._k(scope_delta_ref={"session_id": "sess_4e2", "finding_id": "fnd_delta_002"})
        self.assertNotEqual(k1, k2)

    def test_different_contribution_role(self) -> None:
        k1 = self._k(contribution_role="primary_driver")
        k2 = self._k(contribution_role="secondary_driver")
        self.assertNotEqual(k1, k2)

    def test_comparison_window_not_in_identity(self) -> None:
        k1 = self._k(comparison_window={"left": _LEFT_WIN, "right": _RIGHT_WIN})
        other = {
            "left": _LEFT_WIN,
            "right": {"kind": "range", "start": "2024-02-01", "end": "2024-02-07"},
        }
        k2 = self._k(comparison_window=other)
        self.assertEqual(k1, k2)


class TestAnomalyIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_ANOMALY, **override}
        return _norm(
            "anomaly",
            p,
            subject=_SUBJECT_ANOMALY,
            derivation_version="seed.anomaly_from_candidate.identity.v1",
        )

    def test_different_candidate_ref(self) -> None:
        ref1 = {
            "artifact_id": "art_001",
            "item_ref": {"collection": "candidates", "index": None, "key": "0"},
        }
        ref2 = {
            "artifact_id": "art_001",
            "item_ref": {"collection": "candidates", "index": None, "key": "1"},
        }
        k1 = self._k(candidate_ref=ref1)
        k2 = self._k(candidate_ref=ref2)
        self.assertNotEqual(k1, k2)

    def test_different_validation_goal(self) -> None:
        k1 = self._k(validation_goal="validate_candidate")
        k2 = self._k(validation_goal="rule_out_noise")
        self.assertNotEqual(k1, k2)

    def test_anomaly_kind_not_in_identity(self) -> None:
        k1 = self._k(anomaly_kind="candidate")
        k2 = self._k(anomaly_kind="point_anomaly")
        self.assertEqual(k1, k2)

    def test_observed_window_not_in_identity(self) -> None:
        w1 = {"kind": "range", "start": "2024-01-08", "end": "2024-01-09"}
        w2 = {"kind": "range", "start": "2024-01-15", "end": "2024-01-16"}
        k1 = self._k(observed_window=w1)
        k2 = self._k(observed_window=w2)
        self.assertEqual(k1, k2)


class TestCorrelationIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_CORR, **override}
        return _norm(
            "correlation",
            p,
            subject=_SUBJECT_CORR,
            derivation_version="seed.correlation_from_result.identity.v1",
        )

    def test_different_right_subject(self) -> None:
        rs1 = {**_RIGHT_SUB_CORR, "metric": "revenue"}
        rs2 = {**_RIGHT_SUB_CORR, "metric": "orders"}
        k1 = self._k(right_subject=rs1)
        k2 = self._k(right_subject=rs2)
        self.assertNotEqual(k1, k2)

    def test_different_relationship(self) -> None:
        k1 = self._k(relationship_of_interest="positive_association")
        k2 = self._k(relationship_of_interest="negative_association")
        self.assertNotEqual(k1, k2)

    def test_different_join_basis(self) -> None:
        jb1 = {"kind": "time_aligned", "grain": "day", "key_fields": ["date"]}
        jb2 = {"kind": "shared_key", "key_fields": ["user_id"], "grain": None}
        k1 = self._k(join_basis=jb1)
        k2 = self._k(join_basis=jb2)
        self.assertNotEqual(k1, k2)

    def test_different_aligned_window(self) -> None:
        w1 = {"kind": "range", "start": "2024-01-01", "end": "2024-01-31"}
        w2 = {"kind": "range", "start": "2024-02-01", "end": "2024-02-29"}
        k1 = self._k(aligned_window=w1)
        k2 = self._k(aligned_window=w2)
        self.assertNotEqual(k1, k2)


class TestTestHypothesisIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_TEST, **override}
        return _norm(
            "test_hypothesis",
            p,
            subject=_SUBJECT_TEST,
            derivation_version="seed.test_hypothesis_from_result.identity.v1",
        )

    def test_alpha_canonical_decimal(self) -> None:
        """0.05 and 0.050 must hash identically (canonical decimal)."""
        k1 = self._k(alpha=0.05)
        k2 = self._k(alpha=0.050)
        self.assertEqual(k1, k2)

    def test_alpha_none(self) -> None:
        """alpha=None is a supported value."""
        k = self._k(alpha=None)
        self.assertEqual(len(k), 64)

    def test_alpha_none_vs_float(self) -> None:
        k1 = self._k(alpha=None)
        k2 = self._k(alpha=0.05)
        self.assertNotEqual(k1, k2)

    def test_different_alternative(self) -> None:
        k1 = self._k(alternative="two_sided")
        k2 = self._k(alternative="greater")
        self.assertNotEqual(k1, k2)

    def test_different_method_family(self) -> None:
        k1 = self._k(method_family="welch_t")
        k2 = self._k(method_family="spearman")
        self.assertNotEqual(k1, k2)

    def test_hypothesis_label_not_in_identity(self) -> None:
        k1 = self._k(hypothesis_label=None)
        k2 = self._k(hypothesis_label="users differ by country")
        self.assertEqual(k1, k2)


class TestForecastIdentity(unittest.TestCase):
    def _k(self, **override: Any) -> str:
        p = {**_PAYLOAD_FORECAST, **override}
        return _norm(
            "forecast",
            p,
            subject=_SUBJECT_FORECAST,
            derivation_version="seed.forecast_from_point.identity.v1",
        )

    def test_different_horizon_index(self) -> None:
        k1 = self._k(horizon_index=1)
        k2 = self._k(horizon_index=7)
        self.assertNotEqual(k1, k2)

    def test_horizon_index_none(self) -> None:
        k1 = self._k(horizon_index=None)
        k2 = self._k(horizon_index=3)
        self.assertNotEqual(k1, k2)

    def test_same_forecast_window_stable(self) -> None:
        w = {"kind": "range", "start": "2024-02-01", "end": "2024-02-02"}
        k1 = self._k(forecast_window=w)
        k2 = self._k(forecast_window=w)
        self.assertEqual(k1, k2)

    def test_different_forecast_window(self) -> None:
        w1 = {"kind": "range", "start": "2024-02-01", "end": "2024-02-02"}
        w2 = {"kind": "range", "start": "2024-02-03", "end": "2024-02-04"}
        k1 = self._k(forecast_window=w1)
        k2 = self._k(forecast_window=w2)
        self.assertNotEqual(k1, k2)

    def test_different_expectation_direction(self) -> None:
        k1 = self._k(expectation_direction="open")
        k2 = self._k(expectation_direction="increase")
        self.assertNotEqual(k1, k2)

    def test_forecast_kind_not_in_identity(self) -> None:
        k1 = self._k(forecast_kind="point_forecast")
        k2 = self._k(forecast_kind="interval_forecast")
        self.assertEqual(k1, k2)

    def test_forecast_basis_ref_not_in_identity(self) -> None:
        k1 = self._k(forecast_basis_ref=None)
        k2 = self._k(forecast_basis_ref={"session_id": "sess_4e2", "finding_id": "fnd_xyz"})
        self.assertEqual(k1, k2)


# ---------------------------------------------------------------------------
# make_proposition_id
# ---------------------------------------------------------------------------


class TestMakePropositionId(unittest.TestCase):
    def test_prefix(self) -> None:
        k = "a" * 64
        self.assertTrue(make_proposition_id(k).startswith("prop_"))

    def test_length(self) -> None:
        k = "a" * 64
        pid = make_proposition_id(k)
        # "prop_" (5) + 24 hex chars = 29 total
        self.assertEqual(len(pid), 29)

    def test_stable(self) -> None:
        k = "deadbeef" * 8
        self.assertEqual(make_proposition_id(k), make_proposition_id(k))

    def test_different_identity_keys(self) -> None:
        k1 = "a" * 64
        k2 = "b" * 64
        self.assertNotEqual(make_proposition_id(k1), make_proposition_id(k2))

    def test_derived_from_normalize(self) -> None:
        ik = _norm("change", _PAYLOAD_CHANGE, subject=_SUBJECT_CHANGE)
        pid = make_proposition_id(ik)
        self.assertTrue(pid.startswith("prop_"))
        self.assertEqual(pid, f"prop_{ik[:24]}")


if __name__ == "__main__":
    unittest.main()
