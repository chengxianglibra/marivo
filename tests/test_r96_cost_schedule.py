"""Pin opt-in cost schedules and non-constraining indexed fixture deployments."""

import argparse
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from devtools import analysis_r9_cost as collector
from devtools import r96_cost_cold, r96_cost_observer, r96_cost_scenarios
from devtools.r96_cost_fixture_layout import fixture_layout
from scripts import r96_cost_results


@pytest.fixture
def cost_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    class Fixed:
        def execute(self) -> object:
            calls.append("fixed")
            return object()

    class Work:
        environment: dict[str, object] = {}

        def __init__(self, scenario: str) -> None:
            self.routes = collector.SOURCE_ROUTES[scenario]

        def source(self, route: str) -> object:
            calls.append(route)
            return object()

        def fixed(self, result: object) -> Fixed:
            calls.append("capture")
            return Fixed()

        def validate(self, result: object) -> dict[str, object]:
            return {"expected": 1}

        def identity(self, result: object) -> dict[str, object]:
            return {"artifact_ref": "artifact"}

    class Observer:
        def __init__(self, backend: str) -> None:
            pass

        def __enter__(self) -> "Observer":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def snapshot(self) -> dict[str, object]:
            return {}

    @contextmanager
    def workload(
        backend: str,
        profile: str,
        facts: int,
        scenario: str,
        root: Path,
        patch: pytest.MonkeyPatch,
    ) -> Iterator[Work]:
        yield Work(scenario)

    def cold(*args: object) -> dict[str, object]:
        calls.append("cold")
        return {"status": "passed", "recovery": "cold"}

    def valid(*args: object) -> None:
        pass

    def physical_binding(
        directory: Path,
        work: r96_cost_scenarios.Workload,
        producer: r96_cost_scenarios.Result,
        sample: Mapping[str, object],
        sample_path: Path,
        binding: Mapping[str, object],
    ) -> dict[str, object]:
        path = Path("physical-fixed-bindings") / f"{sample['backend']}-{sample['profile']}.json"
        record = {"status": "passed"}
        collector.write_new(directory / path, record)
        return {"path": path.as_posix(), "sha256": collector.digest(collector.encode(record))}

    monkeypatch.setattr(r96_cost_scenarios, "workload", workload)
    monkeypatch.setattr(r96_cost_observer, "CostObserver", Observer)
    monkeypatch.setattr(r96_cost_cold, "cold_sample", cold)
    monkeypatch.setattr(r96_cost_results, "valid", valid)
    monkeypatch.setattr(r96_cost_results, "valid_functional", valid, raising=False)
    monkeypatch.setattr(collector, "_physical_fixed_binding", physical_binding)
    return calls


@pytest.mark.parametrize("skip", (False, True))
def test_baseline_cold_skip_changes_only_cold_schedule(
    tmp_path: Path, cost_calls: list[str], skip: bool
) -> None:
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="table",
        facts=1000,
        scenario="baseline",
        repeats=0,
        binding={},
        skip_baseline_cold=skip,
    )
    assert [call for call in cost_calls if call != "cold"] == [
        "ibis",
        "ibis_python",
        "capture",
        "fixed",
        "fixed",
    ]
    assert cost_calls.count("cold") == (0 if skip else 2)
    assert len(samples) == (4 if skip else 6)


@pytest.mark.parametrize("functional", (False, True))
def test_efficiency_physical_only_measures_original_source_routes(
    tmp_path: Path, cost_calls: list[str], functional: bool
) -> None:
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="csv",
        facts=1000 if functional else 100000,
        scenario="baseline",
        repeats=0 if functional else 3,
        binding={},
        cost_scope="physical",
        skip_baseline_cold=True,
        acceptance_schedule=collector.EFFICIENCY_SCHEDULE,
        measurement_kind="functional" if functional else "cost",
        include_fixed=False,
    )
    count = 1 if functional else 4
    assert cost_calls == ["ibis"] * count + ["ibis_python"] * count
    assert len(samples) == 2 * count
    assert {sample["requested_route"] for sample in samples} == {"ibis", "ibis_python"}
    assert {sample["measurement_kind"] for sample in samples} == {
        "functional" if functional else "cost"
    }
    assert {sample["schema"] for sample in samples} == {
        "marivo.r96.functional-probe.v1" if functional else "marivo.r96.cost-sample.v1"
    }
    assert [sample["temperature"] for sample in samples] == (
        ["functional"] * 2 if functional else ["warmup", "measured", "measured", "measured"] * 2
    )


