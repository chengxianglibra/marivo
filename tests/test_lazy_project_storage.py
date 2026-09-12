"""One explicit project target, deferred authorization and no target fallback."""

from pathlib import Path

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.project_storage import configured_target
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    ProjectObjectBindings,
    ProjectTarget,
    access_payload,
    decode_access,
    object_access,
)
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database


def test_missing_storage_configuration_is_local(tmp_path: Path) -> None:
    assert isinstance(configured_target(tmp_path), LocalTarget)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "example"\n')
    assert isinstance(configured_target(tmp_path), LocalTarget)


def test_only_explicit_object_credentials_are_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "marivo.toml"
    manifest.write_text("""[analysis]
storage = "object:archive"
[analysis.object_stores.archive]
endpoint_url = "https://objects.example.test"
bucket = "analysis"
access_key_id_env = "MARIVO_TEST_ACCESS_ID"
secret_access_key_env = "MARIVO_TEST_SECRET"
""")
    monkeypatch.delenv("MARIVO_TEST_ACCESS_ID", raising=False)
    monkeypatch.delenv("MARIVO_TEST_SECRET", raising=False)
    assert configured_target(tmp_path) == ObjectTarget("archive")
    binding = ProjectObjectBindings(tmp_path)
    payload = access_payload(binding)
    assert payload == {"project_root": str(tmp_path)}
    assert decode_access(payload) == binding
    with pytest.raises(MaterializationError, match="missing current object credentials"):
        object_access((binding,), "archive")
    monkeypatch.setenv("MARIVO_TEST_ACCESS_ID", "private-access-canary")
    monkeypatch.setenv("MARIVO_TEST_SECRET", "private-secret-canary")
    access = object_access((binding,), "archive")
    assert access.access_key_id == "private-access-canary"
    assert access.secret_access_key == "private-secret-canary"
    assert "private-" not in repr(binding) + repr(access) + manifest.read_text()
    with pytest.raises(MaterializationError, match="missing or ambiguous object binding"):
        object_access((binding,), "other")


@pytest.mark.runtime
@pytest.mark.parametrize(
    "configuration",
    [
        '[analysis]\nstorage = "engine:warehouse"\n',
        '[analysis]\nstorage = "object:missing"\n',
        "[analysis]\nstorage = 42\n",
        "[analysis]\nstorage = [\n",
        """[analysis]
storage = "object:archive"
[analysis.object_stores.archive]
endpoint_url = "https://objects.example.test"
bucket = "analysis"
access_key_id_env = "MARIVO_UNSET_ACCESS_ID"
secret_access_key_env = "MARIVO_UNSET_SECRET"
""",
    ],
)
def test_configuration_failure_is_recorded_without_source_work_or_local_retry(
    tmp_path: Path,
    configuration: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MARIVO_UNSET_ACCESS_ID", raising=False)
    monkeypatch.delenv("MARIVO_UNSET_SECRET", raising=False)
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    (tmp_path / "marivo.toml").write_text(configuration)
    runtime = DatasetRuntime.create(
        tmp_path,
        "configured",
        target=ProjectTarget(tmp_path),
        object_bindings=(ProjectObjectBindings(tmp_path),),
    )
    metric = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"))
        .aggregate()
    )
    with pytest.raises(MaterializationError) as caught:
        metric.execute()
    assert caught.value.stage == "storage_selection"
    assert runtime.last_run_ref is not None
    record = runtime.store.run(runtime.last_run_ref)
    assert (
        record is not None and record.lifecycle == "failed" and record.output_artifact_ref is None
    )
    assert not runtime.statistics.statements
    assert runtime.statistics.events.get("profile_resolution", 0) == 0
    assert runtime.graph().artifacts == ()
    assert runtime.store.resources(runtime.session_ref) == ()
