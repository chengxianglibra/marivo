from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.runtime.intents.diagnose import run_diagnose_intent
from tests.semantic_test_helpers import (
    build_semantic_layer_service,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path

_SPIKE_METRIC = "detect_event_count"
_UNIFORM_METRIC = "uniform_event_count"
_START = "2026-01-01"
_END = "2026-01-15"
_SPIKE_START = "2026-01-08"
_SPIKE_END = "2026-01-09"
_BASELINE_START = "2026-01-07"
_BASELINE_END = "2026-01-08"
_FILTER_ALPHA = {"dialects": [{"dialect": "ANSI_SQL", "expression": "cluster = 'alpha'"}]}


@dataclass
class DiagnoseEnv:
    service: Any
    metadata: SQLiteMetadataStore


@pytest.fixture(scope="module")
def diagnose_env(tmp_path_factory: pytest.TempPathFactory) -> DiagnoseEnv:
    root = tmp_path_factory.mktemp("diagnose_runner")
    db_path = root / "diagnose.duckdb"
    meta_path = root / "diagnose.meta.sqlite"

    get_named_seeded_duckdb_path(db_path, "detect_intent")
    analytics = DuckDBAnalyticsEngine(str(db_path))
    metadata = SQLiteMetadataStore(str(meta_path))
    metadata.initialize()
    analytics.initialize()
    _seed_detect_metadata(metadata, db_path)

    return DiagnoseEnv(
        service=build_semantic_layer_service(metadata, analytics),
        metadata=metadata,
    )


def _seed_detect_metadata(meta: SQLiteMetadataStore, db_path: Path) -> None:
    _seed_metric_metadata(
        meta,
        db_path=db_path,
        source_id="ds_detect_diag_01",
        object_id="obj_detect_diag_01",
        metric_name=_SPIKE_METRIC,
        table_name="detect_events",
        table_fqn="analytics.detect_events",
    )
    _seed_metric_metadata(
        meta,
        db_path=db_path,
        source_id="ds_detect_diag_02",
        object_id="obj_detect_diag_02",
        metric_name=_UNIFORM_METRIC,
        table_name="uniform_events",
        table_fqn="analytics.uniform_events",
    )


def _seed_metric_metadata(
    meta: SQLiteMetadataStore,
    *,
    db_path: Path,
    source_id: str,
    object_id: str,
    metric_name: str,
    table_name: str,
    table_fqn: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    seed_duckdb_source_object(
        meta,
        source_id=source_id,
        object_id=object_id,
        display_name=f"{metric_name} source",
        table_name=table_name,
        table_fqn=table_fqn,
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=["event_date", "cluster"],
        definition_sql="COUNT(*)",
        measure_type="sum",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator=table_fqn,
        source_object_ref=object_id,
        dimension_names=["event_date", "cluster"],
    )


def _make_session(env: DiagnoseEnv, goal: str = "diagnose test") -> str:
    state = env.service.create_session(goal, actor="test_user")
    if isinstance(state, dict):
        return str(state["session_id"])
    return str(state.session_id)


def _auto_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "metric": _SPIKE_METRIC,
        "time_scope": {"field": "event_date", "start": _START, "end": _END},
        "granularity": "day",
        "candidate_dimensions": ["cluster"],
        "strategy": "point_anomaly",
        "sensitivity": "balanced",
        "followup_limit": 1,
        "decomposition_limit": 5,
    }
    params.update(overrides)
    return params


def _explicit_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": "explicit_compare",
        "metric": _SPIKE_METRIC,
        "current": {
            "time_scope": {
                "field": "event_date",
                "start": _SPIKE_START,
                "end": _SPIKE_END,
            }
        },
        "baseline": {
            "time_scope": {
                "field": "event_date",
                "start": _BASELINE_START,
                "end": _BASELINE_END,
            }
        },
        "candidate_dimensions": ["cluster"],
        "strategy": "point_anomaly",
        "decomposition_limit": 5,
    }
    params.update(overrides)
    return params


