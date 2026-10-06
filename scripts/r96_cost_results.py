"""Audit raw R9.6 samples and preserve every frozen cost requirement."""

from __future__ import annotations

import argparse
import ast
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from scripts import r9_qualification_requirements as freeze

GROUP_FIELDS = (
    "backend",
    "profile",
    "facts",
    "scenario",
    "cost_scope",
    "requested_route",
    "fixed_mode",
    "recovery",
)
COHORT_FIELDS = (
    "execution_candidate",
    "fixture_layout",
    "deployment_cohort",
    "deployment_configuration",
    "usage_observation",
)
HTTP_PROFILES = ("duckdb:http-json-public", "duckdb:http-json-auth")
HISTORICAL_OWNERS = {
    "http_refusal": {
        (
            "tests/test_r94_file_admission.py",
            "test_json_request_sources_are_refused_before_source_open",
        ),
        ("marivo/analysis/materialization/graph_preflight.py", "preflight_entities"),
        ("marivo/analysis/materialization/graph_preflight.py", "_local_file"),
    }
}
BOUNDARY_CASES = {
    "extension": (
        "tests.test_r96_extension_boundaries::test_provider_replacement_preserves_business_kernel",
        "tests.test_r96_extension_boundaries::test_missing_or_unavailable_exact_provider_never_borrows_route",
        "tests.test_r96_extension_boundaries::test_wrong_schema_refuses_before_compilation_or_submission",
        "tests.test_datasource_adapter_contract::test_selected_provider_does_not_import_unselected_modules",
    ),
    "http_refusal": tuple(
        "tests.test_r94_file_admission::test_json_request_sources_are_refused_before_source_open["
        + kind
        + "]"
        for kind in ("http", "shadow-http", "parameterized")
    ),
    "resources": (
        "tests.test_analysis_dsl_exchange::test_r43_checked_stream_failure_and_cancel_never_complete",
        "tests.test_analysis_domain_preparation_r72::test_cross_batch_complete_keys_schema_and_early_close",
    ),
}
EFFICIENCY_SCHEDULE = "r96-efficiency-42-v1"
REMAINING_1K_SCHEDULE = "r96-remaining-1k-v1"
SCENARIO_COVERAGE = {
    "event-lifecycle-anchor": "many-occurrences-anchors-lags",
    "forecast-models": "full-training-numeric-extremes",
}
COMPLETE_STATUSES = ("passed", "shared-covered", "scale-waived")


