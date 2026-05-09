from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from marivo.cli.cmd_init import handle
from marivo.contracts.values import LAYOUT_VERSION


def _make_args(workspace_root: str | None = None) -> argparse.Namespace:
    ns = argparse.Namespace()
    ns.workspace_root = workspace_root
    return ns


def test_creates_marivo_layout(tmp_path: Path) -> None:
    result = handle(_make_args(workspace_root=str(tmp_path)))
    assert result["status"] == "initialized"
    assert (tmp_path / ".marivo" / "models").is_dir()
    assert (tmp_path / ".marivo" / "evidence").is_dir()
    assert (tmp_path / ".marivo" / "VERSION").is_file()
    assert (tmp_path / ".marivo" / "VERSION").read_text() == str(LAYOUT_VERSION)
    assert (tmp_path / ".marivo" / "marivo.toml").is_file()
    assert (tmp_path / ".marivo" / "state.db").is_file()


def test_idempotent_on_repeat(tmp_path: Path) -> None:
    handle(_make_args(workspace_root=str(tmp_path)))
    result = handle(_make_args(workspace_root=str(tmp_path)))
    assert result["status"] == "already_initialized"


def test_repairs_missing_subdirs(tmp_path: Path) -> None:
    (tmp_path / ".marivo").mkdir()
    (tmp_path / ".marivo" / "VERSION").write_text(str(LAYOUT_VERSION))
    result = handle(_make_args(workspace_root=str(tmp_path)))
    assert (tmp_path / ".marivo" / "models").is_dir()
    assert (tmp_path / ".marivo" / "evidence").is_dir()


def test_rejects_incompatible_version(tmp_path: Path) -> None:
    (tmp_path / ".marivo").mkdir()
    (tmp_path / ".marivo" / "VERSION").write_text("99")
    with pytest.raises(Exception, match="not supported"):
        handle(_make_args(workspace_root=str(tmp_path)))
