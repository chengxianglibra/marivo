"""Cost qualification requires real repetitions and exact execution routes."""

from __future__ import annotations

import copy

import pytest

from scripts.r96_cost_results import repeated, valid


def sample(index: int, route: str = "ibis") -> dict[str, object]:
    return {
        "schema": "marivo.r96.cost-sample.v1",
        "status": "passed",
        "candidate": {"content_sha256": "candidate"},
        "oracle": {"passed": True, "result_digest": "checked-values-state-keys"},
        "identity": {"artifact_ref": f"artifact-{index}", "root_route": route},
        "requested_route": route,
        "temperature": "warmup" if index == 0 else "measured",
        "iteration": index,
        "elapsed_seconds": 0.01,
        "observations": {
            "resource_closed": True,
            "source_submissions": [{"state": "succeeded"}],
            "storage_writes": [{"receipt": "primary-and-parts"}],
            "phase_calls": {"fixed_kernel": 0},
        },
    }


def test_repetitions_require_new_realizations_and_actual_route() -> None:
    samples = [sample(index) for index in range(4)]
    assert repeated(samples, {"content_sha256": "candidate"}, warmup=True) == (True, "passed")
    same = copy.deepcopy(samples)
    for row in same:
        row["identity"] = {"artifact_ref": "historical-hit", "root_route": "ibis"}
    assert not repeated(same, {"content_sha256": "candidate"}, warmup=True)[0]
    wrong = sample(1)
    wrong["requested_route"] = "ibis_python"
    with pytest.raises(ValueError, match="physical route"):
        valid(wrong, {"content_sha256": "candidate"})


def test_failed_or_missing_measurement_does_not_replace_success_goal() -> None:
    rows = [sample(index) for index in range(3)]
    assert not repeated(rows, {"content_sha256": "candidate"}, warmup=True)[0]
    rows.append({**sample(3), "status": "failed"})
    assert not repeated(rows, {"content_sha256": "candidate"}, warmup=True)[0]
    with pytest.raises(ValueError, match="candidate"):
        valid(sample(1), {"content_sha256": "other"})


def test_fixed_kernel_and_exact_hit_are_independent_measurements() -> None:
    row = sample(1, "artifact_python")
    row["producer_identity"] = {"artifact_ref": "source"}
    row["fixed_mode"] = "kernel"
    row["observations"] = {
        "resource_closed": True,
        "source_submissions": [],
        "storage_writes": [{"receipt": "new-fixed-result"}],
        "phase_calls": {"fixed_kernel": 1},
    }
    valid(row, {"content_sha256": "candidate"})
    row["fixed_mode"] = "exact_hit"
    with pytest.raises(ValueError, match="must not recompute"):
        valid(row, {"content_sha256": "candidate"})
    row["observations"] = {
        "resource_closed": True,
        "source_submissions": [],
        "storage_writes": [],
        "phase_calls": {"fixed_kernel": 0},
    }
    valid(row, {"content_sha256": "candidate"})
    row["fixed_mode"] = "kernel"
    with pytest.raises(ValueError, match="publication"):
        valid(row, {"content_sha256": "candidate"})


def test_long_runs_require_actual_multi_batch_transport() -> None:
    row = sample(1, "ibis_python")
    row["scenario"] = "cross-batch-long-runs-unavailable"
    row["oracle"] = {
        "passed": True,
        "result_digest": "checked-values-state-keys",
        "original_key_binding": True,
        "original_key_digest": "subject-cell-and-segment-mapping",
    }
    observations = row["observations"]
    assert isinstance(observations, dict)
    batch = {"purpose": "observation", "source_identity": "grid", "rows": 24576}
    observations["arrow_exchange"] = {"batches": [batch]}
    with pytest.raises(ValueError, match="multi-batch"):
        valid(row, {"content_sha256": "candidate"})
    observations["arrow_exchange"] = {"batches": [{**batch, "rows": 1024}, {**batch, "rows": 512}]}
    valid(row, {"content_sha256": "candidate"})


