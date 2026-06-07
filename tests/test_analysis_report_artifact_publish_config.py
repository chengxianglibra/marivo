from __future__ import annotations

import pytest


def test_resolve_explicit_target_wins(tmp_path) -> None:
    from marivo.analysis.publish.publish_config import resolve_publish_config

    config = resolve_publish_config("/tmp/explicit", env={}, project_root=tmp_path)

    assert config.base == "/tmp/explicit"
    assert config.prefix_template == "marivo/users/{username}"


def test_resolve_env_dir_fallback(tmp_path) -> None:
    from marivo.analysis.publish.publish_config import resolve_publish_config

    config = resolve_publish_config(
        None, env={"MARIVO_PUBLISH_DIR": "/tmp/env"}, project_root=tmp_path
    )

    assert config.base == "/tmp/env"


def test_resolve_prefers_local_over_project_config(tmp_path) -> None:
    from marivo.analysis.publish.publish_config import resolve_publish_config

    (tmp_path / "marivo.publish.toml").write_text(
        '[storage.local]\ndir = "/tmp/project"\nprefix = "proj/{username}"\n',
        encoding="utf-8",
    )
    (tmp_path / ".marivo").mkdir()
    (tmp_path / ".marivo" / "publish.local.toml").write_text(
        '[storage.local]\ndir = "/tmp/local"\n', encoding="utf-8"
    )

    config = resolve_publish_config(None, env={}, project_root=tmp_path)

    assert config.base == "/tmp/local"  # local dir overrides project dir
    assert config.prefix_template == "proj/{username}"  # prefix falls through to project


def test_resolve_prefix_helper_defaults(tmp_path) -> None:
    from marivo.analysis.publish.publish_config import resolve_publish_prefix

    assert resolve_publish_prefix(env={}, project_root=tmp_path) == "marivo/users/{username}"


def test_resolve_raises_when_unresolved(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishConfigError
    from marivo.analysis.publish.publish_config import resolve_publish_config

    with pytest.raises(ReportPublishConfigError):
        resolve_publish_config(None, env={}, project_root=tmp_path)
