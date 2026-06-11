"""Active session pointer management."""

from marivo.analysis.session.active import (
    read_active_session_name,
    write_active_session_name,
)
from marivo.project import resolve_project_root


def test_active_pointer_round_trip(tmp_path):
    write_active_session_name(tmp_path, "q3-revenue")
    assert read_active_session_name(tmp_path) == "q3-revenue"


def test_active_pointer_missing_returns_none(tmp_path):
    assert read_active_session_name(tmp_path) is None


def test_active_pointer_walks_up_like_resolve(tmp_path, monkeypatch):
    write_active_session_name(tmp_path, "q3-revenue")
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    root = resolve_project_root()
    assert root == tmp_path
    assert read_active_session_name(root) == "q3-revenue"


def test_write_active_creates_parent_dirs(tmp_path):
    write_active_session_name(tmp_path, "demo")
    assert (tmp_path / ".marivo" / "analysis" / "active").read_text().strip() == "demo"
