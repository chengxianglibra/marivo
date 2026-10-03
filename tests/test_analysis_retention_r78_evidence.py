"""Independent preservation of original targets and bounded execution claims."""

import base64
import hashlib
import json
import zlib
from pathlib import Path

SPECS = Path("docs/superpowers/specs")
FROZEN_PATH = SPECS / "2026-10-01-marivo-r71-consumer-snapshot.json"
FROZEN = json.loads(FROZEN_PATH.read_text())
INVENTORY = json.loads(zlib.decompress(base64.b64decode(FROZEN["inventory_payload"]["data"])))
RECORD = json.loads((SPECS / "2026-10-03-marivo-r78-qualification.json").read_text())
PAYLOAD_BYTES = zlib.decompress(base64.b64decode(RECORD["qualification_payload"]["data"]))
PAYLOAD = json.loads(PAYLOAD_BYTES)
PROFILES = {"P19", "P20", "P21", "P22"}


def test_original_qualification_cells_and_profiles_are_unchanged():
    assert (
        RECORD["historical_snapshot_sha256"] == hashlib.sha256(FROZEN_PATH.read_bytes()).hexdigest()
    )
    assert (
        RECORD["qualification_payload"]["sha256_uncompressed"]
        == hashlib.sha256(PAYLOAD_BYTES).hexdigest()
    )
    assert RECORD["qualification_payload"]["uncompressed_bytes"] == len(PAYLOAD_BYTES)
    original = [row for row in INVENTORY["qualification_cells"] if row[1] in PROFILES]
    assert len(original) == 1080
    assert PAYLOAD["r78_cells"] == original
    assert PAYLOAD["method_profiles"] == [
        profile
        for profile in FROZEN["qualification_target"]["method_profiles"]
        if profile["id"] in PROFILES | {"P24", "P50"}
    ]
    assert PAYLOAD["key_profiles"] == FROZEN["qualification_target"]["key_profiles"]
    assert PAYLOAD["time_profiles"] == FROZEN["qualification_target"]["time_profiles"]


def test_original_targets_are_not_qualified_by_adjacent_continuations():
    original = {row[0]: row for row in PAYLOAD["r78_cells"]}
    dispositions = {row[0]: row[1:] for row in PAYLOAD["dispositions"]}
    assert len(dispositions) == len(PAYLOAD["dispositions"]) == 1080
    assert dispositions.keys() == original.keys()
    assert {row[0] for row in dispositions.values()} == {"unverified"}
    missing = {
        identifier
        for identifier, row in original.items()
        if row[1] in {"P19", "P20"} and row[4] == "F"
    }
    assert len(missing) == 180
    assert {
        identifier
        for identifier, row in dispositions.items()
        if row[1] == "starts_only_fixed_anchor_lacks_return_event_inputs"
    } == missing
    assert RECORD["counts"]["qualification_passed"] == 0
    assert RECORD["counts"]["qualification_unverified"] == 1080
    assert RECORD["counts"]["starts_only_fixed_kernel_targets_unverified"] == 180


def test_bounded_execution_receipts_keep_kernel_and_recovery_scope_separate():
    cases = RECORD["bounded_execution_cases"]
    assert len(cases) == 11
    assert len({(case["key_profile"], case["time_profile"]) for case in cases}) == 11
    assert {case["key_profile"] for case in cases if case["time_profile"] == "T01"} == {
        "K11",
        "K12",
        "K13",
        "K21",
        "K22",
        "K23",
        "K31",
        "K32",
        "K33",
    }
    assert {(case["key_profile"], case["time_profile"]) for case in cases} - {
        (f"K{s}{o}", "T01") for s in (1, 2, 3) for o in (1, 2, 3)
    } == {("K22", "T08"), ("K22", "T09")}
    for case in cases + RECORD["final_strengthened_execution_cases"]:
        assert case["status"] == "passed_bounded"
        assert case["source_kernels"] == ["P19", "P20", "P21", "P22"]
        assert case["fixed_kernels"] == ["P21", "P22"]
        assert case["cold_kernels"] == []
        assert case["fixed_outputs"] == case["cold_exact_hits"] == 22
        assert case["original_cold_reads"] == 4
        assert case["snapshot_and_validity_axes"] is False
        assert case["semantic_and_source_loads_guarded"] is True
        assert case["cold_retention_execution_guarded"] is True
        for profile, route in (
            ("P19", "ibis"),
            ("P20", "ibis_python"),
            ("P21", "ibis_python"),
            ("P22", "ibis_python"),
        ):
            assert case["source_method_bindings"][profile]["implementation"].endswith(
                f".{route}@v1"
            )
    assert len(RECORD["final_strengthened_execution_cases"]) == 1
    assert "models/" in RECORD["final_strengthened_execution_cases"][0]["source_files_deleted"]
    counts = RECORD["counts"]
    assert counts["bounded_source_kernel_outputs"] == 44
    assert counts["bounded_fixed_kernel_outputs"] == 22
    assert counts["bounded_fixed_continuation_outputs"] == counts["bounded_cold_exact_hits"] == 242
    assert counts["matrix_cases_not_run"] == 79


def test_shared_and_inherited_targets_keep_their_original_owners():
    assert PAYLOAD["shared_cells"] == [
        row for row in INVENTORY["qualification_cells"] if row[1] in {"P24", "P50"}
    ]
    assert len(PAYLOAD["shared_cells"]) == RECORD["counts"]["original_shared_cells"] == 540
    assert RECORD["inherited_unfinished_r77"]["unverified"] == 3894
    assert RECORD["inherited_unfinished_r77"]["p48_source_native_targets"] == 90
    assert "incomplete" in RECORD["acceptance"]
    assert {"commit", "push", "release", "MinIO", "full release-check"} <= set(
        RECORD["excluded_actions"]
    )