def test_non_baseline_multisets_cannot_replace_original_key_binding() -> None:
    row = sample(1)
    row["scenario"] = "event-lifecycle-anchor"
    with pytest.raises(ValueError, match="original-key binding"):
        valid(row, {"content_sha256": "candidate"})
    row["oracle"] = {
        "passed": True,
        "result_digest": "checked-values-state-keys",
        "original_key_binding": True,
        "original_key_digest": "original-fact-and-journey-mapping",
    }
    valid(row, {"content_sha256": "candidate"})
    row["scenario"] = "baseline"
    row["oracle"] = {"passed": True, "result_digest": "independent-original-integer-sum"}
    valid(row, {"content_sha256": "candidate"})


def test_functional_probe_checks_execution_but_never_satisfies_repeated_cost() -> None:
    from scripts.r96_cost_results import EFFICIENCY_SCHEDULE, _functional_group, valid_functional

    candidate = {"content_sha256": "candidate"}
    probe = {
        **sample(0),
        "schema": "marivo.r96.functional-probe.v1",
        "measurement_kind": "functional",
        "acceptance_schedule": EFFICIENCY_SCHEDULE,
        "temperature": "functional",
        "facts": 1000,
    }
    valid_functional(probe, candidate)
    assert _functional_group([probe], candidate) == (True, "passed")
    assert not repeated([probe], candidate, warmup=True)[0]
    assert not _functional_group([probe, probe], candidate)[0]
    assert not _functional_group([{**probe, "status": "failed"}], candidate)[0]