def _result(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle["result"]


def _product(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle["product_metadata"]


def _step_types(env: DiagnoseEnv, session_id: str) -> list[str]:
    rows = env.metadata.query_rows(
        "SELECT step_type FROM steps WHERE session_id = ?",
        [session_id],
    )
    return [str(row["step_type"]) for row in rows]


def test_auto_detect_follows_detect_artifact_candidates_and_builds_full_chain(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "auto diagnose")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _auto_params(detect_dimension="cluster", candidate_limit=1),
    )

    result = _result(bundle)
    product = _product(bundle)
    diagnosis = result["diagnoses"][0]
    driver = diagnosis["drivers"][0]

    assert product["validation"]["status"] == "diagnosable"
    assert result["mode"] == "auto_detect"
    assert result["granularity"] == "day"
    assert result["detect_dimension"] == "cluster"
    assert result["candidate_dimensions"] == ["cluster"]
    assert result["strategy"] == "point_anomaly"
    assert result["sensitivity"] == "balanced"
    assert result["detect_summary"]["returned_candidate_count"] == 1
    assert result["detect_summary"]["followed_candidate_count"] == 1
    assert result["detect_summary"]["truncated"] is False
    assert diagnosis["candidate"]["slice"] == {"cluster": "alpha"}
    assert diagnosis["baseline_derivation"]["baseline_window"] == {
        "start": _BASELINE_START,
        "end": _BASELINE_END,
    }
    assert diagnosis["status"] == "diagnosed"
    assert diagnosis["comparison"]["absolute_delta"] == 400.0
    assert driver["dimension"] == "cluster"
    assert driver["attribution_status"] == "attributable"
    assert driver["rows"][0]["key"] == "alpha"
    assert driver["rows"][0]["absolute_contribution"] == 400.0

    step_types = _step_types(diagnose_env, session_id)
    assert step_types.count("detect") == 1
    assert step_types.count("observe") == 2
    assert step_types.count("compare") == 1
    assert step_types.count("decompose") == 1
    assert step_types.count("diagnose") == 1


def test_auto_detect_filter_is_applied_to_detect_and_followup(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "auto diagnose filter")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _auto_params(filter=_FILTER_ALPHA),
    )

    result = _result(bundle)
    diagnosis = result["diagnoses"][0]
    driver = diagnosis["drivers"][0]

    assert result["scope"] == {"predicate": "cluster = 'alpha'"}
    assert diagnosis["comparison"]["left_value"] == 500.0
    assert diagnosis["comparison"]["right_value"] == 100.0
    assert [row["key"] for row in driver["rows"]] == ["alpha"]


def test_explicit_compare_uses_slices_without_detect_step_and_builds_drivers(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "explicit diagnose")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _explicit_params(),
    )

    result = _result(bundle)
    diagnosis = result["diagnoses"][0]
    driver = diagnosis["drivers"][0]

    assert _product(bundle)["validation"]["status"] == "diagnosable"
    assert result["mode"] == "explicit_compare"
    assert result["detect_summary"] is None
    assert diagnosis["candidate"]["candidate_type"] == "explicit_compare"
    assert diagnosis["current_ref"]["step_type"] == "observe"
    assert diagnosis["baseline_ref"]["step_type"] == "observe"
    assert diagnosis["compare_ref"]["step_type"] == "compare"
    assert diagnosis["comparison"]["left_value"] == 600.0
    assert diagnosis["comparison"]["right_value"] == 200.0
    assert driver["decompose_ref"]["step_type"] == "decompose"
    assert driver["returned_row_count"] == 2
    assert driver["total_row_count"] == 2
    assert driver["is_truncated"] is False
    assert driver["issues"] == []

    step_types = _step_types(diagnose_env, session_id)
    assert "detect" not in step_types
    assert step_types.count("observe") == 2
    assert step_types.count("compare") == 1
    assert step_types.count("decompose") == 1
    assert step_types.count("diagnose") == 1