@pytest.mark.parametrize("functional", (False, True))
def test_efficiency_compound_captures_once_per_fresh_source_round(
    tmp_path: Path, cost_calls: list[str], functional: bool
) -> None:
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="table",
        facts=1000 if functional else 100000,
        scenario="deviation-runs",
        repeats=0 if functional else 3,
        binding={},
        skip_baseline_cold=True,
        acceptance_schedule=collector.EFFICIENCY_SCHEDULE,
        measurement_kind="functional" if functional else "cost",
    )
    count = 1 if functional else 4
    assert cost_calls == ["ibis_python", "capture", "fixed", "fixed"] * count
    assert len(samples) == 3 * count
    assert {sample.get("fixed_mode") for sample in samples} == {None, "kernel", "exact_hit"}
    pairs = [sample for sample in samples if sample.get("fixed_mode") is not None]
    for index in range(count):
        assert (
            pairs[2 * index]["capture_elapsed_seconds"]
            == pairs[2 * index + 1]["capture_elapsed_seconds"]
        )
        assert (
            pairs[2 * index]["capture_observations"] is pairs[2 * index + 1]["capture_observations"]
        )


def test_functional_probe_never_enters_cost_summary() -> None:
    summary = collector.grouped_summary(
        [
            {
                "measurement_kind": "functional",
                "schema": "marivo.r96.functional-probe.v1",
                "temperature": "functional",
                "elapsed_seconds": 999,
                "status": "passed",
            },
            {"elapsed_seconds": 1, "temperature": "warmup", "status": "passed"},
            {"elapsed_seconds": 2, "temperature": "measured", "status": "passed"},
        ]
    )
    assert summary["totals"] == {
        "passed": 2,
        "failed": 0,
        "measured_samples": 1,
        "elapsed_seconds": {"minimum": 2, "median": 2, "maximum": 2},
    }
    assert summary["functional_probes"] == {"passed": 1, "failed": 0, "records": 1}


@pytest.mark.parametrize("physical", (False, True))
def test_efficiency_closed_schedule_has_42_cost_groups_and_single_1k_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, physical: bool
) -> None:
    calls: list[dict[str, object]] = []

    def run_group(directory: Path, **kwargs: object) -> list[dict[str, object]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(collector, "run_group", run_group)
    monkeypatch.setattr(collector, "candidate", lambda: {})
    monkeypatch.setattr(collector, "requirements", lambda: [])
    args = argparse.Namespace(
        directory=tmp_path,
        acceptance_schedule=collector.EFFICIENCY_SCHEDULE,
        repeats=3,
        sizes=[100000],
        backends=["duckdb"],
        physical_profiles=list(collector.EFFICIENCY_PHYSICAL_PROFILES) if physical else None,
        source_routes=None,
        scenarios=["baseline"],
        skip_baseline_cold=False,
        fixture_layout="unindexed",
        deployment_evidence=None,
        background_load="not isolated",
    )
    assert collector.collect(args) == 0
    expected = 10 if physical else 32
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["expected_cost_groups"] == manifest["expected_functional_probes"] == expected
    assert manifest["formal_sizes"] == [100000] and manifest["functional_sizes"] == [1000]
    assert manifest["scheduled_baseline_cold"] is False
    scenarios = ("baseline",) if physical else collector.EFFICIENCY_SCENARIOS
    assert manifest["scheduled_scenarios"] == list(scenarios)
    assert len(calls) == (10 if physical else 24)
    assert all(call["repeats"] == (0 if call["facts"] == 1000 else 3) for call in calls)
    assert all(call["include_fixed"] is not physical for call in calls)
    assert all(call["skip_baseline_cold"] is True for call in calls)


@pytest.fixture
def remaining_1k_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        directory=tmp_path,
        acceptance_schedule=collector.REMAINING_1K_SCHEDULE,
        repeats=3,
        sizes=[1000],
        backends=["duckdb"],
        physical_profiles=None,
        source_routes=None,
        scenarios=["baseline"],
        skip_baseline_cold=False,
        fixture_layout="unindexed",
        deployment_evidence=None,
        background_load="not isolated",
    )


