from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from marivo.doctor import (
    DoctorCheck,
    DoctorOptions,
    DoctorReport,
    DoctorSection,
    render_fix_snap,
    render_text,
    run_doctor,
    status_from_checks,
)


def _report_with_checks(*checks: DoctorCheck) -> DoctorReport:
    section = DoctorSection(id="installation", label="Installation", checks=checks)
    return DoctorReport(
        status=status_from_checks((section,)),
        project_root="/tmp/project",
        python_executable="/tmp/project/.venv/bin/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/project/marivo",
        sections=(section,),
    )


def test_status_from_checks_prefers_fail_over_warning() -> None:
    section = DoctorSection(
        id="mixed",
        label="Mixed",
        checks=(
            DoctorCheck(id="a", label="A", status="ok", summary="ok"),
            DoctorCheck(id="b", label="B", status="warning", summary="warn"),
            DoctorCheck(id="c", label="C", status="fail", summary="fail"),
        ),
    )

    assert status_from_checks((section,)) == "fail"


def test_status_from_checks_returns_warning_without_failures() -> None:
    section = DoctorSection(
        id="mixed",
        label="Mixed",
        checks=(
            DoctorCheck(id="a", label="A", status="ok", summary="ok"),
            DoctorCheck(id="b", label="B", status="warning", summary="warn"),
        ),
    )

    assert status_from_checks((section,)) == "warning"


def test_doctor_report_to_dict_is_json_safe() -> None:
    report = _report_with_checks(
        DoctorCheck(
            id="secret.env.TRINO_AUTH",
            label="TRINO_AUTH",
            status="fail",
            summary="TRINO_AUTH is missing",
            details={"datasource": "warehouse", "env_var": "TRINO_AUTH"},
            fix=('export TRINO_AUTH="secret_value"',),
        )
    )

    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "fail"
    assert "TRINO_AUTH" in encoded
    assert "secret_value" in encoded


def test_doctor_check_to_dict_preserves_explicit_empty_details_and_fix() -> None:
    check = DoctorCheck(
        id="noop",
        label="No-op",
        status="ok",
        summary="nothing to do",
        details={},
        fix=[],
    )

    payload = check.to_dict()

    assert payload["details"] == {}
    assert payload["fix"] == []


def test_doctor_check_to_dict_omits_default_details_and_fix() -> None:
    check = DoctorCheck(
        id="noop",
        label="No-op",
        status="ok",
        summary="nothing to do",
    )

    payload = check.to_dict()

    assert "details" not in payload
    assert "fix" not in payload


def test_render_text_is_bounded_and_lists_section_status() -> None:
    report = _report_with_checks(
        DoctorCheck(
            id="extra.trino",
            label="Trino backend extra",
            status="warning",
            summary="missing backend extra for trino",
            fix=('/tmp/project/.venv/bin/python -m pip install "marivo[trino]"',),
        )
    )

    text = render_text(report)

    assert text.splitlines()[0] == "Marivo doctor: warning"
    assert "Python: /tmp/project/.venv/bin/python" in text
    assert "Marivo: 0.2.8.dev0 (/tmp/project/marivo)" in text
    assert "[installation] warning 1 warning" in text
    assert "Fix:" in text
    assert '/tmp/project/.venv/bin/python -m pip install "marivo[trino]"' in text


def test_render_fix_snap_prints_only_context_and_fix_commands() -> None:
    report = _report_with_checks(
        DoctorCheck(
            id="secret.env.TRINO_AUTH",
            label="TRINO_AUTH",
            status="fail",
            summary="TRINO_AUTH is missing",
            fix=(
                'export TRINO_AUTH="secret_value"',
                "marivo doctor --datasource warehouse --connect",
            ),
        )
    )

    text = render_fix_snap(report)

    assert text.splitlines()[0] == "Marivo doctor fix snapshot: fail"
    assert 'export TRINO_AUTH="secret_value"' in text
    assert "marivo doctor --datasource warehouse --connect" in text
    assert "[installation]" not in text


def _write_manifest(root: Path) -> None:
    root.joinpath("marivo.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")


def _section(report: DoctorReport, section_id: str) -> DoctorSection:
    for section in report.sections:
        if section.id == section_id:
            return section
    raise AssertionError(f"missing section {section_id}")