def test_explicit_compare_preserves_matching_slice_filters(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "explicit diagnose filters")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _explicit_params(
            current={
                "time_scope": {
                    "field": "event_date",
                    "start": _SPIKE_START,
                    "end": _SPIKE_END,
                },
                "filter": _FILTER_ALPHA,
            },
            baseline={
                "time_scope": {
                    "field": "event_date",
                    "start": _BASELINE_START,
                    "end": _BASELINE_END,
                },
                "filter": _FILTER_ALPHA,
            },
        ),
    )

    diagnosis = _result(bundle)["diagnoses"][0]
    driver = diagnosis["drivers"][0]

    assert diagnosis["comparison"]["left_value"] == 500.0
    assert diagnosis["comparison"]["right_value"] == 100.0
    assert driver["rows"][0]["key"] == "alpha"
    assert driver["rows"][0]["contribution_share"] == 1.0


def test_decomposition_limit_truncates_driver_rows(diagnose_env: DiagnoseEnv) -> None:
    session_id = _make_session(diagnose_env, "explicit diagnose truncation")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _explicit_params(decomposition_limit=1),
    )

    driver = _result(bundle)["diagnoses"][0]["drivers"][0]

    assert driver["returned_row_count"] == 1
    assert driver["total_row_count"] == 2
    assert driver["is_truncated"] is True
    assert driver["rows"][0]["key"] == "alpha"
    assert driver["others_absolute_contribution"] == 0.0
    assert driver["issues"] == []


def test_duplicate_candidate_dimensions_are_deduped(diagnose_env: DiagnoseEnv) -> None:
    session_id = _make_session(diagnose_env, "diagnose dedupe")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _explicit_params(candidate_dimensions=["cluster", "cluster"]),
    )

    result = _result(bundle)

    assert result["candidate_dimensions"] == ["cluster"]
    assert len(result["diagnoses"][0]["drivers"]) == 1


def test_auto_detect_no_candidates_returns_needs_attention(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "diagnose no candidates")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _auto_params(metric=_UNIFORM_METRIC),
    )

    result = _result(bundle)
    validation = _product(bundle)["validation"]

    assert result["diagnoses"] == []
    assert result["detect_summary"]["total_candidate_count"] == 0
    assert validation["status"] == "needs_attention"
    assert {issue["code"] for issue in validation["issues"]} == {"no_detect_candidates"}
    assert validation["guidance"]["recommended_next_action"] == (
        "use_explicit_compare_or_expand_scan"
    )


def test_detect_needs_attention_guidance_is_propagated(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "diagnose insufficient detect")

    bundle = run_diagnose_intent(
        diagnose_env.service,
        session_id,
        _auto_params(
            time_scope={"field": "event_date", "start": "2026-01-01", "end": "2026-01-03"}
        ),
    )

    validation = _product(bundle)["validation"]

    assert validation["status"] == "needs_attention"
    assert {"detect_needs_attention", "no_detect_candidates"} == {
        issue["code"] for issue in validation["issues"]
    }
    assert validation["guidance"]["recommended_next_action"] == "expand_scan_window"
    assert "explicit_compare_fallback" in validation["guidance"]


