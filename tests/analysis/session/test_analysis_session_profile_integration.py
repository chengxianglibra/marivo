"""Project datasource resolution stays separate from Dataset execution targets."""

import shutil
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.datasource.errors import DatasourceFieldInvalidError
from marivo.semantic.errors import SemanticLoadFailed
from tests.shared_fixtures import terminal_runs


def test_model_qualified_datasource_name_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_HOME", str(tmp_path / "home"))
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        md.register(md.DuckDBSpec(name="sales.warehouse", path=":memory:"))
    assert caught.value.expected == "[a-z][a-z0-9_]*"


def test_missing_datasource_is_named_when_sources_are_requested(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    (authoring_evidence_project / "models" / "datasources" / "warehouse.py").unlink()
    session = mv.session.get_or_create("missing-source")
    assert terminal_runs(session) == ()
    with pytest.raises(SemanticLoadFailed) as caught:
        session.observe({"revenue": session.catalog.metrics.get("sales.revenue")})
    assert "warehouse" in str(caught.value)
    assert "unknown datasource" in str(caught.value)
    assert terminal_runs(session) == ()


def test_observe_uses_registered_global_datasource(
    authoring_evidence_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    monkeypatch.setenv("MARIVO_HOME", str(tmp_path / "home"))
    alternate = tmp_path / "alternate.duckdb"
    shutil.copy2(authoring_evidence_project / "warehouse.duckdb", alternate)
    connection = duckdb.connect(str(alternate))
    try:
        connection.execute("ALTER TABLE orders ALTER COLUMN query_id TYPE BIGINT")
        connection.execute("UPDATE orders SET amount = 10.0, log_date = '20260701'")
        connection.execute(
            "ALTER TABLE orders ALTER COLUMN log_date TYPE DATE USING strptime(log_date, '%Y%m%d')::DATE"
        )
        row = connection.execute("SELECT SUM(amount) FROM orders").fetchone()
        assert row is not None
        expected = row[0]
    finally:
        connection.close()
    md.register(md.DuckDBSpec(name="warehouse", path=str(alternate)))
    model = authoring_evidence_project / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        .replace("Asia/Shanghai", "UTC")
        .replace("parse=ms.strptime('%Y%m%d')", "parse=None")
    )
    session = mv.session.get_or_create("registered", timezone="UTC")
    output = session.observe({"revenue": session.catalog.metrics.get("sales.revenue")}).compute()
    assert output.to_pandas()["revenue"].tolist() == [expected]
    assert terminal_runs(session) == ()
    saved = output.persist()
    artifact_id = saved.artifact.id
    assert session.persisted_frame(artifact_id).artifact.id == artifact_id