@pytest.mark.parametrize("physical", (False, True))
def test_remaining_1k_schedule_measures_40_formal_groups_without_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remaining_1k_args: argparse.Namespace,
    physical: bool,
) -> None:
    calls: list[dict[str, object]] = []

    def run_group(directory: Path, **kwargs: object) -> list[dict[str, object]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(collector, "run_group", run_group)
    monkeypatch.setattr(collector, "candidate", lambda: {})
    monkeypatch.setattr(collector, "requirements", lambda: [])
    if physical:
        remaining_1k_args.physical_profiles = list(collector.REMAINING_1K_PHYSICAL_PROFILES)
    assert collector.collect(remaining_1k_args) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["acceptance_schedule"] == collector.REMAINING_1K_SCHEDULE
    assert manifest["expected_cost_groups"] == (8 if physical else 32)
    assert manifest["expected_functional_probes"] == 0
    assert manifest["formal_sizes"] == [1000] and manifest["functional_sizes"] == []
    assert manifest["scheduled_baseline_cold"] is False
    assert len(calls) == (4 if physical else 12)
    assert all(call["facts"] == 1000 and call["repeats"] == 3 for call in calls)
    assert all(call["measurement_kind"] == "cost" for call in calls)
    assert all(call["include_fixed"] is not physical for call in calls)
    assert all(call["skip_baseline_cold"] is True for call in calls)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert "functional_probes" not in summary
    assert collector.REMAINING_1K_PHYSICAL_PROFILES == (
        "duckdb:csv",
        "duckdb:parquet",
        "duckdb:local-json",
        "trino:non-iceberg",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("sizes", [100000]),
        ("repeats", 4),
        ("source_routes", ["ibis_python"]),
        ("scenarios", ["deviation-runs"]),
        ("backends", ["duckdb", "sqlite"]),
        ("physical_profiles", ["clickhouse:distributed"]),
    ),
)
def test_remaining_1k_schedule_rejects_extra_scale_or_already_completed_scope(
    tmp_path: Path, remaining_1k_args: argparse.Namespace, field: str, value: object
) -> None:
    setattr(remaining_1k_args, field, value)
    with pytest.raises(ValueError, match=r"efficiency|Efficiency"):
        collector.collect(remaining_1k_args)
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("physical", (False, True))
def test_remaining_1k_rounds_capture_once_and_bind_only_the_local_warmup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cost_calls: list[str], physical: bool
) -> None:
    binding_calls: list[Mapping[str, object]] = []
    original = collector._physical_fixed_binding

    def physical_binding(
        directory: Path,
        work: r96_cost_scenarios.Workload,
        producer: r96_cost_scenarios.Result,
        sample: Mapping[str, object],
        sample_path: Path,
        binding: Mapping[str, object],
    ) -> dict[str, object]:
        binding_calls.append(dict(sample))
        return original(directory, work, producer, sample, sample_path, binding)

    monkeypatch.setattr(collector, "_physical_fixed_binding", physical_binding)
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="csv" if physical else "table",
        facts=1000,
        scenario="baseline" if physical else "deviation-runs",
        repeats=3,
        binding={},
        cost_scope="physical" if physical else "ordinary",
        skip_baseline_cold=True,
        acceptance_schedule=collector.REMAINING_1K_SCHEDULE,
        include_fixed=not physical,
    )
    assert cost_calls == (
        ["ibis"] * 4 + ["ibis_python"] * 4
        if physical
        else ["ibis_python", "capture", "fixed", "fixed"] * 4
    )
    assert len(samples) == (8 if physical else 12)
    assert {sample["schema"] for sample in samples} == {"marivo.r96.cost-sample.v1"}
    assert {sample["measurement_kind"] for sample in samples} == {"cost"}
    assert {sample["temperature"] for sample in samples} == {"warmup", "measured"}
    assert len(binding_calls) == int(physical)
    assert sum("physical_fixed_binding" in sample for sample in samples) == int(physical)
    if physical:
        assert binding_calls[0]["facts"] == 1000
        assert binding_calls[0]["acceptance_schedule"] == collector.REMAINING_1K_SCHEDULE
        assert binding_calls[0]["iteration"] == 0
        assert binding_calls[0]["requested_route"] == "ibis_python"
    else:
        pairs = [sample for sample in samples if "fixed_mode" in sample]
        for index in range(4):
            assert (
                pairs[2 * index]["capture_observations"]
                is pairs[2 * index + 1]["capture_observations"]
            )


