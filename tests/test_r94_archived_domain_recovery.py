"""Re-run native domain continuations from hashed retained projects, without services."""

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from scripts.r9_qualification_requirements import Json, arr, digest, obj, read
from tests.r94_native_domain_k_worker import run_ids
from tests.shared_fixtures import DslCaseFactory


def restore_project(directory: Path, index: str, backend: str, root: Path) -> str:
    archive = obj(read(directory / index)[backend])
    chunks: list[bytes] = []
    for raw in arr(archive["parts"]):
        part = obj(raw)
        name = part["path"]
        assert isinstance(name, str)
        chunk = (directory / name).read_bytes()
        assert digest(chunk) == part["sha256"]
        chunks.append(chunk)
    packed = b"".join(chunks)
    assert digest(packed) == archive["archive_sha256"]
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as tar:
        tar.extractall(root, filter="data")
    for name, expected in obj(archive["entries"]).items():
        assert digest((root / name).read_bytes()) == expected
    assert not (root / "models").exists()
    assert not (root / "source.duckdb").exists()
    sha = archive["archive_sha256"]
    assert isinstance(sha, str)
    return sha


def test_registered_templates_and_non_scalar_units_keep_their_actions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
    )
    assert "relation.correlate(*others)" in {action.call for action in values.contract().actions}
    statistic = values.summarize(mv.count())
    assert "relation.compare(baseline)" in {action.call for action in statistic.contract().actions}
    assert "comparison_unavailable" not in dict(statistic.contract()._facts)
    assert "relation.correlate(*others)" not in {
        action.call for action in statistic.contract().actions
    }
    assert isinstance(
        statistic.compare(statistic, design=mv.CohortContrast()), mv.LogicalDifferenceRelation
    )


def test_statistic_card_requires_a_registered_frozen_comparison_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = Path(__file__).resolve().parents[1] / (
        "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-root-k-bindings-01"
    )
    restore_project(base, "supplemental-projects.json", "postgres", tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    manifest = read(tmp_path / "r94-domain.json")
    session_id = manifest["session"]
    assert isinstance(session_id, str)
    session = mv.session.resume(session_id, by="id")
    before = run_ids(session)
    for method in ("count", "count_defined", "sum", "min", "max"):
        reference = obj(obj(manifest["supplement"])[f"observation_{method}"])["artifact"]
        assert isinstance(reference, str)
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedStatisticRelation)
        card = value.contract()
        assert "relation.compare(baseline)" not in {action.call for action in card.actions}
        assert "relation.ratio(other)" in {action.call for action in card.actions}
        assert "relation.correlate(*others)" not in {action.call for action in card.actions}
        assert dict(card._facts)["correlation_unavailable"] == (
            "Scalar has no Entity/category/time statistical units"
        )
        reason = "quantity template has no registered comparison rule"
        assert dict(card._facts)["comparison_unavailable"] == reason
        with pytest.raises(DatasetConstructionError) as caught:
            value.compare(value, design=mv.CohortContrast())
        assert caught.value.received == reason
        assert caught.value.expected is not None and caught.value.repair is not None
        ratio_card = value.ratio(value).contract()
        assert ratio_card.kind == "relation_ratio"
        assert "relation.compare(baseline)" not in {action.call for action in ratio_card.actions}
        assert "relation.ratio(other)" in {action.call for action in ratio_card.actions}
    assert run_ids(session) == before
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_R94_ARCHIVE_RECOVERY") != "1",
    reason="Set MARIVO_R94_ARCHIVE_RECOVERY=1 to validate archived native producers",
)
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_archived_native_root_continuations(backend: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    base = repository / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence"
    archive_directory = base / "native-domain-bindings-01"
    archive_sha = restore_project(archive_directory, "portable-projects.json", backend, tmp_path)
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / f"supplement-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_native_domain_k_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"{backend}-{phase}.log").write_text(
                completed.stdout + completed.stderr
            )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[1]["new_runs"] == reports[1]["kernels"] == 0
    assert reports[0]["outputs"] == reports[1]["outputs"]
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"native-root-k-{backend}.json").write_text(
            json.dumps(
                {
                    "backend": backend,
                    "archive_sha256": archive_sha,
                    "reports": reports,
                },
                sort_keys=True,
            )
        )


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_R94_DERIVED_RECOVERY") != "1",
    reason="Set MARIVO_R94_DERIVED_RECOVERY=1 to validate retained statistic K",
)
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_archived_native_statistic_continuations(backend: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    base = repository / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence"
    archive_sha = restore_project(
        base / "native-root-k-bindings-01", "supplemental-projects.json", backend, tmp_path
    )
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / f"statistic-K-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_statistic_k_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"{backend}-statistic-{phase}.log").write_text(
                completed.stdout + completed.stderr
            )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["outputs"] == reports[1]["outputs"]
    assert reports[0]["consumed_K"] == reports[1]["consumed_K"]
    assert reports[1]["successful_new_runs"] == reports[1]["successful_kernels"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"native-statistic-k-{backend}.json").write_text(
            json.dumps(
                {"backend": backend, "archive_sha256": archive_sha, "reports": reports},
                sort_keys=True,
            )
        )


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_R94_FIELD_RECOVERY") != "1",
    reason="Set MARIVO_R94_FIELD_RECOVERY=1 to validate retained owned fields",
)
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_archived_native_owned_field_continuations(backend: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    base = repository / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence"
    archive_sha = restore_project(
        base / "native-statistic-k-bindings-01", "statistic-projects.json", backend, tmp_path
    )
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / f"owned-field-K-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_owned_field_k_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"{backend}-owned-field-{phase}.log").write_text(
                completed.stdout + completed.stderr
            )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["outputs"] == reports[1]["outputs"]
    assert reports[0]["views"] == reports[1]["views"]
    assert reports[0]["consumed_K"] == reports[1]["consumed_K"]
    assert reports[1]["new_runs"] == reports[1]["kernels"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"native-owned-field-k-{backend}.json").write_text(
            json.dumps(
                {"backend": backend, "archive_sha256": archive_sha, "reports": reports},
                sort_keys=True,
            )
        )


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_R94_HISTORY_FIELD_RECOVERY") != "1",
    reason="Set MARIVO_R94_HISTORY_FIELD_RECOVERY=1 to validate retained History fields",
)
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_archived_native_history_field_continuations(backend: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    base = repository / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence"
    archive_sha = restore_project(
        base / "native-statistic-k-bindings-01", "statistic-projects.json", backend, tmp_path
    )
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / f"history-field-K-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_history_field_k_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"{backend}-history-field-{phase}.log").write_text(
                completed.stdout + completed.stderr
            )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["outputs"] == reports[1]["outputs"]
    assert reports[0]["consumed_K"] == reports[1]["consumed_K"]
    assert reports[1]["new_runs"] == reports[1]["kernels"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"native-history-field-k-{backend}.json").write_text(
            json.dumps(
                {"backend": backend, "archive_sha256": archive_sha, "reports": reports},
                sort_keys=True,
            )
        )


