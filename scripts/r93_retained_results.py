"""Bind only the executed public retained numeric profile requirements."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import NamedTemporaryFile

from scripts import r9_qualification_requirements as freeze
from scripts.r93_evidence_transport import verify


def result(directory: Path) -> dict[str, freeze.Json]:
    verify(directory)
    payload = freeze.load(directory / "freeze")
    run = freeze.read(directory / "run.json")
    authority = freeze.obj(payload["candidate"])["content_sha256"]
    if run["candidate_sha256"] != authority or run["exit_code"] != 0:
        raise ValueError("Successful unchanged candidate execution required")
    tests = ET.parse(directory / "junit.xml").getroot().findall(".//testcase")
    owned_tests = [
        item for item in tests if item.attrib.get("classname") == "tests.test_r93_retained_numeric"
    ]
    seen: set[str] = set()
    for item in owned_tests:
        name = item.attrib["name"]
        if name in seen:
            raise ValueError(f"Duplicate retained invocation: {name}")
        seen.add(name)
    passed = {item.attrib["name"] for item in owned_tests if not list(item)}
    supporting = {
        name
        for name in passed
        if name.startswith("test_public_retained_cell_boundaries_supporting[")
    }
    attachments = freeze.obj(run["attachments"])
    expected_supporting = {
        f"test_public_retained_cell_boundaries_supporting[{method}-empty-null-unavailable]": family
        + "."
        + method
        + "@v1"
        for family, methods in (
            ("deviation", ("zscore", "mad")),
            ("association", ("pearson", "spearman", "kendall")),
            ("forecast", ("naive", "drift", "seasonal_naive")),
            ("time", ("runs",)),
        )
        for method in methods
    }
    observed_supporting: set[str] = set()
    for path in sorted(directory.glob("supporting-retained-*.json")):
        if attachments.get(path.name) != freeze.digest(path.read_bytes()):
            raise ValueError("Supporting receipt is not bound to the execution manifest")
        receipt = freeze.read(path)
        family = str(receipt["family"])
        method = family.split(".")[1].split("@")[0]
        node = f"test_public_retained_cell_boundaries_supporting[{method}-empty-null-unavailable]"
        physical = freeze.obj(receipt["physical_key"])
        if (
            expected_supporting.get(node) != family
            or node not in supporting
            or node in observed_supporting
            or receipt["scenario"] != "empty-null-unavailable"
            or receipt["numeric_unknown_runtime_verified"] is not False
            or receipt["source_semantic_duckdb_forbidden"] is not True
            or physical["method"] != family
            or physical["route"] != "artifact_python"
            or freeze.obj(physical["shape"])["kind"] != "FixedShape"
            or not freeze.arr(receipt["retained_parts"])
        ):
            raise ValueError("Supporting Cell receipt exceeds its observed boundary")
        observed_supporting.add(node)
    if observed_supporting != supporting:
        raise ValueError("Unbound supporting Cell invocation")
    passed -= supporting
    rows = {
        str(freeze.obj(row)["id"]): freeze.obj(row) for row in freeze.arr(payload["requirements"])
    }
    overrides: dict[str, freeze.Json] = {}
    for path in sorted(directory.glob("retained-*.json")):
        if attachments.get(path.name) != freeze.digest(path.read_bytes()):
            raise ValueError("Retained receipt is not bound to the execution manifest")
        receipt = freeze.read(path)
        identity = str(receipt["id"])
        if identity not in rows or identity in overrides:
            raise ValueError("Unknown or duplicate retained requirement")
        row = rows[identity]
        family = str(row["family"])
        method = family.split(".")[1].split("@")[0]
        profile = str(row["scenario"])
        if profile in {
            "int64-near-extremes",
            "finite-float64",
            "decimal-exact",
        }:
            function = (
                "test_public_retained_time_numeric_risk"
                if family.startswith(("forecast.", "time."))
                else "test_public_retained_numeric_risk"
            )
        elif profile == "complete-identity-domain":
            function = "test_public_retained_identity"
        elif family == "time.runs@v1" and profile in {
            "full-grid-dst-adjacency-duration",
            "cross-batch-long-run",
            "unavailable-breaks",
        }:
            function = "test_public_retained_runs_algorithm"
        elif family.startswith("deviation.") and profile in {
            "original-fit-domain",
            "zero-scale",
            "decimal-center-scale-extremes",
        }:
            function = "test_public_retained_deviation_algorithm"
        elif family.startswith("forecast.") and profile in {
            "complete-training",
            "model-innovation-variance",
            "future-grid-interval",
        }:
            function = "test_public_retained_forecast_algorithm"
        elif family.startswith("association.") and profile in {
            "complete-pair-lag-order",
            "invalid-constant-pairs",
            "centered-numeric-extremes",
            "ties-average-rank-tau-b",
        }:
            function = "test_public_retained_association_algorithm"
        else:
            raise ValueError("Unsupported retained scenario invocation")
        node = (
            f"{function}[{profile}]"
            if function == "test_public_retained_runs_algorithm"
            else f"{function}[{method}-{profile}]"
        )
        if node not in passed:
            raise ValueError("Required retained scenario invocation not passed")
        if (
            row["backend"] != "none"
            or row["route"] != "artifact_python"
            or row["profile"] != "retained-input"
        ):
            raise ValueError("Only retained numeric requirements may be bound")
        physical = freeze.obj(receipt["physical_key"])
        if set(map(str, freeze.arr(row["required_proofs"]))) != {
            "numeric_state",
            "type_domain_parts",
        }:
            raise ValueError("Unsupported retained proof obligation")
        if (
            physical["method"] != family
            or physical["route"] != "artifact_python"
            or freeze.obj(physical["shape"])["kind"] != "FixedShape"
            or receipt["source_semantic_duckdb_forbidden"] is not True
        ):
            raise ValueError("Retained method authority mismatch")
        required_parts = (
            {"pair_inputs", "association_state"}
            if family.startswith("association.")
            else {"fit_inputs", "fit_state"}
            if family.startswith("deviation.")
            else {"training_inputs", "forecast_state", "future_cells"}
            if family.startswith("forecast.")
            else {"condition_cells", "run_cells", "grid_cells"}
        )
        if not required_parts <= set(map(str, freeze.arr(receipt["retained_parts"]))):
            raise ValueError("Incomplete retained method parts")
        proofs: dict[str, freeze.Json] = {
            str(obligation): {
                "status": "passed",
                "requirement_id": identity,
                "candidate_sha256": authority,
                "attachment": path.name,
                "sha256": freeze.digest(path.read_bytes()),
            }
            for obligation in freeze.arr(row["required_proofs"])
        }
        overrides[identity] = {
            "status": "passed",
            "proof_class": "runtime",
            "expectation": row["expectation"],
            "exit_code": 0,
            "skipped": False,
            "owner_sha256": payload["owners"],
            "environment": {
                "candidate_dependencies": freeze.obj(payload["candidate"])["dependencies"],
                "source_forbidden": True,
            },
            "input_sha256": freeze.digest(
                freeze.encode(
                    {"x": receipt["original_x"], "y": receipt["original_y"], "key": physical}
                )
            ),
            "oracle": receipt["oracle"],
            "command": run["command"],
            "started": run["started"],
            "finished": run["finished"],
            "test_node": "tests/test_r93_retained_numeric.py::" + node,
            "proofs": proofs,
        }
    if len(overrides) != len(passed):
        raise ValueError("Missing, duplicate or unbound retained invocation")
    return {"candidate_sha256": authority, "default": "unverified", "overrides": overrides}


def save_result(directory: Path, result: dict[str, freeze.Json]) -> dict[str, freeze.Json]:
    output = directory / "results-index.json"
    encoded = freeze.encode(result)
    if output.exists() and output.read_bytes() != encoded:
        raise ValueError("Existing result index differs; preserve its evidence binding")
    payload = freeze.load(directory / "freeze")
    with NamedTemporaryFile(dir=directory, prefix=".result-validation-", delete=False) as temporary:
        temporary.write(encoded)
        temporary_path = Path(temporary.name)
    try:
        counts = freeze.verify_results(payload, temporary_path)
        temporary_path.replace(output)
        return counts
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def bind(directory: Path) -> dict[str, freeze.Json]:
    return save_result(directory, result(directory))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    print(freeze.encode(bind(arguments.directory)).decode())
