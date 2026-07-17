"""Tests for marivo.cli — the marivo init command."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import marivo
import marivo.datasource as md
import marivo.skills
from marivo import __version__
from marivo.cli import init_project, main

# ---------------------------------------------------------------------------
# init_project creates all artifacts
# ---------------------------------------------------------------------------


def test_creates_marivo_toml(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / "marivo.toml").is_file()


def test_creates_marivo_toml_with_project_name(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


def test_creates_marivo_toml_with_default_telemetry_on(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["telemetry"]["enabled"] == "on"


def test_creates_models_dir(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / "models").is_dir()


def test_creates_dot_marivo_dir(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / ".marivo").is_dir()


def test_installs_skills_for_supported_agent_skill_dirs(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    skills_src = Path(marivo.skills.__file__).parent.resolve()

    for agent_dir in (".agents/skills", ".claude/skills", ".codex/skills"):
        for skill_name in ("marivo-semantic", "marivo-analysis"):
            link_path = tmp_path / agent_dir / skill_name
            assert link_path.is_symlink()
            assert link_path.resolve() == (skills_src / skill_name).resolve()


def test_prints_initialized_header(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert f"Initializing Marivo project in {tmp_path}" in captured.out


# ---------------------------------------------------------------------------
# init_project warns but continues when artifacts exist (no --force)
# ---------------------------------------------------------------------------


def test_warns_if_marivo_toml_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "marivo.toml already exists" in captured.err


def test_warns_if_models_dir_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "models").mkdir()
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "models/ already exists" in captured.err


def test_warns_if_dot_marivo_dir_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".marivo").mkdir()
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert ".marivo/ already exists" in captured.err


def test_does_not_overwrite_existing_marivo_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == "x"


def test_warns_but_creates_missing_artifacts(tmp_path: Path) -> None:
    """When some artifacts exist, missing ones are still created."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    # marivo.toml was skipped (already exists), but models/ and .marivo/ were created
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / ".marivo").is_dir()


# ---------------------------------------------------------------------------
# init_project with force=True replaces existing artifacts
# ---------------------------------------------------------------------------


