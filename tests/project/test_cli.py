"""Tests for marivo.cli — the marivo init command."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import marivo
import marivo.skills
from marivo import __version__
from marivo._compat import tomllib
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


def test_init_command_accepts_explicit_project_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "project"
    main(["init", "--project-root", str(project_root)])
    captured = capsys.readouterr()

    assert f"Initializing Marivo project in {project_root}" in captured.out
    assert (project_root / "marivo.toml").is_file()
    assert (project_root / "models").is_dir()
    assert (project_root / ".marivo").is_dir()


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


@pytest.mark.parametrize("arguments", ((), ("--help",)))
def test_top_level_help_advertised_live_help_commands_resolve(
    arguments: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(list(arguments))
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    commands = re.findall(r"^  marivo (help(?: [^\n]*)?)$", captured.out, re.MULTILINE)
    assert "help" in commands
    assert "help analysis.analysis_frame.select" in commands
    for command in commands:
        main(command.split())
        output = capsys.readouterr()
        target = command.removeprefix("help ") if command != "help" else "root"
        assert output.out.startswith(f"Target: {target}\n")
        assert output.err == ""


@pytest.mark.parametrize(
    "arguments",
    (("observe",), ("analysis", "observe"), ("semantic", "load"), ("datasource", "inspect")),
)
def test_cli_help_rejects_unknown_or_multi_part_targets(
    arguments: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["help", *arguments])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"marivo {__version__}"


def test_version_subcommand_repairs_to_canonical_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["version"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Use `marivo --version`."


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


def test_doctor_command_leaves_empty_current_workspace_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("MARIVO_TELEMETRY", raising=False)

    main(["doctor", "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["project_root"] == str(tmp_path.resolve())
    assert list(tmp_path.iterdir()) == []


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


@pytest.mark.parametrize(
    "target",
    [
        None,
        "analysis",
        "analysis.analysis_frame",
        "analysis.analysis_frame.output_names",
        "analysis.analysis_frame.correlate",
        "analysis.column.corr",
        "analysis.column.pair_count",
        "analysis.frame_axis",
        "analysis.frame_axis.role",
        "analysis.session.frame",
        "analysis.session.persisted_frame",
        "analysis.session.population_from",
        "analysis.analysis_frame.attribute",
        "analysis.frame_attribute_metric",
        "analysis.frame_attribute_metric.outputs",
        "analysis.analysis_frame.method_facts",
        "analysis.frame_method_facts",
        "analysis.frame_method_facts.show",
        "analysis.frame_linear_term.method",
    ],
)
def test_cli_and_python_help_have_identical_facts_and_targets(
    target: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert callable(marivo.help)
    marivo.help(target)
    expected = capsys.readouterr().out
    expected = re.sub(
        r"marivo\.help\(([\"'])([a-z_][a-z0-9_.]*)\1\)",
        lambda match: "marivo help " + match.group(2),
        expected,
    ).replace("marivo.help()", "marivo help")
    main(["help"] if target is None else ["help", target])
    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""


def test_cli_help_render_failure_is_diagnostic_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marivo._help import render

    def fail(_target: str | None = None) -> tuple[str, str, str | None]:
        raise RuntimeError("broken renderer")

    monkeypatch.setattr(render, "render_help_text", fail)
    with pytest.raises(SystemExit) as error:
        main(["help", "analysis"])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cause='RuntimeError'" in captured.err
    assert "Traceback" not in captured.err


def test_cli_rejects_retired_analysis_construction_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for suffix in (
        "before_end_boundary",
        "before_end_boundary.end",
        "before_end_boundary.boundary_timezone",
        "before_end_boundary.show",
        "time_scope.before_end",
        "grain.to_token",
        "grain.width_seconds",
        "grain.is_day",
        "grain.is_subday",
        "time_scope.contract",
        "time_scope.model_dump",
    ):
        with pytest.raises(SystemExit) as error:
            main(["help", "analysis." + suffix])
        assert error.value.code == 2
        assert "not registered" in capsys.readouterr().err
