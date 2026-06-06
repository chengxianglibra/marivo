"""Directory load/write helpers for Marivo report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marivo.analysis.publish.report_models import (
    Dataset,
    Flow,
    Grounding,
    MarivoReportArtifact,
    ReportManifest,
    ReportSpec,
)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(path)

    def reject_non_standard_constant(value: str) -> None:
        raise ValueError(f"{path}: non-standard JSON constant is not allowed: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_non_standard_constant,
    )


def _render_json(payload: Any) -> str:
    return json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _remove_json_files(root: Path) -> None:
    for path in root.glob("*.json"):
        if path.is_file():
            path.unlink()


def _package_json_path(root: Path, stem: str, label: str) -> Path:
    if not stem or stem in {".", ".."} or "/" in stem or "\\" in stem:
        raise ValueError(f"{label} is not safe for a package file name: {stem!r}")
    return root / f"{stem}.json"


def load_report_artifact(root: str | Path) -> MarivoReportArtifact:
    """Load a report artifact from the canonical package directory layout."""
    package_root = Path(root)
    datasets_root = package_root / "datasets"
    evidence_root = package_root / "evidence"
    datasets = {
        path.stem: Dataset.model_validate(_read_json(path))
        for path in sorted(datasets_root.glob("*.json"))
    }
    evidence = {path.stem: _read_json(path) for path in sorted(evidence_root.glob("*.json"))}
    return MarivoReportArtifact(
        manifest=ReportManifest.model_validate(_read_json(package_root / "manifest.json")),
        report_spec=ReportSpec.model_validate(_read_json(package_root / "report_spec.json")),
        flow=Flow.model_validate(_read_json(package_root / "flow.json")),
        grounding=Grounding.model_validate(_read_json(package_root / "grounding.json")),
        datasets=datasets,
        evidence=evidence,
    )


def write_report_artifact(artifact: MarivoReportArtifact, root: str | Path) -> None:
    """Write a report artifact to the canonical package directory layout."""
    package_root = Path(root)
    writes = [
        (package_root / "manifest.json", _render_json(artifact.manifest.model_dump(mode="json"))),
        (
            package_root / "report_spec.json",
            _render_json(artifact.report_spec.model_dump(mode="json")),
        ),
        (package_root / "flow.json", _render_json(artifact.flow.model_dump(mode="json"))),
        (package_root / "grounding.json", _render_json(artifact.grounding.model_dump(mode="json"))),
    ]
    writes.extend(
        (
            _package_json_path(package_root / "datasets", dataset.dataset_id, "dataset id"),
            _render_json(dataset.model_dump(mode="json")),
        )
        for dataset in artifact.datasets.values()
    )
    writes.extend(
        (
            _package_json_path(package_root / "evidence", evidence_id, "evidence id"),
            _render_json(evidence_payload),
        )
        for evidence_id, evidence_payload in artifact.evidence.items()
    )
    _remove_json_files(package_root / "datasets")
    _remove_json_files(package_root / "evidence")
    for path, text in writes:
        _write_text(path, text)
