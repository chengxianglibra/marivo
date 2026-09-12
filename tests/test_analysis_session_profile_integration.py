"""Project datasource resolution stays separate from Dataset execution targets."""

import shutil
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.errors import DatasourceFieldInvalidError
from marivo.semantic.errors import SemanticLoadFailed


def test_model_qualified_datasource_name_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_HOME", str(tmp_path / "home"))
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        md.register(md.DuckDBSpec(name="sales.warehouse", path=":memory:"))
    assert caught.value.expected == "[a-z][a-z0-9_]*"


def test_missing_datasource_is_named_when_sources_are_requested(
    authoring_evidence_project: Path, monkeypatch
):
    monkeypatch.chdir(authoring_evidence_project)
    (authoring_evidence_project / "models" / "datasources" / "warehouse.py").unlink()
    session = mv.session.get_or_create("missing-source")
    assert session.runs().items == ()
    with pytest.raises(SemanticLoadFailed) as caught:
        session.observe(ms.ref.metric("sales.revenue"))
    assert "warehouse" in str(caught.value)
    assert "unknown datasource" in str(caught.value)
    assert session.runs().items == ()


@pytest.mark.parametrize(
    "kwargs", [{"backends": {}}, {"backend_factory": None}, {"use_datasources": False}]
)
def test_session_rejects_removed_backend_override_parameters(tmp_path, monkeypatch, kwargs):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError):
        mv.session.get_or_create("override", **kwargs)
    assert not (tmp_path / ".marivo").exists()


@pytest.mark.runtime
def test_observe_uses_registered_global_datasource(
    authoring_evidence_project: Path, tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(authoring_evidence_project)
    monkeypatch.setenv("MARIVO_HOME", str(tmp_path / "home"))
    alternate = tmp_path / "alternate.duckdb"
    shutil.copy2(authoring_evidence_project / "warehouse.duckdb", alternate)
    connection = duckdb.connect(str(alternate))
    try:
        connection.execute("UPDATE orders SET amount = 10.0")
        expected = connection.execute("SELECT SUM(amount) FROM orders").fetchone()[0]
    finally:
        connection.close()
    md.register(md.DuckDBSpec(name="warehouse", path=str(alternate)))
    session = mv.session.get_or_create("registered")
    output = session.observe(ms.ref.metric("sales.revenue")).aggregate().execute()
    assert output.to_pandas()["revenue"].tolist() == [expected]
    assert (
        session.artifact(output.state.artifact_ref).state.artifact_ref == output.state.artifact_ref
    )
