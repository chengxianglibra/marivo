"""Tests for marivo.cli — the marivo init command."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

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


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"marivo {__version__}"


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.puts.append(kwargs)
        return {}


class _FakeClientFactory:
    def __init__(self) -> None:
        self.client = _FakeS3Client()

    def __call__(self, *args: Any, **kwargs: Any) -> _FakeS3Client:
        return self.client


def _write_publish_project_config(root: Path) -> None:
    (root / "marivo.toml").write_text(
        "[project]\n"
        'name = "demo"\n\n'
        "[publish.s3]\n"
        'S3_BUCKET_PATH = "s3://bucket/base"\n'
        'AWS_ENDPOINT_URL_S3 = "https://s3.example.com"\n',
        encoding="utf-8",
    )


def _write_publish_secret_config(home: Path, *, secret_key: str = "sk") -> None:
    path = home / ".marivo" / "secrets.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'[publish.s3]\nAWS_ACCESS_KEY_ID = "ak"\nAWS_SECRET_ACCESS_KEY = "{secret_key}"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_publish_file_command_uploads_and_prints_uri(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    _write_publish_project_config(tmp_path)
    _write_publish_secret_config(home)
    source = tmp_path / "report.html"
    source.write_text("<h1>report</h1>", encoding="utf-8")
    factory = _FakeClientFactory()
    monkeypatch.setattr("marivo.cli._s3_client_factory", factory)

    main(["publish", str(source)])

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "Uploaded 1 file",
        "URL: https://s3.example.com/bucket/base/report.html",
        "S3: s3://bucket/base/report.html",
    ]
    assert factory.client.puts[0]["Key"] == "base/report.html"


def test_publish_directory_command_uploads_nested_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    _write_publish_project_config(tmp_path)
    _write_publish_secret_config(home)
    source = tmp_path / "out"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("index", encoding="utf-8")
    (source / "assets" / "app.js").write_text("app", encoding="utf-8")
    factory = _FakeClientFactory()
    monkeypatch.setattr("marivo.cli._s3_client_factory", factory)

    main(["publish", str(source)])

    captured = capsys.readouterr()
    assert "s3://bucket/base/out" in captured.out
    assert "Uploaded 2 files" in captured.out
    keys = sorted(put["Key"] for put in factory.client.puts)
    assert keys == ["base/out/assets/app.js", "base/out/index.html"]


def test_publish_missing_path_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["publish", "/does/not/exist"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "publish path does not exist" in captured.err


def test_publish_missing_config_names_key_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _write_publish_secret_config(home, secret_key="do-not-print")
    source = tmp_path / "report.html"
    source.write_text("<h1>report</h1>", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["publish", str(source)])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "marivo.toml [publish.s3] S3_BUCKET_PATH" in captured.err
    assert str(tmp_path / "marivo.toml") in captured.err
    assert "do-not-print" not in captured.out
    assert "do-not-print" not in captured.err