@pytest.mark.parametrize("schedule", ("r96-efficiency-42-v1", "r96-remaining-1k-v1"))
def test_efficiency_audit_preserves_alias_ids_without_inventing_scale_timings(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, schedule: str
) -> None:
    import json
    from pathlib import Path

    from devtools.analysis_r9_cost import EFFICIENCY_SCENARIOS, FIXED_APPLICABLE, SOURCE_ROUTES
    from scripts import r9_qualification_requirements as freeze
    from scripts import r96_cost_results as results

    assert isinstance(tmp_path, Path)
    remaining = schedule == results.REMAINING_1K_SCHEDULE
    candidate = {"content_sha256": "candidate"}
    directory = tmp_path / "measurements"
    (directory / "samples").mkdir(parents=True)
    manifest = {
        "schema": "marivo.r96.cost-manifest.v1",
        "candidate": candidate,
        "required_source_routes": {key: list(value) for key, value in SOURCE_ROUTES.items()},
        "fixed_applicable": FIXED_APPLICABLE,
        "acceptance_schedule": schedule,
        "formal_sizes": [1000] if remaining else [100000],
        "functional_sizes": [] if remaining else [1000],
        "scenario_coverage": results.SCENARIO_COVERAGE,
        "physical_fixed_cost_owner": "ordinary-fixed-shape",
        "scheduled_scenarios": list(EFFICIENCY_SCENARIOS),
        "expected_cost_groups": 32,
        "expected_functional_probes": 0 if remaining else 32,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    scenarios = (*results.SCENARIO_COVERAGE, *results.SCENARIO_COVERAGE.values())
    rows = [
        {
            "id": name,
            "family": "cost-scenario",
            "scenario": name,
            "backend": "none",
            "route": "none",
            "gap_owner": "R9.6",
        }
        for name in scenarios
    ]
    rows.extend(
        [
            {
                "id": "fixed-owner",
                "family": "cost-baseline",
                "scenario": "baseline",
                "backend": "none",
                "route": "artifact_python",
                "gap_owner": "R9.6",
            },
            {
                "id": "v17",
                "family": "boundary",
                "backend": "none",
                "route": "none",
                "gap_owner": "R9.6",
            },
        ]
    )
    requirements = tmp_path / "requirements"
    freeze.save(
        freeze.obj(freeze.checked({"schema": "fixture.freeze.v1", "requirements": rows})),
        requirements,
    )
    monkeypatch.setattr(freeze, "OUTPUT", requirements)
    number = 0
    for scenario in results.SCENARIO_COVERAGE.values():
        modes: list[tuple[str, str | None]] = [("ibis_python", None)]
        if FIXED_APPLICABLE[scenario]:
            modes.extend(("artifact_python", mode) for mode in ("kernel", "exact_hit"))
        for route, mode in modes:
            for facts, iterations in (
                ((1000, range(4)),) if remaining else ((1000, range(1)), (100000, range(4)))
            ):
                for index in iterations:
                    record = {
                        **sample(index, route),
                        "candidate": candidate,
                        "backend": "duckdb",
                        "profile": "table",
                        "scenario": scenario,
                        "facts": facts,
                        "cost_scope": "ordinary",
                        "fixed_mode": mode,
                        "measurement_kind": "cost",
                        "acceptance_schedule": schedule,
                        "oracle": {
                            "passed": True,
                            "result_digest": "fixture-result",
                            "original_key_binding": True,
                            "original_key_digest": "fixture-full-key-map",
                        },
                    }
                    if route == "artifact_python":
                        record["producer_identity"] = {"artifact_ref": "fixture-producer"}
                        record["observations"] = {
                            "resource_closed": True,
                            "source_submissions": [],
                            "storage_writes": [] if mode == "exact_hit" else [{}],
                            "phase_calls": {"fixed_kernel": int(mode == "kernel")},
                        }
                    if facts == 1000 and not remaining:
                        record.update(
                            schema="marivo.r96.functional-probe.v1",
                            measurement_kind="functional",
                            temperature="functional",
                        )
                    (directory / "samples" / f"{number}.json").write_text(json.dumps(record))
                    number += 1
    report = results.audit([directory])
    qualified = results.obj(report["results"])
    assert set(qualified) == {str(row["id"]) for row in rows}
    for alias, representative in results.SCENARIO_COVERAGE.items():
        covered = results.obj(qualified[alias])
        assert covered["status"] == "shared-covered"
        assert covered["representative"] == representative
        assert covered["waived_cost_scales"] == ([100000] if remaining else [1000])
        assert covered["formal_sizes"] == ([1000] if remaining else [100000])
    assert report["raw_samples"] == 16
    assert len(results.arr(report["functional_probes"])) == (0 if remaining else 4)
    assert results.obj(qualified["v17"])["status"] == "unverified"
    if remaining:
        physical = [
            results.obj(row)
            for row in results.arr(results.obj(qualified["v17"])["physical_applicability"])
        ]
        assert all(
            row["status"] == "scale-waived"
            for row in physical
            if row["facts"] == 100000
            and row["profile"] not in (*results.HTTP_PROFILES, "clickhouse:distributed")
        )
        assert report["acceptance_schedule"] == results.REMAINING_1K_SCHEDULE


@pytest.mark.parametrize("junit_status", ("passed", "skipped", "failed"))
def test_c7_boundary_run_keeps_its_original_candidate_and_actual_junit_status(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, junit_status: str
) -> None:
    import json
    from pathlib import Path

    from scripts import r9_qualification_requirements as freeze
    from scripts import r96_cost_results as results

    assert isinstance(tmp_path, Path)
    owner = "marivo/analysis/materialization/graph_exchange.py"
    (tmp_path / owner).parent.mkdir(parents=True)
    (tmp_path / owner).write_text("unchanged boundary owner\n")
    owner_digest = freeze.digest((tmp_path / owner).read_bytes())
    previous = {"content_sha256": "actual-c7", "source_sha256": {owner: owner_digest}}
    candidate = {"content_sha256": "actual-c8", "source_sha256": {owner: owner_digest}}
    repair = {
        "repairs": {
            "efficiency_schedule": {
                "original_candidate": previous,
                "candidate": candidate,
                "unchanged_boundary_sources": {owner: owner_digest},
            }
        }
    }
    monkeypatch.setattr(freeze, "ROOT", tmp_path)
    cases = []
    for name in results.BOUNDARY_CASES["resources"]:
        classname, test = name.split("::")
        child = (
            ""
            if junit_status == "passed"
            else f"<{junit_status if junit_status == 'skipped' else 'failure'}/>"
        )
        cases.append(f'<testcase classname="{classname}" name="{test}">{child}</testcase>')
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite>" + "".join(cases) + "</testsuite>")
    junit_digest = freeze.digest(junit.read_bytes())
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "candidate": previous,
                "command": [".venv/bin/pytest", "actual-boundary-nodes"],
                "exit_code": 0,
                "attachments": {"junit.xml": junit_digest},
            }
        )
    )
    evidence = {
        "boundary_runs": {
            "resources": [
                {
                    "run": {"path": "run.json", "sha256": freeze.digest(run.read_bytes())},
                    "junit": {"path": "junit.xml", "sha256": junit_digest},
                }
            ]
        }
    }
    if junit_status == "failed":
        with pytest.raises(ValueError, match="failed JUnit"):
            results._boundary_runs(evidence, tmp_path, candidate, repair)
    else:
        outcomes, attachments = results._boundary_runs(evidence, tmp_path, candidate, repair)
        assert outcomes["resources"] == ("passed" if junit_status == "passed" else "unverified")
        assert attachments[0]["sha256"] == freeze.digest(run.read_bytes())
        assert results.load(run)["candidate"] == previous
        assert not results._retained_boundary_candidate(
            {"candidate": {**previous, "content_sha256": "other"}}, candidate, repair
        )


