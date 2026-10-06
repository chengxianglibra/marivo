"""Measure public R9 graph workloads without changing execution admission.

Run with the repository interpreter. Services must already be ready and opted
in through the existing MARIVO_*_ANALYSIS_TEST flags. Setup and independent
oracle reads are outside the measured public execution boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from devtools.r96_cost_fixture_layout import FixtureLayout, fixture_layout  # noqa: E402
from scripts import r9_qualification_requirements as freeze  # noqa: E402

if TYPE_CHECKING:
    from devtools.r96_cost_scenarios import Result, Workload

Json: TypeAlias = freeze.Json
BACKENDS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
SCENARIOS = (
    "multi-root-ratio-empty-groups",
    "exact-distinct-quantile",
    "comparison-members-observe",
    "joint-topk-attribution",
    "event-lifecycle-anchor",
    "deviation-runs",
    "association-lags",
    "forecast-models",
    "skew-empty-groups",
    "ties-null",
    "high-cardinality",
    "cross-batch-long-runs-unavailable",
    "many-occurrences-anchors-lags",
    "full-training-numeric-extremes",
)
SOURCE_ROUTES: dict[str, tuple[str, ...]] = {
    scenario: ("ibis",)
    if scenario
    in (
        "exact-distinct-quantile",
        "comparison-members-observe",
        "multi-root-ratio-empty-groups",
        "skew-empty-groups",
    )
    else ("ibis_python",)
    for scenario in SCENARIOS
}
SOURCE_ROUTES["baseline"] = ("ibis", "ibis_python")
FIXED_APPLICABLE = {
    scenario: scenario
    not in ("exact-distinct-quantile", "event-lifecycle-anchor", "many-occurrences-anchors-lags")
    for scenario in (*SCENARIOS, "baseline")
}
PHYSICAL_COST_PROFILES = (
    "duckdb:csv",
    "duckdb:parquet",
    "duckdb:local-json",
    "duckdb:http-json-public",
    "duckdb:http-json-auth",
    "trino:non-iceberg",
    "clickhouse:distributed",
)
EFFICIENCY_SCHEDULE = "r96-efficiency-42-v1"
REMAINING_1K_SCHEDULE = "r96-remaining-1k-v1"
EFFICIENCY_SCENARIO_COVERAGE = {
    "event-lifecycle-anchor": "many-occurrences-anchors-lags",
    "forecast-models": "full-training-numeric-extremes",
}
EFFICIENCY_SCENARIOS = tuple(
    scenario for scenario in SCENARIOS if scenario not in EFFICIENCY_SCENARIO_COVERAGE
)
EFFICIENCY_PHYSICAL_PROFILES = tuple(
    profile for profile in PHYSICAL_COST_PROFILES if ":http-json-" not in profile
)
REMAINING_1K_PHYSICAL_PROFILES = tuple(
    profile for profile in EFFICIENCY_PHYSICAL_PROFILES if profile != "clickhouse:distributed"
)
MeasurementKind: TypeAlias = Literal["cost", "functional"]


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candidate() -> dict[str, object]:
    paths = sorted(
        path
        for directory in ("marivo", "devtools", "scripts", "tests")
        for path in (ROOT / directory).rglob("*.py")
    )
    hashes = {path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in paths}
    dependencies = {
        name: importlib.metadata.version(name)
        for name in ("ibis-framework", "duckdb", "pyarrow", "pandas", "numpy", "pytest")
    }
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "source_sha256": hashes,
        "content_sha256": digest(encode(hashes)),
        "dependencies": dependencies,
    }


def requirements() -> list[dict[str, Json]]:
    frozen = freeze.load(freeze.OUTPUT)
    return [
        freeze.obj(value)
        for value in freeze.arr(frozen["requirements"])
        if freeze.obj(value)["gap_owner"] == "R9.6"
    ]


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encode(value))


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a list")
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected a string-keyed object")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a numeric observation")
    return float(value)


def summarize(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    elapsed = [
        _number(sample["elapsed_seconds"])
        for sample in samples
        if sample.get("status") == "passed" and sample.get("temperature") != "warmup"
    ]
    return {
        "passed": sum(sample.get("status") == "passed" for sample in samples),
        "failed": sum(sample.get("status") == "failed" for sample in samples),
        "measured_samples": len(elapsed),
        "elapsed_seconds": {
            "minimum": min(elapsed),
            "median": statistics.median(elapsed),
            "maximum": max(elapsed),
        }
        if elapsed
        else None,
    }


def grouped_summary(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    groups: dict[str, list[Mapping[str, object]]] = {}
    functional: list[Mapping[str, object]] = []
    for sample in samples:
        if sample.get("measurement_kind") == "functional":
            functional.append(sample)
            continue
        key = "/".join(
            str(sample.get(field, "source"))
            for field in (
                "backend",
                "profile",
                "facts",
                "scenario",
                "requested_route",
                "recovery",
                "fixed_mode",
            )
        )
        groups.setdefault(key, []).append(sample)
    result: dict[str, object] = {
        "totals": summarize([sample for group in groups.values() for sample in group]),
        "groups": {key: summarize(group) for key, group in sorted(groups.items())},
    }
    if functional:
        result["functional_probes"] = {
            "passed": sum(sample.get("status") == "passed" for sample in functional),
            "failed": sum(sample.get("status") == "failed" for sample in functional),
            "records": len(functional),
        }
    return result


def _physical_snapshot(result: Result) -> dict[str, object]:
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR
    from marivo.analysis.materialization.graph_protocol import encode as graph_encode

    if result._dataset is None:
        raise ValueError("A physical binding requires a receipt-owned producer")
    checked = result._dataset.verified()
    if checked.primary is None:
        raise ValueError("A physical binding requires its complete retained primary")
    descriptor = result._dataset.artifact.descriptor
    parts = {part.role: part for part in checked.parts}
    return {
        "artifact_ref": result.state.artifact_ref.ref,
        "carrier": type(result).__name__,
        "descriptor": dict(_mapping(json.loads(graph_encode(descriptor, DESCRIPTOR)))),
        "schema": [[field.name, str(field.type)] for field in checked.primary.schema],
        "schema_sha256": digest(checked.primary.schema.serialize().to_pybytes()),
        "primary_rows": checked.primary.num_rows,
        "primary_receipt": asdict(descriptor.primary_receipt),
        "parts": [
            {
                "role": receipt.role,
                "schema": [
                    [field.name, str(field.type)] for field in parts[receipt.role].table.schema
                ],
                "schema_sha256": digest(parts[receipt.role].table.schema.serialize().to_pybytes()),
                "rows": parts[receipt.role].table.num_rows,
                "receipt": asdict(receipt),
            }
            for receipt in descriptor.parts
        ],
    }


def _physical_fixed_binding(
    directory: Path,
    work: Workload,
    producer: Result,
    sample: Mapping[str, object],
    sample_path: Path,
    binding: Mapping[str, object],
) -> dict[str, object]:
    import marivo.analysis as mv
    from devtools.r96_cost_observer import CostObserver
    from marivo.datasource.adapters import SourceSession
    from marivo.semantic.reader import SemanticProject

    backend, profile = str(sample["backend"]), str(sample["profile"])
    path = directory / "physical-fixed-bindings" / f"{backend}-{profile}.json"
    record: dict[str, object] = {
        "schema": "marivo.r96.physical-fixed-binding.v1",
        "candidate": binding,
        "acceptance_schedule": sample["acceptance_schedule"],
        "backend": backend,
        "profile": profile,
        "facts": sample["facts"],
        "scenario": sample["scenario"],
        "temperature": sample["temperature"],
        "iteration": sample["iteration"],
        "cost_measured": False,
        "producer_sample_path": sample_path.relative_to(directory).as_posix(),
        "producer_identity": sample["identity"],
        "producer_oracle": sample["oracle"],
        "source_semantic_forbidden": True,
    }
    observer = CostObserver(backend)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Physical fixed binding must not open sources or current Semantic")

    try:
        with pytest.MonkeyPatch.context() as offline:
            offline.setattr(SourceSession, "__enter__", forbidden)
            offline.setattr(SemanticProject, "load", forbidden)
            with observer:
                original = _physical_snapshot(producer)
                restored = work.session.artifact(str(_mapping(sample["identity"])["artifact_ref"]))
                if not isinstance(
                    restored,
                    (
                        mv.MaterializedNumericRelation,
                        mv.MaterializedRolledNumericRelation,
                        mv.MaterializedGroupedNumericRelation,
                    ),
                ):
                    raise ValueError("Physical fixed binding requires the original numeric carrier")
                recovered = _physical_snapshot(restored)
                if recovered != original:
                    raise ValueError("Restored physical carrier, schemas, receipts or parts differ")
                record["producer_snapshot"] = original
                record["recovered_snapshot"] = recovered
                retained = restored.summarize(mv.sum()).execute()
                record["fixed"] = {
                    "identity": work.identity(retained),
                    "oracle": work.validate(retained),
                }
            observations = observer.snapshot()
            record["observations"] = observations
            fixed = dict(_mapping(record["fixed"]))
            fixed["observations"] = observations
            record["fixed"] = fixed
            if (
                observations["source_submissions"] != []
                or observations["source_sessions"] != []
                or observations["resource_closed"] is not True
                or _mapping(observations["phase_calls"]).get("fixed_kernel", 0) == 0
                or not observations["storage_writes"]
                or work.session._runtime.store.resources(work.session.id) != ()
            ):
                raise ValueError(
                    "Physical binding must publish once with all source and reader owners closed"
                )
        record["status"] = "passed"
    except Exception as error:
        record["status"] = "failed"
        record["error_type"] = type(error).__name__
        record["traceback"] = traceback.format_exc()
        record["observations"] = observer.snapshot()
    write_new(path, record)
    return {"path": path.relative_to(directory).as_posix(), "sha256": digest(encode(record))}


def run_group(
    directory: Path,
    *,
    backend: str,
    profile: str,
    facts: int,
    scenario: str,
    repeats: int,
    binding: Mapping[str, object],
    cost_scope: str = "ordinary",
    source_routes: Sequence[str] | None = None,
    skip_baseline_cold: bool = False,
    layout: FixtureLayout = "unindexed",
    deployment: Mapping[str, object] | None = None,
    measurement_kind: MeasurementKind = "cost",
    acceptance_schedule: str | None = None,
    include_fixed: bool = True,
) -> list[dict[str, object]]:
    from devtools.r96_cost_cold import cold_sample
    from devtools.r96_cost_observer import CostObserver
    from devtools.r96_cost_scenarios import workload
    from scripts import r96_cost_results

    if measurement_kind == "functional" and (
        facts != 1000 or repeats != 0 or acceptance_schedule != EFFICIENCY_SCHEDULE
    ):
        raise ValueError("Efficiency functional probes require one 1k invocation")
    valid = (
        r96_cost_results.valid_functional
        if measurement_kind == "functional"
        else r96_cost_results.valid
    )

    samples: list[dict[str, object]] = []
    group = f"{backend}-{profile}-{facts}-{scenario}"
    with tempfile.TemporaryDirectory(prefix="marivo-r96-") as temporary:
        root = Path(temporary)
        with pytest.MonkeyPatch.context() as patch:
            patch.chdir(root)
            patch.setenv("MARIVO_PROJECT_ROOT", str(root))
            try:
                with (
                    fixture_layout(layout),
                    workload(backend, profile, facts, scenario, root, patch) as work,
                ):
                    if deployment is not None:
                        work.environment["deployment_evidence"] = dict(deployment)
                        work.environment["deployment_cohort"] = deployment["deployment_cohort"]
                    if tuple(work.routes) != SOURCE_ROUTES[scenario]:
                        raise ValueError(
                            "Workload routes differ from the current cost execution schedule"
                        )
                    for route in work.routes:
                        if source_routes is not None and route not in source_routes:
                            continue
                        for index in range(repeats + 1):
                            record: dict[str, object] = {
                                "schema": "marivo.r96.functional-probe.v1"
                                if measurement_kind == "functional"
                                else "marivo.r96.cost-sample.v1",
                                "candidate": binding,
                                "backend": backend,
                                "profile": profile,
                                "facts": facts,
                                "scenario": scenario,
                                "cost_scope": cost_scope,
                                "requested_route": route,
                                "iteration": index,
                                "temperature": "functional"
                                if measurement_kind == "functional"
                                else "warmup"
                                if index == 0
                                else "measured",
                                "setup_measured": False,
                            }
                            if acceptance_schedule is not None:
                                record["acceptance_schedule"] = acceptance_schedule
                                record["measurement_kind"] = measurement_kind
                            if layout != "unindexed" or deployment is not None:
                                record["environment"] = work.environment
                            began = time.monotonic()
                            observer = None
                            try:
                                with CostObserver(backend) as observer:
                                    result = work.source(route)
                                record["elapsed_seconds"] = time.monotonic() - began
                                record["observations"] = observer.snapshot()
                                record["oracle"] = work.validate(result)
                                record["identity"] = work.identity(result)
                                record["environment"] = work.environment
                                record["status"] = "passed"
                                valid(record, binding)
                            except Exception as error:
                                record["elapsed_seconds"] = time.monotonic() - began
                                record["status"] = "failed"
                                record["error_type"] = type(error).__name__
                                record["traceback"] = traceback.format_exc()
                                if observer is not None:
                                    record["observations"] = observer.snapshot()
                            sample_path = directory / "samples" / f"{group}-{route}-{index}.json"
                            if (
                                record["status"] == "passed"
                                and acceptance_schedule
                                in (EFFICIENCY_SCHEDULE, REMAINING_1K_SCHEDULE)
                                and cost_scope == "physical"
                                and measurement_kind == "cost"
                                and facts
                                == (
                                    1000 if acceptance_schedule == REMAINING_1K_SCHEDULE else 100000
                                )
                                and scenario == "baseline"
                                and route == "ibis_python"
                                and index == 0
                            ):
                                try:
                                    reference = _physical_fixed_binding(
                                        directory, work, result, record, sample_path, binding
                                    )
                                    record["physical_fixed_binding"] = reference
                                    proof = _mapping(
                                        json.loads(
                                            (directory / str(reference["path"])).read_bytes()
                                        )
                                    )
                                    if proof["status"] != "passed":
                                        raise ValueError(
                                            "The actual physical producer failed its offline binding"
                                        )
                                except Exception as error:
                                    record["source_status"] = "passed"
                                    record["status"] = "failed"
                                    record["stage"] = "physical_fixed_binding"
                                    record["error_type"] = type(error).__name__
                                    record["traceback"] = traceback.format_exc()
                            write_new(sample_path, record)
                            samples.append(record)
                            print(f"{group} {route} {index}: {record['status']}", flush=True)
                            if record["status"] != "passed":
                                break
                            if scenario == "baseline" and route == "ibis":
                                if skip_baseline_cold:
                                    continue
                                oracle = _mapping(record["oracle"])
                                expected = oracle["expected"]
                                assert isinstance(expected, int)
                                identity = _mapping(record["identity"])
                                for mode in ("kernel", "exact_hit"):
                                    cold = {
                                        **record,
                                        **cold_sample(
                                            root,
                                            "r96-cost",
                                            str(identity["artifact_ref"]),
                                            backend,
                                            expected,
                                            mode,
                                        ),
                                    }
                                    if cold["status"] == "passed":
                                        valid(cold, binding)
                                    write_new(
                                        directory / "samples" / f"{group}-cold-{index}-{mode}.json",
                                        cold,
                                    )
                                    samples.append(cold)
                            if scenario == "baseline" and route != "ibis_python":
                                continue
                            if not include_fixed or not FIXED_APPLICABLE[scenario]:
                                continue
                            capture_began = time.monotonic()
                            with CostObserver(backend) as capture_observer:
                                fixed = work.fixed(result)
                            capture_seconds = time.monotonic() - capture_began
                            capture_observations = capture_observer.snapshot()
                            for mode in ("kernel", "exact_hit"):
                                fixed_record = {
                                    **record,
                                    "requested_route": "artifact_python",
                                    "fixed_mode": mode,
                                    "producer_identity": record["identity"],
                                    "producer_route": route,
                                    "capture_elapsed_seconds": capture_seconds,
                                    "capture_observations": capture_observations,
                                }
                                began = time.monotonic()
                                observer = None
                                try:
                                    with CostObserver(backend) as observer:
                                        retained = fixed.execute()
                                    fixed_record["elapsed_seconds"] = time.monotonic() - began
                                    fixed_record["observations"] = observer.snapshot()
                                    fixed_record["oracle"] = work.validate(retained)
                                    fixed_record["identity"] = work.identity(retained)
                                    fixed_record["status"] = "passed"
                                    valid(fixed_record, binding)
                                except Exception as error:
                                    fixed_record["elapsed_seconds"] = time.monotonic() - began
                                    fixed_record["status"] = "failed"
                                    fixed_record["error_type"] = type(error).__name__
                                    fixed_record["traceback"] = traceback.format_exc()
                                    if observer is not None:
                                        fixed_record["observations"] = observer.snapshot()
                                write_new(
                                    directory
                                    / "samples"
                                    / f"{group}-{route}-fixed-{index}-{mode}.json",
                                    fixed_record,
                                )
                                samples.append(fixed_record)
            except (Exception, pytest.skip.Exception) as error:
                record = {
                    "schema": "marivo.r96.functional-probe.v1"
                    if measurement_kind == "functional"
                    else "marivo.r96.cost-sample.v1",
                    "candidate": binding,
                    "backend": backend,
                    "profile": profile,
                    "facts": facts,
                    "scenario": scenario,
                    "cost_scope": cost_scope,
                    "status": "failed",
                    "stage": "fixture_setup",
                    "error_type": type(error).__name__,
                    "traceback": traceback.format_exc(),
                }
                if acceptance_schedule is not None:
                    record["acceptance_schedule"] = acceptance_schedule
                    record["measurement_kind"] = measurement_kind
                write_new(directory / "samples" / f"{group}-setup-failed.json", record)
                samples.append(record)
    return samples


def collect(args: argparse.Namespace) -> int:
    if args.repeats < 3:
        raise ValueError("R9.6 requires at least three measured samples after one warmup")
    schedule = args.acceptance_schedule
    remaining_1k = schedule == REMAINING_1K_SCHEDULE
    efficient = schedule in (EFFICIENCY_SCHEDULE, REMAINING_1K_SCHEDULE)
    if efficient:
        formal_size = 1000 if remaining_1k else 100000
        if args.sizes != [formal_size] or args.repeats != 3 or args.source_routes is not None:
            raise ValueError(
                f"This efficiency schedule requires {formal_size} facts, one warmup plus three repeats, and all source routes"
            )
        if args.physical_profiles:
            physical_profiles = (
                REMAINING_1K_PHYSICAL_PROFILES if remaining_1k else EFFICIENCY_PHYSICAL_PROFILES
            )
            if any(profile not in physical_profiles for profile in args.physical_profiles):
                raise ValueError(
                    "Efficiency physical costs are limited to the selected remaining source shapes"
                )
        elif len(args.backends) != 1:
            raise ValueError(
                "Efficiency shared scenarios require exactly one representative backend"
            )
        if args.scenarios != ["baseline"]:
            raise ValueError("Efficiency scenarios are selected by their closed coverage schedule")
    directory: Path = args.directory.resolve()
    binding = candidate()
    deployment = None
    if args.deployment_evidence is not None:
        data = args.deployment_evidence.read_bytes()
        configuration = dict(_mapping(json.loads(data)))
        cohort = configuration.get("deployment_cohort")
        if not isinstance(cohort, str) or not cohort:
            raise ValueError("Deployment evidence requires a nonempty deployment_cohort")
        deployment = {
            "path": str(args.deployment_evidence.resolve()),
            "sha256": digest(data),
            "configuration": configuration,
            "deployment_cohort": cohort,
            "usage_observation": False,
        }
    manifest: dict[str, object] = {
        "schema": "marivo.r96.cost-manifest.v1",
        "candidate": binding,
        "requirements": requirements(),
        "required_source_routes": SOURCE_ROUTES,
        "scheduled_source_routes": args.source_routes,
        "scheduled_baseline_cold": not (args.skip_baseline_cold or efficient),
        "fixture_layout": args.fixture_layout,
        "deployment_evidence": deployment,
        "fixed_applicable": FIXED_APPLICABLE,
        "required_physical_cost_profiles": PHYSICAL_COST_PROFILES,
        "fixed_inapplicable": {
            "exact-distinct-quantile": "The native distribution owning contract has no public fixed re-aggregation; tests/test_r93_distribution_consumers.py pins both logical and materialized rollup refusal",
            "event-lifecycle-anchor": "The final Anchor Metric observation requires LiveBinding; the owning public_dsl contract refuses fixed Anchor plus live Metric",
            "many-occurrences-anchors-lags": "The compound's final Anchor Metric observation requires LiveBinding; fixed Journey/History continuations answer different questions",
        },
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "measurement": {
            "warmups": 1,
            "repeats": args.repeats,
            "execution_order": "backend/profile/size/scenario/route/iteration",
            "concurrency": 1,
            "cold_definition": "first execution of a new fixture; not an OS cache flush",
            "warm_definition": "same immutable fixture and process, fresh source realization",
            "connection_reuse": "execution owner creates and closes its connection",
            "background_load": args.background_load,
            "wire_bytes": "unobserved; Arrow nbytes is decoded exchange data",
            "server_memory": "unobserved; service deployment is recorded by fixture",
            "deadline_seconds": 600,
        },
    }
    scenarios = (
        (("baseline",) if args.physical_profiles else EFFICIENCY_SCENARIOS)
        if efficient
        else tuple(args.scenarios)
    )
    if efficient:
        groups = 2 * len(args.physical_profiles) if args.physical_profiles else 32
        manifest.update(
            acceptance_schedule=schedule,
            formal_sizes=[1000] if remaining_1k else [100000],
            functional_sizes=[] if remaining_1k else [1000],
            scenario_coverage=EFFICIENCY_SCENARIO_COVERAGE,
            physical_fixed_cost_owner="ordinary-fixed-shape",
            scheduled_scenarios=list(scenarios),
            expected_cost_groups=groups,
            expected_functional_probes=0 if remaining_1k else groups,
        )
    write_new(directory / "manifest.json", manifest)
    samples: list[dict[str, object]] = []
    jobs = (
        [tuple(profile.split(":")) for profile in args.physical_profiles]
        if args.physical_profiles
        else [
            (backend, {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table"))
            for backend in args.backends
        ]
    )
    for backend, profile in jobs:
        for facts in [1000, 100000] if efficient and not remaining_1k else args.sizes:
            for scenario in scenarios:
                functional = efficient and not remaining_1k and facts == 1000
                samples.extend(
                    run_group(
                        directory,
                        backend=backend,
                        profile=profile,
                        facts=facts,
                        scenario=scenario,
                        repeats=0 if functional else args.repeats,
                        binding=binding,
                        cost_scope="physical" if args.physical_profiles else "ordinary",
                        source_routes=args.source_routes,
                        skip_baseline_cold=args.skip_baseline_cold or efficient,
                        layout=args.fixture_layout,
                        deployment=deployment,
                        measurement_kind="functional" if functional else "cost",
                        acceptance_schedule=schedule,
                        include_fixed=not (efficient and args.physical_profiles),
                    )
                )
    if candidate() != binding:
        raise ValueError("Candidate changed during measurement; retain samples without a grant")
    write_new(directory / "summary.json", grouped_summary(samples))
    return int(any(sample["status"] != "passed" for sample in samples))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--acceptance-schedule", choices=(EFFICIENCY_SCHEDULE, REMAINING_1K_SCHEDULE)
    )
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 100000])
    parser.add_argument("--physical-profiles", nargs="+", choices=PHYSICAL_COST_PROFILES)
    parser.add_argument("--source-routes", nargs="+", choices=("ibis", "ibis_python"))
    parser.add_argument("--skip-baseline-cold", action="store_true")
    parser.add_argument(
        "--fixture-layout", choices=("unindexed", "indexed-keys"), default="unindexed"
    )
    parser.add_argument("--deployment-evidence", type=Path)
    parser.add_argument(
        "--scenarios", nargs="+", choices=("baseline", *SCENARIOS), default=["baseline"]
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--background-load", default="not isolated; serial harness, other load unobserved"
    )
    return collect(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