def test_followup_limit_truncation_is_reported_from_detect_artifact_payload() -> None:
    runtime = MagicMock()
    runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: f"metric.{metric}"
    runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
    runtime.insert_artifact.return_value = "art_diag_bundle"
    runtime.insert_step.return_value = None

    candidates = [
        {
            "candidate_ref": {"item_ref": {"collection": "candidates", "index": idx}},
            "window": {"start": f"2026-01-0{idx + 2}", "end": f"2026-01-0{idx + 3}"},
            "slice": None,
            "candidate_score": 10.0 - idx,
        }
        for idx in range(2)
    ]
    detect_result = {
        "step_ref": {"session_id": "sess_trunc", "step_id": "step_detect", "step_type": "detect"},
        "artifact_id": "art_detect",
        "result": {
            "artifact_id": "art_detect",
            "detectability": {"status": "detectable", "issues": [], "guidance": None},
            "scan_summary": {"total_candidate_count": 2},
            "candidates": candidates,
        },
    }

    with (
        patch("marivo.runtime.intents.diagnose.run_detect_intent", return_value=detect_result),
        patch(
            "marivo.runtime.intents.diagnose._follow_up_candidate",
            return_value={"status": "diagnosed", "issues": []},
        ) as follow_up,
    ):
        bundle = run_diagnose_intent(
            runtime,
            "sess_trunc",
            _auto_params(metric="mock_metric", followup_limit=1),
        )

    summary = _result(bundle)["detect_summary"]
    validation = _product(bundle)["validation"]

    assert summary["returned_candidate_count"] == 2
    assert summary["followed_candidate_count"] == 1
    assert summary["truncated"] is True
    assert any(issue["code"] == "candidate_followup_truncated" for issue in validation["issues"])
    follow_up.assert_called_once()


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {"metric": "", "candidate_dimensions": ["cluster"], "strategy": "point_anomaly"},
            "metric",
        ),
        (_auto_params(candidate_dimensions=[]), "candidate_dimensions"),
        (_auto_params(granularity="quarter"), "granularity"),
        (_auto_params(strategy="unknown"), "strategy"),
        (_auto_params(sensitivity="wild"), "sensitivity"),
        (_auto_params(candidate_limit=0), "candidate_limit"),
        (_auto_params(followup_limit=0), "followup_limit"),
        (_auto_params(followup_limit=11), "followup_limit"),
        (_auto_params(decomposition_limit=0), "decomposition_limit"),
        (_auto_params(decomposition_limit=101), "decomposition_limit"),
        (_explicit_params(current=None), "current and baseline"),
        (
            _explicit_params(time_scope={"field": "event_date", "start": _START, "end": _END}),
            "time_scope",
        ),
        (_explicit_params(filter=_FILTER_ALPHA), "filter"),
        (_explicit_params(candidate_limit=1), "candidate_limit"),
        (
            _auto_params(
                current={"time_scope": {"field": "event_date", "start": _START, "end": _END}}
            ),
            "current",
        ),
        (_auto_params(scope={"constraints": {"cluster": "alpha"}}), "unsupported parameter"),
        (_auto_params(baseline_policy="previous_adjacent_equal_length"), "unsupported parameter"),
        (
            _explicit_params(
                current={
                    "time_scope": {
                        "field": "event_date",
                        "start": _SPIKE_START,
                        "end": _SPIKE_END,
                    },
                    "scope": {"constraints": {"cluster": "alpha"}},
                }
            ),
            "unsupported current field",
        ),
    ],
)
def test_diagnose_rejects_invalid_and_legacy_runner_inputs(
    diagnose_env: DiagnoseEnv,
    params: dict[str, Any],
    message: str,
) -> None:
    session_id = _make_session(diagnose_env, "diagnose invalid")

    with pytest.raises(ValueError, match=message):
        run_diagnose_intent(diagnose_env.service, session_id, params)


def test_explicit_compare_rejects_mismatched_slice_filters(
    diagnose_env: DiagnoseEnv,
) -> None:
    session_id = _make_session(diagnose_env, "explicit mismatched filters")

    with pytest.raises(ValueError, match=r"current\.scope and baseline\.scope must match"):
        run_diagnose_intent(
            diagnose_env.service,
            session_id,
            _explicit_params(
                current={
                    "time_scope": {
                        "field": "event_date",
                        "start": _SPIKE_START,
                        "end": _SPIKE_END,
                    },
                    "filter": _FILTER_ALPHA,
                }
            ),
        )