@pytest.mark.parametrize(
    "outcome",
    (
        "passed",
        "failed",
        "missing",
        "another-producer",
        "different-receipt",
        "different-part-receipt",
        "retained-c8",
        "remaining-1k",
        "remaining-1k-wrong-scale",
        "retained-c8-through-c10",
    ),
)
def test_physical_fixed_cover_requires_the_actual_source_offline_producer_binding(
    tmp_path: object, outcome: str
) -> None:
    import json
    from dataclasses import asdict, replace
    from pathlib import Path

    import pyarrow as pa

    from marivo.analysis.core.model import Binding, DomainSignature, Signature
    from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt, manifest_digest
    from marivo.analysis.materialization.graph_protocol import (
        DESCRIPTOR,
        Descriptor,
        MethodState,
        PartReceipt,
        PrimaryReceipt,
        RowContract,
        RowSetContract,
        encode,
        schema_text,
    )
    from scripts import r9_qualification_requirements as freeze
    from scripts import r96_cost_results as results
    from scripts import r96_cost_reuse as reuse

    assert isinstance(tmp_path, Path)
    backend, profile = (
        ("clickhouse", "distributed") if outcome.startswith("retained-c8") else ("duckdb", "csv")
    )
    directory = tmp_path / "physical"
    (directory / "physical-fixed-bindings").mkdir(parents=True)
    producer = {
        **sample(0, "ibis_python"),
        "backend": backend,
        "profile": profile,
        "facts": 100000,
        "scenario": "baseline",
        "cost_scope": "physical",
        "oracle": {"passed": True, "result_digest": "actual-original-sum", "expected": 529},
        "identity": {
            "artifact_ref": "actual-csv-producer",
            "root_route": "ibis_python",
            "parts": ["original_state", "coverage"],
        },
    }
    if outcome.startswith("retained-c8"):
        producer["candidate"] = {"content_sha256": reuse.PHYSICAL_BINDING_ORIGINAL_SHA256}
    if outcome.startswith("remaining-1k"):
        producer["facts"] = 1000
    candidate = results.obj(producer["candidate"])
    entries = (FileEntry("data.parquet", 10, "a" * 64),)
    local = LocalReceipt(
        "capture/primary", entries, manifest_digest(entries), "a" * 64, "b" * 64, 1, 12
    )
    primary = PrimaryReceipt(
        "marivo.analysis.receipt/v1", "primary", "actual-source-capture", (), local
    )
    retained_parts = tuple(
        PartReceipt(
            "marivo.analysis.receipt/v1",
            "part",
            "actual-source-capture",
            (),
            replace(local, project_relative_path=f"capture/{role}"),
            role,
            f"marivo.analysis.part.{role}",
            1,
            1,
        )
        for role in ("original_state", "coverage")
    )
    descriptor = Descriptor(
        schema="marivo.analysis.artifact_descriptor/v1",
        definition_fingerprint="actual-definition",
        producing_run_ref="actual-run",
        execution_key_digest="actual-execution",
        signature=Signature(
            DomainSignature(
                Binding("session", "owner", "input", "scope"), "singleton", (), (), "scalar"
            )
        ),
        row_contract=RowContract((), ()),
        row_set_contract=RowSetContract("singleton", "unordered"),
        realized_schema=schema_text(pa.schema([("value", pa.int64())])),
        semantic_dependency_digest="actual-semantic-dependency",
        method_bindings=(),
        completed_checks=(),
        primary_receipt=primary,
        parts=retained_parts,
        method_state=MethodState(
            "marivo.analysis.method_state/v1",
            "original_sum_zero",
            "marivo.analysis.state.original_sum_zero",
            1,
            "state_rollup.sum_zero",
            1,
            "actual-source-capture",
            ("original_state", "coverage"),
        ),
        continuation_snapshot="actual-continuation",
        continuation_snapshot_digest="actual-continuation-digest",
    )
    primary_receipt = results.obj(json.loads(json.dumps(asdict(primary))))
    parts = [results.obj(json.loads(json.dumps(asdict(part)))) for part in retained_parts]
    snapshot = {
        "artifact_ref": "actual-csv-producer",
        "carrier": "MaterializedRolledNumericRelation",
        "descriptor": json.loads(encode(descriptor, DESCRIPTOR)),
        "schema": [["value", "int64"], ["cell_tag", "string"], ["cell_reason", "string"]],
        "schema_sha256": "actual-schema-digest",
        "primary_rows": 1,
        "primary_receipt": primary_receipt,
        "parts": [
            {
                "role": part["role"],
                "schema": [[part["role"], "int64"]],
                "schema_sha256": "actual-part-schema",
                "rows": 1,
                "receipt": part,
            }
            for part in parts
        ],
    }
    observations = {
        "resource_closed": True,
        "source_submissions": [],
        "source_sessions": [],
        "native_submissions": [{"category": "store"}],
        "storage_writes": [{"receipt": "actual-fixed-publication"}],
        "phase_calls": {"fixed_kernel": 1},
    }
    binding = {
        "schema": "marivo.r96.physical-fixed-binding.v1",
        "candidate": candidate,
        "acceptance_schedule": results.REMAINING_1K_SCHEDULE
        if outcome.startswith("remaining-1k")
        else results.EFFICIENCY_SCHEDULE,
        "backend": backend,
        "profile": profile,
        "facts": 1000 if outcome == "remaining-1k" else 100000,
        "scenario": "baseline",
        "temperature": "warmup",
        "iteration": 0,
        "cost_measured": False,
        "status": "failed" if outcome == "failed" else "passed",
        "producer_identity": producer["identity"],
        "producer_oracle": producer["oracle"],
        "producer_sample_path": "samples/actual-source.json",
        "producer_snapshot": snapshot,
        "recovered_snapshot": snapshot,
        "source_semantic_forbidden": True,
        "observations": observations,
        "fixed": {
            "identity": {
                "root_route": "artifact_python",
                "artifact_ref": "actual-fixed-artifact",
                "method_bindings": [
                    {
                        "route": "artifact_python",
                        "key": {"method": "row.sum", "shape": "fixed-int64-scalar"},
                    }
                ],
            },
            "oracle": producer["oracle"],
            "observations": observations,
        },
    }
    if outcome == "another-producer":
        binding["producer_identity"] = {
            "artifact_ref": "other-file-producer",
            "root_route": "ibis_python",
        }
    if outcome == "different-receipt":
        snapshot["primary_receipt"] = {
            **primary_receipt,
            "input_binding": "different-source-capture",
        }
    if outcome == "different-part-receipt":
        snapshot["parts"] = [
            {
                **results.obj(part),
                "receipt": {**parts[0], "input_binding": "different-source-capture"},
            }
            if index == 0
            else part
            for index, part in enumerate(results.arr(snapshot["parts"]))
        ]
    filename = f"{backend}-{profile}.json"
    path = directory / "physical-fixed-bindings" / filename
    path.write_text(json.dumps(binding))
    reference = {"path": str(path), "sha256": freeze.digest(path.read_bytes())}
    producer["physical_fixed_binding"] = {
        **reference,
        "path": "physical-fixed-bindings/" + filename,
    }
    evidence = {"physical_fixed_bindings": [] if outcome == "missing" else [reference]}
    references = {id(producer): {"path": str(directory / "samples" / "actual-source.json")}}
    ordinary = {
        **sample(0, "artifact_python"),
        "scenario": "baseline",
        "cost_scope": "ordinary",
        "fixed_mode": "kernel",
        "identity": results.obj(binding["fixed"])["identity"],
    }
    samples = [producer, ordinary]
    audit_candidate = (
        {"content_sha256": "after-receipt-repair"}
        if outcome.startswith("retained-c8")
        else candidate
    )
    repair = (
        {
            "repairs": {
                "physical_binding": {"original_candidate": candidate, "candidate": audit_candidate}
            }
        }
        if outcome.startswith("retained-c8")
        else None
    )
    if outcome == "retained-c8-through-c10":
        predecessor = {"content_sha256": reuse.REMAINING_1K_ORIGINAL_SHA256}
        repair = {
            "repairs": {
                "physical_binding": {"original_candidate": candidate, "candidate": predecessor},
                "remaining_1k_schedule": {
                    "original_candidate": predecessor,
                    "candidate": audit_candidate,
                },
            }
        }
    if outcome in (
        "another-producer",
        "different-receipt",
        "different-part-receipt",
        "remaining-1k-wrong-scale",
    ):
        expected = (
            "actual retained producer"
            if outcome == "another-producer"
            else "schedule-bound"
            if outcome == "remaining-1k-wrong-scale"
            else "primary/part receipts"
        )
        with pytest.raises(ValueError, match=expected):
            results._physical_fixed_bindings(evidence, tmp_path, candidate, samples, references)
    else:
        outcomes, attachments = results._physical_fixed_bindings(
            evidence, tmp_path, audit_candidate, samples, references, repair
        )
        if outcome == "missing":
            assert outcomes == {} and attachments == []
        else:
            assert outcomes[f"{backend}:{profile}"][0] == (
                "passed"
                if outcome.startswith("retained-c8") or outcome == "remaining-1k"
                else outcome
            )
            assert outcomes[f"{backend}:{profile}"][2] == reference


