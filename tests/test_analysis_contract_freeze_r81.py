"""Independent static obligations for the inactive R8.1 contract freeze."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import zlib
from functools import lru_cache
from pathlib import Path
from typing import TypeAlias

import pytest

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/superpowers/specs/2026-10-03-marivo-r81-consumer-snapshot.json"
METHODS = {
    "deviation.zscore@v1",
    "deviation.mad@v1",
    "time.runs@v1",
    "association.pearson@v1",
    "association.spearman@v1",
    "association.kendall@v1",
    "forecast.naive@v1",
    "forecast.drift@v1",
    "forecast.seasonal_naive@v1",
}


def _json(value: object) -> Json:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Json] = {}
        for key, item in value.items():
            assert isinstance(key, str)
            result[key] = _json(item)
        return result
    raise AssertionError("invalid JSON value")


def _object(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict)
    return value


def _array(value: Json) -> list[Json]:
    assert isinstance(value, list)
    return value


def _payload(snapshot: dict[str, Json]) -> bytes:
    payload = _object(snapshot["inventory_payload"])
    parts: list[bytes] = []
    for value in _array(payload["chunks"]):
        record = _object(value)
        size = record["bytes"]
        assert type(size) is int and size <= 800_000
        raw = (ROOT / str(record["path"])).read_bytes()
        assert len(raw) == size
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        parts.append(raw)
    compressed = b"".join(parts)
    assert len(compressed) == payload["compressed_bytes"]
    return zlib.decompress(compressed)


@lru_cache(maxsize=1)
def _frozen() -> tuple[dict[str, Json], dict[str, Json]]:
    decoded: object = json.loads(SNAPSHOT.read_text())
    snapshot = _object(_json(decoded))
    payload = _object(snapshot["inventory_payload"])
    raw = _payload(snapshot)
    assert len(raw) == payload["uncompressed_bytes"]
    assert hashlib.sha256(raw).hexdigest() == payload["sha256_uncompressed"]
    inventory: object = json.loads(raw)
    return snapshot, _object(_json(inventory))


def test_freeze_is_static_and_preserves_actual_dirty_baseline() -> None:
    snapshot, _ = _frozen()
    assert snapshot["schema"] == "marivo.r81.static_freeze/v1"
    counts = _object(snapshot["counts"])
    assert counts["executed"] == counts["qualified"] == 0
    baseline = _object(snapshot["baseline"])
    assert baseline["branch"] == "panda"
    assert baseline["head"] == "4ffbc66ba2a718a083d7284fd40a40ff74fd80dc"
    assert len(_object(baseline["protected_paths"])) == 6
    assert all(
        _object(item)["status"] == "D " for item in _object(baseline["protected_paths"]).values()
    )
    assert len(_object(baseline["owner_input_sha256"])) == 14
    assert _object(_object(baseline["records"])["unstaged_diff"])["bytes"] == 0


def test_all_decisions_have_one_existing_resolvable_owner() -> None:
    snapshot, _ = _frozen()
    owners = _object(snapshot["decision_owners"])
    assert set(owners) == {f"F{number:02}" for number in range(1, 15)}
    for reference in owners.values():
        path, separator, anchor = str(reference).partition("#")
        assert separator and anchor
        body = (ROOT / path).read_text()
        headings = [
            line.lstrip("# ").strip().lower() for line in body.splitlines() if line.startswith("#")
        ]
        slugs = {re.sub(r"[^a-z0-9 -]", "", heading).replace(" ", "-") for heading in headings}
        assert anchor in slugs, reference
    assert owners["F04"] == owners["F08"] == owners["F09"]
    assert owners["F10"] == owners["F11"] == owners["F12"] == owners["F13"] == owners["F14"]
    temporal = str(_object(snapshot["support_owners"])["temporal"])
    path, _, anchor = temporal.partition("#")
    assert path == "docs/specs/analysis/timezone-and-calendar-design.md"
    assert anchor == "r81-frozen-statistical-grid-authority"
    assert path in _object(snapshot["owner_inputs"])
    assert "## R8.1 frozen statistical grid authority" in (ROOT / path).read_text()


def test_requirement_ids_versions_parts_routes_and_unexecuted_flags() -> None:
    snapshot, inventory = _frozen()
    rows = [_object(item) for item in _array(inventory["requirements"])]
    ids = [str(row["id"]) for row in rows]
    assert len(ids) == len(set(ids)) == _object(snapshot["counts"])["requirements"]
    assert {row["method"] for row in rows} == METHODS
    assert all(row["required"] is True and row["status"] in ("planned", "blocked") for row in rows)
    assert all(
        row["state_version"] == row["implementation_contract_version"] == "v1" for row in rows
    )
    assert all(row["numeric_policy"] == "r8_numeric_v1" for row in rows)
    for row in rows:
        assert row["input_types"] and row["domain"] and row["origin_profile"]
        domains = _array(row["input_domains"])
        assert len(domains) == len(_array(row["input_types"]))
        assert {str(domain) for domain in domains} <= {"singleton", "entity", "group"}
        assert row["key_profile"]
        key = _object(row["qualification_key"])
        assert key["method"] == row["method"] and key["input_types"] == row["input_types"]
        assert key["input_domains"] == domains and key["route"] == row["route"]
        assert row["implementation_id"] == (
            "r8-"
            + str(row["method"]).removesuffix("@v1").replace(".", "-")
            + "-"
            + str(row["route"])
            + "-v1"
        )
        shape = _object(key["shape"])
        if row["route"] == "artifact_python":
            assert set(shape) == {"kind", "time"} and shape["kind"] == "FixedShape"
        else:
            assert shape["kind"] == "SourceShape" and shape["backend"] == "duckdb"
            assert shape["table_kind"] == (
                "native" if row["physical_form"] == "table" else "parquet"
            )
        assert row["precision_contract"] == (
            "exact" if row["method"] == "time.runs@v1" else "certified_statistical"
        )
        assert row["required_parts"] and row["oracle"] and row["V"]
        assert row["route"] in ("ibis", "ibis_python", "artifact_python")
        assert row["physical_form"] in ("table", "parquet") and row["backend"] == "duckdb"
        assert "finding_policy" in _array(row["required_parts"])
        if "time" in str(row["domain"]):
            assert "grid_cells" in _array(row["required_parts"])
        if row["proof_class"] in ("fixed_kernel", "fresh_process_source_offline_kernel"):
            assert row["route"] == "artifact_python"
        if row["route"] == "ibis":
            assert row["status"] == "blocked" and row["blocked_reason"]
    assert sum(row["status"] == "planned" for row in rows) == _object(snapshot["counts"])["planned"]
    assert sum(row["status"] == "blocked" for row in rows) == _object(snapshot["counts"])["blocked"]


def test_full_decimal_numeric_law_sweep_remains_required() -> None:
    _, inventory = _frozen()
    rows = [_object(item) for item in _array(inventory["requirements"])]
    decimal_rows = [row for row in rows if str(row["id"]).startswith("Q-R8-DECIMAL-LAW-")]
    expected = {
        f"decimal({precision},{scale})"
        for precision in range(1, 39)
        for scale in range(precision + 1)
    }
    assert len(expected) == 779
    for method in METHODS - {"time.runs@v1"}:
        selected = [row for row in decimal_rows if row["method"] == method]
        assert {str(_array(row["input_types"])[0]) for row in selected} == expected
        assert len(selected) == 779 * 2 * 3
        assert {row["physical_form"] for row in selected} == {"table", "parquet"}
        assert {row["proof_class"] for row in selected} == {
            "source_kernel",
            "fixed_kernel",
            "fresh_process_source_offline_kernel",
        }


def test_integrated_shapes_time_origins_and_numeric_pairs_are_explicit() -> None:
    _, inventory = _frozen()
    rows = [
        _object(item)
        for item in _array(inventory["requirements"])
        if str(_object(item)["id"]).startswith("Q-R8-INTEGRATED-")
    ]
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        assert selected
        domains = {str(row["domain"]) for row in selected}
        if method.startswith("association"):
            assert domains == {"entity", "category", "time", "category_time"}
            pairs = {tuple(str(value) for value in _array(row["input_types"])) for row in selected}
            assert ("int64", "float64") in pairs and ("float64", "int64") in pairs
            assert ("decimal(9,2)", "decimal(38,18)") in pairs
        if method.startswith("forecast"):
            assert domains == {"time", "category_time"}
        if method == "time.runs@v1":
            assert domains == {"time", "category_time", "entity_time"}
        for domain in domains & {"entity", "entity_time"}:
            assert {row["key_profile"] for row in selected if row["domain"] == domain} == {
                "string",
                "int64",
                "composite(string,int64)",
            }
        temporal = [row for row in selected if "time" in str(row["domain"])]
        assert {row["source_precision"] for row in temporal} == {"s", "ms", "us", "ns"}
        assert any("America/New_York" in str(row["time_profile"]) for row in temporal)
        assert any("certified_unequal" in str(row["time_profile"]) for row in temporal)


def test_all_v_obligations_and_public_journeys_have_concrete_scenarios() -> None:
    _, inventory = _frozen()
    obligations = _object(inventory["V_obligations"])
    assert set(obligations) == {f"V{number:02}" for number in range(1, 21)}
    rows = [_object(item) for item in _array(inventory["requirements"])]
    for v, value in obligations.items():
        obligation = _object(value)
        assert obligation["owner"] and obligation["oracle"]
        expected = {str(item) for item in _array(obligation["scenarios"])}
        actual = {str(row["scenario"]) for row in rows if row["V"] == v and "scenario" in row}
        assert actual == expected, v
    journeys = _array(_object(obligations["V16"])["scenarios"])
    assert all(name in journeys for name in ("A02", "A04", "A07", "J1", "J2", "J3", "J4"))
    assert sum(str(name).startswith("A11_") for name in journeys) == 7
    assert {"timeout_below", "timeout_at", "timeout_above", "committed_success"} <= {
        str(item) for item in _array(_object(obligations["V18"])["scenarios"])
    }
    assert {"types_positive", "types_negative"} <= {
        str(item) for item in _array(_object(obligations["V01"])["scenarios"])
    }
    for row in rows:
        scenario = row.get("scenario")
        if scenario in ("dst_23h", "dst_25h"):
            assert "America/New_York" in str(row["time_profile"])
        if scenario in ("positive_lag", "negative_lag", "calendar_lag"):
            assert row["domain"] == "time"
        if scenario == "arity_16":
            assert len(_array(row["input_types"])) == 16
        if scenario == "arity_17_reject":
            assert len(_array(row["input_types"])) == 17
        if scenario in ("types_positive", "types_negative"):
            assert row["proof_class"] == "future_typing_target"
    for scenario in journeys:
        journey_rows = [row for row in rows if row.get("scenario") == scenario]
        assert {row["kernel_proof_class"] for row in journey_rows} == {
            "source_kernel",
            "fixed_kernel",
            "fresh_process_source_offline_kernel",
        }
    prepared = [row for row in rows if str(row["id"]).startswith("Q-R8-F11-")]
    assert prepared
    assert {row["domain"] for row in prepared} == {"entity", "entity_time"}
    assert {row["physical_form"] for row in prepared} == {"table", "parquet"}
    for row in prepared:
        downstream = _object(row["downstream_observation"])
        assert downstream["value_type"] == _array(row["input_types"])[0]
        if row["domain"] == "entity_time" or str(downstream["value_type"]).startswith("decimal"):
            assert row["status"] == "blocked" and "prepared observation" in str(
                row["blocked_reason"]
            )


def test_every_relevant_original_test_node_has_exact_disposition() -> None:
    snapshot, inventory = _frozen()
    expected: dict[str, Json] = {}
    for value in _array(inventory["test_files"]):
        record = _object(value)
        if str(record["path"]).endswith("test_analysis_contract_freeze_r81.py"):
            continue
        for item in _array(record["tests"]):
            test = _object(item)
            if test["r8_candidate"]:
                expected[str(test["node"])] = test
    actual = {
        _object(row)["node"]: _object(row)
        for row in _array(inventory["legacy_test_dispositions"])
        if isinstance(_object(row)["node"], str)
    }
    assert set(actual) == set(expected)
    assert len(actual) == _object(snapshot["counts"])["legacy_test_dispositions"]
    for node, original in expected.items():
        row = actual[node]
        assert row["body_sha256"] == _object(original)["body_sha256"]
        assert row["original_assertions"] == _object(original)["assertions"]
        assert row["replacement_owner"] and row["purpose"]
        assert row["replacement_entry"] and row["replacement_evidence"]
        assert row["deletion_condition"] and row["remaining_shared_owner"]
        assert row["disposition"] in ("preserve_oracle", "replace_entry", "retire_heuristic")
        assert row["replacement_execution"] == "planned"
        assert row["dynamic_unreachability"] == "unverified"
        assert row["physical_deletion"] == "not_performed"


def test_every_statistical_symbol_candidate_has_a_scoped_disposition() -> None:
    snapshot, inventory = _frozen()
    expected: dict[str, Json] = {}
    pattern = re.compile(r"candidate|discover|driver_|association|correlat|spearman|forecast", re.I)
    for value in _array(inventory["source_files"]):
        record = _object(value)
        if not record["migration_ids"]:
            continue
        for item in _array(record["symbols"]):
            symbol = _object(item)
            if symbol["r8_candidate"] or pattern.search(str(record["path"])):
                expected[str(record["path"]) + "::" + str(symbol["symbol"])] = symbol
    actual = {_object(row)["id"]: _object(row) for row in _array(inventory["symbol_dispositions"])}
    assert set(actual) == set(expected)
    assert len(actual) == _object(snapshot["counts"])["symbol_dispositions"]
    for identity, original in expected.items():
        row = actual[identity]
        assert row["body_sha256"] == _object(original)["body_sha256"]
        assert row["migration_ids"] and row["replacement_entry"] and row["replacement_owner"]
        assert (
            row["replacement_evidence"]
            and row["deletion_condition"]
            and row["remaining_shared_owner"]
        )
        assert row["disposition"] in ("preserve_shared", "replace_entry")
        assert row["dynamic_unreachability"] == "unverified"
        assert (
            row["replacement_execution"] == "planned"
            and row["physical_deletion"] == "not_performed"
        )


def test_snapshot_has_all_migration_groups_and_actual_shared_callers() -> None:
    _, inventory = _frozen()
    groups = _object(inventory["migration_groups"])
    assert set(groups) == {f"M{number:02}" for number in range(1, 19)}
    covered = {
        str(group)
        for family in ("source_files", "test_files", "disclosure_files")
        for record in _array(inventory[family])
        for group in _array(_object(record)["migration_ids"])
    }
    assert set(groups) <= covered
    source = {
        _object(row)["path"]: _object(row)
        for row in _array(inventory["source_files"])
        if isinstance(_object(row)["path"], str)
    }
    for name in (
        "marivo/analysis/operators/association_contracts.py",
        "marivo/analysis/operators/forecast_contracts.py",
        "marivo/analysis/operators/candidate_values.py",
        "marivo/analysis/materialization/graph_findings.py",
        "marivo/analysis/materialization/finding_values.py",
    ):
        assert name in source and source[name]["symbols"]
    assert source["marivo/analysis/public_dsl.py"]["calls"]
    assert source["marivo/analysis/operators/registry.py"]["branches"]
    disclosure = [_object(row) for row in _array(inventory["disclosure_files"])]
    assert any("/zh-cn/" in str(row["path"]) and row["matches"] for row in disclosure)
    assert any("/latest/" in str(row["path"]) and row["matches"] for row in disclosure)
    workers = _array(inventory["fixture_worker_dispositions"])
    assert workers
    for worker in workers:
        row = _object(worker)
        assert row["replacement_entry"] and row["replacement_owner"] and row["replacement_evidence"]
        assert row["deletion_condition"] and row["remaining_shared_owner"]
        assert (
            row["replacement_execution"] == "planned"
            and row["dynamic_unreachability"] == "unverified"
        )


def test_import_only_probe_proves_target_api_not_yet_public() -> None:
    snapshot, _ = _frozen()
    probe = _object(snapshot["probes"])
    assert probe["exit_code"] == 0
    assert probe["proof_class"] == "import_only_no_session_run_or_business_io"
    result = _object(probe["result"])
    assert all(value is False for value in _object(result["target_symbols"]).values())
    for name in ("LogicalNumericRelation", "MaterializedNumericRelation"):
        methods = _object(_object(result["methods"])[name])
        assert methods["deviation"] is methods["runs"] is methods["forecast"] is False
        assert methods["correlate"] is True
        assert "spearman" in str(_object(result["current_correlate_signatures"])[name])
    assert all(value == 8 for value in _object(result["legacy_stage"]).values())
    assert not any(
        "DeviationResult" in str(target)
        or "TimeRunResult" in str(target)
        or "ForecastResult" in str(target)
        for target in _array(result["help_targets"])
    )


@pytest.mark.parametrize("kind", ("owner", "chunk", "payload"))
def test_verifier_rejects_independent_digest_corruption(tmp_path: Path, kind: str) -> None:
    snapshot, inventory = _frozen()
    matching = copy.deepcopy(snapshot)
    fixture_inventory = copy.deepcopy(inventory)
    for group in ("source_files", "test_files", "disclosure_files"):
        for value in _array(fixture_inventory[group]):
            record = _object(value)
            path = str(record["path"])
            raw = (ROOT / path).read_bytes()
            destination = tmp_path / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            record["sha256"] = hashlib.sha256(raw).hexdigest()
    for path in _object(matching["owner_inputs"]):
        raw = (ROOT / path).read_bytes()
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        _object(matching["owner_inputs"])[path] = hashlib.sha256(raw).hexdigest()
    collector = _object(matching["collector"])
    raw = (ROOT / str(collector["path"])).read_bytes()
    destination = tmp_path / str(collector["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    collector["sha256"] = hashlib.sha256(raw).hexdigest()
    raw = json.dumps(fixture_inventory).encode()
    compressed = zlib.compress(raw)
    path = "fixture-inventory.zlib"
    (tmp_path / path).write_bytes(compressed)
    matching["inventory_payload"] = {
        **_object(matching["inventory_payload"]),
        "chunks": [
            {
                "path": path,
                "bytes": len(compressed),
                "sha256": hashlib.sha256(compressed).hexdigest(),
            }
        ],
        "compressed_bytes": len(compressed),
        "uncompressed_bytes": len(raw),
        "sha256_uncompressed": hashlib.sha256(raw).hexdigest(),
    }
    target = tmp_path / "fixture-snapshot.json"
    target.write_text(json.dumps(matching))
    command = (
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/r81_static_freeze.py"),
        "--root",
        str(tmp_path),
        "--output",
        str(target),
        "--verify-current",
    )
    valid = subprocess.run(command, text=True, capture_output=True, check=False)
    assert valid.returncode == 0, valid.stderr
    damaged = copy.deepcopy(matching)
    if kind == "owner":
        owners = _object(damaged["owner_inputs"])
        owners[next(iter(owners))] = "0" * 64
    elif kind == "chunk":
        _object(_array(_object(damaged["inventory_payload"])["chunks"])[0])["sha256"] = "0" * 64
    else:
        _object(damaged["inventory_payload"])["sha256_uncompressed"] = "0" * 64
    target.write_text(json.dumps(damaged))
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "differs" in result.stderr or "mismatch" in result.stderr
