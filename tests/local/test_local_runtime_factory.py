from __future__ import annotations

from pathlib import Path

import pytest

from marivo.contracts.ids import SessionId
from marivo.profiles.local import LocalConfig, create_local_runtime


def test_creates_runtime_with_all_ports(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    assert runtime is not None
    assert runtime._ports is not None
    assert runtime._ports.model_store is not None
    assert runtime._ports.session_store is not None
    assert runtime._ports.evidence_store is not None
    assert runtime._ports.data_source is not None
    assert runtime.evidence_repos is not None
    assert {
        "finding_repo",
        "proposition_repo",
        "assessment_repo",
        "gap_repo",
        "inference_record_repo",
        "proposal_repo",
    }.issubset(runtime.evidence_repos)


def test_runtime_creates_session(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    state = runtime.create_session(goal="test")
    assert state is not None
    assert state.session_id.startswith("sess_")


def test_runtime_datasource_service_is_usable(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    ds_service = runtime.get_service("datasource")
    datasources = ds_service.list_datasources()
    assert isinstance(datasources, list)


def test_runtime_wires_calendar_data_service_and_reader(tmp_path: Path):
    from marivo.runtime.semantic.calendar_data_runtime import CalendarDataReader
    from marivo.runtime.semantic.calendar_data_service import CalendarDataService

    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)

    assert isinstance(runtime.get_service("calendar_data"), CalendarDataService)
    assert isinstance(runtime.calendar_data_reader, CalendarDataReader)


def test_local_artifact_commit_syncs_canonical_context(tmp_path: Path) -> None:
    _init_marivo_dir(tmp_path)
    runtime = create_local_runtime(LocalConfig(workspace_root=tmp_path))
    session = runtime.create_session(goal="local canonical context")
    session_id = str(session.session_id)

    artifact_id = runtime.commit_artifact_with_extraction(
        SessionId(session_id),
        "step_compare_local_context",
        "compare_artifact",
        "local_context_compare",
        _scalar_compare_artifact(),
        step_type="compare",
        artifact_schema_version="v1",
    )

    finding_rows = runtime.metadata.query_rows(
        "SELECT finding_id FROM findings WHERE artifact_id = ?",
        [artifact_id],
    )
    assert len(finding_rows) == 1

    proposition_rows = runtime.metadata.query_rows(
        "SELECT proposition_id FROM propositions WHERE session_id = ?",
        [session_id],
    )
    assert len(proposition_rows) == 1

    context = runtime.get_proposition_context(session_id, proposition_rows[0]["proposition_id"])
    assert context["schema_version"] == "proposition_context_view.v1"
    assert context["proposition"]["proposition_type"] == "change"
    assert context["seed_entries"][0]["finding"]["artifact_id"] == artifact_id

    state_view = runtime.get_session_state(SessionId(session_id))
    assert state_view["schema_version"] == "session_state_view.v1"
    state_view = runtime.get_session_state(SessionId(session_id), limit=10)
    assert (
        state_view["active_propositions"][0]["proposition"]["proposition_id"]
        == proposition_rows[0]["proposition_id"]
    )


def test_explicit_local_at_local_entry_succeeds(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config, explicit="local")
    assert runtime is not None


def test_marivo_profile_server_at_local_entry_raises(tmp_path, monkeypatch) -> None:
    from marivo.profiles.resolver import ProfileResolutionError

    _init_marivo_dir(tmp_path)
    monkeypatch.setenv("MARIVO_PROFILE", "server")
    config = LocalConfig(workspace_root=tmp_path)
    with pytest.raises(ProfileResolutionError):
        create_local_runtime(config)


def test_marivo_profile_local_at_local_entry_succeeds(tmp_path, monkeypatch) -> None:
    _init_marivo_dir(tmp_path)
    monkeypatch.setenv("MARIVO_PROFILE", "local")
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    assert runtime is not None


def test_workspace_toml_profile_server_raises(tmp_path, monkeypatch) -> None:
    from marivo.profiles.resolver import ProfileResolutionError

    monkeypatch.delenv("MARIVO_PROFILE", raising=False)
    marivo_dir = tmp_path / ".marivo"
    marivo_dir.mkdir()
    (marivo_dir / "models").mkdir(exist_ok=True)
    (marivo_dir / "evidence").mkdir(exist_ok=True)
    (marivo_dir / "VERSION").write_text("1")
    (marivo_dir / "marivo.toml").write_text('profile = "server"\n')
    config = LocalConfig(workspace_root=tmp_path)
    with pytest.raises(ProfileResolutionError):
        create_local_runtime(config)


def _init_marivo_dir(root: Path) -> None:
    marivo = root / ".marivo"
    marivo.mkdir(exist_ok=True)
    (marivo / "models").mkdir(exist_ok=True)
    (marivo / "evidence").mkdir(exist_ok=True)
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        'profile = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )


def _scalar_compare_artifact() -> dict:
    left_window = {"kind": "range", "start": "2026-05-01", "end": "2026-05-08"}
    right_window = {"kind": "range", "start": "2026-04-24", "end": "2026-05-01"}
    return {
        "comparison_type": "scalar_delta",
        "metric": "revenue",
        "current_value": 120.0,
        "baseline_value": 100.0,
        "absolute_delta": 20.0,
        "relative_delta": 0.2,
        "direction": "increase",
        "unit": "usd",
        "current_ref": {"artifact_id": "art_left_observe"},
        "baseline_ref": {"artifact_id": "art_right_observe"},
        "resolved_input_summary": {
            "current_scope": {},
            "current_time_scope": left_window,
            "baseline_time_scope": right_window,
        },
    }