def _check(report: DoctorReport, section_id: str, check_id: str) -> DoctorCheck:
    section = _section(report, section_id)
    for check in section.checks:
        if check.id == check_id:
            return check
    raise AssertionError(f"missing check {section_id}.{check_id}")


def _write_external_layer_project(root: Path, *, duplicate_local: bool = False) -> Path:
    external_models = root.parent / "external" / "models"
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    external_ds = external_models / "datasources"
    external_ds.mkdir(parents=True)
    (external_models / "semantic").mkdir(parents=True)
    external_ds.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    if duplicate_local:
        local_ds = root / "models" / "datasources"
        local_ds.mkdir(parents=True)
        local_ds.joinpath("warehouse.py").write_text(
            "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
            encoding="utf-8",
        )
    return external_models


def test_default_doctor_reports_installation_and_project(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    tmp_path.joinpath("models", "datasources").mkdir(parents=True)
    tmp_path.joinpath("models", "semantic").mkdir(parents=True)

    report = run_doctor(DoctorOptions(project_root=tmp_path))

    assert report.status == "ok"
    assert report.project_root == str(tmp_path.resolve())
    assert _section(report, "installation").status == "ok"
    assert _check(report, "project", "project.marivo_toml").status == "ok"
    assert _check(report, "project", "project.models").status == "ok"


def test_default_doctor_accepts_missing_local_models_when_layer_paths_are_valid(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _write_external_layer_project(project_root)

    report = run_doctor(DoctorOptions(project_root=project_root))

    assert _check(report, "project", "project.models").status == "ok"
    assert _check(report, "project", "project.datasources").status == "ok"
    assert _check(report, "project", "project.semantic").status == "ok"
    assert _check(report, "datasources", "datasource.warehouse").status == "ok"


def test_default_doctor_fails_missing_project_manifest(tmp_path: Path) -> None:
    report = run_doctor(DoctorOptions(project_root=tmp_path))

    assert report.status == "fail"
    check = _check(report, "project", "project.marivo_toml")
    assert check.status == "fail"
    assert "marivo.toml was not found" in check.summary
    assert "marivo init" in "\n".join(check.fix)


def test_scoped_doctor_finds_external_layer_datasource(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_external_layer_project(project_root)

    report = run_doctor(DoctorOptions(project_root=project_root, datasource="warehouse"))

    assert _check(report, "datasources", "datasource.warehouse").status == "ok"


def test_doctor_reports_invalid_layer_paths_config(tmp_path: Path) -> None:
    tmp_path.joinpath("marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = "external/models"
            """
        ),
        encoding="utf-8",
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path))
    check = _check(report, "project", "project.semantic.layer_paths")

    assert report.status == "fail"
    assert "marivo.toml [semantic].layer_paths must be a list of strings" in check.summary


def test_doctor_reports_duplicate_layer_datasource_names(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external_models = _write_external_layer_project(project_root, duplicate_local=True)

    report = run_doctor(DoctorOptions(project_root=project_root))
    duplicate = _check(report, "datasources", "datasource.warehouse.duplicate")

    assert report.status == "fail"
    assert "Duplicate datasource name: 'warehouse'" in duplicate.summary
    assert str(project_root / "models" / "datasources" / "warehouse.py") in duplicate.summary
    assert str(external_models / "datasources" / "warehouse.py") in duplicate.summary


def test_default_doctor_loads_datasources_without_connecting(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path, datasource="warehouse"))

    assert _check(report, "datasources", "datasource.warehouse").status in {"ok", "warning"}
    secret_check = _check(report, "secrets", "secret.env.warehouse.auth.TRINO_AUTH")
    assert secret_check.status == "fail"
    assert secret_check.fix == (
        'export TRINO_AUTH="secret_value"',
        f"marivo doctor --project-root {tmp_path.resolve()} --datasource warehouse --connect",
    )
    assert 'export TRINO_AUTH="secret_value"' in render_fix_snap(report)


def test_default_doctor_does_not_create_analysis_state(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    report = run_doctor(DoctorOptions(project_root=tmp_path))

    assert report.project_root == str(tmp_path.resolve())
    assert not (tmp_path / ".marivo" / "analysis" / "session_store.db").exists()
    assert _check(report, "state", "state.analysis_dir").status == "skipped"


def test_default_doctor_does_not_write_secret_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _write_manifest(project)
    ds_dir = project / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    run_doctor(DoctorOptions(project_root=project))

    assert not (home / ".marivo" / "secrets.toml").exists()


def test_default_doctor_reports_insecure_secret_cache_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    secret_file = home / ".marivo" / "secrets.toml"
    secret_file.parent.mkdir()
    secret_file.write_text('TRINO_AUTH = "cached"\n', encoding="utf-8")
    secret_file.chmod(0o644)
    project = tmp_path / "project"
    project.mkdir()
    _write_manifest(project)
    monkeypatch.setattr(Path, "home", lambda: home)

    report = run_doctor(DoctorOptions(project_root=project))

    check = _check(report, "secrets", "secret.cache_permissions")
    assert check.status == "fail"
    assert "chmod 600 ~/.marivo/secrets.toml" in "\n".join(check.fix)


def test_default_doctor_statically_inspects_datasources_without_executing_files(
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    side_effect = tmp_path / "executed.txt"
    ds_dir.joinpath("warehouse.py").write_text(
        "from pathlib import Path\n"
        "import marivo.datasource as md\n"
        f"Path({str(side_effect)!r}).write_text('executed', encoding='utf-8')\n"
        "md.duckdb(name='warehouse', path='warehouse.duckdb')\n",
        encoding="utf-8",
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path))

    assert not side_effect.exists()
    assert _check(report, "datasources", "datasource.warehouse").status in {"ok", "warning"}


def test_scoped_doctor_ignores_unrelated_broken_datasource_files(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )
    ds_dir.joinpath("broken.py").write_text("this is not valid python(\n", encoding="utf-8")

    report = run_doctor(DoctorOptions(project_root=tmp_path, datasource="warehouse"))

    datasource_checks = _section(report, "datasources").checks
    assert _check(report, "datasources", "datasource.warehouse").status in {"ok", "warning"}
    assert all(check.id != "datasource.parse_error.broken" for check in datasource_checks)


def test_scoped_doctor_detects_duplicate_datasource_names(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )
    ds_dir.joinpath("warehouse_copy.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino-backup.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path, datasource="warehouse"))

    duplicate = _check(report, "datasources", "datasource.warehouse.duplicate")
    assert duplicate.status == "fail"
    assert "Duplicate datasource name: 'warehouse'" in duplicate.summary


def test_secret_check_ids_are_unique_when_env_var_is_reused(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )
    ds_dir.joinpath("analytics.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='analytics', host='trino.example', catalog='hive', auth_env='TRINO_AUTH')\n",
        encoding="utf-8",
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path))

    ids = [
        check.id
        for check in _section(report, "secrets").checks
        if check.label == "TRINO_AUTH" and check.id.startswith("secret.env.")
    ]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_doctor_semantic_flag_uses_semantic_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run_check(
        *, workspace_dir: str | Path | None, readiness: bool, format: str
    ) -> dict[str, object]:
        calls.append({"workspace_dir": workspace_dir, "readiness": readiness, "format": format})
        return {
            "status": "blocked",
            "errors": [],
            "warnings": [],
            "readiness": {
                "status": "blocked",
                "blockers": [{"kind": "missing_business_definition", "message": "missing"}],
                "warnings": [],
            },
        }

    monkeypatch.setattr("marivo.semantic.check.run_check", fake_run_check)

    report = run_doctor(DoctorOptions(project_root=tmp_path, semantic=True))

    assert calls == [{"workspace_dir": tmp_path.resolve(), "readiness": True, "format": "json"}]
    check = _check(report, "semantic", "semantic.readiness")
    assert check.status == "fail"
    assert "blocked" in check.summary
    assert check.fix == (
        f"marivo doctor --project-root {tmp_path.resolve()} --semantic --format json",
    )


def test_doctor_semantic_json_details_preserve_checker_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)

    def fake_run_check(
        *, workspace_dir: str | Path | None, readiness: bool, format: str
    ) -> dict[str, object]:
        assert workspace_dir == tmp_path.resolve()
        assert readiness is True
        assert format == "json"
        return {
            "status": "blocked",
            "errors": [
                {
                    "kind": "invalid_metric",
                    "message": "metric failed to load",
                    "refs": ["sales.total"],
                    "location": {"file": "models/semantic/sales.py", "line": 8},
                    "hint": "fix the expression",
                }
            ],
            "warnings": [
                {
                    "kind": "deprecated_ref",
                    "message": "uses deprecated ref",
                    "refs": ["sales.legacy"],
                    "location": {"file": "models/semantic/sales.py", "line": 14},
                }
            ],
            "readiness": {
                "status": "blocked",
                "analysis_ready_refs": ["sales.ok_metric"],
                "blockers": [
                    {
                        "kind": "missing_business_definition",
                        "severity": "blocker",
                        "refs": ["sales.total"],
                        "message": "missing business definition",
                        "suggested_action": "add ai_context.business_definition",
                    }
                ],
                "warnings": [
                    {
                        "kind": "sql_parity_unverified",
                        "severity": "warning",
                        "refs": ["sales.margin"],
                        "message": "parity not verified",
                        "suggested_action": "run parity check",
                    }
                ],
                "input_summary": {
                    "datasources": ["warehouse"],
                    "refs": ["sales.total"],
                    "tables": ["warehouse.sales"],
                },
                "checked_at": "2026-07-07T12:00:00Z",
            },
        }

    monkeypatch.setattr("marivo.semantic.check.run_check", fake_run_check)

    report = run_doctor(DoctorOptions(project_root=tmp_path, semantic=True))

    check = _check(report, "semantic", "semantic.readiness")
    assert check.status == "fail"
    assert check.details == {
        "semantic_status": "blocked",
        "errors": [
            {
                "kind": "invalid_metric",
                "message": "metric failed to load",
                "refs": ["sales.total"],
                "location": {"file": "models/semantic/sales.py", "line": 8},
                "hint": "fix the expression",
            }
        ],
        "warnings": [
            {
                "kind": "deprecated_ref",
                "message": "uses deprecated ref",
                "refs": ["sales.legacy"],
                "location": {"file": "models/semantic/sales.py", "line": 14},
            }
        ],
        "readiness": {
            "status": "blocked",
            "blockers": [
                {
                    "kind": "missing_business_definition",
                    "severity": "blocker",
                    "refs": ["sales.total"],
                    "message": "missing business definition",
                    "suggested_action": "add ai_context.business_definition",
                }
            ],
            "warnings": [
                {
                    "kind": "sql_parity_unverified",
                    "severity": "warning",
                    "refs": ["sales.margin"],
                    "message": "parity not verified",
                    "suggested_action": "run parity check",
                }
            ],
        },
    }
    assert "1 load error" in check.summary
    assert "1 load warning" in check.summary
    assert "1 readiness blocker" in check.summary
    assert "1 readiness warning" in check.summary
    json.dumps(report.to_dict())
    readiness_details = check.details["readiness"]  # type: ignore[index]
    assert isinstance(readiness_details, dict)
    assert "analysis_ready_refs" not in readiness_details
    assert "input_summary" not in readiness_details
    assert "checked_at" not in readiness_details


def test_doctor_semantic_warnings_surface_as_warning_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)

    def fake_run_check(
        *, workspace_dir: str | Path | None, readiness: bool, format: str
    ) -> dict[str, object]:
        assert workspace_dir == tmp_path.resolve()
        assert readiness is True
        assert format == "json"
        return {
            "status": "ready_with_warnings",
            "errors": [],
            "warnings": [],
            "readiness": {
                "status": "ready_with_warnings",
                "blockers": [],
                "warnings": [
                    {
                        "kind": "sql_parity_unverified",
                        "severity": "warning",
                        "refs": ["sales.margin"],
                        "message": "parity not verified",
                        "suggested_action": "run parity check",
                    }
                ],
                "analysis_ready_refs": ["sales.margin"],
                "input_summary": {"datasources": ["warehouse"], "refs": [], "tables": []},
                "checked_at": "2026-07-07T12:00:00Z",
            },
        }

    monkeypatch.setattr("marivo.semantic.check.run_check", fake_run_check)

    report = run_doctor(DoctorOptions(project_root=tmp_path, semantic=True))

    assert report.status == "warning"
    check = _check(report, "semantic", "semantic.readiness")
    assert check.status == "warning"
    assert check.details == {
        "semantic_status": "ready_with_warnings",
        "errors": [],
        "warnings": [],
        "readiness": {
            "status": "ready_with_warnings",
            "blockers": [],
            "warnings": [
                {
                    "kind": "sql_parity_unverified",
                    "severity": "warning",
                    "refs": ["sales.margin"],
                    "message": "parity not verified",
                    "suggested_action": "run parity check",
                }
            ],
        },
    }
    assert "1 readiness warning" in check.summary


def test_doctor_connect_flag_uses_no_persist_connect_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, Path | None, bool]] = []

    class FakeResult:
        name = "warehouse"
        ok = True
        error = None
        latency_ms = 3

    def fake_test_no_persist(
        name: object,
        *,
        project_root: Path | None = None,
        include_semantic_layers: bool = False,
    ) -> FakeResult:
        calls.append((str(name), project_root, include_semantic_layers))
        return FakeResult()

    monkeypatch.setattr("marivo.datasource.manage.test_no_persist", fake_test_no_persist)

    report = run_doctor(DoctorOptions(project_root=tmp_path, connect=True, datasource="warehouse"))

    assert calls == [("warehouse", tmp_path.resolve(), True)]
    check = _check(report, "connect", "connect.warehouse")
    assert check.status == "ok"
    assert "3ms" in check.summary


def test_doctor_connect_flag_uses_layered_datasource_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    _write_external_layer_project(project_root)
    calls: list[tuple[str, Path | None, bool]] = []

    class FakeResult:
        name = "warehouse"
        ok = True
        error = None
        latency_ms = 3

    def fake_test_no_persist(
        name: object,
        *,
        project_root: Path | None = None,
        include_semantic_layers: bool = False,
    ) -> FakeResult:
        calls.append((str(name), project_root, include_semantic_layers))
        return FakeResult()

    monkeypatch.setattr("marivo.datasource.manage.test_no_persist", fake_test_no_persist)

    report = run_doctor(
        DoctorOptions(project_root=project_root, connect=True, datasource="warehouse")
    )

    assert calls == [("warehouse", project_root.resolve(), True)]
    assert _check(report, "connect", "connect.warehouse").status == "ok"


def test_doctor_connect_flag_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    ds_dir.joinpath("warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )

    class FakeResult:
        name = "warehouse"
        ok = False
        error = "RuntimeError: connection refused"
        latency_ms = None

    monkeypatch.setattr(
        "marivo.datasource.manage.test_no_persist",
        lambda name, *, project_root=None, include_semantic_layers=False: FakeResult(),
    )

    report = run_doctor(DoctorOptions(project_root=tmp_path, connect=True, datasource="warehouse"))

    check = _check(report, "connect", "connect.warehouse")
    assert check.status == "fail"
    assert "connection refused" in check.summary
    assert check.fix == (
        f"marivo doctor --project-root {tmp_path.resolve()} --datasource warehouse --connect",
    )


def test_test_no_persist_uses_project_root_and_suppresses_disconnect_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import manage as datasource_manage

    class FakeBackend:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.disconnect_calls = 0

        def raw_sql(self, sql: str) -> None:
            self.queries.append(sql)

        def disconnect(self) -> None:
            self.disconnect_calls += 1
            raise RuntimeError("disconnect failed")

    backend = FakeBackend()
    load_calls: list[tuple[str, Path | None]] = []

    def fake_load_one(name: str, project_root: Path | None = None):  # type: ignore[no-untyped-def]
        load_calls.append((name, project_root))
        return type(
            "FakeDatasourceIR",
            (),
            {
                "name": name,
                "backend_type": "duckdb",
                "fields": {"path": ":memory:"},
                "env_refs": {},
            },
        )()

    def fake_build_backend_with_secrets(datasource):  # type: ignore[no-untyped-def]
        return type("BuiltBackend", (), {"backend": backend, "env_sourced_secrets": {}})()

    monkeypatch.setattr(datasource_manage._store, "load_one", fake_load_one)
    monkeypatch.setattr(
        datasource_manage._backends,
        "build_backend_with_secrets",
        fake_build_backend_with_secrets,
    )

    result = datasource_manage.test_no_persist("warehouse", project_root=tmp_path)

    assert result.ok is True
    assert result.error is None
    assert backend.queries == ["SELECT 1"]
    assert backend.disconnect_calls == 1
    assert load_calls == [("warehouse", tmp_path)]