def test_force_overwrites_marivo_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "old"\n')
    init_project(force=True, project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


def test_force_overwrites_models_dir(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    init_project(force=True, project_dir=tmp_path)
    assert (tmp_path / "models").is_dir()


# ---------------------------------------------------------------------------
# --force deletes and recreates non-empty .marivo/
# ---------------------------------------------------------------------------


def test_force_deletes_nonempty_dot_marivo(tmp_path: Path) -> None:
    (tmp_path / ".marivo").mkdir()
    (tmp_path / ".marivo" / "analysis").mkdir()
    (tmp_path / ".marivo" / "analysis" / "session.json").write_text("{}")
    init_project(force=True, project_dir=tmp_path)
    # .marivo/ was deleted and recreated, so the old file is gone
    assert not (tmp_path / ".marivo" / "analysis" / "session.json").exists()
    assert (tmp_path / ".marivo").is_dir()


# ---------------------------------------------------------------------------
# --force overwrites invalid TOML
# ---------------------------------------------------------------------------


def test_force_overwrites_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text("this is not valid [[toml")
    init_project(force=True, project_dir=tmp_path)
    # marivo.toml should now be valid and contain the project name
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


# ---------------------------------------------------------------------------
# Invalid TOML without --force still errors
# ---------------------------------------------------------------------------


def test_rejects_invalid_toml_without_force(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text("this is not valid [[toml")
    with pytest.raises(SystemExit) as exc_info:
        init_project(project_dir=tmp_path)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# No subcommand prints help and exits 0
# ---------------------------------------------------------------------------


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Marivo" in captured.out or "marivo" in captured.out


def test_root_help_points_analysis_to_python_workflow(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Analysis workflow:" in captured.out
    assert "marivo help analysis" in captured.out
    assert "Use the Python interpreter where marivo is installed." in captured.out
    assert ".venv/bin/python" not in captured.out
    assert "marivo doctor --semantic" in captured.out
    assert "marivo doctor --datasource <name> --connect" in captured.out
    # The CLI command set is init, doctor, and help.
    # argparse renders the subcommand group as
    # "{init,doctor,help}" (insertion order) rather than the literal
    # "marivo <cmd>".
    assert "{init,doctor,help}" in captured.out
    assert "marivo doctor" in captured.out
    # Root help advertises the CLI analysis help subcommand.
    assert "marivo help analysis" in captured.out
    assert "marivo help datasource" in captured.out
    # Semantic authoring routing block points agents to the CLI semantic track.
    assert "Semantic authoring workflow:" in captured.out
    assert "marivo help semantic" in captured.out


def test_cli_datasource_help_matches_python_adapter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Datasource CLI help must dispatch to the live Python adapter unchanged."""
    main(["help", "datasource", "inspect"])

    output = capsys.readouterr().out.strip()
    assert output == md.help_text("inspect")
    assert "import marivo.datasource as md" in output
    assert "import marivo.semantic as ms" not in output


def test_cli_datasource_unknown_target_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Datasource target errors are typed CLI errors, never tracebacks."""
    with pytest.raises(SystemExit) as exc_info:
        main(["help", "datasource", "inspekt"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "DatasourceHelpTargetError" in captured.err
    assert "Traceback" not in captured.err


def test_module_datasource_help_uses_subprocess_environment_fingerprint() -> None:
    """Module CLI help reports the interpreter and package that executed it."""
    result = subprocess.run(
        [sys.executable, "-m", "marivo", "help", "datasource"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    # Help must report the interpreter actually running marivo (sys.executable),
    # not the symlink-resolved system Python. doctor reports the same value.
    assert f"Python: {sys.executable}" in result.stdout
    assert f"Package: {Path(marivo.__file__).resolve()}" in result.stdout


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"marivo {__version__}"


def test_doctor_command_prints_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.doctor import DoctorCheck, DoctorReport, DoctorSection

    report = DoctorReport(
        status="ok",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(
            DoctorSection(
                id="installation",
                label="Installation",
                checks=(DoctorCheck(id="i", label="i", status="ok", summary="ok"),),
            ),
        ),
    )
    monkeypatch.setattr("marivo.doctor.run_doctor", lambda options: report)

    main(["doctor", "--project-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert "Marivo doctor: ok" in captured.out
    assert "Python: /tmp/python" in captured.out


def test_doctor_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.doctor import DoctorReport

    report = DoctorReport(
        status="ok",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(),
    )
    monkeypatch.setattr("marivo.doctor.run_doctor", lambda options: report)

    main(["doctor", "--project-root", str(tmp_path), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["project_root"] == str(tmp_path)


def test_doctor_command_exits_one_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.doctor import DoctorReport

    report = DoctorReport(
        status="fail",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(),
    )
    monkeypatch.setattr("marivo.doctor.run_doctor", lambda options: report)

    with pytest.raises(SystemExit) as exc_info:
        main(["doctor", "--project-root", str(tmp_path)])

    assert exc_info.value.code == 1


def test_doctor_command_prints_fix_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.doctor import DoctorCheck, DoctorReport, DoctorSection

    report = DoctorReport(
        status="fail",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(
            DoctorSection(
                id="secrets",
                label="Secrets",
                checks=(
                    DoctorCheck(
                        id="secret.env.TRINO_AUTH",
                        label="TRINO_AUTH",
                        status="fail",
                        summary="missing",
                        fix=('export TRINO_AUTH="secret_value"',),
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr("marivo.doctor.run_doctor", lambda options: report)

    with pytest.raises(SystemExit) as exc_info:
        main(["doctor", "--project-root", str(tmp_path), "--fix-snap"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Marivo doctor fix snapshot: fail" in captured.out
    assert 'export TRINO_AUTH="secret_value"' in captured.out


def test_doctor_command_builds_doctor_options_from_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.doctor import DoctorOptions, DoctorReport

    seen: list[DoctorOptions] = []

    report = DoctorReport(
        status="ok",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(),
    )

    def fake_run_doctor(options: DoctorOptions) -> DoctorReport:
        seen.append(options)
        return report

    monkeypatch.setattr("marivo.doctor.run_doctor", fake_run_doctor)
    monkeypatch.setattr("marivo.doctor.render_fix_snap", lambda report: "FIX SNAP")

    main(
        [
            "doctor",
            "--project-root",
            str(tmp_path),
            "--format",
            "json",
            "--fix-snap",
            "--semantic",
            "--connect",
            "--datasource",
            "warehouse",
        ]
    )

    captured = capsys.readouterr()
    assert captured.out == "FIX SNAP\n"
    assert seen == [
        DoctorOptions(
            project_root=str(tmp_path),
            format="json",
            fix_snap=True,
            semantic=True,
            connect=True,
            datasource="warehouse",
        )
    ]


def test_doctor_command_fix_snapshot_takes_precedence_over_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.doctor import DoctorReport

    report = DoctorReport(
        status="ok",
        project_root=str(tmp_path),
        python_executable="/tmp/python",
        marivo_version="0.2.8.dev0",
        marivo_package_path="/tmp/marivo",
        sections=(),
    )

    monkeypatch.setattr("marivo.doctor.run_doctor", lambda options: report)
    monkeypatch.setattr("marivo.doctor.render_fix_snap", lambda report: "FIX SNAP WINS")

    main(["doctor", "--project-root", str(tmp_path), "--format", "json", "--fix-snap"])

    captured = capsys.readouterr()
    assert captured.out == "FIX SNAP WINS\n"


# ---------------------------------------------------------------------------
# help semantic track
# ---------------------------------------------------------------------------


def test_cli_help_semantic_root_prints_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["help", "semantic"])

    output = capsys.readouterr().out
    assert "marivo.semantic" in output
    assert "Capabilities:" in output
    assert "import marivo.semantic as ms" in output
    assert "import marivo.datasource as md" not in output


def test_cli_help_semantic_target_prints_focused_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["help", "semantic", "authoring"])

    output = capsys.readouterr().out
    assert "authoring" in output
    assert "import marivo.datasource as md" in output
    assert "import marivo.semantic as ms" in output


def test_cli_help_semantic_unknown_target_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["help", "semantic", "nonexistent_target"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "not registered" in captured.err or "semantic help target" in captured.err


def test_help_unknown_track_suggests_valid_tracks_and_target_form(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown CLI help track must teach the valid tracks and the
    `marivo help <track> <target>` form, not just argparse's 'invalid choice'.
    See issue #35.
    """
    with pytest.raises(SystemExit) as exc_info:
        main(["help", "catalog"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    err = captured.err
    assert "analysis" in err and "datasource" in err and "semantic" in err
    # Hint the target form so 'catalog' (a target, not a track) is recoverable
    # with a concrete suggested command, beyond argparse's bare 'invalid choice'.
    assert "marivo help analysis catalog" in err
