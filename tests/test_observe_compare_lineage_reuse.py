"""Integration tests for observe -> compare predicate lineage reuse (task 7.5).

Verifies that compare-like intents reuse frozen observation lineage instead of
recalculating predicate semantics.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.intents.predicate_lineage_reuse import assert_predicate_lineage_refs_only
from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _insert_observe_artifact(
    service: Any,
    *,
    session_id: str,
    step_id: str,
    metric: str,
    observation_type: str,
    time_scope: dict[str, object],
    value: float | None = None,
    unit: str | None = None,
    predicate_filter_lineage: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "intent_type": "observe",
        "observation_type": observation_type,
        "metric": metric,
        "time_scope": time_scope,
        "scope": {},
        "unit": unit,
        "analytical_metadata": {
            "quality_status": "ready",
            "aggregation_semantics": "sum",
            "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
            "row_count": 1,
        },
        "execution_metadata": {
            "query_hash": "test",
            "engine": "duckdb",
            "executed_at": "2026-01-01T00:00:00",
        },
    }
    if observation_type == "scalar":
        payload["value"] = value
    if predicate_filter_lineage is not None:
        payload["predicate_filter_lineage"] = predicate_filter_lineage
    artifact_id = service.insert_artifact(
        session_id,
        step_id,
        "observation",
        f"{metric}_{observation_type}",
        payload,
    )
    result = {
        "intent_type": "observe",
        "step_type": "observe",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "observe",
        },
        "artifact_id": artifact_id,
        **payload,
    }
    service.insert_step(
        step_id,
        session_id,
        "observe",
        f"seeded observe {metric}",
        result,
        provenance={"seeded": True},
    )
    return artifact_id


def _make_lineage(
    *,
    gov_refs: list[str] | None = None,
    carrier_refs: list[str] | None = None,
    request_scope_ref: str | None = None,
    default_refs: list[str] | None = None,
    component_lineages: list[dict[str, Any]] | None = None,
    component_scopes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    shared: dict[str, Any] = {
        "carrier_row_filter_refs": carrier_refs or [],
    }
    if request_scope_ref is not None:
        shared["request_scope_ref"] = request_scope_ref
    return {
        "shared_effective_scope": shared,
        "metric_default_lineage": {"default_predicate_refs": default_refs or []},
        "component_qualifier_lineages": component_lineages or [],
        "component_effective_scopes": component_scopes or [],
    }


def _make_component_scope(
    component_field: str,
    *,
    effective_scope_refs: list[str] | None = None,
    fingerprint: str = "abcd1234efgh5678",
) -> dict[str, Any]:
    return {
        "component_field": component_field,
        "effective_scope_refs": effective_scope_refs or [],
        "scope_fingerprint": fingerprint,
    }


def _make_component_lineage(
    component_field: str,
    *,
    qualifier_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "component_field": component_field,
        "qualifier_refs": qualifier_refs or [],
    }


class _CompareReuseTestBase(unittest.TestCase):
    """Base class with shared setup for compare lineage reuse tests."""

    client: TestClient
    session_id: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / f"{cls.__name__.lower()}.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.client = TestClient(cls.app)

        resp = cls.client.post(
            "/sessions",
            json={"goal": f"{cls.__name__} session", "budget": {}, "policy": {}},
        )
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _service(self) -> Any:
        return self.client.app.state.services.runtime

    def _compare(self, left_step_id: str, right_step_id: str) -> dict[str, Any]:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ---------------------------------------------------------------------------
# Compare reuses frozen observation lineage
# ---------------------------------------------------------------------------


class TestCompareReusesObservationLineage(_CompareReuseTestBase):
    """Verify that compare artifacts contain reused predicate lineage from observations."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        svc = cls.client.app.state.services.runtime
        lineage = _make_lineage(
            carrier_refs=["predicate.car1"],
            default_refs=["predicate.d1"],
            component_lineages=[_make_component_lineage("count_target")],
            component_scopes=[
                _make_component_scope("count_target", fingerprint="aaaa1111bbbb2222")
            ],
        )
        cls.left_step_id = f"step_left_{cls.__name__.lower()}"
        cls.right_step_id = f"step_right_{cls.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.left_step_id,
            metric="metric.reuse_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=100.0,
            unit="users",
            predicate_filter_lineage=lineage,
        )
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.right_step_id,
            metric="metric.reuse_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            value=80.0,
            unit="users",
            predicate_filter_lineage=lineage,
        )

    def test_compare_artifact_contains_predicate_lineage_in_resolved_input_summary(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        summary = result.get("resolved_input_summary", {})
        self.assertIn("predicate_lineage", summary)
        self.assertIsNotNone(summary["predicate_lineage"])

    def test_compare_reuse_source_is_observation_lineage(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        self.assertEqual(pl["reuse_source"], "observation_predicate_filter_lineage")

    def test_compare_does_not_recalculate_predicate_semantics(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        assert_predicate_lineage_refs_only(pl, surface="compare_resolved_input_summary")

    def test_compare_fingerprints_match_observations(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        expected_fp = "aaaa1111bbbb2222"
        self.assertEqual(pl["left_scope_fingerprints"].get("count_target"), expected_fp)
        self.assertEqual(pl["right_scope_fingerprints"].get("count_target"), expected_fp)

    def test_compare_default_refs_match_observations(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        self.assertEqual(pl["metric_default_predicate_refs"], ["predicate.d1"])


# ---------------------------------------------------------------------------
# Compare with multi-component (rate metric) lineage
# ---------------------------------------------------------------------------


class TestCompareMultiComponentLineageReuse(_CompareReuseTestBase):
    """Verify compare reuses multi-component lineage from rate metric observations."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        svc = cls.client.app.state.services.runtime
        lineage = _make_lineage(
            carrier_refs=["predicate.car1"],
            default_refs=["predicate.d1"],
            component_lineages=[
                _make_component_lineage("numerator", qualifier_refs=["predicate.q_num"]),
                _make_component_lineage("denominator"),
            ],
            component_scopes=[
                _make_component_scope("numerator", fingerprint="num_fp_11112222"),
                _make_component_scope("denominator", fingerprint="den_fp_33334444"),
            ],
        )
        cls.left_step_id = f"step_mc_left_{cls.__name__.lower()}"
        cls.right_step_id = f"step_mc_right_{cls.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.left_step_id,
            metric="metric.rate_reuse_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=0.75,
            unit="ratio",
            predicate_filter_lineage=lineage,
        )
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.right_step_id,
            metric="metric.rate_reuse_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            value=0.60,
            unit="ratio",
            predicate_filter_lineage=lineage,
        )

    def test_compare_with_rate_metric_reuses_lineage(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        self.assertIn("numerator", pl["component_fields"])
        self.assertIn("denominator", pl["component_fields"])

    def test_compare_rate_metric_fingerprints_match_observations(self) -> None:
        result = self._compare(self.left_step_id, self.right_step_id)
        pl = result["resolved_input_summary"]["predicate_lineage"]
        self.assertEqual(pl["left_scope_fingerprints"]["numerator"], "num_fp_11112222")
        self.assertEqual(pl["left_scope_fingerprints"]["denominator"], "den_fp_33334444")
        self.assertEqual(pl["right_scope_fingerprints"]["numerator"], "num_fp_11112222")
        self.assertEqual(pl["right_scope_fingerprints"]["denominator"], "den_fp_33334444")


# ---------------------------------------------------------------------------
# Compare rejects lineage mismatch
# ---------------------------------------------------------------------------


class TestCompareLineageMismatchRejection(_CompareReuseTestBase):
    """Verify compare rejects observations with incompatible lineage metadata."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        svc = cls.client.app.state.services.runtime
        # Left: has lineage
        cls.left_lineage = _make_lineage(
            carrier_refs=["predicate.car1"],
            default_refs=["predicate.d1"],
        )
        cls.left_step_id = f"step_mismatch_left_{cls.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.left_step_id,
            metric="metric.mismatch_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=100.0,
            unit="users",
            predicate_filter_lineage=cls.left_lineage,
        )
        # Right: no lineage
        cls.right_step_id = f"step_mismatch_right_{cls.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=cls.session_id,
            step_id=cls.right_step_id,
            metric="metric.mismatch_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            value=80.0,
            unit="users",
            # No predicate_filter_lineage
        )

    def test_compare_rejects_lineage_metadata_mismatch(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("NOT_COMPARABLE", r.text)
        self.assertIn("predicate filter lineage is missing", r.text)

    def test_compare_warns_on_scope_divergence_but_succeeds(self) -> None:
        svc = self.client.app.state.services.runtime
        # Left: carrier car1
        left_lineage = _make_lineage(carrier_refs=["predicate.car1"])
        left_id = f"step_div_left_{self.__class__.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=self.session_id,
            step_id=left_id,
            metric="metric.diverge_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=100.0,
            unit="users",
            predicate_filter_lineage=left_lineage,
        )
        # Right: carrier car2 (different)
        right_lineage = _make_lineage(carrier_refs=["predicate.car2"])
        right_id = f"step_div_right_{self.__class__.__name__.lower()}"
        _insert_observe_artifact(
            svc,
            session_id=self.session_id,
            step_id=right_id,
            metric="metric.diverge_test",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            value=80.0,
            unit="users",
            predicate_filter_lineage=right_lineage,
        )
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()
        comparability = result.get("comparability", {})
        self.assertEqual(comparability.get("status"), "needs_attention")
        issue_codes = [i["code"] for i in comparability.get("issues", [])]
        self.assertIn("scope_divergence", issue_codes)


if __name__ == "__main__":
    unittest.main()