def test_hour_candidate_followup_preserves_hour_windows_for_compare() -> None:
    runtime = MagicMock()
    runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: f"metric.{metric}"
    runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
    runtime.insert_artifact.return_value = "art_diag_hour_bundle"
    runtime.insert_step.return_value = None

    detect_result = {
        "step_ref": {
            "session_id": "sess_diag_hour",
            "step_id": "step_detect_hour",
            "step_type": "detect",
        },
        "artifact_id": "art_detect_hour",
        "result": {
            "artifact_id": "art_detect_hour",
            "detectability": {"status": "detectable", "issues": [], "guidance": None},
            "scan_summary": {"total_candidate_count": 1},
            "candidates": [
                {
                    "candidate_ref": {"item_ref": {"collection": "candidates", "index": 0}},
                    "window": {
                        "start": "2026-04-09T14:00:00",
                        "end": "2026-04-09T15:00:00",
                    },
                    "candidate_score": 99.0,
                }
            ],
        },
    }
    observe_results = [
        {
            "step_ref": {
                "session_id": "sess_diag_hour",
                "step_id": "step_obs_current",
                "step_type": "observe",
            },
            "artifact_id": "art_obs_current",
        },
        {
            "step_ref": {
                "session_id": "sess_diag_hour",
                "step_id": "step_obs_baseline",
                "step_type": "observe",
            },
            "artifact_id": "art_obs_baseline",
        },
    ]
    compare_result = {
        "step_ref": {
            "session_id": "sess_diag_hour",
            "step_id": "step_compare",
            "step_type": "compare",
        },
        "artifact_id": "art_compare",
        "left_value": 29.0,
        "right_value": 3.0,
        "absolute_delta": 26.0,
        "relative_delta": 8.6,
        "direction": "up",
        "comparability": {"status": "comparable", "issues": []},
    }
    decompose_result = {
        "step_ref": {
            "session_id": "sess_diag_hour",
            "step_id": "step_decompose",
            "step_type": "decompose",
        },
        "artifact_id": "art_decompose",
        "attribution": {"status": "attributable", "issues": []},
        "rows": [{"key": "rg_a", "absolute_contribution": 26.0}],
    }

    with (
        patch("marivo.runtime.intents.diagnose.run_detect_intent", return_value=detect_result),
        patch(
            "marivo.runtime.intents.diagnose.run_observe_intent", side_effect=observe_results
        ) as observe,
        patch(
            "marivo.runtime.intents.diagnose.run_compare_intent", return_value=compare_result
        ) as compare,
        patch(
            "marivo.runtime.intents.diagnose.run_decompose_intent", return_value=decompose_result
        ),
    ):
        bundle = run_diagnose_intent(
            runtime,
            "sess_diag_hour",
            {
                "metric": "trino_elapsed_seconds_p95",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-04-09T00:00:00",
                    "end": "2026-04-10T00:00:00",
                },
                "granularity": "hour",
                "candidate_dimensions": ["trino_resource_group"],
                "strategy": "point_anomaly",
                "followup_limit": 1,
            },
        )

    assert _product(bundle)["validation"]["status"] == "diagnosable"
    assert _result(bundle)["diagnoses"][0]["status"] == "diagnosed"
    assert observe.call_args_list[0].args[2]["time_scope"] == {
        "kind": "range",
        "start": "2026-04-09T14:00:00",
        "end": "2026-04-09T15:00:00",
        "field": "event_time",
    }
    assert compare.call_args.args[2] == {
        "left_artifact_id": "art_obs_current",
        "right_artifact_id": "art_obs_baseline",
    }


class TestCombineScope:
    def _fn(self, base: dict[str, Any] | None, slc: dict[str, Any] | None) -> dict[str, Any] | None:
        from marivo.runtime.intents.diagnose import _combine_scope

        return _combine_scope(base, slc)

    def test_null_slice_returns_base_scope(self) -> None:
        base = {"constraints": {"region": "US"}}

        assert self._fn(base, None) is base

    def test_slice_merges_into_constraints_and_preserves_predicate(self) -> None:
        predicate = "region = 'US'"
        result = self._fn(
            {"constraints": {"channel": "organic"}, "predicate": predicate}, {"channel": "paid"}
        )

        assert result == {"constraints": {"channel": "paid"}, "predicate": predicate}

    def test_null_base_scope_with_slice(self) -> None:
        assert self._fn(None, {"channel": "paid"}) == {"constraints": {"channel": "paid"}}