def test_remaining_1k_schedule_never_accepts_an_extra_functional_probe(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="functional probes"):
        collector.run_group(
            tmp_path,
            backend="duckdb",
            profile="table",
            facts=1000,
            scenario="deviation-runs",
            repeats=0,
            binding={},
            measurement_kind="functional",
            acceptance_schedule=collector.REMAINING_1K_SCHEDULE,
        )


def test_efficiency_scenario_aliases_keep_all_original_algorithm_classes() -> None:
    assert len(collector.EFFICIENCY_SCENARIOS) == 12
    assert sum(collector.FIXED_APPLICABLE[name] for name in collector.EFFICIENCY_SCENARIOS) == 10
    assert set(collector.EFFICIENCY_SCENARIOS) | set(collector.EFFICIENCY_SCENARIO_COVERAGE) == set(
        collector.SCENARIOS
    )
    assert collector.EFFICIENCY_SCENARIO_COVERAGE == {
        "event-lifecycle-anchor": "many-occurrences-anchors-lags",
        "forecast-models": "full-training-numeric-extremes",
    }
    assert set(collector.EFFICIENCY_SCENARIO_COVERAGE.values()) <= set(
        collector.EFFICIENCY_SCENARIOS
    )


@pytest.mark.parametrize("scope", ("physical", "functional", "ordinary", "legacy", "native"))
def test_physical_binding_hook_runs_once_only_after_the_100k_local_warmup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cost_calls: list[str], scope: str
) -> None:
    binding_calls: list[Mapping[str, object]] = []
    original = collector._physical_fixed_binding

    def physical_binding(
        directory: Path,
        work: r96_cost_scenarios.Workload,
        producer: r96_cost_scenarios.Result,
        sample: Mapping[str, object],
        sample_path: Path,
        binding: Mapping[str, object],
    ) -> dict[str, object]:
        binding_calls.append(dict(sample))
        return original(directory, work, producer, sample, sample_path, binding)

    monkeypatch.setattr(collector, "_physical_fixed_binding", physical_binding)
    functional = scope == "functional"
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="csv",
        facts=1000 if functional else 100000,
        scenario="baseline",
        repeats=0 if functional else 3,
        binding={},
        cost_scope="ordinary" if scope == "ordinary" else "physical",
        source_routes=["ibis"] if scope == "native" else None,
        skip_baseline_cold=True,
        acceptance_schedule=None if scope == "legacy" else collector.EFFICIENCY_SCHEDULE,
        measurement_kind="functional" if functional else "cost",
        include_fixed=False,
    )
    assert len(binding_calls) == int(scope == "physical")
    attached = [sample for sample in samples if "physical_fixed_binding" in sample]
    assert len(attached) == int(scope == "physical")
    assert all(sample["status"] == "passed" for sample in samples)
    if scope == "physical":
        assert binding_calls[0]["temperature"] == "warmup"
        assert binding_calls[0]["iteration"] == 0
        assert binding_calls[0]["requested_route"] == "ibis_python"
        assert cost_calls == ["ibis"] * 4 + ["ibis_python"] * 4


