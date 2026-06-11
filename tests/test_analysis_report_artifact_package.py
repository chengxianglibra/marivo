from __future__ import annotations

import json

import pytest

from tests.test_analysis_report_artifact_validation import _valid_artifact


def test_write_and_load_report_artifact_round_trips(tmp_path) -> None:
    from marivo.analysis.publish.report_package import load_report_artifact, write_report_artifact

    artifact = _valid_artifact()
    write_report_artifact(artifact, tmp_path)

    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "report_spec.json").is_file()
    assert (tmp_path / "flow.json").is_file()
    assert (tmp_path / "grounding.json").is_file()
    assert (tmp_path / "datasets" / "headline_metrics.json").is_file()
    assert (tmp_path / "evidence" / "artifact_observe_1.json").is_file()

    restored = load_report_artifact(tmp_path)

    assert restored == artifact
    assert (
        json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["kind"]
        == "marivo_analysis_report"
    )


def test_load_report_artifact_validates_required_files(tmp_path) -> None:
    from marivo.analysis.publish.report_package import load_report_artifact

    try:
        load_report_artifact(tmp_path)
    except FileNotFoundError as exc:
        assert "manifest.json" in str(exc)
    else:
        raise AssertionError("load_report_artifact should fail on an empty package directory")


def test_write_report_artifact_removes_stale_dataset_and_evidence_files(tmp_path) -> None:
    from marivo.analysis.publish.report_package import load_report_artifact, write_report_artifact

    artifact = _valid_artifact()
    write_report_artifact(artifact, tmp_path)

    artifact_without_snapshots = artifact.model_copy(update={"datasets": {}, "evidence": {}})
    write_report_artifact(artifact_without_snapshots, tmp_path)

    assert not (tmp_path / "datasets" / "headline_metrics.json").exists()
    assert not (tmp_path / "evidence" / "artifact_observe_1.json").exists()
    assert load_report_artifact(tmp_path) == artifact_without_snapshots


def test_write_report_artifact_rejects_path_unsafe_dataset_ids(tmp_path) -> None:
    from marivo.analysis.publish.report_package import write_report_artifact

    artifact = _valid_artifact()
    dataset = artifact.datasets["headline_metrics"]
    unsafe_dataset = dataset.model_copy(
        update={
            "dataset_id": "../escape",
            "metadata": dataset.metadata.model_copy(update={"dataset_id": "../escape"}),
        }
    )
    unsafe_artifact = artifact.model_copy(update={"datasets": {"../escape": unsafe_dataset}})

    with pytest.raises(ValueError):
        write_report_artifact(unsafe_artifact, tmp_path)

    assert not (tmp_path / "escape.json").exists()


def test_write_report_artifact_rejects_path_unsafe_evidence_ids(tmp_path) -> None:
    from marivo.analysis.publish.report_package import write_report_artifact

    unsafe_artifact = _valid_artifact().model_copy(
        update={"evidence": {"../escape": {"summary": "bad"}}}
    )

    with pytest.raises(ValueError):
        write_report_artifact(unsafe_artifact, tmp_path)

    assert not (tmp_path / "escape.json").exists()


def test_write_report_artifact_preserves_existing_snapshots_on_serialization_error(
    tmp_path,
) -> None:
    from marivo.analysis.publish.report_package import write_report_artifact

    artifact = _valid_artifact()
    write_report_artifact(artifact, tmp_path)

    unserializable_artifact = artifact.model_copy(update={"evidence": {"bad": {"bad": object()}}})

    with pytest.raises(TypeError):
        write_report_artifact(unserializable_artifact, tmp_path)

    assert (tmp_path / "datasets" / "headline_metrics.json").is_file()
    assert (tmp_path / "evidence" / "artifact_observe_1.json").is_file()


def test_write_report_artifact_rejects_non_finite_evidence_json(tmp_path) -> None:
    from marivo.analysis.publish.report_package import write_report_artifact

    unsafe_artifact = _valid_artifact().model_copy(
        update={"evidence": {"artifact_observe_1": {"value": float("nan")}}}
    )

    with pytest.raises(ValueError):
        write_report_artifact(unsafe_artifact, tmp_path)

    assert not (tmp_path / "evidence" / "artifact_observe_1.json").exists()


def test_load_report_artifact_rejects_non_standard_json_constants(tmp_path) -> None:
    from marivo.analysis.publish.report_package import load_report_artifact, write_report_artifact

    write_report_artifact(_valid_artifact(), tmp_path)
    evidence_path = tmp_path / "evidence" / "artifact_observe_1.json"
    evidence_path.write_text('{"value": NaN}', encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_report_artifact(tmp_path)

    assert str(evidence_path) in str(exc_info.value)
    assert "NaN" in str(exc_info.value)