@pytest.mark.parametrize("successor", ("c9", "c10"))
def test_physical_repair_preserves_the_actual_c7_boundary_candidate_chain(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, successor: str
) -> None:
    from pathlib import Path

    from scripts import r9_qualification_requirements as freeze
    from scripts import r96_cost_results as results

    assert isinstance(tmp_path, Path)
    path = "marivo/analysis/materialization/graph_exchange.py"
    owner = tmp_path / path
    owner.parent.mkdir(parents=True)
    owner.write_text("unchanged owning source")
    owners = {path: freeze.digest(owner.read_bytes())}
    previous = {"content_sha256": "actual-c7", "source_sha256": owners}
    intermediate = {"content_sha256": "actual-c8", "source_sha256": owners}
    current = {"content_sha256": "actual-c9", "source_sha256": owners}
    repair = {
        "repairs": {
            "efficiency_schedule": {
                "original_candidate": previous,
                "candidate": intermediate,
                "unchanged_boundary_sources": owners,
            },
            "physical_binding": {
                "original_candidate": intermediate,
                "candidate": current,
                "unchanged_boundary_sources": owners,
            },
        }
    }
    monkeypatch.setattr(freeze, "ROOT", tmp_path)
    if successor == "c10":
        remaining = {"content_sha256": "actual-c10", "source_sha256": owners}
        repair["repairs"]["remaining_1k_schedule"] = {
            "original_candidate": current,
            "candidate": remaining,
            "unchanged_boundary_sources": owners,
        }
        current = remaining
    run = {"candidate": previous}
    assert results._retained_boundary_candidate(run, current, repair)
    assert run["candidate"] == previous
    assert not results._retained_boundary_candidate({"candidate": intermediate}, current, repair)
    assert not results._retained_boundary_candidate(
        run, {**current, "content_sha256": "other"}, repair
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("formal_sizes", [100000]), ("functional_sizes", [1000]), ("expected_functional_probes", 32)),
)
def test_remaining_1k_manifest_refuses_unapproved_scales_and_extra_probes(
    field: str, value: object
) -> None:
    from devtools.analysis_r9_cost import EFFICIENCY_SCENARIOS
    from scripts import r96_cost_results as results

    manifest = {
        "acceptance_schedule": results.REMAINING_1K_SCHEDULE,
        "formal_sizes": [1000],
        "functional_sizes": [],
        "scenario_coverage": results.SCENARIO_COVERAGE,
        "physical_fixed_cost_owner": "ordinary-fixed-shape",
        "scheduled_scenarios": list(EFFICIENCY_SCENARIOS),
        "expected_cost_groups": 32,
        "expected_functional_probes": 0,
    }
    assert results._efficiency_manifest(manifest)
    with pytest.raises(ValueError):
        results._efficiency_manifest({**manifest, field: value})