def test_failed_physical_binding_retains_source_duration_and_refuses_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cost_calls: list[str]
) -> None:
    source_duration: list[object] = []

    def failed_binding(
        directory: Path,
        work: object,
        producer: object,
        sample: Mapping[str, object],
        sample_path: Path,
        binding: Mapping[str, object],
    ) -> dict[str, object]:
        source_duration.append(sample["elapsed_seconds"])
        path = Path("physical-fixed-bindings/duckdb-csv.json")
        record = {"status": "failed", "error_type": "AssertionError"}
        collector.write_new(directory / path, record)
        return {"path": path.as_posix(), "sha256": collector.digest(collector.encode(record))}

    monkeypatch.setattr(collector, "_physical_fixed_binding", failed_binding)
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="csv",
        facts=100000,
        scenario="baseline",
        repeats=3,
        binding={},
        cost_scope="physical",
        skip_baseline_cold=True,
        acceptance_schedule=collector.EFFICIENCY_SCHEDULE,
        include_fixed=False,
    )
    assert cost_calls == ["ibis"] * 4 + ["ibis_python"]
    assert len(source_duration) == 1
    failed = samples[-1]
    assert failed["status"] == "failed" and failed["source_status"] == "passed"
    assert failed["stage"] == "physical_fixed_binding"
    assert failed["elapsed_seconds"] == source_duration[0]
    reference = failed["physical_fixed_binding"]
    assert isinstance(reference, dict)
    assert collector.digest((tmp_path / str(reference["path"])).read_bytes()) == reference["sha256"]


def test_failed_physical_source_never_attempts_offline_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cost_calls: list[str]
) -> None:
    def reject_local(sample: Mapping[str, object], binding: object) -> None:
        if sample["requested_route"] == "ibis_python":
            raise ValueError("Source validation failed")

    def forbidden_binding(*args: object) -> dict[str, object]:
        pytest.fail("A failed source cannot acquire an offline binding")

    monkeypatch.setattr(r96_cost_results, "valid", reject_local)
    monkeypatch.setattr(collector, "_physical_fixed_binding", forbidden_binding)
    samples = collector.run_group(
        tmp_path,
        backend="duckdb",
        profile="csv",
        facts=100000,
        scenario="baseline",
        repeats=3,
        binding={},
        cost_scope="physical",
        skip_baseline_cold=True,
        acceptance_schedule=collector.EFFICIENCY_SCHEDULE,
        include_fixed=False,
    )
    assert samples[-1]["status"] == "failed"
    assert "physical_fixed_binding" not in samples[-1]
    assert cost_calls == ["ibis"] * 4 + ["ibis_python"]


def test_indexed_sqlite_preserves_null_duplicate_facts_and_reports_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = r96_cost_scenarios._tables
    rows = r96_cost_scenarios._rows(2, "baseline")
    rows[0]["id"] = None
    rows[1] = dict(rows[0])
    with (
        fixture_layout("indexed-keys"),
        r96_cost_scenarios._tables("sqlite", "table", tmp_path, monkeypatch, rows) as (
            case,
            subjects,
            other,
        ),
    ):
        assert subjects == "r96_subjects" and other == "r96_other"
        assert case.environment["fixture_index_unique"] is False
        assert case.environment["read_only"] is True
        assert case.environment["fixture_index_ddl"] == [
            "CREATE INDEX r96_facts_full_key ON r96_facts (tenant, id, revision)",
            "CREATE INDEX r96_other_full_key ON r96_other (tenant, id, revision)",
        ]
        with sqlite3.connect(tmp_path / "r96.sqlite") as admin:
            assert admin.execute("SELECT COUNT(*) FROM r96_facts WHERE id IS NULL").fetchone() == (
                2,
            )
            indices = admin.execute("PRAGMA index_list(r96_facts)").fetchall()
            assert indices[0][1:3] == ("r96_facts_full_key", 0)
            assert [row[2] for row in admin.execute("PRAGMA index_info(r96_facts_full_key)")] == [
                "tenant",
                "id",
                "revision",
            ]
    assert r96_cost_scenarios._tables is original


def test_default_layout_is_noop_and_indexed_scope_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r96_cost_scenarios._tables
    with fixture_layout("unindexed"):
        assert r96_cost_scenarios._tables is original
    with (
        fixture_layout("indexed-keys"),
        pytest.raises(ValueError, match="ordinary tables"),
        r96_cost_scenarios._tables("duckdb", "table", Path("."), monkeypatch, []),
    ):
        pytest.fail("Unsupported deployment should not open a fixture")
    assert r96_cost_scenarios._tables is original
