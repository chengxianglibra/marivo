from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from marivo.agent_skills import (
    MARKER_FILENAME,
    directory_hash,
    find_bundled_skills_root,
    iter_skill_dirs,
    resolve_default_target,
    sync_skills,
)


def _make_source(root: Path, *skill_names: str) -> Path:
    source = root / "source"
    for name in skill_names:
        skill = source / name
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n")
        (skill / "references" / "workflow.md").write_text("Use Marivo.\n")
    return source


def test_resolve_default_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"),
        "OPENCODE_CONFIG_DIR": str(tmp_path / "opencode-home"),
        "OPENCLAW_HOME": str(tmp_path / "openclaw-home"),
        "HERMES_HOME": str(tmp_path / "hermes-home"),
    }

    assert resolve_default_target("codex", env=env) == tmp_path / "codex-home" / "skills"
    assert resolve_default_target("claude", env=env) == tmp_path / "claude-home" / "skills"
    assert resolve_default_target("opencode", env=env) == tmp_path / "opencode-home" / "skill"
    assert (
        resolve_default_target("openclaw", env=env)
        == tmp_path / "openclaw-home" / "skills" / "marivo"
    )
    assert (
        resolve_default_target("hermes", env=env)
        == tmp_path / "hermes-home" / "skills" / "marivo"
    )


def test_resolve_opencode_uses_xdg_fallback(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    assert resolve_default_target("opencode", env=env) == tmp_path / "xdg" / "opencode" / "skill"


def test_sync_skills_creates_managed_skill_dirs(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis", "marivo-datasource")
    target = tmp_path / "skills"

    report = sync_skills(agent="custom", target_root=target, source_root=source)

    assert report["status"] == "ok"
    assert {action["status"] for action in report["results"][0]["actions"]} == {"created"}
    for skill in ("marivo-analysis", "marivo-datasource"):
        assert (target / skill / "SKILL.md").is_file()
        marker = (target / skill / MARKER_FILENAME).read_text()
        assert '"managed_by": "marivo"' in marker
        assert f'"skill_name": "{skill}"' in marker


def test_sync_skills_skips_unchanged_managed_dirs(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"

    sync_skills(agent="custom", target_root=target, source_root=source)
    report = sync_skills(agent="custom", target_root=target, source_root=source)

    assert report["status"] == "ok"
    assert report["results"][0]["actions"][0]["status"] == "skipped"


def test_sync_skills_refreshes_marker_when_version_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"

    monkeypatch.setattr("marivo.agent_skills.current_marivo_version", lambda: "1.0.0")
    sync_skills(agent="custom", target_root=target, source_root=source)
    monkeypatch.setattr("marivo.agent_skills.current_marivo_version", lambda: "1.0.1")
    report = sync_skills(agent="custom", target_root=target, source_root=source)

    action = report["results"][0]["actions"][0]
    assert action["status"] == "updated"
    assert action["reason"] == "refreshed version marker"
    assert '"marivo_version": "1.0.1"' in (
        target / "marivo-analysis" / MARKER_FILENAME
    ).read_text()


def test_sync_skills_updates_old_managed_dir(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"
    old_time = datetime(2026, 5, 28, 1, 2, 3, tzinfo=UTC)

    sync_skills(agent="custom", target_root=target, source_root=source)
    (source / "marivo-analysis" / "SKILL.md").write_text("# marivo-analysis\nupdated\n")
    report = sync_skills(
        agent="custom",
        target_root=target,
        source_root=source,
        now=old_time,
    )

    action = report["results"][0]["actions"][0]
    assert action["status"] == "updated"
    assert Path(action["backup"]).name == "marivo-analysis.bak-20260528010203"
    assert (target / "marivo-analysis" / "SKILL.md").read_text() == "# marivo-analysis\nupdated\n"


def test_sync_skills_reports_conflict_for_unmanaged_existing_dir(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"
    (target / "marivo-analysis").mkdir(parents=True)
    (target / "marivo-analysis" / "SKILL.md").write_text("# local copy\n")

    report = sync_skills(agent="custom", target_root=target, source_root=source)

    assert report["status"] == "conflict"
    action = report["results"][0]["actions"][0]
    assert action["status"] == "conflict"
    assert action["reason"] == "existing directory is unmanaged"


def test_sync_skills_force_backs_up_unmanaged_existing_dir(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"
    (target / "marivo-analysis").mkdir(parents=True)
    (target / "marivo-analysis" / "SKILL.md").write_text("# local copy\n")

    report = sync_skills(
        agent="custom",
        target_root=target,
        source_root=source,
        force=True,
        now=datetime(2026, 5, 28, 1, 2, 3, tzinfo=UTC),
    )

    action = report["results"][0]["actions"][0]
    assert action["status"] == "updated"
    assert (target / "marivo-analysis.bak-20260528010203" / "SKILL.md").read_text() == (
        "# local copy\n"
    )
    assert (target / "marivo-analysis" / "SKILL.md").read_text() == "# marivo-analysis\n"


def test_sync_skills_dry_run_does_not_write(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")
    target = tmp_path / "skills"

    report = sync_skills(
        agent="custom",
        target_root=target,
        source_root=source,
        dry_run=True,
    )

    assert report["dry_run"] is True
    assert report["results"][0]["actions"][0]["status"] == "would_create"
    assert not target.exists()


def test_directory_hash_ignores_sync_marker(tmp_path: Path) -> None:
    skill = _make_source(tmp_path, "marivo-analysis") / "marivo-analysis"
    before = directory_hash(skill)
    (skill / MARKER_FILENAME).write_text("{}")

    assert directory_hash(skill) == before


def test_find_bundled_skills_root_uses_source_checkout_fallback() -> None:
    root = find_bundled_skills_root()
    names = {path.name for path in iter_skill_dirs(root)}

    assert "marivo-py-analysis" in names
    assert "marivo-py-semantic" in names


def test_sync_skills_requires_target_for_custom_agent(tmp_path: Path) -> None:
    source = _make_source(tmp_path, "marivo-analysis")

    with pytest.raises(Exception, match="Unsupported agent"):
        sync_skills(agent="custom", source_root=source)
