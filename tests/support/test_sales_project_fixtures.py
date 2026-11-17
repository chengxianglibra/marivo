"""Sales project fixtures stay independent of stale shared templates."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import marivo.semantic as ms
from tests import shared_fixtures


def test_sales_projects_ignore_incomplete_cache_and_are_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache" / "sales_project_v4"
    for tag in ("with_time", "no_time"):
        (cache / tag / "models" / "semantic" / "sales").mkdir(parents=True)
    monkeypatch.setattr(shared_fixtures, "_template_cache_dir", lambda: cache.parent)
    projects = [tmp_path / f"project-{index}" for index in range(8)]
    for project in projects:
        project.mkdir()

    def build(index: int) -> None:
        shared_fixtures.bootstrap_sales_project(projects[index], with_time=index % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(build, range(len(projects))))

    for index, project in enumerate(projects):
        assert (project / "marivo.toml").read_text() == '[project]\nname = "test"\n'
        model = (project / "models" / "semantic" / "sales" / "datasets.py").read_text()
        assert ("def order_date(" in model) == (index % 2 == 0)
    # Loading exercises the complete generated project, not just its file names.
    for project in projects[:2]:
        catalog = ms.load(workspace_dir=project)
        assert catalog.get("metric:sales.revenue").path == "sales.revenue"
    (projects[0] / "marivo.toml").unlink()
    assert (projects[2] / "marivo.toml").is_file()
    assert not (cache / "with_time" / "marivo.toml").exists()
    assert not (cache / "no_time" / "marivo.toml").exists()


def test_sales_project_refuses_to_overwrite_existing_models(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    with pytest.raises(FileExistsError):
        shared_fixtures.bootstrap_sales_project(tmp_path)
