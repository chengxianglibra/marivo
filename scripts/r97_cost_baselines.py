"""Bind accepted ordinary cost evidence without collecting or relabeling measurements."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from devtools.analysis_r9_cost import candidate
from scripts import r9_qualification_requirements as freeze
from scripts import r96_cost_results as costs

BACKENDS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
INDEXES = (
    "cohort-inventory.json",
    "native-groups-07.json",
    "trino-groups-07.json",
    "clickhouse-groups-07.json",
)
DEFAULT_ACCEPTANCE = (
    freeze.ROOT
    / "docs/superpowers/specs/evidence/r96/acceptance-08/acceptance-after-distributed.json"
)


def _key(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _reference(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": freeze.digest(path.read_bytes())}


def _coordinates(value: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(value.get(field) for field in costs.GROUP_FIELDS)


def _expected() -> set[tuple[object, ...]]:
    return {
        (
            backend,
            {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table"),
            facts,
            "baseline",
            "ordinary",
            route,
            mode,
            recovery,
        )
        for backend in BACKENDS
        for facts in (1000, 100000)
        for route, mode, recovery in (
            ("ibis", None, None),
            ("ibis_python", None, None),
            ("artifact_python", "kernel", None),
            ("artifact_python", "exact_hit", None),
            ("artifact_python", "kernel", "cold"),
            ("artifact_python", "exact_hit", "cold"),
        )
    }


def _numeric_and_resources(sample: Mapping[str, object]) -> None:
    oracle, identity = costs.obj(sample["oracle"]), costs.obj(sample["identity"])
    parts = set(costs.arr(identity["parts"]))
    fixed = sample["requested_route"] == "artifact_python"
    expected_parts = {"row_state"} if fixed else {"original_state", "coverage"}
    if not expected_parts <= parts or not costs.arr(identity["method_bindings"]):
        raise ValueError("The ordinary result must retain its actual method and required parts")
    if not oracle.get("expected_digest") or (
        oracle.get("values_digest", oracle.get("result_digest")) != oracle["expected_digest"]
    ):
        raise ValueError("The original exact value oracle must retain its matching digest")
    observations = costs.obj(sample["observations"])
    sessions = costs.arr(observations["source_sessions"])
    if fixed:
        if sessions:
            raise ValueError("Retained ordinary execution must remain source-offline")
    elif not sessions or any(
        (owner := costs.obj(value)).get("closed") is not True
        or owner.get("backend_disconnected") is not True
        or owner.get("cancel_control_released") is not True
        or owner.get("active_readers") != 0
        or owner.get("staged_relations") != 0
        for value in sessions
    ):
        raise ValueError("Every actual source owner must close readers, controls and connection")
    if sample.get("recovery") == "cold" and (
        oracle.get("state_checked") != "defined"
        or oracle.get("pending_resources") != 0
        or oracle.get("source_native_submissions") != 0
    ):
        raise ValueError("Cold restoration must independently check state and pending resources")


def audit_baselines(acceptance_path: Path) -> dict[str, object]:
    """Resolve the 72 selected original cohorts into their 13 frozen baseline proof owners."""
    acceptance = costs.load(acceptance_path)
    if acceptance.get("schema") != "marivo.r96.acceptance-evidence.v1":
        raise ValueError("Expected an explicit R9.6 cohort acceptance")
    selected = [costs.obj(row) for row in costs.arr(acceptance["accepted_cohorts"])]
    if any(set(row) != {*costs.GROUP_FIELDS, *costs.COHORT_FIELDS} for row in selected):
        raise ValueError("Accepted cohorts require their complete original identity")
    ordinary = [row for row in selected if row["cost_scope"] == "ordinary"]
    if len(ordinary) != 72 or {_coordinates(row) for row in ordinary} != _expected():
        raise ValueError(
            "Exactly the 72 distinct ordinary source, warm and cold groups are required"
        )
    physical = [row for row in selected if row["cost_scope"] != "ordinary"]
    if physical and (
        len(physical) != 2
        or {_coordinates(row) for row in physical}
        != {
            ("clickhouse", "distributed", 100000, "baseline", "physical", route, None, None)
            for route in ("ibis", "ibis_python")
        }
    ):
        raise ValueError(
            "Only the separately owned two Distributed groups may accompany ordinary evidence"
        )
    groups: dict[str, Mapping[str, object]] = {}
    manifests: dict[Path, Mapping[str, object]] = {}
    attachments: list[dict[str, object]] = [_reference(acceptance_path)]
    failures: list[dict[str, object]] = []
    index_root = acceptance_path.parent.parent / "acceptance-07"
    for name in INDEXES:
        path = index_root / name
        index = costs.load(path)
        attachments.append(_reference(path))
        phases = costs.arr(index.get("phases", []))
        if "manifest" in index:
            phases = [*phases, {"manifest": index["manifest"]}]
        for value in phases:
            phase = costs.obj(value)
            declared = phase.get(
                "manifest",
                {
                    "path": str(Path(str(phase.get("directory"))) / "manifest.json"),
                    "sha256": phase.get("manifest_sha256"),
                },
            )
            manifest_path, reference = costs._attachment(declared, path.parent)
            manifest = costs.load(manifest_path)
            if manifest.get("schema") != "marivo.r96.cost-manifest.v1":
                raise ValueError("A raw phase requires its original measurement manifest")
            manifests[manifest_path.parent] = costs.obj(manifest["candidate"])
            attachments.append(reference)
        for value in costs.arr(index["groups"]):
            group = costs.obj(value)
            identity = dict(costs.obj(group["identity"]))
            if "deployment_configuration" not in identity:
                identity["deployment_configuration"] = index["shared_deployment_configuration"]
            key = _key(identity)
            if key in groups:
                raise ValueError("Duplicate indexed cohort identity")
            groups[key] = group
            for raw in costs.arr(group.get("raw_refs", [])):
                if costs.obj(raw).get("status") == "failed":
                    _, reference = costs._attachment(raw, path.parent)
                    failures.append({**reference, "status": "failed"})
    bound: list[dict[str, object]] = []
    cold: list[Mapping[str, object]] = []
    used: set[Path] = set()
    for cohort in ordinary:
        selected_group = groups.get(_key(cohort))
        if selected_group is None or selected_group.get("status") != "passed":
            raise ValueError("An accepted cohort has no successful indexed original group")
        declared_raws = selected_group.get("raw_refs")
        if declared_raws is None:
            declared_raws = [
                costs.obj(item)["raw"] for item in costs.arr(selected_group["measurements"])
            ]
        samples: list[Mapping[str, object]] = []
        references: list[dict[str, object]] = []
        for raw in costs.arr(declared_raws):
            path, reference = costs._attachment(raw, index_root)
            original_candidate = manifests.get(path.parent.parent)
            if path in used or original_candidate is None:
                raise ValueError("An orphan or duplicate raw sample cannot qualify a cohort")
            used.add(path)
            sample = costs.load(path)
            costs.valid(sample, original_candidate)
            if costs._group_identity(sample) != cohort:
                raise ValueError(
                    "A raw sample differs from the selected candidate/layout/deployment cohort"
                )
            _numeric_and_resources(sample)
            samples.append(sample)
            references.append({**reference, "execution_candidate": cohort["execution_candidate"]})
        if (
            len(samples) != 4
            or {sample["iteration"] for sample in samples} != {0, 1, 2, 3}
            or any(
                type(sample["iteration"]) is not int
                or sample["temperature"] != ("warmup" if sample["iteration"] == 0 else "measured")
                for sample in samples
            )
        ):
            raise ValueError("Each selected cohort needs exactly warmup 0 and measurements 1, 2, 3")
        passed, reason = costs.repeated(samples, costs.obj(samples[0]["candidate"]), warmup=True)
        if not passed:
            raise ValueError(reason)
        cold.extend(sample for sample in samples if sample.get("recovery") == "cold")
        bound.append({"cohort": dict(cohort), "status": "passed", "samples": references})
    if errors := costs._cold_resources(cold):
        raise ValueError("; ".join(errors))
    requirements = [
        freeze.obj(value)
        for value in freeze.arr(freeze.load(freeze.OUTPUT)["requirements"])
        if freeze.obj(value)["family"] == "cost-baseline"
    ]
    if len(requirements) != 13:
        raise ValueError("The frozen ordinary baseline denominator must remain 13")
    results: dict[str, object] = {}
    for row in requirements:
        owners = [
            group
            for group in bound
            if (
                costs.obj(group["cohort"])["requested_route"] == row["route"]
                and (
                    row["backend"] == "none"
                    or costs.obj(group["cohort"])["backend"] == row["backend"]
                )
            )
        ]
        evidence = [ref for group in owners for ref in costs.arr(group["samples"])]
        proofs = list(freeze.arr(row["required_proofs"]))
        expected_proofs = {
            "cost",
            "numeric_state",
            "publication_recovery" if row["backend"] == "none" else "resource_cancel",
        }
        if set(proofs) != expected_proofs or len(owners) != (48 if row["backend"] == "none" else 2):
            raise ValueError(
                "Each frozen baseline needs its exact proof slots and original route owners"
            )
        results[str(row["id"])] = {
            "status": "passed",
            "required_proofs": proofs,
            "proofs": {str(proof): {"status": "passed", "evidence": evidence} for proof in proofs},
            "execution_candidates": sorted(
                {str(costs.obj(group["cohort"])["execution_candidate"]) for group in owners}
            ),
            "timing_authority": "Original raw execution candidates only; no current timing grant",
        }
    return {
        "schema": "marivo.r97.cost-baseline-binding.v1",
        "candidate": candidate(),
        "acceptance": attachments[0],
        "results": results,
        "counts": {"passed": 13},
        "accepted_ordinary_cohorts": 72,
        "group_count": len(bound),
        "groups": bound,
        "historical_failures": failures,
        "attachments": attachments,
        "validator_sources": [_reference(Path(__file__)), _reference(Path(costs.__file__))],
        "full_cost_acceptance": False,
        "new_cost_execution": False,
        "boundary": "Only original ordinary observations; shared, physical and V17 acceptance remain separately owned",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_baselines(args.acceptance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
