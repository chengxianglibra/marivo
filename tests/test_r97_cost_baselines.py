"""Ordinary evidence binding refuses tampered, mixed and incomplete original cohorts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import r96_cost_results as costs
from scripts import r97_cost_baselines as baselines

ORIGINAL = {"content_sha256": "original-observation", "head": "historical-head"}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


@pytest.fixture
def accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(baselines, "candidate", lambda: {"head": "current-delivery"})
    phase = tmp_path / "original-phase"
    _write(
        phase / "manifest.json", {"schema": "marivo.r96.cost-manifest.v1", "candidate": ORIGINAL}
    )
    groups: list[dict[str, object]] = []
    cohorts: list[dict[str, object]] = []
    for number, coordinates in enumerate(sorted(baselines._expected(), key=str)):
        fields = dict(zip(costs.GROUP_FIELDS, coordinates, strict=True))
        fixed = fields["requested_route"] == "artifact_python"
        cold = fields["recovery"] == "cold"
        rows: list[dict[str, object]] = []
        for iteration in range(4):
            record: dict[str, object] = {
                **fields,
                "schema": "marivo.r96.cost-sample.v1",
                "status": "passed",
                "candidate": ORIGINAL,
                "iteration": iteration,
                "temperature": "warmup" if iteration == 0 else "measured",
                "elapsed_seconds": 0.01,
                "identity": {
                    "artifact_ref": f"result-{number}-{iteration}",
                    "root_route": fields["requested_route"],
                    "parts": ["row_state"] if fixed else ["original_state", "coverage"],
                    "method_bindings": [{"method": "exact-original-total"}],
                },
                "oracle": {
                    "passed": True,
                    "result_digest": "exact-values-and-state",
                    "expected_digest": "exact-original-total",
                    "values_digest": "exact-original-total",
                    "state_checked": "defined",
                    "pending_resources": 0,
                    "source_native_submissions": 0,
                },
                "observations": {
                    "resource_closed": True,
                    "source_submissions": [] if fixed else [{"state": "succeeded"}],
                    "source_sessions": []
                    if fixed
                    else [
                        {
                            "closed": True,
                            "backend_disconnected": True,
                            "cancel_control_released": True,
                            "active_readers": 0,
                            "staged_relations": 0,
                        }
                    ],
                    "storage_writes": []
                    if fields["fixed_mode"] == "exact_hit"
                    else [{"receipt": "primary-and-parts"}],
                    "phase_calls": {"fixed_kernel": int(fields["fixed_mode"] == "kernel")},
                    "native_submissions": [{"category": "store"}] if fixed else [],
                },
            }
            if fixed:
                record["producer_identity"] = {"artifact_ref": f"producer-{number}-{iteration}"}
            if cold:
                record["process"] = {"pid": 100 + number * 4 + iteration, "parent_pid": 1}
            path = phase / "samples" / f"{number}-{iteration}.json"
            _write(path, record)
            rows.append(baselines._reference(path))
        identity = costs._group_identity(record)
        groups.append({"identity": identity, "status": "passed", "raw_refs": rows})
        cohorts.append(identity)
    failure = phase / "samples" / "historical-failure.json"
    _write(failure, {"status": "failed", "candidate": ORIGINAL, "error_type": "TimeoutError"})
    groups.append(
        {
            "identity": {
                "historical": "failed-unselected-cohort",
                "deployment_configuration": None,
            },
            "status": "failed",
            "raw_refs": [{**baselines._reference(failure), "status": "failed"}],
        }
    )
    index = {
        "phases": [{"manifest": baselines._reference(phase / "manifest.json")}],
        "groups": groups,
    }
    for name in baselines.INDEXES:
        _write(
            tmp_path / "acceptance-07" / name,
            index if name == baselines.INDEXES[0] else {"groups": []},
        )
    acceptance = tmp_path / "acceptance-08" / "acceptance.json"
    _write(acceptance, {"schema": "marivo.r96.acceptance-evidence.v1", "accepted_cohorts": cohorts})
    return acceptance


def _change_raw(acceptance: Path, change: str, *, refresh: bool = True) -> None:
    index_path = acceptance.parent.parent / "acceptance-07" / baselines.INDEXES[0]
    index = dict(costs.load(index_path))
    groups = list(costs.arr(index["groups"]))
    position = next(
        number
        for number, row in enumerate(groups)
        if (
            costs.obj(costs.obj(row)["identity"]).get("requested_route")
            == ("artifact_python" if change in ("fixed-source", "cold-process") else "ibis")
            and costs.obj(costs.obj(row)["identity"]).get("recovery")
            == ("cold" if change == "cold-process" else None)
        )
    )
    group = dict(costs.obj(groups[position]))
    groups[position] = group
    index["groups"] = groups
    references = list(costs.arr(group["raw_refs"]))
    group["raw_refs"] = references
    reference = costs.obj(references[0])
    path = Path(str(reference["path"]))
    raw = dict(costs.load(path))
    if change == "candidate":
        raw["candidate"] = {"content_sha256": "different-candidate"}
    elif change == "mixed-layout":
        raw["environment"] = {"fixture_layout": "indexed-keys"}
    elif change == "parts":
        raw["identity"] = {**costs.obj(raw["identity"]), "parts": []}
    elif change == "numeric":
        raw["oracle"] = {**costs.obj(raw["oracle"]), "values_digest": "different-total"}
    elif change == "resources":
        raw["observations"] = {**costs.obj(raw["observations"]), "resource_closed": False}
    elif change == "fixed-source":
        raw["observations"] = {
            **costs.obj(raw["observations"]),
            "source_submissions": [{"state": "succeeded"}],
        }
    elif change == "cold-process":
        raw["process"] = {**costs.obj(raw["process"]), "pid": 1}
    elif change == "missing":
        path.unlink()
        return
    elif change == "orphan":
        other = path.parent.parent.parent / "orphan" / "samples" / path.name
        _write(other, raw)
        references[0] = baselines._reference(other)
        _write(index_path, index)
        return
    _write(path, raw)
    if refresh:
        references[0] = baselines._reference(path)
        _write(index_path, index)


def test_baselines_bind_original_owners_and_keep_skipped_costs_outside_grants(
    accepted: Path,
) -> None:
    report = baselines.audit_baselines(accepted)
    assert report["counts"] == {"passed": 13}
    assert report["group_count"] == 72
    assert report["full_cost_acceptance"] is False
    assert report["new_cost_execution"] is False
    assert len(costs.arr(report["historical_failures"])) == 1
    for identifier, value in costs.obj(report["results"]).items():
        assert identifier.startswith("R9:cost-baseline:")
        row = costs.obj(value)
        assert set(costs.obj(row["proofs"])) == set(costs.arr(row["required_proofs"]))
        assert row["execution_candidates"] == ["original-observation"]
        assert all(costs.obj(proof)["evidence"] for proof in costs.obj(row["proofs"]).values())


@pytest.mark.parametrize(
    "change",
    (
        "candidate",
        "mixed-layout",
        "parts",
        "numeric",
        "resources",
        "fixed-source",
        "cold-process",
        "missing",
        "orphan",
    ),
)
def test_baselines_refuse_invalid_original_evidence(accepted: Path, change: str) -> None:
    _change_raw(accepted, change)
    with pytest.raises((ValueError, FileNotFoundError)):
        baselines.audit_baselines(accepted)


def test_baselines_refuse_changed_hash_and_missing_cohort(accepted: Path) -> None:
    _change_raw(accepted, "numeric", refresh=False)
    with pytest.raises(ValueError, match="hash"):
        baselines.audit_baselines(accepted)
    declaration = dict(costs.load(accepted))
    declaration["accepted_cohorts"] = costs.arr(declaration["accepted_cohorts"])[1:]
    _write(accepted, declaration)
    with pytest.raises(ValueError, match="72 distinct"):
        baselines.audit_baselines(accepted)


def test_cli_keeps_existing_output_immutable(
    accepted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = accepted.parent / "new-output" / "nested" / "bound.json"
    monkeypatch.setattr(
        sys, "argv", ["r97_cost_baselines", "--acceptance", str(accepted), "--output", str(output)]
    )
    assert baselines.main() == 0
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        baselines.main()
    assert output.read_bytes() == before
