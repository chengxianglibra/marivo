"""Independent raw-cost schedule checks against all frozen R9.6 obligations."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree

import pytest

from devtools.analysis_r9_cost import FIXED_APPLICABLE, PHYSICAL_COST_PROFILES, SOURCE_ROUTES
from scripts import r9_qualification_requirements as freeze
from scripts.r96_cost_results import (
    BOUNDARY_CASES,
    _boundary_runs,
    _group_identity,
    arr,
    audit,
    obj,
)

CANDIDATE = {"content_sha256": "controlled-cost-candidate"}
SCENARIO = "multi-root-ratio-empty-groups"


def _rows() -> list[dict[str, freeze.Json]]:
    return [
        freeze.obj(row)
        for row in freeze.arr(freeze.load(freeze.OUTPUT)["requirements"])
        if freeze.obj(row)["gap_owner"] == "R9.6"
    ]


def _identity(*, scenario: str, backend: str = "shared", route: str = "cost") -> str:
    return str(
        next(
            row["id"]
            for row in _rows()
            if row["scenario"] == scenario and row["backend"] == backend and row["route"] == route
        )
    )


def _samples(
    *,
    scenario: str = SCENARIO,
    backend: str = "duckdb",
    profile: str = "table",
    sizes: Sequence[int] = (1000, 100000),
    source_routes: Sequence[str] | None = None,
    fixed_modes: Sequence[str] = ("kernel", "exact_hit"),
) -> list[dict[str, object]]:
    routes = SOURCE_ROUTES[scenario] if source_routes is None else source_routes
    result: list[dict[str, object]] = []
    for size in sizes:
        for route, mode in (
            *((route, None) for route in routes),
            *(("artifact_python", mode) for mode in fixed_modes),
        ):
            for index in range(4):
                identity = f"{backend}-{profile}-{size}-{scenario}-{route}-{mode}-{index}"
                fixed = route == "artifact_python"
                record: dict[str, object] = {
                    "schema": "marivo.r96.cost-sample.v1",
                    "status": "passed",
                    "candidate": CANDIDATE,
                    "backend": backend,
                    "profile": profile,
                    "facts": size,
                    "scenario": scenario,
                    "requested_route": route,
                    "temperature": "warmup" if index == 0 else "measured",
                    "iteration": index,
                    "elapsed_seconds": 0.01,
                    "oracle": {
                        "passed": True,
                        "result_digest": "values-state-and-keys",
                        "original_key_binding": True,
                        "original_key_digest": "independent-original-key-map",
                    },
                    "identity": {"artifact_ref": identity, "root_route": route},
                    "observations": {
                        "resource_closed": True,
                        "source_submissions": [] if fixed else [{"state": "succeeded"}],
                        "storage_writes": [] if mode == "exact_hit" else [{"receipt": identity}],
                        "phase_calls": {"fixed_kernel": int(mode == "kernel")},
                    },
                }
                if fixed:
                    record["fixed_mode"] = mode
                    record["producer_identity"] = {"artifact_ref": f"producer-{identity}"}
                result.append(record)
    return result


def _write(directory: Path, samples: Sequence[dict[str, object]]) -> None:
    directory.mkdir()
    (directory / "samples").mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "marivo.r96.cost-manifest.v1",
                "candidate": CANDIDATE,
                "required_source_routes": SOURCE_ROUTES,
                "fixed_applicable": FIXED_APPLICABLE,
            }
        )
    )
    for index, sample in enumerate(samples):
        (directory / "samples" / f"sample-{index:03}.json").write_text(json.dumps(sample))


def test_audit_preserves_all_frozen_rows_and_keeps_owner_repetitions_separate(
    tmp_path: Path,
) -> None:
    samples = [*_samples(), *_samples(backend="sqlite")]
    directory = tmp_path / "cost"
    _write(directory, samples)
    report = audit((directory,))
    results = obj(report["results"])
    assert len(results) == 28
    assert set(results) == {str(row["id"]) for row in _rows()}
    assert obj(results[_identity(scenario=SCENARIO)])["status"] == "passed"
    assert obj(results[_identity(scenario="association-lags")])["status"] == "unverified"
    assert report["raw_samples"] == len(samples)
    assert len(list(directory.joinpath("samples").glob("*.json"))) == len(samples)


@pytest.mark.parametrize("missing", ("source", "kernel", "exact_hit"))
def test_audit_never_grants_an_incomplete_scenario_schedule(tmp_path: Path, missing: str) -> None:
    directory = tmp_path / "cost"
    _write(
        directory,
        _samples(
            source_routes=() if missing == "source" else None,
            fixed_modes=tuple(mode for mode in ("kernel", "exact_hit") if mode != missing),
        ),
    )
    result = obj(obj(audit((directory,))["results"])[_identity(scenario=SCENARIO)])
    assert result["status"] == "unverified"
    assert result["errors"]


@pytest.mark.parametrize("failure_size", (1000, 1000000))
def test_baseline_rejects_the_wrong_physical_profile_and_preserves_raw_failures(
    tmp_path: Path,
    failure_size: int,
) -> None:
    baseline = "1k-100k-warmup-three-samples"
    identity = _identity(scenario=baseline, backend="sqlite", route="ibis")
    wrong_profile = tmp_path / "view"
    _write(
        wrong_profile,
        _samples(scenario="baseline", backend="sqlite", profile="view", fixed_modes=()),
    )
    result = obj(obj(audit((wrong_profile,))["results"])[identity])
    assert result["status"] == "unverified"
    assert "ordinary-table shape" in str(result["errors"])

    failures = tmp_path / "failed"
    samples = _samples(scenario="baseline", backend="sqlite", fixed_modes=())
    samples.append(
        {
            **samples[1],
            "status": "failed",
            "facts": failure_size,
            "iteration": 4,
            "error_type": "TimeoutError",
            "traceback": "controlled pressure input exceeded its execution deadline",
        }
    )
    _write(failures, samples)
    report = audit((failures,))
    assert obj(obj(report["results"])[identity])["status"] == "failed"
    assert report["raw_samples"] == len(samples)
    assert len(arr(report["attachments"])) == len(samples)
    assert (
        "TimeoutError" in (failures / "samples" / f"sample-{len(samples) - 1:03}.json").read_text()
    )


def test_scenario_scale_measurements_cannot_switch_physical_owner(tmp_path: Path) -> None:
    directory = tmp_path / "cost"
    _write(directory, [*_samples(sizes=(1000,)), *_samples(backend="sqlite", sizes=(100000,))])
    result = obj(obj(audit((directory,))["results"])[_identity(scenario=SCENARIO)])
    assert result["status"] == "unverified"
    assert result["errors"]


def test_additional_physical_costs_do_not_replace_or_invalidate_ordinary_baseline(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "cost"
    ordinary = _samples(scenario="baseline", backend="duckdb", fixed_modes=())
    physical = [
        {**sample, "cost_scope": "physical"}
        for sample in _samples(
            scenario="baseline", backend="duckdb", profile="parquet", fixed_modes=()
        )
    ]
    _write(directory, [*ordinary, *physical])
    identity = _identity(scenario="1k-100k-warmup-three-samples", backend="duckdb", route="ibis")
    report = audit((directory,))
    assert obj(obj(report["results"])[identity])["status"] == "passed"
    v17 = next(row for row in _rows() if row["family"] == "V17")
    assert obj(obj(report["results"])[str(v17["id"])])["physical_profile_errors"]


@pytest.mark.parametrize(
    "scenario",
    ("exact-distinct-quantile", "event-lifecycle-anchor", "many-occurrences-anchors-lags"),
)
def test_scenario_requires_only_its_legal_routes(tmp_path: Path, scenario: str) -> None:
    assert FIXED_APPLICABLE[scenario] is False
    directory = tmp_path / "cost"
    _write(directory, _samples(scenario=scenario, fixed_modes=()))
    result = obj(obj(audit((directory,))["results"])[_identity(scenario=scenario)])
    assert result["status"] == "passed"
    assert result["errors"] == []


@pytest.mark.parametrize("layout", ("unindexed", "indexed-keys"))
def test_alternative_configuration_reports_positive_cohort_without_erasing_original_failure(
    tmp_path: Path, layout: str
) -> None:
    directory = tmp_path / "cost"
    original = _samples(scenario="baseline", backend="mysql", fixed_modes=())
    failed = next(sample for sample in original if sample["facts"] == 100000)
    failed["status"] = "failed"
    failed["error_type"] = "MaterializationError"
    failed["traceback"] = "Lost connection to server during the original unindexed query"
    alternative = [
        {
            **sample,
            "identity": {
                "artifact_ref": f"alternative-{sample['iteration']}",
                "root_route": "ibis",
            },
            "environment": {
                "fixture_layout": layout,
                "deployment_cohort": "bounded-higher-memory",
                "deployment_evidence": {
                    "configuration": {"container_limits": {"memory_bytes": 2147483648}},
                    "usage_observation": False,
                },
            },
        }
        for sample in _samples(
            scenario="baseline",
            backend="mysql",
            sizes=(100000,),
            source_routes=("ibis",),
            fixed_modes=(),
        )
    ]
    _write(directory, [*original, *alternative])
    report = audit((directory,))
    identity = _identity(scenario="1k-100k-warmup-three-samples", backend="mysql", route="ibis")
    assert obj(obj(report["results"])[identity])["status"] == "failed"
    cohorts = [obj(value) for value in arr(report["cohort_results"])]
    assert any(
        row["fixture_layout"] == "archived_no_index" and row["status"] == "failed"
        for row in cohorts
    )
    positive = next(row for row in cohorts if row["deployment_cohort"] == "bounded-higher-memory")
    assert positive["status"] == "passed"
    assert positive["fixture_layout"] == layout
    assert positive["usage_observation"] is False
    assert report["raw_counts"] == {"passed": len(original) + len(alternative) - 1, "failed": 1}


def test_repetitions_cannot_be_completed_across_deployment_cohorts(tmp_path: Path) -> None:
    directory = tmp_path / "cost"
    samples = _samples(
        scenario="baseline", backend="mysql", source_routes=("ibis",), fixed_modes=()
    )
    for sample in samples:
        iteration = sample["iteration"]
        assert isinstance(iteration, int)
        sample["environment"] = {
            "fixture_layout": "unindexed",
            "deployment_cohort": "first" if iteration < 2 else "second",
        }
    _write(directory, samples)
    report = audit((directory,))
    identity = _identity(scenario="1k-100k-warmup-three-samples", backend="mysql", route="ibis")
    assert obj(obj(report["results"])[identity])["status"] == "unverified"


def _acceptance(directory: Path, samples: Sequence[dict[str, object]] | None = None) -> Path:
    evidence: dict[str, object] = {
        "schema": "marivo.r96.acceptance-evidence.v1",
        "candidate": CANDIDATE,
    }
    if samples is not None:
        cohorts = {json.dumps(_group_identity(sample), sort_keys=True) for sample in samples}
        evidence["accepted_cohorts"] = [json.loads(value) for value in sorted(cohorts)]
    path = directory / "acceptance.json"
    path.write_text(json.dumps(evidence))
    return path


def _gates(directory: Path, *, skipped: str | None = None) -> dict[str, object]:
    root = ElementTree.Element("testsuites")
    suite = ElementTree.SubElement(root, "testsuite")
    for name in (case for cases in BOUNDARY_CASES.values() for case in cases):
        module, case_name = name.split("::")
        case = ElementTree.SubElement(suite, "testcase", classname=module, name=case_name)
        if name == skipped:
            ElementTree.SubElement(case, "skipped")
    junit = directory / "boundary.xml"
    ElementTree.ElementTree(root).write(junit)
    run = directory / "boundary-run.json"
    run.write_text(
        json.dumps(
            {
                "candidate": CANDIDATE,
                "command": ["controlled-pytest", "boundary-cases"],
                "exit_code": 0,
                "attachments": {junit.name: freeze.digest(junit.read_bytes())},
            }
        )
    )
    reference = {
        "run": {"path": str(run), "sha256": freeze.digest(run.read_bytes())},
        "junit": {"path": str(junit), "sha256": freeze.digest(junit.read_bytes())},
    }
    return {kind: [reference] for kind in BOUNDARY_CASES}


def test_explicit_accepted_deployment_preserves_original_failure_as_bounded_reference(
    tmp_path: Path,
) -> None:
    original = _samples(
        scenario="baseline", backend="mysql", source_routes=("ibis",), fixed_modes=()
    )
    original[0]["status"] = "failed"
    original[0]["traceback"] = "Original complete query failed; retained diagnostic" * 10000
    accepted = [
        {
            **sample,
            "environment": {
                "fixture_layout": "indexed-keys",
                "deployment_cohort": "accepted-deployment",
            },
        }
        for sample in _samples(
            scenario="baseline", backend="mysql", source_routes=("ibis",), fixed_modes=()
        )
    ]
    directory = tmp_path / "cost"
    _write(directory, [*original, *accepted])
    report = audit((directory,), acceptance_evidence=_acceptance(tmp_path, accepted))
    identity = _identity(scenario="1k-100k-warmup-three-samples", backend="mysql", route="ibis")
    assert obj(obj(report["results"])[identity])["status"] == "passed"
    assert obj(report["raw_counts"])["failed"] == 1
    assert any(obj(row)["status"] == "failed" for row in arr(report["cohort_results"]))
    references = arr(report["unaccepted_cohort_samples"])
    assert len(references) == len(original)
    assert "traceback" not in json.dumps(references)
    assert "retained diagnostic" in Path(str(obj(references[0])["path"])).read_text()


def test_acceptance_cannot_borrow_measurements_or_claim_an_absent_cohort(tmp_path: Path) -> None:
    samples = _samples(
        scenario="baseline", backend="mysql", source_routes=("ibis",), fixed_modes=()
    )
    for sample in samples:
        sample["environment"] = {
            "deployment_cohort": "first" if sample["iteration"] in (0, 1) else "second"
        }
    directory = tmp_path / "cost"
    _write(directory, samples)
    evidence = _acceptance(tmp_path, samples)
    report = audit((directory,), acceptance_evidence=evidence)
    identity = _identity(scenario="1k-100k-warmup-three-samples", backend="mysql", route="ibis")
    assert obj(obj(report["results"])[identity])["status"] == "unverified"
    record = dict(load_json(evidence))
    first = dict(obj(arr(record["accepted_cohorts"])[0]))
    first["execution_candidate"] = "never-executed"
    record["accepted_cohorts"] = [first]
    evidence.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="no eligible original"):
        audit((directory,), acceptance_evidence=evidence)


def load_json(path: Path) -> dict[str, object]:
    return dict(obj(json.loads(path.read_text())))


def test_physical_missing_key_and_http_refusal_do_not_become_positive_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.r96_cost_results as results

    directory = tmp_path / "cost"
    physical = [
        {**sample, "cost_scope": "physical"}
        for sample in _samples(scenario="baseline", profile="csv")
    ]
    _write(directory, physical)
    monkeypatch.setattr(
        results, "_physical_key_available", lambda profile, route: route == "artifact_python"
    )
    report = audit((directory,))
    v17 = next(
        obj(row) for row in obj(report["results"]).values() if "physical_applicability" in obj(row)
    )
    modes = [obj(row) for row in arr(v17["physical_applicability"])]
    assert len(modes) == len(PHYSICAL_COST_PROFILES) * 2 * 4
    assert all(
        row["status"] == "unverified"
        for row in modes
        if row["profile"] == "duckdb:csv" and row["route"] != "artifact_python"
    )
    assert all(
        row["status"] == "contract-inapplicable"
        for row in modes
        if "http-json" in str(row["profile"])
    )
    assert obj(v17["boundary_status"])["http_refusal"] == "unverified"
    illegal = tmp_path / "http"
    _write(illegal, [{**sample, "profile": "http-json-public"} for sample in physical])
    with pytest.raises(ValueError, match="cannot contain positive"):
        audit((illegal,))


def test_boundary_attachments_reject_skips_hash_drift_and_unrelated_old_owners(
    tmp_path: Path,
) -> None:
    skipped = BOUNDARY_CASES["extension"][0]
    evidence = {"boundary_runs": _gates(tmp_path, skipped=skipped)}
    statuses, _ = _boundary_runs(evidence, tmp_path, CANDIDATE)
    assert statuses["extension"] == "unverified"
    assert statuses["http_refusal"] == "passed"
    entries = obj(evidence["boundary_runs"])
    reference = dict(obj(arr(entries["extension"])[0]))
    reference["junit"] = {**obj(reference["junit"]), "sha256": "different"}
    evidence["boundary_runs"] = {"extension": [reference]}
    with pytest.raises(ValueError, match="hash differs"):
        _boundary_runs(evidence, tmp_path, CANDIDATE)
    other = tmp_path / "other"
    other.mkdir()
    evidence = {"boundary_runs": _gates(other)}
    reference = dict(obj(arr(obj(evidence["boundary_runs"])["extension"])[0]))
    run_path = Path(str(obj(reference["run"])["path"]))
    run = load_json(run_path)
    run["candidate"] = {"content_sha256": "older"}
    run_path.write_text(json.dumps(run))
    reference["run"] = {**obj(reference["run"]), "sha256": freeze.digest(run_path.read_bytes())}
    evidence["boundary_runs"] = {"extension": [reference]}
    with pytest.raises(ValueError, match="unchanged owner"):
        _boundary_runs(evidence, other, CANDIDATE)


def test_combined_v17_requires_cold_process_ownership_and_all_independent_boundaries(
    tmp_path: Path,
) -> None:
    samples: list[dict[str, object]] = []
    for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"):
        profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
        samples.extend(_samples(scenario="baseline", backend=backend, profile=profile))
        for index, sample in enumerate(
            _samples(scenario="baseline", backend=backend, profile=profile, source_routes=())
        ):
            observations = dict(obj(sample["observations"]))
            observations.update(
                {"source_sessions": [], "native_submissions": [{"category": "store"}]}
            )
            samples.append(
                {
                    **sample,
                    "recovery": "cold",
                    "process": {"pid": 1000 + index, "parent_pid": 999},
                    "observations": observations,
                }
            )
    for scenario in SOURCE_ROUTES:
        if scenario != "baseline":
            scenario_samples = _samples(
                scenario=scenario,
                fixed_modes=("kernel", "exact_hit") if FIXED_APPLICABLE[scenario] else (),
            )
            if scenario == "cross-batch-long-runs-unavailable":
                for sample in scenario_samples:
                    sample["observations"] = {
                        **obj(sample["observations"]),
                        "arrow_exchange": {
                            "batches": [
                                {"purpose": "runs", "source_identity": "capture", "rows": 1000},
                                {"purpose": "runs", "source_identity": "capture", "rows": 1000},
                            ]
                        },
                    }
            samples.extend(scenario_samples)
    for profile in PHYSICAL_COST_PROFILES:
        if "http-json" in profile:
            continue
        backend, shape = profile.split(":")
        samples.extend(
            {**sample, "cost_scope": "physical"}
            for sample in _samples(scenario="baseline", backend=backend, profile=shape)
        )
    directory = tmp_path / "cost"
    _write(directory, samples)
    evidence_path = _acceptance(tmp_path)
    evidence = load_json(evidence_path)
    evidence["boundary_runs"] = _gates(tmp_path)
    evidence_path.write_text(json.dumps(evidence))
    v17_id = str(next(row["id"] for row in _rows() if row["family"] == "V17"))
    report = audit((directory,), acceptance_evidence=evidence_path)
    assert obj(obj(report["results"])[v17_id])["status"] == "passed", {
        identity: result
        for identity, result in obj(report["results"]).items()
        if obj(result)["status"] != "passed"
    }
    assert obj(report["counts"])["passed"] == 28
    cold = next(sample for sample in samples if sample.get("recovery") == "cold")
    cold["observations"] = {**obj(cold["observations"]), "source_sessions": [{"opened": True}]}
    path = directory / "samples" / f"sample-{samples.index(cold):03}.json"
    path.write_text(json.dumps(cold))
    report = audit((directory,), acceptance_evidence=evidence_path)
    assert obj(obj(report["results"])[v17_id])["status"] == "unverified"


def test_successful_boundary_exit_rejects_unrelated_failed_junit_case(tmp_path: Path) -> None:
    evidence = {"boundary_runs": _gates(tmp_path)}
    entry = obj(arr(obj(evidence["boundary_runs"])["extension"])[0])
    junit_path = Path(str(obj(entry["junit"])["path"]))
    run_path = Path(str(obj(entry["run"])["path"]))
    tree = ElementTree.parse(junit_path)
    failed = ElementTree.SubElement(
        tree.getroot(), "testcase", classname="unrelated", name="failure"
    )
    ElementTree.SubElement(failed, "failure")
    tree.write(junit_path)
    digest = freeze.digest(junit_path.read_bytes())
    run = load_json(run_path)
    run["attachments"] = {junit_path.name: digest}
    run_path.write_text(json.dumps(run))
    evidence["boundary_runs"] = {
        "extension": [
            {
                "run": {"path": str(run_path), "sha256": freeze.digest(run_path.read_bytes())},
                "junit": {"path": str(junit_path), "sha256": digest},
            }
        ]
    }
    with pytest.raises(ValueError, match="cannot contain failed JUnit"):
        _boundary_runs(evidence, tmp_path, CANDIDATE)


def test_phase_manifest_must_bind_the_raw_execution_candidate(tmp_path: Path) -> None:
    directory = tmp_path / "cost"
    samples = _samples()
    samples[0]["candidate"] = {"content_sha256": "different-original-execution"}
    _write(directory, samples)
    with pytest.raises(ValueError, match="original phase manifest"):
        audit((directory,))


def test_http_fixture_failure_stays_raw_without_becoming_a_runtime_refusal(tmp_path: Path) -> None:
    directory = tmp_path / "cost"
    failure = {
        **_samples(scenario="baseline", profile="http-json-public")[0],
        "cost_scope": "physical",
        "status": "failed",
        "error_type": "FixtureSetupError",
        "traceback": "HTTP fixture could not be authored; no graph execution occurred",
    }
    _write(directory, [failure])
    report = audit((directory,))
    assert obj(report["raw_counts"])["failed"] == 1
    assert obj(arr(report["cohort_results"])[0])["status"] == "failed"
    v17 = next(
        obj(row) for row in obj(report["results"]).values() if "physical_applicability" in obj(row)
    )
    mode = next(
        obj(row)
        for row in arr(v17["physical_applicability"])
        if obj(row)["profile"] == "duckdb:http-json-public"
        and obj(row)["facts"] == 1000
        and obj(row)["route"] == "ibis"
    )
    assert mode["status"] == "contract-inapplicable"
    assert mode["failed_attempts"] == 1
    assert obj(v17["boundary_status"])["http_refusal"] == "unverified"
    assert v17["status"] == "unverified"


def test_require_complete_cli_distinguishes_partial_report_from_28_id_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.r96_cost_results as results

    report: dict[str, object] = {}

    def controlled_audit(
        directories: Sequence[Path],
        reuse_snapshot: Path | None = None,
        acceptance_evidence: Path | None = None,
    ) -> dict[str, object]:
        return report

    monkeypatch.setattr(results, "audit", controlled_audit)
    for index, (passed, failed, strict, expected) in enumerate(
        ((27, 0, False, 0), (27, 0, True, 1), (28, 0, True, 0), (27, 1, False, 1))
    ):
        report["counts"] = {"passed": passed, "failed": failed, "unverified": 28 - passed - failed}
        report["accepted_requirement_ids"] = passed
        output = tmp_path / f"report-{index}.json"
        monkeypatch.setattr(
            sys,
            "argv",
            ["r96_cost_results", "--directories", str(tmp_path), "--output", str(output)]
            + (["--require-complete"] if strict else []),
        )
        assert results.main() == expected
        assert obj(load_json(output)["counts"])["passed"] == passed
