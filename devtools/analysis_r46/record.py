"""Freeze reproducible input and evidence hashes after the R4.6 gates pass."""

from __future__ import annotations

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/superpowers/specs/evidence/r46"
OWNERS = {
    "V01": (
        "test_members_schema_preflight",
        "test_incompatible_inputs",
        "test_mixed_and_foreign_session",
        "test_unqualified_method",
    ),
    "V02": (
        "test_public_source_repeats",
        "test_explicit_sharing",
        "test_window_composition",
        "test_each_invocation",
    ),
    "V03": (
        "test_fixed_key_",
        "test_equal_values_from_distinct",
        "test_valid_parquet_with_changed",
        "test_source_roundtrip",
    ),
    "V04": (
        "test_four_cells",
        "test_r43_source_parquet",
        "test_r43_empty_three",
        "test_real_ibis_exchange_retains",
        "test_governed_parquet",
    ),
    "V05": (
        "test_r43_checked_stream",
        "test_checks_keep_origin",
        "test_inconsistent_live_method",
        "test_retained_reader_failure",
        "test_native_write_failure",
    ),
    "V06": (
        "test_faults_have_no_success",
        "test_lost_ack",
        "test_unknown_commit",
        "test_unknown_precommit",
        "test_process_exit",
        "test_contradictory_commit",
    ),
    "V07": ("test_two_process_writers",),
    "V08": (
        "test_cold_source_free",
        "test_public_exact_cold",
        "test_public_cold_process",
        "test_fixed_difference_uses",
        "test_fixed_spearman_coefficient",
    ),
    "V09": (
        "test_strict_descriptor",
        "test_old_project_untouched",
        "test_exact_artifact_corruption",
        "test_snapshot_receipt",
        "test_public_session_rejects_existing",
    ),
    "V10": (
        "test_public_j",
        "test_ratio_complete",
        "test_ratio_original",
        "test_numeric_selection",
        "test_public_parquet",
    ),
    "V11": (
        "test_analysis_public",
        "test_bilingual_examples",
        "test_dataset_help",
        "test_workflow_evidence",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = []
    for name in ("contracts", "runtime"):
        root = ET.parse(EVIDENCE / f"installed-wheel/{name}.xml").getroot()
        for case in root.iter("testcase"):
            assert not list(case.iter("failure")) and not list(case.iter("error"))
            cases.append(
                {
                    "name": case.attrib["name"],
                    "class": case.attrib["classname"],
                    "status": "skipped" if case.find("skipped") is not None else "passed",
                }
            )
    matrix: dict[str, object] = {}
    for cell, prefixes in OWNERS.items():
        matches = [case for case in cases if case["name"].startswith(prefixes)]
        assert matches and all(case["status"] == "passed" for case in matches), cell
        matrix[cell] = {"status": "passed", "installed_test_nodes": matches}
    commands = json.loads((EVIDENCE / "installed-wheel/commands.json").read_text())
    assert all(
        item["exit_code"] == (1 if item["name"] == "foreign-import-rejected" else 0)
        for item in commands
    )
    for source in ("table", "parquet"):
        for journey in ("j1", "j2", "j3", "j4"):
            reports = [
                json.loads(
                    (EVIDENCE / f"installed-wheel/{source}-{journey}-{phase}.json").read_text()
                )
                for phase in ("produce", "continue", "recover")
            ]
            assert len({report["pid"] for report in reports}) == 3
            assert reports[1]["continuations"] == reports[2]["continuations"]
    matrix["V12"] = {
        "status": "passed",
        "wheel_sha256": json.loads((EVIDENCE / "installed-wheel/archives.json").read_text())[
            "wheel_sha256"
        ],
        "process_runs": 24,
        "commands": "installed-wheel/commands.json",
        "source_scan": "residue-scan.json",
    }
    assert json.loads((EVIDENCE / "residue-scan.json").read_text())["passed"]
    (EVIDENCE / "matrix.json").write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    # Normalize text logs before hashing; no successful count is inferred from historical logs.
    for path in EVIDENCE.rglob("*.log"):
        content = path.read_text()
        if content:
            path.write_text("\n".join(line.rstrip() for line in content.splitlines()) + "\n")
    inputs = {}
    for base in (
        ROOT / "marivo",
        ROOT / "tests",
        ROOT / "devtools/analysis_r46",
        ROOT / "site/src/content/docs",
        ROOT / "docs/specs/analysis",
    ):
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".md", ".mdx"):
                if "site" in path.parts and "latest" not in path.parts:
                    continue
                inputs[str(path.relative_to(ROOT))] = sha(path)
    for name in (
        "2026-09-26-marivo-full-refactor-acceptance.md",
        "2026-09-28-marivo-full-algebra-dsl-r4-implementation-plan.md",
    ):
        path = ROOT / "docs/superpowers/specs" / name
        inputs[str(path.relative_to(ROOT))] = sha(path)
    diff = subprocess.check_output(
        ["git", "diff", "--binary", "--", "marivo", "tests", "site", "devtools"], cwd=ROOT
    )
    manifest = {
        "base_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "tracked_code_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "inputs": inputs,
        "input_inventory_sha256": hashlib.sha256(
            json.dumps(inputs, sort_keys=True).encode()
        ).hexdigest(),
        "evidence": {
            str(path.relative_to(EVIDENCE)): sha(path)
            for path in sorted(EVIDENCE.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        },
        "protocols": {"store": 7, "graph_envelopes": 1, "method_state": 1, "physical_parquet": 1},
        "qualification": "Existing DuckDB native-table/local-Parquet J1-J4 only; no remote backend or real Agent qualification",
        "excluded": [
            "R5 private concurrency baseline failures",
            "19 historical default skips",
            "full release-check",
            "real Agent",
            "R9 six-backend qualification",
        ],
    }
    (EVIDENCE / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