def test_retained_retention_preserves_boolean_field_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = Path(__file__).resolve().parents[1] / (
        "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-statistic-k-bindings-01"
    )
    restore_project(base, "statistic-projects.json", "postgres", tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    manifest = read(tmp_path / "r94-domain.json")
    session_id = manifest["session"]
    assert isinstance(session_id, str)
    session = mv.session.resume(session_id, by="id")
    before = run_ids(session)
    for name in ("status", "true", "false", "unknown"):
        reference = obj(obj(manifest["supplement"])["retention_" + name])["artifact"]
        assert isinstance(reference, str)
        value = session.artifact(reference)
        if name == "status":
            assert isinstance(value, mv.MaterializedBooleanRelation)
        else:
            assert isinstance(value, mv.MaterializedSelectedBooleanRelation)
        value.value.eq(True)
        value.value.eq(False)
    assert run_ids(session) == before
    assert session._runtime.store.resources(session.id) == ()


def test_retained_journey_count_keys_stay_fixed_and_typed() -> None:
    from marivo.analysis.methods.journey_physical import consumers
    from marivo.analysis.methods.physical import DurationType, FixedShape, NoTime, ScalarType
    from marivo.analysis.methods.semantics import MethodKey

    for method in ("row.count", "row.count_defined"):
        declarations = consumers(MethodKey(method))
        assert {item.key.input_types for item in declarations} == {
            (ScalarType("string"),),
            (ScalarType("timestamp"),),
            (DurationType("us"),),
        }
        assert all(item.key.shape == FixedShape(NoTime()) for item in declarations)
        assert all(item.key.input_domains == ("journey",) for item in declarations)
        assert all(item.key.route == "artifact_python" for item in declarations)
        assert all(item.precision == "checked_int64" for item in declarations)


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_R94_JOURNEY_RETENTION_RECOVERY") != "1",
    reason="Set MARIVO_R94_JOURNEY_RETENTION_RECOVERY=1 to validate retained field K",
)
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_archived_native_journey_retention_continuations(backend: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    base = repository / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence"
    archive_sha = restore_project(
        base / "native-statistic-k-bindings-01", "statistic-projects.json", backend, tmp_path
    )
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / f"journey-retention-K-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_journey_retention_k_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"{backend}-journey-retention-{phase}.log").write_text(
                completed.stdout + completed.stderr
            )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["outputs"] == reports[1]["outputs"]
    assert reports[0]["consumed_K"] == reports[1]["consumed_K"]
    assert reports[1]["new_runs"] == reports[1]["kernels"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"native-journey-retention-k-{backend}.json").write_text(
            json.dumps(
                {"backend": backend, "archive_sha256": archive_sha, "reports": reports},
                sort_keys=True,
            )
        )