def obj(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected a JSON object")
    return value


def arr(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return value


def load(path: Path) -> Mapping[str, object]:
    return obj(json.loads(path.read_text()))


def valid(sample: Mapping[str, object], candidate: Mapping[str, object]) -> None:
    if sample.get("schema") != "marivo.r96.cost-sample.v1" or sample.get("status") != "passed":
        raise ValueError("A successful raw measured sample is required")
    if obj(sample["candidate"]) != candidate:
        raise ValueError("Sample candidate differs from its measurement manifest")
    elapsed = sample.get("elapsed_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("A finite nonnegative elapsed measurement is required")
    oracle = obj(sample["oracle"])
    if oracle.get("passed") is not True or not oracle.get("result_digest"):
        raise ValueError("A result, state and identity oracle must pass with its digest")
    if sample.get("scenario") not in (None, "baseline") and (
        oracle.get("original_key_binding") is not True or not oracle.get("original_key_digest")
    ):
        raise ValueError(
            "A non-baseline sample requires independent original-key binding and digest"
        )
    identity = obj(sample["identity"])
    route = sample["requested_route"]
    if identity.get("root_route") != route or not identity.get("artifact_ref"):
        raise ValueError("The executed physical route must match the recorded route")
    observations = obj(sample["observations"])
    if observations.get("resource_closed") is not True:
        raise ValueError("Every measured source/reader owner must be released")
    if not arr(observations["storage_writes"]) and sample.get("fixed_mode") != "exact_hit":
        raise ValueError("A kernel sample must contain receipt-bound publication")
    if route == "artifact_python":
        if arr(observations["source_submissions"]):
            raise ValueError("Fixed continuation must not access a source")
        if not sample.get("producer_identity"):
            raise ValueError("Fixed cost must bind its actual retained producer")
        kernel_calls = obj(observations["phase_calls"]).get("fixed_kernel", 0)
        if sample.get("fixed_mode") == "kernel" and kernel_calls == 0:
            raise ValueError("A kernel measurement cannot be replaced by an exact hit")
        if sample.get("fixed_mode") == "exact_hit" and (
            kernel_calls != 0 or arr(observations["storage_writes"])
        ):
            raise ValueError("An exact hit must not recompute or publish")
    elif not arr(observations["source_submissions"]):
        raise ValueError("A source measurement must use a new source realization")
    if sample.get("scenario") == "cross-batch-long-runs-unavailable" and route != "artifact_python":
        groups: dict[tuple[str, str], list[int]] = {}
        for value in arr(obj(observations["arrow_exchange"])["batches"]):
            batch = obj(value)
            rows = batch["rows"]
            if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
                raise ValueError("A nonnegative actual exchange batch size is required")
            key = (str(batch["purpose"]), str(batch["source_identity"]))
            groups.setdefault(key, []).append(rows)
        if not any(len(sizes) > 1 and sum(sizes) >= 1536 for sizes in groups.values()):
            raise ValueError("Long runs require an observed multi-batch complete-grid exchange")


def repeated(
    samples: Sequence[Mapping[str, object]],
    candidate: Mapping[str, object],
    *,
    warmup: bool,
) -> tuple[bool, str]:
    try:
        for sample in samples:
            valid(sample, candidate)
        measured = [sample for sample in samples if sample.get("temperature") == "measured"]
        if len(measured) < 3 or len({sample["iteration"] for sample in measured}) != len(measured):
            raise ValueError("At least three distinct measured iterations are required")
        if warmup and len([s for s in samples if s.get("temperature") == "warmup"]) != 1:
            raise ValueError("Exactly one warmup must precede source measurements")
        if warmup:
            identities = [obj(sample["identity"])["artifact_ref"] for sample in samples]
            if len(set(identities)) != len(identities):
                raise ValueError("Source repetitions must not measure a historical definition hit")
        return True, "passed"
    except (ValueError, KeyError, TypeError) as error:
        return False, str(error)


def valid_functional(sample: Mapping[str, object], candidate: Mapping[str, object]) -> None:
    """Validate a single 1k execution without treating it as a repeated cost group."""
    if (
        sample.get("schema") != "marivo.r96.functional-probe.v1"
        or sample.get("measurement_kind") != "functional"
        or sample.get("temperature") != "functional"
        or type(sample.get("iteration")) is not int
        or sample["iteration"] != 0
        or sample.get("facts") != 1000
        or sample.get("acceptance_schedule") != EFFICIENCY_SCHEDULE
    ):
        raise ValueError("The efficiency schedule requires one exact 1k functional execution")
    valid({**sample, "schema": "marivo.r96.cost-sample.v1"}, candidate)


def _functional_group(
    samples: Sequence[Mapping[str, object]], candidate: Mapping[str, object]
) -> tuple[bool, str]:
    if len(samples) != 1:
        return False, "Exactly one functional probe is required per applicable group"
    try:
        valid_functional(samples[0], candidate)
        return True, "passed"
    except (ValueError, KeyError, TypeError) as error:
        return False, str(error)


def _efficiency_manifest(manifest: Mapping[str, object]) -> bool:
    schedule = manifest.get("acceptance_schedule")
    if schedule is None:
        return False
    remaining = schedule == REMAINING_1K_SCHEDULE
    if (
        schedule not in (EFFICIENCY_SCHEDULE, REMAINING_1K_SCHEDULE)
        or manifest.get("formal_sizes") != ([1000] if remaining else [100000])
        or manifest.get("functional_sizes") != ([] if remaining else [1000])
        or manifest.get("scenario_coverage") != SCENARIO_COVERAGE
        or manifest.get("physical_fixed_cost_owner") != "ordinary-fixed-shape"
    ):
        raise ValueError("Manifest differs from its explicitly approved bounded cost schedule")
    from devtools.analysis_r9_cost import EFFICIENCY_SCENARIOS

    groups = manifest.get("expected_cost_groups")
    scenarios = manifest.get("scheduled_scenarios")
    if (
        type(groups) is not int
        or manifest.get("expected_functional_probes") != (0 if remaining else groups)
        or not (
            (scenarios == list(EFFICIENCY_SCENARIOS) and groups == 32)
            or (
                scenarios == ["baseline"]
                and groups in ((2, 4, 6, 8) if remaining else (2, 4, 6, 8, 10))
            )
        )
    ):
        raise ValueError("Efficiency manifest must retain its exact scenario and group counts")
    return True


def _group_candidate(
    samples: Sequence[Mapping[str, object]], fallback: Mapping[str, object]
) -> Mapping[str, object]:
    if not samples:
        return fallback
    binding = obj(samples[0]["candidate"])
    if any(obj(sample["candidate"]) != binding for sample in samples):
        raise ValueError("A repetition group must retain one original execution candidate")
    return binding


def _cohort(sample: Mapping[str, object]) -> dict[str, object]:
    environment = obj(sample.get("environment", {}))
    layout = environment.get("fixture_layout")
    if layout is None:
        layout = (
            "archived_no_index"
            if sample.get("cost_scope", "ordinary") == "ordinary"
            else "archived_profile"
        )
    deployment = environment.get("deployment_cohort", "unrecorded")
    evidence = environment.get("deployment_evidence")
    configuration = obj(evidence).get("configuration") if evidence is not None else None
    return {
        "execution_candidate": obj(sample["candidate"]).get("content_sha256"),
        "fixture_layout": layout,
        "deployment_cohort": deployment,
        "deployment_configuration": configuration,
        "usage_observation": False,
    }


def _cohort_groups(
    samples: Sequence[Mapping[str, object]],
) -> list[list[Mapping[str, object]]]:
    groups: dict[str, list[Mapping[str, object]]] = {}
    for sample in samples:
        key = json.dumps(_cohort(sample), sort_keys=True, allow_nan=False)
        groups.setdefault(key, []).append(sample)
    return list(groups.values())


def _group_identity(sample: Mapping[str, object]) -> dict[str, object]:
    return {**{field: sample.get(field) for field in GROUP_FIELDS}, **_cohort(sample)}


def _accepted_samples(
    samples: Sequence[Mapping[str, object]], accepted: Sequence[Mapping[str, object]] | None
) -> list[Mapping[str, object]]:
    if accepted is None:
        return list(samples)
    keys = {json.dumps(row, sort_keys=True, allow_nan=False) for row in accepted}
    return [
        sample
        for sample in samples
        if json.dumps(_group_identity(sample), sort_keys=True, allow_nan=False) in keys
    ]


def _attachment(value: object, directory: Path) -> tuple[Path, dict[str, object]]:
    reference = obj(value)
    path = Path(str(reference["path"]))
    if not path.is_absolute():
        path = directory / path
    path = path.resolve()
    digest = freeze.digest(path.read_bytes())
    if reference.get("sha256") != digest:
        raise ValueError("Acceptance attachment hash differs")
    return path, {"path": str(path), "sha256": digest}


def _retained_boundary_candidate(
    run: Mapping[str, object],
    candidate: Mapping[str, object],
    reuse_proof: Mapping[str, object] | None,
) -> bool:
    if reuse_proof is None:
        return False
    repair = obj(reuse_proof.get("repairs", {})).get("efficiency_schedule")
    if repair is None:
        return False
    reviewed = obj(repair)
    if run.get("candidate") != reviewed.get("original_candidate"):
        return False
    if candidate != reviewed.get("candidate"):
        physical = obj(obj(reuse_proof.get("repairs", {})).get("physical_binding", {}))
        remaining = obj(obj(reuse_proof.get("repairs", {})).get("remaining_1k_schedule", {}))
        if (
            physical.get("original_candidate") != reviewed.get("candidate")
            or (
                physical.get("candidate") != candidate
                and not (
                    remaining.get("original_candidate") == physical.get("candidate")
                    and remaining.get("candidate") == candidate
                    and remaining.get("unchanged_boundary_sources")
                    == reviewed.get("unchanged_boundary_sources")
                )
            )
            or physical.get("unchanged_boundary_sources")
            != reviewed.get("unchanged_boundary_sources")
        ):
            return False
    owners = obj(reviewed["unchanged_boundary_sources"])
    if not owners:
        raise ValueError("The concrete C7 boundary reuse must retain its owning sources")
    for path, expected in owners.items():
        if (
            obj(candidate["source_sha256"]).get(path) != expected
            or obj(obj(run["candidate"])["source_sha256"]).get(path) != expected
            or freeze.digest((freeze.ROOT / path).read_bytes()) != expected
        ):
            raise ValueError("A retained C7 boundary owner differs from its actual execution")
    return True


def _retained_physical_candidate(
    sample: Mapping[str, object],
    candidate: Mapping[str, object],
    reuse_proof: Mapping[str, object] | None,
) -> bool:
    if reuse_proof is None:
        return False
    from scripts.r96_cost_reuse import eligible_physical_binding_sample

    repair = obj(obj(reuse_proof.get("repairs", {})).get("physical_binding", {}))
    remaining = obj(obj(reuse_proof.get("repairs", {})).get("remaining_1k_schedule", {}))
    return (
        (
            repair.get("candidate") == candidate
            or (
                remaining.get("original_candidate") == repair.get("candidate")
                and remaining.get("candidate") == candidate
                and remaining.get("unchanged_boundary_sources")
                == repair.get("unchanged_boundary_sources")
            )
        )
        and repair.get("original_candidate") == sample.get("candidate")
        and eligible_physical_binding_sample(sample)
    )


def _boundary_runs(
    evidence: Mapping[str, object],
    directory: Path,
    candidate: Mapping[str, object],
    reuse_proof: Mapping[str, object] | None = None,
) -> tuple[dict[str, str], list[dict[str, object]]]:
    outcomes: dict[str, str] = {}
    attachments: list[dict[str, object]] = []
    for kind, required in BOUNDARY_CASES.items():
        entries = obj(evidence.get("boundary_runs", {})).get(kind, [])
        seen: dict[str, list[bool]] = {}
        for entry in arr(entries):
            declared = obj(entry)
            run_path, run_reference = _attachment(declared["run"], directory)
            junit_path, junit_reference = _attachment(declared["junit"], directory)
            run = load(run_path)
            command = arr(run["command"])
            if (
                type(run.get("exit_code")) is not int
                or run["exit_code"] != 0
                or not command
                or any(not isinstance(token, str) or not token for token in command)
            ):
                raise ValueError("A boundary run must retain its successful actual command")
            if obj(run["attachments"]).get(junit_path.name) != junit_reference["sha256"]:
                raise ValueError("The actual boundary run must bind its JUnit attachment")
            run_candidate = run.get(
                "candidate_sha256", obj(run.get("candidate", {})).get("content_sha256")
            )
            if run_candidate != candidate.get(
                "content_sha256"
            ) and not _retained_boundary_candidate(run, candidate, reuse_proof):
                owners = arr(declared.get("unchanged_owners", []))
                expected_owners = HISTORICAL_OWNERS.get(kind)
                actual_owners = {
                    (str(obj(owner)["path"]), str(obj(owner)["symbol"])) for owner in owners
                }
                if (
                    expected_owners is None
                    or actual_owners != expected_owners
                    or len(owners) != len(expected_owners)
                ):
                    raise ValueError("Historical boundary evidence needs unchanged owner sources")
                for owner in owners:
                    item = obj(owner)
                    old_path, old_reference = _attachment(item["original_source"], directory)
                    commit = item.get("original_commit")
                    if (
                        not isinstance(commit, str)
                        or len(commit) != 40
                        or any(c not in "0123456789abcdef" for c in commit)
                    ):
                        raise ValueError(
                            "Historical owner source needs its exact original Git commit"
                        )
                    original = subprocess.check_output(
                        ["git", "show", f"{commit}:{item['path']}"], cwd=freeze.ROOT
                    )
                    if freeze.digest(original) != old_reference["sha256"]:
                        raise ValueError(
                            "Historical owner source differs from its committed origin"
                        )
                    recorded_owners = run.get(
                        "source_sha256", obj(run.get("candidate", {})).get("source_sha256")
                    )
                    if (
                        recorded_owners is not None
                        and obj(recorded_owners).get(str(item["path"])) != old_reference["sha256"]
                    ):
                        raise ValueError(
                            "Historical owner source differs from the original run freeze"
                        )
                    current_path = freeze.ROOT / str(item["path"])
                    current_digest = freeze.digest(current_path.read_bytes())
                    if current_digest != obj(candidate["source_sha256"]).get(str(item["path"])):
                        raise ValueError("Boundary owner does not match the final candidate")
                    symbol = str(item["symbol"])
                    definitions = []
                    environments = []
                    for source in (old_path.read_text(), current_path.read_text()):
                        module = ast.parse(source)
                        nodes = [
                            node
                            for node in module.body
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name == symbol
                        ]
                        if len(nodes) != 1:
                            raise ValueError("A boundary owner symbol must be uniquely retained")
                        definitions.append(ast.dump(nodes[0], include_attributes=False))
                        environments.append(
                            [
                                ast.dump(node, include_attributes=False)
                                for node in module.body
                                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            ]
                        )
                    if definitions[0] != definitions[1] or environments[0] != environments[1]:
                        raise ValueError("Boundary owner changed from its original execution")
                    attachments.append(old_reference)
            attachments.extend((run_reference, junit_reference))
            cases = list(ElementTree.parse(junit_path).getroot().iter("testcase"))
            if any(child.tag in ("failure", "error") for case in cases for child in case):
                raise ValueError("A successful boundary run cannot contain failed JUnit cases")
            for case in cases:
                name = case.get("classname", "") + "::" + case.get("name", "")
                if name in required:
                    seen.setdefault(name, []).append(
                        not any(child.tag in ("failure", "error", "skipped") for child in case)
                    )
        outcomes[kind] = (
            "passed"
            if all(seen.get(name) and all(seen[name]) for name in required)
            else "unverified"
        )
    return outcomes, attachments


def _physical_fixed_bindings(
    evidence: Mapping[str, object],
    directory: Path,
    candidate: Mapping[str, object],
    samples: Sequence[Mapping[str, object]],
    references: Mapping[int, Mapping[str, object]],
    reuse_proof: Mapping[str, object] | None = None,
) -> tuple[dict[str, tuple[str, str, dict[str, object]]], list[dict[str, object]]]:
    outcomes: dict[str, tuple[str, str, dict[str, object]]] = {}
    attachments: list[dict[str, object]] = []
    for declared in arr(evidence.get("physical_fixed_bindings", [])):
        path, reference = _attachment(declared, directory)
        binding = load(path)
        binding_candidate = obj(binding["candidate"])
        binding_schedule = binding.get("acceptance_schedule")
        binding_facts = 1000 if binding_schedule == REMAINING_1K_SCHEDULE else 100000
        profile = f"{binding.get('backend')}:{binding.get('profile')}"
        if profile in outcomes or profile in HTTP_PROFILES:
            raise ValueError(
                "Each legal physical profile needs one independently bound fixed proof"
            )
        if (
            binding.get("schema") != "marivo.r96.physical-fixed-binding.v1"
            or (
                binding_candidate != candidate
                and not _retained_physical_candidate(binding, candidate, reuse_proof)
            )
            or binding_schedule not in (EFFICIENCY_SCHEDULE, REMAINING_1K_SCHEDULE)
            or binding.get("facts") != binding_facts
            or binding.get("scenario") != "baseline"
            or binding.get("temperature") != "warmup"
            or type(binding.get("iteration")) is not int
            or binding["iteration"] != 0
            or binding.get("cost_measured") is not False
        ):
            raise ValueError(
                "Physical fixed binding needs its actual unmeasured schedule-bound warmup owner"
            )
        attachments.append(reference)
        if binding.get("status") != "passed":
            outcomes[profile] = (
                "failed",
                "The actual source-offline producer binding failed",
                reference,
            )
            continue
        producers = [
            sample
            for sample in samples
            if sample.get("cost_scope") == "physical"
            and f"{sample.get('backend')}:{sample.get('profile')}" == profile
            and sample.get("facts") == binding_facts
            and sample.get("scenario") == "baseline"
            and sample.get("requested_route") == "ibis_python"
            and sample.get("temperature") == "warmup"
            and sample.get("iteration") == 0
            and sample.get("candidate") == binding_candidate
        ]
        if len(producers) != 1:
            raise ValueError("Physical fixed binding must select one actual accepted source warmup")
        producer = producers[0]
        producer_directory = Path(str(references[id(producer)]["path"])).parent.parent
        sample_path = producer_directory / str(binding["producer_sample_path"])
        producer_path, producer_reference = _attachment(
            producer["physical_fixed_binding"], producer_directory
        )
        if (
            sample_path.resolve() != Path(str(references[id(producer)]["path"])).resolve()
            or producer_path != path
            or producer_reference["sha256"] != reference["sha256"]
            or binding.get("producer_identity") != producer.get("identity")
            or binding.get("producer_oracle") != producer.get("oracle")
        ):
            raise ValueError("The physical fixed proof differs from its actual retained producer")
        valid(producer, binding_candidate)
        snapshot = obj(binding["producer_snapshot"])
        descriptor = obj(snapshot["descriptor"])
        from marivo.analysis.materialization.contracts import canonical_json
        from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, decode

        decoded_descriptor = decode(canonical_json(descriptor), DESCRIPTOR)
        descriptor_primary = obj(json.loads(json.dumps(asdict(decoded_descriptor.primary_receipt))))
        identity = obj(producer["identity"])
        parts = [obj(part) for part in arr(snapshot["parts"])]
        descriptor_parts = {
            part.role: obj(json.loads(json.dumps(asdict(part))))
            for part in decoded_descriptor.parts
        }
        roles = [str(part["role"]) for part in parts]
        if (
            binding.get("recovered_snapshot") != snapshot
            or snapshot.get("artifact_ref") != identity.get("artifact_ref")
            or not snapshot.get("carrier")
            or not arr(snapshot["schema"])
            or not snapshot.get("schema_sha256")
            or snapshot.get("primary_rows") != 1
            or snapshot.get("primary_receipt") != descriptor_primary
            or not obj(snapshot["primary_receipt"])
            or not descriptor.get("producing_run_ref")
            or not descriptor.get("realized_schema")
            or len(set(roles)) != len(roles)
            or set(roles) != set(arr(identity["parts"]))
            or set(roles) != set(descriptor_parts)
            or not {"original_state", "coverage"} <= set(roles)
            or any(
                not arr(part["schema"])
                or not part.get("schema_sha256")
                or isinstance(part.get("rows"), bool)
                or not isinstance(row_count := part.get("rows"), int)
                or row_count < 0
                or part.get("receipt") != descriptor_parts[str(part["role"])]
                for part in parts
            )
        ):
            raise ValueError(
                "Offline recovery must retain the complete carrier, schema and primary/part receipts"
            )
        observations = obj(binding["observations"])
        fixed = obj(binding["fixed"])
        if (
            binding.get("source_semantic_forbidden") is not True
            or arr(observations["source_submissions"])
            or arr(observations["source_sessions"])
            or observations.get("resource_closed") is not True
            or any(
                obj(item).get("category") != "store"
                for item in arr(observations["native_submissions"])
            )
        ):
            raise ValueError(
                "Physical producer recovery and fixed execution must stay source-offline"
            )
        fixed_observations = obj(fixed["observations"])
        if (
            fixed.get("observations") != observations
            or obj(fixed["identity"]).get("root_route") != "artifact_python"
            or obj(fixed["oracle"]).get("expected") != obj(producer["oracle"]).get("expected")
            or not obj(fixed["oracle"]).get("result_digest")
            or obj(fixed["oracle"]).get("passed") is not True
            or not arr(fixed_observations["storage_writes"])
            or obj(fixed_observations["phase_calls"]).get("fixed_kernel", 0) == 0
        ):
            raise ValueError(
                "The physical binding must execute its actual fixed kernel, not a static shape or hit"
            )
        fixed_keys = sorted(
            json.dumps(obj(item)["key"], sort_keys=True)
            for item in arr(obj(fixed["identity"])["method_bindings"])
            if obj(item).get("route") == "artifact_python"
        )
        ordinary_keys = [
            sorted(
                json.dumps(obj(item)["key"], sort_keys=True)
                for item in arr(obj(sample["identity"])["method_bindings"])
                if obj(item).get("route") == "artifact_python"
            )
            for sample in samples
            if sample.get("status") == "passed"
            and sample.get("scenario") == "baseline"
            and sample.get("cost_scope", "ordinary") == "ordinary"
            and sample.get("requested_route") == "artifact_python"
            and sample.get("fixed_mode") == "kernel"
        ]
        if not fixed_keys or fixed_keys not in ordinary_keys:
            raise ValueError(
                "Physical fixed execution must use the exact ordinary FixedShape cost owner"
            )
        outcomes[profile] = (
            "passed",
            f"Actual {binding_facts}-fact local producer restored and continued with source/Semantic forbidden; no fixed cost timing collected",
            reference,
        )
    return outcomes, attachments


def _physical_key_available(profile: str, route: str) -> bool:
    if not profile.startswith("duckdb:") or route == "artifact_python":
        return True
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.physical import Qualified, ScalarType, SourceShape, TimeShape
    from marivo.analysis.methods.semantics import MethodKey

    form: Literal["csv", "parquet", "json"]
    if profile == "duckdb:csv":
        form = "csv"
    elif profile == "duckdb:parquet":
        form = "parquet"
    elif profile == "duckdb:local-json":
        form = "json"
    else:
        raise ValueError("Unexpected physical file profile")
    return any(
        item.key.shape == SourceShape("duckdb", form, form, TimeShape("instant", "us", "UTC"))
        and item.key.route == route
        and item.key.input_types == (ScalarType("int64"),)
        and item.key.input_domains == ("entity",)
        and isinstance(item.qualification, Qualified)
        for item in implementations(MethodKey("state_rollup.sum_zero"))
    )


def _cold_resources(samples: Sequence[Mapping[str, object]]) -> list[str]:
    errors: list[str] = []
    processes: dict[str, set[int]] = {}
    for sample in samples:
        if sample.get("recovery") != "cold":
            continue
        observations = obj(sample.get("observations", {}))
        process = obj(sample.get("process", {}))
        pid, parent = process.get("pid"), process.get("parent_pid")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(parent, bool)
            or not isinstance(parent, int)
            or parent <= 0
            or pid == parent
            or observations.get("source_sessions") != []
            or not isinstance(observations.get("native_submissions"), list)
            or any(
                obj(row).get("category") != "store"
                for row in arr(observations.get("native_submissions", []))
            )
        ):
            errors.append(
                f"{sample.get('backend')}/{sample.get('facts')}/{sample.get('fixed_mode')}: cold process/source ownership is unverified"
            )
        elif isinstance(pid, int):
            key = json.dumps(_group_identity(sample), sort_keys=True, allow_nan=False)
            seen = processes.setdefault(key, set())
            if pid in seen:
                errors.append(
                    f"{sample.get('backend')}/{sample.get('facts')}/{sample.get('fixed_mode')}: cold repetitions reused one process"
                )
            seen.add(pid)
    return errors


def _repeated_cohorts(
    samples: Sequence[Mapping[str, object]],
    fallback: Mapping[str, object],
    *,
    warmup: bool,
) -> tuple[bool, str]:
    if not samples:
        return repeated(samples, fallback, warmup=warmup)
    errors: list[str] = []
    for group in _cohort_groups(samples):
        passed, reason = repeated(group, _group_candidate(group, fallback), warmup=warmup)
        if not passed:
            errors.append(f"{json.dumps(_cohort(group[0]), sort_keys=True)}: {reason}")
    return not errors, "; ".join(errors) if errors else "passed"


def _cohort_results(samples: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    groups: dict[str, list[Mapping[str, object]]] = {}
    fields = (
        "backend",
        "profile",
        "facts",
        "scenario",
        "cost_scope",
        "requested_route",
        "fixed_mode",
        "recovery",
    )
    for sample in samples:
        identity = {field: sample.get(field) for field in fields}
        key = json.dumps({**identity, **_cohort(sample)}, sort_keys=True, allow_nan=False)
        groups.setdefault(key, []).append(sample)
    result: list[dict[str, object]] = []
    for group in groups.values():
        passed, reason = repeated(group, _group_candidate(group, {}), warmup=True)
        result.append(
            {
                **{field: group[0].get(field) for field in fields},
                **_cohort(group[0]),
                "status": "failed"
                if any(sample.get("status") == "failed" for sample in group)
                else "passed"
                if passed
                else "unverified",
                "reason": reason,
                "sample_count": len(group),
            }
        )
    return result


def audit(
    directories: Sequence[Path],
    reuse_snapshot: Path | None = None,
    acceptance_evidence: Path | None = None,
) -> dict[str, object]:
    from devtools.analysis_r9_cost import FIXED_APPLICABLE, PHYSICAL_COST_PROFILES, SOURCE_ROUTES

    rows = [
        freeze.obj(row)
        for row in freeze.arr(freeze.load(freeze.OUTPUT)["requirements"])
        if freeze.obj(row)["gap_owner"] == "R9.6"
    ]
    candidates: list[Mapping[str, object]] = []
    samples: list[Mapping[str, object]] = []
    raw_samples: list[Mapping[str, object]] = []
    probes: list[Mapping[str, object]] = []
    efficiency = False
    remaining = False
    attachments: list[dict[str, object]] = []
    references: dict[int, dict[str, object]] = {}
    for directory in directories:
        manifest = load(directory / "manifest.json")
        if manifest.get("schema") != "marivo.r96.cost-manifest.v1":
            raise ValueError("Unexpected measurement manifest")
        efficiency = _efficiency_manifest(manifest) or efficiency
        remaining = manifest.get("acceptance_schedule") == REMAINING_1K_SCHEDULE or remaining
        if obj(manifest["required_source_routes"]) != {
            key: list(value) for key, value in SOURCE_ROUTES.items()
        }:
            raise ValueError("Manifest differs from the recipe's required execution schedule")
        if obj(manifest["fixed_applicable"]) != FIXED_APPLICABLE:
            raise ValueError("Manifest differs from the current fixed-route applicability contract")
        candidates.append(obj(manifest["candidate"]))
        for path in sorted((directory / "samples").glob("*.json")):
            sample = load(path)
            if obj(sample["candidate"]) != obj(manifest["candidate"]):
                raise ValueError("Raw sample candidate differs from its original phase manifest")
            if sample.get("schema") == "marivo.r96.functional-probe.v1":
                if (
                    not _efficiency_manifest(manifest)
                    or manifest.get("acceptance_schedule") == REMAINING_1K_SCHEDULE
                ):
                    raise ValueError("Functional probes require their declared efficiency manifest")
                probes.append(sample)
            else:
                if _efficiency_manifest(manifest) and (
                    sample.get("facts")
                    != (
                        1000
                        if manifest.get("acceptance_schedule") == REMAINING_1K_SCHEDULE
                        else 100000
                    )
                    or sample.get("measurement_kind") != "cost"
                    or sample.get("acceptance_schedule") != manifest.get("acceptance_schedule")
                ):
                    raise ValueError(
                        "Efficiency formal samples must preserve their declared fact scale"
                    )
                samples.append(sample)
            reference = {
                "path": str(path.resolve()),
                "sha256": freeze.digest(path.read_bytes()),
                "status": sample.get("status"),
                "execution_candidate": obj(sample["candidate"]).get("content_sha256"),
            }
            references[id(sample)] = reference
            attachments.append(reference)
    if not candidates:
        raise ValueError("At least one measurement candidate is required")
    binding = candidates[-1]
    raw_samples = samples
    acceptance: Mapping[str, object] = {}
    accepted: list[Mapping[str, object]] | None = None
    boundary_status: dict[str, str] = dict.fromkeys(BOUNDARY_CASES, "unverified")
    acceptance_attachments: list[dict[str, object]] = []
    if acceptance_evidence is not None:
        acceptance = load(acceptance_evidence)
        if (
            acceptance.get("schema") != "marivo.r96.acceptance-evidence.v1"
            or obj(acceptance["candidate"]) != binding
        ):
            raise ValueError("Acceptance evidence must bind the final measurement candidate")
        if "accepted_cohorts" in acceptance:
            accepted = [obj(value) for value in arr(acceptance["accepted_cohorts"])]
            for cohort in accepted:
                if set(cohort) != {*GROUP_FIELDS, *COHORT_FIELDS}:
                    raise ValueError(
                        "An accepted cohort needs the complete group, candidate, layout and deployment identity"
                    )
            if len({json.dumps(row, sort_keys=True) for row in accepted}) != len(accepted):
                raise ValueError("Accepted cohorts must be unique")
        acceptance_attachments.append(
            {
                "path": str(acceptance_evidence.resolve()),
                "sha256": freeze.digest(acceptance_evidence.read_bytes()),
            }
        )
    reuse_proof: dict[str, object] | None = None
    historical: list[Mapping[str, object]] = []
    if any(candidate != binding for candidate in candidates):
        if reuse_snapshot is None:
            raise ValueError("One unchanged candidate is required across measurement phases")
        from scripts.r96_cost_reuse import (
            REMAINING_1K_ORIGINAL_SHA256,
            eligible_direct_sample,
            eligible_optimized_sample,
            eligible_physical_binding_sample,
            eligible_pre_direct_sample,
            eligible_pre_pressure_sample,
            eligible_sample,
            validate_current_reuse,
            validate_reuse,
        )

        authorities: list[Mapping[str, object]] = []
        for candidate in candidates:
            if candidate not in authorities:
                authorities.append(candidate)
        if remaining and len(authorities) == 9:
            intermediate = obj(
                load(reuse_snapshot / "physical-binding-authority-09" / "manifest.json")[
                    "candidate"
                ]
            )
            if intermediate.get("content_sha256") != REMAINING_1K_ORIGINAL_SHA256:
                raise ValueError(
                    "The remaining 1k schedule requires its actual repair-only C9 authority"
                )
            authorities.insert(-1, intermediate)
        if len(authorities) not in (2, 3, 4, 5, 6, 7, 8, 9, 10) or authorities[-1] != binding:
            raise ValueError("Only the explicitly verified ordered recipe candidates may combine")
        previous = authorities[0]
        pre_pressure = authorities[1] if len(authorities) >= 3 else None
        reuse_proof = (
            validate_current_reuse(authorities, reuse_snapshot)
            if len(authorities) >= 4
            else validate_reuse(
                previous, binding, reuse_snapshot, pre_pressure_candidate=pre_pressure
            )
        )

        def retained(sample: Mapping[str, object]) -> bool:
            authority = obj(sample["candidate"])
            if authority == binding:
                return True
            if len(authorities) >= 4:
                if authority == previous:
                    return eligible_sample(sample) and eligible_pre_direct_sample(sample)
                if authority == pre_pressure:
                    return eligible_pre_pressure_sample(sample) and eligible_pre_direct_sample(
                        sample
                    )
                if authority == authorities[2]:
                    return eligible_pre_direct_sample(sample)
                if authority == authorities[3]:
                    return eligible_direct_sample(sample)
                if len(authorities) >= 9 and authority == authorities[7]:
                    return eligible_optimized_sample(sample) or eligible_physical_binding_sample(
                        sample
                    )
                return authority in authorities[4:-1] and eligible_optimized_sample(sample)
            if authority == previous:
                return eligible_sample(sample)
            return authority == pre_pressure and eligible_pre_pressure_sample(sample)

        historical = [sample for sample in samples if not retained(sample)]
        samples = [sample for sample in samples if retained(sample)]
    probes = [
        probe
        for probe in probes
        if probe.get("candidate") == binding
        or _retained_physical_candidate(probe, binding, reuse_proof)
    ]
    eligible = samples
    samples = _accepted_samples(eligible, accepted)
    if acceptance_evidence is not None:
        boundary_status, boundary_attachments = _boundary_runs(
            acceptance, acceptance_evidence.parent, binding, reuse_proof
        )
        acceptance_attachments.extend(boundary_attachments)
    if accepted is not None:
        actual = {json.dumps(_group_identity(sample), sort_keys=True) for sample in eligible}
        if any(json.dumps(row, sort_keys=True) not in actual for row in accepted):
            raise ValueError("An accepted cohort has no eligible original raw samples")
    results: dict[str, dict[str, object]] = {}
    for row in rows:
        family, backend, route = row["family"], row["backend"], row["route"]
        if family not in ("cost-baseline", "cost-scenario"):
            continue
        scenario = str(row.get("scenario", "baseline"))
        representative = SCENARIO_COVERAGE.get(scenario, scenario) if efficiency else scenario
        selected = (
            [
                sample
                for sample in samples
                if (
                    sample.get("scenario") == "baseline"
                    and sample.get("cost_scope", "ordinary") == "ordinary"
                    and sample.get("requested_route") == route
                    and (backend == "none" or sample.get("backend") == backend)
                )
            ]
            if family == "cost-baseline"
            else [sample for sample in samples if sample.get("scenario") == representative]
        )
        errors: list[str] = []
        if not selected:
            results[str(row["id"])] = {"status": "unverified", "reason": "No raw cost sample"}
            continue
        selected_owners = {
            (str(sample.get("backend")), str(sample.get("profile"))) for sample in selected
        }
        for size in (
            ((1000,) if remaining else (100000,))
            if efficiency and family == "cost-scenario"
            else (1000, 100000)
        ):
            sized = [sample for sample in selected if sample.get("facts") == size]
            if not sized:
                errors.append(f"Missing {size}-fact measurements")
                continue
            if route == "artifact_python":
                for owner in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"):
                    for mode in ("kernel", "exact_hit"):
                        group = [
                            sample
                            for sample in sized
                            if sample.get("backend") == owner
                            and sample.get("fixed_mode") == mode
                            and sample.get("recovery") != "cold"
                        ]
                        passed, reason = _repeated_cohorts(group, binding, warmup=True)
                        if not passed:
                            errors.append(f"{owner}/{size}/{mode}: {reason}")
            else:
                owners = selected_owners
                for owner, profile in owners:
                    if family == "cost-baseline":
                        expected_profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(
                            str(owner), "table"
                        )
                        if profile != expected_profile:
                            errors.append(
                                f"{owner}/{profile}: not the mandatory ordinary-table shape"
                            )
                    expected_routes = (
                        (str(route),)
                        if family == "cost-baseline"
                        else (
                            *SOURCE_ROUTES[representative],
                            *(("artifact_python",) if FIXED_APPLICABLE[representative] else ()),
                        )
                    )
                    for expected_route in expected_routes:
                        modes = (
                            ("kernel", "exact_hit")
                            if expected_route == "artifact_python"
                            else (None,)
                        )
                        for expected_mode in modes:
                            group = [
                                sample
                                for sample in sized
                                if sample.get("backend") == owner
                                and sample.get("profile") == profile
                                and sample.get("requested_route") == expected_route
                                and sample.get("fixed_mode") == expected_mode
                                and sample.get("recovery") != "cold"
                            ]
                            passed, reason = _repeated_cohorts(group, binding, warmup=True)
                            if not passed:
                                errors.append(
                                    f"{owner}/{profile}/{size}/{expected_route}/{expected_mode}: {reason}"
                                )
                            if efficiency and not remaining and family == "cost-scenario":
                                functional = [
                                    probe
                                    for probe in probes
                                    if probe.get("backend") == owner
                                    and probe.get("profile") == profile
                                    and probe.get("scenario") == representative
                                    and probe.get("requested_route") == expected_route
                                    and probe.get("fixed_mode") == expected_mode
                                    and probe.get("candidate") == binding
                                ]
                                functional_passed, functional_reason = _functional_group(
                                    functional, binding
                                )
                                if not functional_passed:
                                    errors.append(f"{owner}/1k functional: {functional_reason}")
        results[str(row["id"])] = {
            "status": "failed"
            if any(sample.get("status") == "failed" for sample in selected)
            else "unverified"
            if errors
            else "shared-covered"
            if efficiency and scenario in SCENARIO_COVERAGE
            else "passed",
            "errors": errors,
            "sample_count": len(selected),
        }
        if efficiency and family == "cost-scenario":
            results[str(row["id"])].update(
                {
                    "coverage_kind": "shared-cover"
                    if scenario in SCENARIO_COVERAGE
                    else "measured",
                    "representative": representative,
                    "formal_sizes": [1000] if remaining else [100000],
                    "waived_cost_scales": [100000] if remaining else [1000],
                    "functional_sizes": [] if remaining else [1000],
                    "timing_authority": "Representative original raw samples only; unmeasured scales and aliases have no timing",
                    "functionality_authority": "Actual formal samples retain complete keyed/state oracles; no extra functional probes"
                    if remaining
                    else "Independent single 1k functional probes",
                }
            )
    v17 = next(row for row in rows if row["family"] not in ("cost-baseline", "cost-scenario"))
    cold_errors: list[str] = []
    for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"):
        for size in (1000, 100000):
            for mode in ("kernel", "exact_hit"):
                group = [
                    sample
                    for sample in samples
                    if sample.get("backend") == backend
                    and sample.get("facts") == size
                    and sample.get("scenario") == "baseline"
                    and sample.get("recovery") == "cold"
                    and sample.get("fixed_mode") == mode
                ]
                passed, reason = _repeated_cohorts(group, binding, warmup=True)
                if not passed:
                    cold_errors.append(f"{backend}/{size}/cold/{mode}: {reason}")
    physical: list[dict[str, object]] = []
    physical_bindings: dict[str, tuple[str, str, dict[str, object]]] = {}
    if efficiency and acceptance_evidence is not None:
        physical_bindings, physical_binding_attachments = _physical_fixed_bindings(
            acceptance, acceptance_evidence.parent, binding, samples, references, reuse_proof
        )
        acceptance_attachments.extend(physical_binding_attachments)
    for profile in PHYSICAL_COST_PROFILES:
        for size in (1000, 100000):
            for physical_route, physical_mode in (
                ("ibis", None),
                ("ibis_python", None),
                ("artifact_python", "kernel"),
                ("artifact_python", "exact_hit"),
            ):
                group = [
                    sample
                    for sample in samples
                    if sample.get("cost_scope") == "physical"
                    and f"{sample.get('backend')}:{sample.get('profile')}" == profile
                    and sample.get("facts") == size
                    and sample.get("scenario") == "baseline"
                    and sample.get("requested_route") == physical_route
                    and sample.get("fixed_mode") == physical_mode
                    and sample.get("recovery") != "cold"
                ]
                fixed_reference: dict[str, object] | None = None
                fixed_owner_id: str | None = None
                if profile in HTTP_PROFILES:
                    status, reason = (
                        "contract-inapplicable",
                        "Owning preflight refuses HTTP Analysis; no legal same-origin fixed producer",
                    )
                    if any(sample.get("status") == "passed" for sample in group):
                        raise ValueError(
                            "HTTP contract-inapplicable modes cannot contain positive cost samples"
                        )
                elif remaining and size == 100000 and profile != "clickhouse:distributed":
                    status, reason = (
                        "scale-waived",
                        "Explicit user authorization waives remaining 100k costs and growth claims; no physical producer or timing at this scale is granted",
                    )
                elif efficiency and physical_route == "artifact_python":
                    fixed_owner_id = next(
                        row_id
                        for row_id, result in results.items()
                        if any(
                            row["id"] == row_id
                            and row["family"] == "cost-baseline"
                            and row["route"] == "artifact_python"
                            for row in rows
                        )
                    )
                    fixed_owner = results[fixed_owner_id]
                    producers = [
                        sample
                        for sample in samples
                        if sample.get("cost_scope") == "physical"
                        and f"{sample.get('backend')}:{sample.get('profile')}" == profile
                        and sample.get("facts")
                        == (1000 if remaining and profile != "clickhouse:distributed" else 100000)
                        and sample.get("scenario") == "baseline"
                        and sample.get("requested_route") == "ibis_python"
                    ]
                    producer_passed, producer_reason = _repeated_cohorts(
                        producers, binding, warmup=True
                    )
                    binding_status, binding_reason, fixed_reference = physical_bindings.get(
                        profile,
                        (
                            "unverified",
                            "Missing actual source-offline physical producer binding",
                            {},
                        ),
                    )
                    fixed_reference = fixed_reference or None
                    status, reason = (
                        (
                            "shared-covered",
                            "Ordinary fixed-shape cost owner plus actual source-offline physical producer proof; no profile-specific fixed timing was collected",
                        )
                        if fixed_owner["status"] == "passed"
                        and producer_passed
                        and binding_status == "passed"
                        else (
                            "failed" if binding_status == "failed" else "unverified",
                            "Ordinary fixed owner or actual physical producer binding is incomplete: "
                            + producer_reason
                            + "; "
                            + binding_reason,
                        )
                    )
                elif (
                    efficiency
                    and size == 1000
                    and (not remaining or profile == "clickhouse:distributed")
                ):
                    functional = [
                        probe
                        for probe in probes
                        if probe.get("cost_scope") == "physical"
                        and f"{probe.get('backend')}:{probe.get('profile')}" == profile
                        and probe.get("requested_route") == physical_route
                    ]
                    passed, reason = _functional_group(
                        functional, _group_candidate(functional, binding)
                    )
                    status = "scale-waived" if passed else "unverified"
                    if passed:
                        reason = (
                            "1k repeated cost is waived; the single functional execution passed"
                        )
                elif not _physical_key_available(profile, physical_route):
                    status, reason = (
                        "unverified",
                        "Missing exact original-total physical qualification key",
                    )
                else:
                    passed, reason = _repeated_cohorts(group, binding, warmup=True)
                    status = (
                        "failed"
                        if any(sample.get("status") == "failed" for sample in group)
                        else "passed"
                        if passed
                        else "unverified"
                    )
                physical.append(
                    {
                        "profile": profile,
                        "facts": size,
                        "route": physical_route,
                        "fixed_mode": physical_mode,
                        "status": status,
                        "reason": reason,
                        "sample_count": len(group),
                        "failed_attempts": sum(
                            sample.get("status") == "failed" for sample in group
                        ),
                        "fixed_binding": fixed_reference,
                        "fixed_cost_owner_id": fixed_owner_id,
                        "fixed_cost_samples": [
                            references[id(sample)]
                            for sample in samples
                            if fixed_owner_id is not None
                            and sample.get("scenario") == "baseline"
                            and sample.get("cost_scope", "ordinary") == "ordinary"
                            and sample.get("requested_route") == "artifact_python"
                            and sample.get("fixed_mode") == physical_mode
                            and sample.get("facts") == size
                            and sample.get("recovery") != "cold"
                        ],
                        "timing_authority": "Original ordinary fixed owner only; physical execution supplies offline binding, not new cost"
                        if fixed_reference is not None
                        else None,
                    }
                )
    physical_errors = [
        f"{row['profile']}: {row['facts']}/{row['route']}/{row['fixed_mode']}: {row['reason']}"
        for row in physical
        if row["status"] not in (*COMPLETE_STATUSES, "contract-inapplicable")
    ]
    resource_errors = _cold_resources(samples)
    complete = (
        all(row["status"] in COMPLETE_STATUSES for row in results.values())
        and not cold_errors
        and not physical_errors
        and not resource_errors
        and all(status == "passed" for status in boundary_status.values())
    )
    results[str(v17["id"])] = {
        "status": "passed"
        if complete
        else "failed"
        if any(row["status"] == "failed" for row in (*physical, *results.values()))
        else "unverified",
        "reason": "All exact cost groups, cold/resource ownership and independently executed boundaries are bound"
        if complete
        else "Combined cost, cold/resource and independent boundary evidence is incomplete",
        "cold_errors": cold_errors,
        "physical_profile_errors": physical_errors,
        "physical_applicability": physical,
        "boundary_status": boundary_status,
        "resource_errors": resource_errors,
        "acceptance_scope": "Actual ordinary 1k/100k and retained Distributed 100k costs; remaining physical/shared 1k costs only, with explicit unmeasured 100k/growth waiver"
        if remaining
        else "Declared original cost scales",
        "remaining_100k_growth_authority": "user-authorized-scale-waiver; no measurement or growth success granted"
        if remaining
        else None,
    }
    return {
        "schema": "marivo.r96.cost-results.v2",
        "candidate": binding,
        "execution_candidates": candidates,
        "reuse_proof": reuse_proof,
        "results": results,
        "counts": {
            status: sum(row["status"] == status for row in results.values())
            for status in (
                ("passed", "failed", "unverified", "shared-covered", "scale-waived")
                if efficiency
                else ("passed", "failed", "unverified")
            )
        },
        "acceptance_schedule": REMAINING_1K_SCHEDULE
        if remaining
        else EFFICIENCY_SCHEDULE
        if efficiency
        else None,
        "accepted_requirement_ids": sum(
            row["status"] in COMPLETE_STATUSES for row in results.values()
        ),
        "functional_probes": [references[id(probe)] for probe in probes],
        "functional_probe_counts": {
            status: sum(probe.get("status") == status for probe in probes)
            for status in ("passed", "failed")
        },
        "raw_samples": len(raw_samples),
        "raw_counts": {
            status: sum(sample.get("status") == status for sample in raw_samples)
            for status in ("passed", "failed")
        },
        "historical_samples": [references[id(sample)] for sample in historical],
        "unaccepted_cohort_samples": [
            references[id(sample)]
            for sample in eligible
            if id(sample) not in {id(row) for row in samples}
        ],
        "accepted_cohorts": accepted,
        "effective_samples": len(samples),
        "cohort_results": _cohort_results(eligible),
        "attachments": attachments,
        "acceptance_attachments": acceptance_attachments,
        "boundary": "Cost samples only; original method/resource/recovery qualification remains separately owned",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directories", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-snapshot", type=Path)
    parser.add_argument("--acceptance-evidence", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = audit(args.directories, args.reuse_snapshot, args.acceptance_evidence)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(report["counts"], sort_keys=True))
    counts = obj(report["counts"])
    return int(
        counts["failed"] != 0
        or (args.require_complete and report["accepted_requirement_ids"] != 28)
    )


if __name__ == "__main__":
    raise SystemExit(main())
