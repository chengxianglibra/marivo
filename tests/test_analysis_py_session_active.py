"""Project root resolution and .marivo/analysis/active pointer."""

from marivo.analysis_py.session.active import (
    read_active_session_name,
    resolve_project_root,
    write_active_session_name,
)


def test_resolve_project_root_uses_current_dir_when_no_dotmarivo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_project_root() == tmp_path


def test_resolve_project_root_finds_dotmarivo_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".marivo").mkdir()
    monkeypatch.chdir(tmp_path)
    assert resolve_project_root() == tmp_path


def test_resolve_project_root_walks_up_to_parent(tmp_path, monkeypatch):
    (tmp_path / ".marivo").mkdir()
    nested = tmp_path / "src" / "lib"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert resolve_project_root() == tmp_path


def test_resolve_project_root_accepts_explicit_start(tmp_path):
    (tmp_path / ".marivo").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert resolve_project_root(start=nested) == tmp_path


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
