"""Static source/candidate fixtures for the confirmed R9.6 reuse repairs."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from scripts import r96_cost_reuse as reuse
from scripts.r96_cost_results import arr, audit, obj

SOURCE = """
from typing import Literal, TypeAlias
def _author(root: Path, backend: str, case: Case, subjects: str, other: str) -> None:
    arguments = {"path": "facts"}
    write(arguments)
class Workload:
    def source(self, route: str):
        if route == "ibis_python":
            result = prepared.rollup().rollup().execute()
        return result
    def validate(self, result):
        if self.scenario == "baseline":
            return result == 529
        return {"passed": True}
def workload(root, backend, case, subjects, other, monkeypatch):
    _author(root, backend, case, subjects, other)
"""
COLLECTOR = """
def run_group(directory, *, cost_scope: str = "ordinary"):
    for route in work.routes:
        result = execute(route)
    return result
def collect(args):
    record = {"required_source_routes": SOURCE_ROUTES}
    return run_group(directory, cost_scope="ordinary")
def main():
    parser = Parser()
    parser.add_argument("--physical-profiles")
    return parser.parse_args()
"""
RESULTS = """
def valid(sample, candidate):
    return sample["candidate"] == candidate
def repeated(samples, candidate, *, warmup):
    return all(valid(sample, candidate) for sample in samples)
def audit(directories):
    return directories
"""
EXTREME_ROWS = """
def _rows(facts, scenario):
    for index in range(facts):
        amount = index % 17 + 1
        if scenario == "full-training-numeric-extremes":
            amount = (1 if (index // 16) % 2 == 0 else -1) * (2**53 + index % 17)
    return amount
"""
DIRECT_ORIGINAL = """
def execute_source_graph(prepared, lowered, source):
    if prepared.preparation:
        return execute_preparation(prepared, lowered, source)
    local = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    primary = next((stage for stage in lowered.stages if stage.output == lowered.primary_output), None)
    if primary is None and not local:
        raise _invalid("missing lowered primary relation")
    for stage in lowered.stages:
        issued = _issue(
            source, lowered, stage.expression,
            purpose="analysis.graph.stage", replacements=replacements,
        )
        issued_reads[stage.output] = issued
        staged, table = source.stage_derived(issued)
        owned.append(staged)
        tables[stage.output] = table
        replacements[stage.expression.op()] = staged.op()
    return tables
"""
DIRECT_CURRENT = """
def execute_source_graph(prepared, lowered, source):
    if prepared.preparation:
        return execute_preparation(prepared, lowered, source)
    local = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    primary = next((stage for stage in lowered.stages if stage.output == lowered.primary_output), None)
    if primary is None and not local:
        raise _invalid("missing lowered primary relation")
    direct_native = not local and all(
        isinstance(stage, LoweredRelation) for stage in lowered.stages
    )
    for stage in lowered.stages:
        if direct_native:
            if stage.output == lowered.primary_output:
                tables[stage.output] = _read(
                    source, lowered, stage.expression,
                    purpose="analysis.graph.stage", replacements={},
                    cell_reasons=stage.cell_reasons,
                )
        else:
            issued = _issue(
                source, lowered, stage.expression,
                purpose="analysis.graph.stage", replacements=replacements,
            )
            issued_reads[stage.output] = issued
            staged, table = source.stage_derived(issued)
            owned.append(staged)
            tables[stage.output] = table
            replacements[stage.expression.op()] = staged.op()
    return tables
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _candidate(files: dict[str, str]) -> dict[str, object]:
    hashes = {path: _hash(text) for path, text in files.items()}
    encoded = json.dumps(hashes, sort_keys=True, indent=2).encode() + b"\n"
    return {
        "head": "original-candidate-head",
        "dependencies": {"ibis-framework": "12.0.0", "pyarrow": "25.0.1"},
        "source_sha256": hashes,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _files() -> tuple[dict[str, str], dict[str, str]]:
    original = {
        "devtools/r96_cost_scenarios.py": SOURCE,
        "devtools/analysis_r9_cost.py": COLLECTOR,
        "scripts/r96_cost_results.py": RESULTS,
        "marivo/analysis/methods/builtin.py": "# unchanged product\n",
        reuse.DIRECT_SOURCE: DIRECT_ORIGINAL,
        **dict.fromkeys(reuse.DIRECT_TEST_ONLY, "# unchanged regression\n"),
        **dict.fromkeys(reuse.PROTECTED, "# unchanged dependency\n"),
    }
    current = dict(original)
    current["devtools/r96_cost_scenarios.py"] = (
        SOURCE.replace(
            "other: str) -> None:", "other: str, monkeypatch: pytest.MonkeyPatch) -> None:"
        )
        .replace(
            '    arguments = {"path": "facts"}\n',
            '    arguments = {"path": "facts"}\n'
            '    if "user" in arguments:\n'
            '        reader = arguments.pop("user")\n'
            '        if "user_env" not in arguments:\n'
            '            monkeypatch.setenv("MARIVO_R96_READER", str(reader))\n'
            '            arguments["user_env"] = "MARIVO_R96_READER"\n',
        )
        .replace("prepared.rollup().rollup().execute()", "prepared.rollup().execute()")
        .replace(
            "_author(root, backend, case, subjects, other)\n",
            "_author(root, backend, case, subjects, other, monkeypatch)\n",
        )
    )
    current["devtools/analysis_r9_cost.py"] = (
        COLLECTOR.replace(
            'cost_scope: str = "ordinary"):',
            'cost_scope: str = "ordinary", source_routes: Sequence[str] | None = None):',
        )
        .replace(
            "    for route in work.routes:\n",
            "    for route in work.routes:\n"
            "        if source_routes is not None and route not in source_routes:\n"
            "            continue\n",
        )
        .replace(
            '{"required_source_routes": SOURCE_ROUTES}',
            '{"required_source_routes": SOURCE_ROUTES, "scheduled_source_routes": args.source_routes}',
        )
        .replace(
            'cost_scope="ordinary")', 'cost_scope="ordinary", source_routes=args.source_routes)'
        )
        .replace(
            '    parser.add_argument("--physical-profiles")\n',
            '    parser.add_argument("--physical-profiles")\n'
            '    parser.add_argument("--source-routes", nargs="+", choices=("ibis", "ibis_python"))\n',
        )
    )
    current["scripts/r96_cost_results.py"] = RESULTS.replace(
        "return directories", "return list(directories)"
    )
    return original, current


def _write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: dict[str, str],
    current: dict[str, str],
) -> tuple[dict[str, object], dict[str, object], Path]:
    root = tmp_path / "candidate"
    for path, text in current.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    for path, archive in reuse.ARCHIVES.items():
        (snapshot / archive).write_text(original[path])
    monkeypatch.setattr(reuse, "ROOT", root)
    return _candidate(original), _candidate(current), snapshot


def test_exact_repairs_preserve_original_candidate_and_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, current, snapshot = _write(tmp_path, monkeypatch, *_files())
    original_digest = old["content_sha256"]
    proof = reuse.validate_reuse(old, current, snapshot)
    assert proof["original_candidate"] == old
    assert proof["candidate"] == current
    assert old["content_sha256"] == original_digest
    assert original_digest != current["content_sha256"]
    snapshots = proof["snapshot_sha256"]
    assert isinstance(snapshots, dict)
    assert len(snapshots) == 3


@pytest.mark.parametrize(
    "boundary", ("product", "observer", "oracle", "question", "measurement", "checker", "archive")
)
def test_reuse_refuses_changes_outside_the_confirmed_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    original, current = _files()
    if boundary == "product":
        current["marivo/analysis/methods/builtin.py"] += "# different registration\n"
    elif boundary == "observer":
        current["devtools/r96_cost_observer.py"] += "# different measurement\n"
    elif boundary == "oracle":
        current["devtools/r96_cost_scenarios.py"] = current[
            "devtools/r96_cost_scenarios.py"
        ].replace("result == 529", "result == 530")
    elif boundary == "question":
        current["devtools/r96_cost_scenarios.py"] = current[
            "devtools/r96_cost_scenarios.py"
        ].replace("prepared.rollup().execute()", "prepared.where(predicate).rollup().execute()")
    elif boundary == "measurement":
        current["devtools/analysis_r9_cost.py"] = current["devtools/analysis_r9_cost.py"].replace(
            "execute(route)", "execute(route, approximate=True)"
        )
    elif boundary == "checker":
        current["scripts/r96_cost_results.py"] = current["scripts/r96_cost_results.py"].replace(
            'sample["candidate"] == candidate', "True"
        )
    old, changed, snapshot = _write(tmp_path, monkeypatch, original, current)
    if boundary == "archive":
        (snapshot / "original_cost_scenarios.py").write_text(SOURCE + "# incomplete archive\n")
    with pytest.raises(ValueError):
        reuse.validate_reuse(old, changed, snapshot)


def test_reuse_scope_excludes_the_repaired_sqlite_route_and_failed_groups() -> None:
    sample: dict[str, object] = {
        "status": "passed",
        "scenario": "baseline",
        "cost_scope": "ordinary",
        "profile": "table",
        "facts": 1000,
        "backend": "duckdb",
        "requested_route": "ibis_python",
    }
    assert reuse.eligible_sample(sample)
    assert reuse.eligible_sample({**sample, "backend": "sqlite", "requested_route": "ibis"})
    assert reuse.eligible_sample(
        {
            **sample,
            "backend": "sqlite",
            "requested_route": "artifact_python",
            "recovery": "cold",
            "fixed_mode": "kernel",
        }
    )
    for changed in (
        {"backend": "sqlite"},
        {"status": "failed"},
        {"backend": "postgres"},
        {"scenario": "forecast-models"},
        {"profile": "view"},
        {"cost_scope": "physical"},
        {"backend": "sqlite", "requested_route": "artifact_python", "fixed_mode": "kernel"},
    ):
        assert not reuse.eligible_sample({**sample, **changed})


def _three_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, incorrect_extremes: bool = False
) -> tuple[dict[str, object], dict[str, object], dict[str, object], Path]:
    original, intermediate = _files()
    original["devtools/r96_cost_scenarios.py"] += EXTREME_ROWS
    intermediate["devtools/r96_cost_scenarios.py"] += EXTREME_ROWS
    intermediate["scripts/r96_cost_reuse.py"] = "# original concrete harness proof\n"
    current = dict(intermediate)
    replacement = (
        "(2**53 + 1024 + index % 17 if index < 16 "
        "else (1 if (index // 16) % 2 == 0 else -1) * (index % 17 + 1))"
    )
    if incorrect_extremes:
        replacement = replacement.replace("index < 16", "index < 32")
    current["devtools/r96_cost_scenarios.py"] = current["devtools/r96_cost_scenarios.py"].replace(
        "(1 if (index // 16) % 2 == 0 else -1) * (2**53 + index % 17)", replacement
    )
    current["scripts/r96_cost_reuse.py"] = "# exact pressure fixture proof\n"
    old, final, snapshot = _write(tmp_path, monkeypatch, original, current)
    pre_pressure = snapshot / "pre-pressure"
    pre_pressure.mkdir()
    for path, archive in reuse.PRESSURE_ARCHIVES.items():
        (pre_pressure / archive).write_text(intermediate[path])
    return old, _candidate(intermediate), final, snapshot


def test_pressure_repair_preserves_three_original_authorities_and_exact_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, intermediate, final, snapshot = _three_candidates(tmp_path, monkeypatch)
    proof = reuse.validate_reuse(original, final, snapshot, pre_pressure_candidate=intermediate)
    assert proof["schema"] == "marivo.r96.cost-reuse-proof.v2"
    assert proof["original_candidate"] == original
    assert proof["pre_pressure_candidate"] == intermediate
    assert proof["candidate"] == final
    assert (
        len({str(candidate["content_sha256"]) for candidate in (original, intermediate, final)})
        == 3
    )
    sample: dict[str, object] = {
        "status": "failed",
        "scenario": "baseline",
        "cost_scope": "ordinary",
        "profile": "table",
        "facts": 100000,
        "backend": "sqlite",
        "requested_route": "ibis_python",
        "elapsed_seconds": 602.198,
        "error_type": "DomainPreparationError",
    }
    assert reuse.eligible_pre_pressure_sample(sample)
    assert reuse.eligible_pre_pressure_sample({**sample, "status": "passed", "facts": 1000})
    assert not reuse.eligible_pre_pressure_sample(
        {**sample, "scenario": "full-training-numeric-extremes"}
    )


def test_pressure_reuse_requires_the_exact_confirmed_extreme_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, intermediate, final, snapshot = _three_candidates(
        tmp_path, monkeypatch, incorrect_extremes=True
    )
    with pytest.raises(ValueError, match="confirmed extreme amount assignment"):
        reuse.validate_reuse(original, final, snapshot, pre_pressure_candidate=intermediate)


def _baseline_sample(
    candidate: dict[str, object], *, route: str, mode: str | None, iteration: int
) -> dict[str, object]:
    identity = f"sqlite-1000-{route}-{mode}-{iteration}"
    fixed = route == "artifact_python"
    return {
        "schema": "marivo.r96.cost-sample.v1",
        "status": "passed",
        "candidate": candidate,
        "scenario": "baseline",
        "cost_scope": "ordinary",
        "backend": "sqlite",
        "profile": "table",
        "facts": 1000,
        "requested_route": route,
        "fixed_mode": mode,
        "iteration": iteration,
        "temperature": "warmup" if iteration == 0 else "measured",
        "elapsed_seconds": 0.1,
        "oracle": {"passed": True, "result_digest": "values-state-and-keys"},
        "identity": {"artifact_ref": identity, "root_route": route},
        "producer_identity": {"artifact_ref": f"producer-{identity}"} if fixed else None,
        "observations": {
            "resource_closed": True,
            "source_submissions": [] if fixed else [{"state": "succeeded"}],
            "storage_writes": [] if mode == "exact_hit" else [{"receipt": identity}],
            "phase_calls": {"fixed_kernel": int(mode == "kernel")},
        },
    }


def _phase(directory: Path, candidate: dict[str, object], samples: list[dict[str, object]]) -> None:
    from devtools.analysis_r9_cost import FIXED_APPLICABLE, SOURCE_ROUTES

    directory.mkdir()
    (directory / "samples").mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "marivo.r96.cost-manifest.v1",
                "candidate": candidate,
                "required_source_routes": SOURCE_ROUTES,
                "fixed_applicable": FIXED_APPLICABLE,
            }
        )
    )
    for index, sample in enumerate(samples):
        (directory / "samples" / f"sample-{index:03}.json").write_text(json.dumps(sample))


def test_three_candidate_audit_keeps_the_unaffected_sqlite_deadline_effective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, intermediate, final, snapshot = _three_candidates(tmp_path, monkeypatch)
    native = [
        _baseline_sample(original, route="ibis", mode=None, iteration=index) for index in range(4)
    ]
    historical = {
        **_baseline_sample(original, route="ibis_python", mode=None, iteration=0),
        "status": "failed",
        "error_type": "MethodRegistrationError",
    }
    local = [
        _baseline_sample(intermediate, route=route, mode=mode, iteration=index)
        for route, mode in (
            ("ibis_python", None),
            ("artifact_python", "kernel"),
            ("artifact_python", "exact_hit"),
        )
        for index in range(4)
    ]
    deadline = {
        **local[0],
        "status": "failed",
        "facts": 100000,
        "elapsed_seconds": 602.198,
        "error_type": "DomainPreparationError",
        "traceback": "completion within 600 monotonic seconds: execute deadline exceeded",
    }
    directories = tuple(tmp_path / name for name in ("original", "pre-pressure", "final"))
    _phase(directories[0], original, [*native, historical])
    _phase(directories[1], intermediate, [*local, deadline])
    _phase(directories[2], final, [])
    failed_path = directories[1] / "samples/sample-012.json"
    failed_bytes = failed_path.read_bytes()
    report = audit(directories, snapshot)
    results = obj(report["results"])
    identity = "R9:cost-baseline:sqlite:ordinary-table:ibis_python:1k-100k-warmup-three-samples"
    assert len(results) == 28
    assert obj(results[identity])["status"] == "failed"
    assert report["raw_counts"] == {"passed": 16, "failed": 2}
    assert report["effective_samples"] == 17
    historical_path = directories[0] / "samples/sample-004.json"
    assert arr(report["historical_samples"]) == [
        {
            "path": str(historical_path.resolve()),
            "sha256": hashlib.sha256(historical_path.read_bytes()).hexdigest(),
            "status": "failed",
            "execution_candidate": original["content_sha256"],
        }
    ]
    assert len(arr(report["attachments"])) == 18
    assert failed_path.read_bytes() == failed_bytes
    assert obj(obj(report["reuse_proof"])["pre_pressure_candidate"]) == intermediate


def _five_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, object]], Path]:
    original, intermediate, pressure, snapshot = _three_candidates(tmp_path, monkeypatch)
    hashes = obj(pressure["source_sha256"])
    pressure_files = {path: (reuse.ROOT / path).read_text() for path in hashes}
    pre_direct = snapshot / "pre-direct"
    pre_keyed = snapshot / "pre-keyed-oracle"
    pre_direct.mkdir()
    pre_keyed.mkdir()
    for path, archive in reuse.PRESSURE_ARCHIVES.items():
        (pre_direct / archive).write_text(pressure_files[path])
    (pre_direct / reuse.DIRECT_ARCHIVE).write_text(DIRECT_ORIGINAL)
    direct_files = {
        **pressure_files,
        reuse.DIRECT_SOURCE: DIRECT_CURRENT,
        "tests/test_r96_native_direct.py": "# confirmed direct regression\n",
    }
    for path, archive in reuse.PRESSURE_ARCHIVES.items():
        (pre_keyed / archive).write_text(direct_files[path])
    (pre_keyed / reuse.DIRECT_ARCHIVE).write_text(DIRECT_CURRENT)
    current_files = dict(direct_files)
    current_files["devtools/r96_cost_scenarios.py"] = direct_files[
        "devtools/r96_cost_scenarios.py"
    ].replace(
        '        return {"passed": True}\n',
        "        key_binding_digest = _keyed_oracle(self, result)\n"
        '        return {"passed": True, "original_key_binding": True,\n'
        '                "original_key_digest": key_binding_digest}\n',
    ) + "\n".join(
        f"def {name}(work, result):\n    return 'keyed'\n" for name in sorted(reuse.KEYED_HELPERS)
    )
    current_files["devtools/r96_cost_scenarios.py"] = current_files[
        "devtools/r96_cost_scenarios.py"
    ].replace(
        "from typing import Literal, TypeAlias\n",
        "from typing import TYPE_CHECKING, Literal, TypeAlias\n"
        "if TYPE_CHECKING:\n"
        + "\n".join("    " + code for code in reuse.KEYED_IMPORT_CODES)
        + "\n",
    )
    required = (
        "    if sample.get('scenario') not in (None, 'baseline') and (\n"
        "        oracle.get('original_key_binding') is not True or not oracle.get('original_key_digest')\n"
        "    ):\n"
        "        raise ValueError('A non-baseline sample requires independent original-key binding and digest')\n"
    )
    current_files["scripts/r96_cost_results.py"] = direct_files[
        "scripts/r96_cost_results.py"
    ].replace("def valid(sample, candidate):\n", "def valid(sample, candidate):\n" + required)
    current_files["scripts/r96_cost_reuse.py"] = "# concrete direct and keyed proof\n"
    for path, text in current_files.items():
        target = reuse.ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return [
        original,
        intermediate,
        pressure,
        _candidate(direct_files),
        _candidate(current_files),
    ], snapshot


OPTIMIZATION_ORIGINAL = {
    "marivo/analysis/materialization/graph_exchange.py": """
class CheckedStream:
    def __init__(self):
        self.completed = False
    def __iter__(self):
        return self._batches()
    def _batches(self):
        yield old_keys()
    def _validate_cells(self, batch):
        validate_scalar(batch)
    def close(self):
        self.source.close()
def collect(source):
    return old_index(source)
def _table_keys(table):
    return old_keys(table)
def state_owner(value):
    return exact_state(value)
""",
    "marivo/analysis/materialization/graph_preparation.py": """
def _observation(stage, selected):
    return old_rows(stage, selected)
def execute(prepared, lowered, source):
    return governed_source_prefix(prepared, lowered, source)
""",
}
OPTIMIZATION_CURRENT = {
    "marivo/analysis/materialization/graph_exchange.py": """
def _key_rows(batch, fields):
    yield bulk_keys(batch, fields)
class CheckedStream:
    def __init__(self):
        self.completed = False
        self._key_index = None
    def __iter__(self):
        return self._batches()
    def _batches(self):
        yield bulk_keys()
    def _validate_cells(self, batch):
        validate_bulk(batch)
    def _complete_key_index(self):
        return validated_index(self)
    def close(self):
        self.source.close()
def collect(source):
    return validated_index(source)
def _table_keys(table):
    return bulk_keys(table)
def state_owner(value):
    return exact_state(value)
""",
    "marivo/analysis/materialization/graph_preparation.py": """
def _observation(stage, selected):
    return reused_restrictions(stage, selected)
def execute(prepared, lowered, source):
    return governed_source_prefix(prepared, lowered, source)
""",
}


def _six_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, object]], Path]:
    candidates, snapshot = _five_candidates(tmp_path, monkeypatch)
    for index, candidate in enumerate(candidates):
        hashes = dict(obj(candidate["source_sha256"]))
        hashes.update({path: _hash(text) for path, text in OPTIMIZATION_ORIGINAL.items()})
        candidates[index] = {
            **candidate,
            "source_sha256": hashes,
            "content_sha256": hashlib.sha256(
                json.dumps(hashes, sort_keys=True, indent=2).encode() + b"\n"
            ).hexdigest(),
        }
    pre_optimization = snapshot / "pre-optimization"
    pre_optimization.mkdir()
    for path, archive in reuse.PRESSURE_ARCHIVES.items():
        (pre_optimization / archive).write_bytes((reuse.ROOT / path).read_bytes())
    for path, archive in reuse.OPTIMIZATION_ARCHIVES.items():
        (pre_optimization / archive).write_text(OPTIMIZATION_ORIGINAL[path])
    files = {
        path: (reuse.ROOT / path).read_text()
        for path in obj(candidates[-1]["source_sha256"])
        if path not in OPTIMIZATION_ORIGINAL
    }
    tests = dict.fromkeys(reuse.OPTIMIZATION_TESTS, "# exact mechanism regression\n")
    files.update(OPTIMIZATION_CURRENT)
    files.update(tests)
    monkeypatch.setattr(
        reuse,
        "OPTIMIZATION_HASHES",
        {
            path: (_hash(OPTIMIZATION_ORIGINAL[path]), _hash(text))
            for path, text in OPTIMIZATION_CURRENT.items()
        },
    )
    monkeypatch.setattr(
        reuse, "OPTIMIZATION_TESTS", {path: _hash(text) for path, text in tests.items()}
    )
    for path, text in files.items():
        target = reuse.ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return [*candidates, _candidate(files)], snapshot


def test_common_optimization_binds_exact_sources_without_relabeling_cost_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates, snapshot = _six_candidates(tmp_path, monkeypatch)
    proof = reuse.validate_current_reuse(candidates, snapshot)
    assert proof["schema"] == "marivo.r96.cost-reuse-proof.v5"
    assert proof["execution_candidates"] == candidates
    repair = obj(obj(proof["repairs"])["common_optimization"])
    assert repair["original_candidate"] == candidates[4]
    assert repair["candidate"] == candidates[5]
    assert repair["changed_files"] == sorted(reuse.OPTIMIZATION_ARCHIVES)
    assert repair["performance_authority"] == reuse.PERFORMANCE_AUTHORITY
    assert repair["rss_authority"] == reuse.RSS_AUTHORITY


@pytest.mark.parametrize(
    "boundary", ["hash", "close", "prefix", "oracle", "regression", "dependency", "archive"]
)
def test_common_optimization_refuses_changes_beyond_the_concrete_mechanism(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    candidates, snapshot = _six_candidates(tmp_path, monkeypatch)
    files = {path: (reuse.ROOT / path).read_text() for path in obj(candidates[-1]["source_sha256"])}
    exchange = "marivo/analysis/materialization/graph_exchange.py"
    preparation = "marivo/analysis/materialization/graph_preparation.py"
    if boundary == "hash":
        files[exchange] += "# not the reviewed implementation\n"
    elif boundary == "close":
        files[exchange] = files[exchange].replace("self.source.close()", "skip_close()")
    elif boundary == "prefix":
        files[preparation] = files[preparation].replace(
            "governed_source_prefix", "changed_source_prefix"
        )
    elif boundary == "oracle":
        files["devtools/r96_cost_scenarios.py"] += "# changed oracle\n"
    elif boundary == "regression":
        files[next(iter(reuse.OPTIMIZATION_TESTS))] += "# changed regression\n"
    elif boundary == "archive":
        (snapshot / "pre-optimization" / reuse.OPTIMIZATION_ARCHIVES[exchange]).write_text(
            "# corrupt old source\n"
        )
    for path, text in files.items():
        (reuse.ROOT / path).write_text(text)
    if boundary in ("close", "prefix"):
        monkeypatch.setattr(
            reuse,
            "OPTIMIZATION_HASHES",
            {
                path: (_hash(OPTIMIZATION_ORIGINAL[path]), _hash(files[path]))
                for path in OPTIMIZATION_ORIGINAL
            },
        )
    candidates[-1] = _candidate(files)
    if boundary == "dependency":
        candidates[-1]["dependencies"] = {"ibis-framework": "changed"}
    with pytest.raises(ValueError):
        reuse.validate_current_reuse(candidates, snapshot)


@pytest.mark.parametrize(
    "backend,profile",
    [
        ("duckdb", "table"),
        ("sqlite", "table"),
        ("postgres", "table"),
        ("mysql", "table"),
        ("trino", "iceberg"),
        ("clickhouse", "mergetree"),
    ],
)
def test_optimized_reuse_is_closed_to_ordinary_baselines(backend: str, profile: str) -> None:
    sample: dict[str, object] = {
        "backend": backend,
        "profile": profile,
        "facts": 100000,
        "scenario": "baseline",
        "cost_scope": "ordinary",
        "status": "passed",
        "requested_route": "ibis",
        "identity": {"root_route": "ibis"},
    }
    assert reuse.eligible_optimized_sample(sample)
    assert reuse.eligible_optimized_sample({**sample, "status": "failed"})
    assert not reuse.eligible_optimized_sample({**sample, "scenario": "cross-batch-runs"})
    assert not reuse.eligible_optimized_sample({**sample, "cost_scope": "physical"})
    assert not reuse.eligible_optimized_sample({**sample, "profile": "unknown"})
    fixed = {
        **sample,
        "requested_route": "artifact_python",
        "fixed_mode": "kernel",
        "recovery": "cold",
        "identity": {"root_route": "artifact_python"},
        "producer_identity": {"root_route": "ibis"},
        "observations": {"source_submissions": []},
    }
    assert reuse.eligible_optimized_sample(fixed)
    assert not reuse.eligible_optimized_sample(
        {**fixed, "observations": {"source_submissions": ["forbidden"]}}
    )


FILE_BUILTIN_ORIGINAL = """
def implementations(method):
    if method.name.startswith("deviation."):
        return deviation(method)
    return original_keys(method)
def specialize_numeric(implementation, key):
    if implementation.id.startswith(("r93.c12.", "r93.c13.", "r93.c18.")):
        return implementation
    return original_specialization(implementation, key)
"""
FILE_BUILTIN_CURRENT = """
def implementations(method):
    if method.name.startswith("deviation."):
        declarations = deviation(method)
        if method.name in ("deviation.zscore", "deviation.read"):
            declarations += exact_new_file_keys(method)
        return declarations
    if method.name == "state_rollup.sum_zero":
        declarations += exact_new_state_keys(method)
    return original_keys(method)
def specialize_numeric(implementation, key):
    if implementation.id.startswith(("r93.c12.", "r93.c13.", "r93.c18.", "r96.local_file.")):
        return implementation
    return original_specialization(implementation, key)
"""
FILE_FIXTURE_ORIGINAL = """
class SourceData:
    rows: list[dict[str, object]]
def source_case():
    if backend in {"duckdb", "sqlite"}:
        ds = datasource(backend, {"path": str(path), "read_only": True})
        return original_facts(ds)
"""
FILE_FIXTURE_CURRENT = """
class SourceData:
    rows: list[dict[str, object]]
    sqlite_type_map: dict[str, str] | None = None
def source_case():
    if backend in {"duckdb", "sqlite"}:
        fields: dict[str, object] = {"path": str(path), "read_only": True}
        if backend == "sqlite" and data is not None and data.sqlite_type_map is not None:
            fields["type_map"] = data.sqlite_type_map
        ds = datasource(backend, fields)
        return original_facts(ds)
"""


@pytest.mark.parametrize("damage", (None, "old_key", "guard", "new_branch"))
def test_file_builtin_proof_preserves_old_keys_and_exact_specialization_guard(
    monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    function = reuse._function(ast.parse(FILE_BUILTIN_CURRENT), "implementations")
    branches = [node for node in function.body if isinstance(node, ast.If)]
    monkeypatch.setattr(
        reuse,
        "FILE_BRANCHES",
        {
            "deviation": _hash(reuse._dump(branches[0])),
            "state_rollup": _hash(reuse._dump(branches[1])),
        },
    )
    current = FILE_BUILTIN_CURRENT
    if damage == "old_key":
        current = current.replace("original_keys(method)", "changed_table_keys(method)")
    elif damage == "guard":
        current = current.replace(', "r96.local_file."', "")
    elif damage == "new_branch":
        current = current.replace("exact_new_file_keys(method)", "widened_file_keys(method)")
    if damage is None:
        reuse._file_builtin_unchanged(ast.parse(FILE_BUILTIN_ORIGINAL), ast.parse(current))
    else:
        with pytest.raises(ValueError):
            reuse._file_builtin_unchanged(ast.parse(FILE_BUILTIN_ORIGINAL), ast.parse(current))


@pytest.mark.parametrize("damage", (None, "default", "backend", "facts"))
def test_file_fixture_proof_preserves_default_none_and_original_facts(damage: str | None) -> None:
    current = FILE_FIXTURE_CURRENT
    if damage == "default":
        current = current.replace("= None", '= {"numeric": "decimal(18,6)"}')
    elif damage == "backend":
        current = current.replace('backend == "sqlite"', 'backend == "duckdb"')
    elif damage == "facts":
        current = current.replace("original_facts(ds)", "changed_facts(ds)")
    if damage is None:
        reuse._sqlite_fixture_unchanged(ast.parse(FILE_FIXTURE_ORIGINAL), ast.parse(current))
    else:
        with pytest.raises(ValueError):
            reuse._sqlite_fixture_unchanged(ast.parse(FILE_FIXTURE_ORIGINAL), ast.parse(current))


@pytest.mark.parametrize("damage", (None, "facts"))
def test_sqlite_refusal_proof_preserves_other_risk_facts(damage: str | None) -> None:
    old = "from dataclasses import asdict\ndef risk_data():\n    return original_facts()\ndef test_source_type_risk():\n    return previous_expectation()\n"
    current = old.replace("import asdict", "import asdict, replace").replace(
        "previous_expectation()", "exact_refusal()"
    )
    current += "def _sqlite_decimal_refusal():\n    return actual_float_refusal()\ndef test_sqlite_numeric_type_map_cannot_restore_exact_decimal():\n    return exact_refusal()\n"
    if damage is not None:
        current = current.replace("original_facts()", "changed_facts()")
        with pytest.raises(ValueError):
            reuse._sqlite_risk_unchanged(ast.parse(old), ast.parse(current))
    else:
        reuse._sqlite_risk_unchanged(ast.parse(old), ast.parse(current))


@pytest.mark.parametrize("owner", ("authoring.py", "registry.py"))
@pytest.mark.parametrize("damage", (False, True))
def test_sqlite_disclosure_proof_cannot_change_execution(owner: str, damage: bool) -> None:
    if owner == "authoring.py":
        old = 'def sqlite():\n    """Original constraints."""\n    return original_spec()\n'
        current = old.replace(
            "Original constraints.", "Original constraints plus exact Decimal refusal."
        )
    else:
        old = 'def _build_registry():\n    return original_spec("Build a SQLite table/view datasource; median, percentile, and string strptime are unsupported.")\n'
        current = old.replace(
            "datasource; median", "datasource; native NUMERIC cannot supply exact Decimal. Median"
        )
    if damage:
        current = current.replace("original_spec(", "changed_spec(")
        with pytest.raises(ValueError):
            reuse._sqlite_disclosure_unchanged(owner, ast.parse(old), ast.parse(current))
    else:
        reuse._sqlite_disclosure_unchanged(owner, ast.parse(old), ast.parse(current))


@pytest.mark.parametrize(
    "owner",
    ("scripts/r9_qualification_requirements.py", "tests/test_full_algebra_backend_matrix.py"),
)
@pytest.mark.parametrize("damage", (False, True))
def test_qualification_proof_cannot_amend_other_ids_or_cost_rows(owner: str, damage: bool) -> None:
    if owner.startswith("scripts/"):
        old = 'def build():\n    add("source-types", backend, risk, "R9.2", fields)\n    return original_cost_rows()\n'
        current = old.replace(
            '"R9.2", fields)',
            '"R9.2", fields, "rejection" if backend == "sqlite" and risk == "decimal-precision-scale" else "success")',
        )
    else:
        old = 'def test_sql_and_risk_obligations_are_retained():\n    assert len([row for row in rows if row["expectation"] == "rejection"]) == 5\n    assert original_cost_rows()\n'
        current = old.replace("== 5", "== 6")
        current += "def test_sqlite_decimal_amendment_changes_only_its_expectation():\n    assert exact_single_amendment()\n"
    if damage:
        current = current.replace("original_cost_rows()", "changed_cost_rows()")
        with pytest.raises(ValueError):
            reuse._qualification_unchanged(owner, ast.parse(old), ast.parse(current))
    else:
        reuse._qualification_unchanged(owner, ast.parse(old), ast.parse(current))


def _binder_source(*, results: bool, updated: bool) -> str:
    preserved: tuple[str, ...]
    added: tuple[str, ...]
    constants: tuple[str, ...]
    if results:
        changed = ("audit", "main")
        preserved = ("valid", "repeated", "_group_candidate", "_cohort")
        added = (
            "_accepted_samples",
            "_attachment",
            "_boundary_runs",
            "_cold_resources",
            "_group_identity",
            "_physical_key_available",
        )
        constants = (
            "GROUP_FIELDS",
            "COHORT_FIELDS",
            "HTTP_PROFILES",
            "HISTORICAL_OWNERS",
            "BOUNDARY_CASES",
        )
        imports = "import ast\nimport subprocess\nfrom typing import Literal\nfrom xml.etree import ElementTree\n"
    else:
        changed = ("_keyed_reuse", "validate_current_reuse")
        preserved = ("eligible_sample", "eligible_pre_direct_sample", "eligible_direct_sample")
        added = (
            "_optimization_source_unchanged",
            "_optimization_reuse",
            "eligible_optimized_sample",
            "_file_builtin_unchanged",
            "_sqlite_disclosure_unchanged",
            "_sqlite_fixture_unchanged",
            "_sqlite_risk_unchanged",
            "_qualification_unchanged",
            "_binder_unchanged",
            "_file_reuse",
        )
        constants = (
            "OPTIMIZATION_ARCHIVES",
            "OPTIMIZATION_HASHES",
            "OPTIMIZATION_TESTS",
            "PERFORMANCE_AUTHORITY",
            "FILE_BUILTIN",
            "FILE_BUILTIN_HASHES",
            "FILE_BRANCHES",
            "FILE_TESTS",
            "FILE_FIXED_SOURCES",
            "FILE_CHANGED",
            "FILE_ADDED",
        )
        imports = ""
    text = "".join(f"def {name}():\n    return 'preserved'\n" for name in preserved)
    text += "".join(f"def {name}():\n    return {updated!r}\n" for name in changed)
    if updated:
        text += imports
        text += "".join(f"{name} = None\n" for name in constants)
        text += "".join(f"def {name}():\n    return 'concrete-new-owner'\n" for name in added)
    return text


@pytest.mark.parametrize("results", (False, True))
@pytest.mark.parametrize("damage", (None, "old_owner", "new_import"))
def test_binder_proof_preserves_previous_eligibility_and_validation(
    results: bool, damage: str | None
) -> None:
    old, current = (
        _binder_source(results=results, updated=False),
        _binder_source(results=results, updated=True),
    )
    if damage == "old_owner":
        current = current.replace("return 'preserved'", "return 'weakened'", 1)
    elif damage == "new_import":
        current += "import unrelated_engine\n"
    if damage is None:
        reuse._binder_unchanged(ast.parse(old), ast.parse(current), results=results)
    else:
        with pytest.raises(ValueError):
            reuse._binder_unchanged(ast.parse(old), ast.parse(current), results=results)


def test_direct_and_keyed_reuse_preserve_all_five_execution_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates, snapshot = _five_candidates(tmp_path, monkeypatch)
    proof = reuse.validate_current_reuse(candidates, snapshot)
    assert proof["schema"] == "marivo.r96.cost-reuse-proof.v4"
    assert proof["execution_candidates"] == candidates
    assert proof["candidate"] == candidates[-1]
    repairs = obj(proof["repairs"])
    assert set(repairs) == {"harness", "pressure_fixture", "direct_native", "keyed_oracle"}
    assert obj(repairs["keyed_oracle"])["oracle_helpers"] == sorted(reuse.KEYED_HELPERS)
    assert proof["rss_authority"] == reuse.RSS_AUTHORITY
    assert obj(repairs["keyed_oracle"])["rss_authority"] == reuse.RSS_AUTHORITY
    assert (
        reuse.validate_current_reuse(candidates[:4], snapshot)["schema"]
        == "marivo.r96.cost-reuse-proof.v3"
    )


@pytest.mark.parametrize(
    "boundary",
    (
        "direct",
        "preparation",
        "observer",
        "baseline",
        "keyed_position",
        "eager_import",
        "cold",
        "archive",
    ),
)
def test_current_reuse_refuses_unproved_product_or_baseline_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    candidates, snapshot = _five_candidates(tmp_path, monkeypatch)
    current = candidates[-1]
    files = {path: (reuse.ROOT / path).read_text() for path in obj(current["source_sha256"])}
    if boundary == "direct":
        files[reuse.DIRECT_SOURCE] = files[reuse.DIRECT_SOURCE].replace(
            "replacements={}", "replacements=replacements"
        )
    elif boundary == "preparation":
        archived = snapshot / "pre-keyed-oracle" / reuse.DIRECT_ARCHIVE
        text = archived.read_text().replace(
            "return execute_preparation", "return changed_preparation"
        )
        archived.write_text(text)
        candidates[3] = _candidate({**files, reuse.DIRECT_SOURCE: text})
    elif boundary == "observer":
        files["devtools/r96_cost_observer.py"] += "# changed measurement\n"
    elif boundary == "baseline":
        files["devtools/r96_cost_scenarios.py"] = files["devtools/r96_cost_scenarios.py"].replace(
            "result == 529", "result == 530"
        )
    elif boundary == "keyed_position":
        files["devtools/r96_cost_scenarios.py"] = (
            files["devtools/r96_cost_scenarios.py"]
            .replace(
                '        if self.scenario == "baseline":\n',
                '        key_binding_digest = _keyed_oracle(self, result)\n        if self.scenario == "baseline":\n',
            )
            .replace(
                '        key_binding_digest = _keyed_oracle(self, result)\n        return {"passed"',
                '        return {"passed"',
            )
        )
    elif boundary == "eager_import":
        files["devtools/r96_cost_scenarios.py"] += reuse.KEYED_IMPORT_CODES[0] + "\n"
    elif boundary == "cold":
        files["devtools/r96_cost_cold.py"] += "# changed recovery\n"
    else:
        (snapshot / "pre-direct" / reuse.DIRECT_ARCHIVE).write_text(
            DIRECT_ORIGINAL + "# corrupt archive\n"
        )
    for path, text in files.items():
        (reuse.ROOT / path).write_text(text)
    candidates[-1] = _candidate(files)
    with pytest.raises(ValueError):
        reuse.validate_current_reuse(candidates, snapshot)


def _prepared_identity(backend: str) -> dict[str, object]:
    methods = (
        ("deviation.zscore@v1", "deviation.read@v1", "state_rollup.sum_zero@v1")
        if backend == "duckdb"
        else ("metric.sum_zero@v1", "state_rollup.sum_zero@v1")
    )
    return {
        "root_route": "ibis_python",
        "actual_routes": ["ibis", "ibis_python"],
        "method_bindings": [
            {
                "route": "ibis_python",
                "key": {
                    "method": method,
                    "route": "ibis_python",
                    "shape": {
                        "kind": "SourceShape",
                        "backend": backend,
                        "form": "table",
                        "table_kind": "native",
                        "time": {"kind": "NoTime"}
                        if method in ("deviation.zscore@v1", "deviation.read@v1")
                        else {"kind": "instant", "timezone": "UTC", "unit": "us"},
                    },
                },
            }
            for method in methods
        ],
    }


@pytest.mark.parametrize("backend", ("duckdb", "sqlite", "postgres", "mysql"))
def test_pre_direct_scope_requires_real_local_method_shapes_and_never_native_timing(
    backend: str,
) -> None:
    record = {
        **_baseline_sample({"content_sha256": "old"}, route="ibis_python", mode=None, iteration=0),
        "backend": backend,
        "identity": _prepared_identity(backend),
    }
    assert reuse.eligible_pre_direct_sample(record)
    assert not reuse.eligible_pre_direct_sample({**record, "requested_route": "ibis"})
    assert not reuse.eligible_pre_direct_sample(
        {**record, "identity": {"root_route": "ibis_python"}}
    )
    assert not reuse.eligible_pre_direct_sample({**record, "scenario": "event-lifecycle-anchor"})
    assert not reuse.eligible_pre_direct_sample({**record, "cost_scope": "physical"})
    fixed = {
        **record,
        "requested_route": "artifact_python",
        "fixed_mode": "kernel",
        "recovery": "cold",
        "identity": {"root_route": "artifact_python"},
        "producer_identity": {"artifact_ref": "original-producer"},
        "observations": {"source_submissions": []},
    }
    assert reuse.eligible_pre_direct_sample(fixed)
    assert not reuse.eligible_pre_direct_sample(
        {**fixed, "observations": {"source_submissions": [{"purpose": "source"}]}}
    )


def test_five_candidate_audit_keeps_failed_local_and_post_direct_outcomes_without_weak_pressure_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates, snapshot = _five_candidates(tmp_path, monkeypatch)
    local = {
        **_baseline_sample(candidates[0], route="ibis_python", mode=None, iteration=0),
        "backend": "duckdb",
        "identity": {**_prepared_identity("duckdb"), "artifact_ref": "old-local"},
    }
    native = _baseline_sample(candidates[0], route="ibis", mode=None, iteration=0)
    deadline = {
        **_baseline_sample(candidates[1], route="ibis_python", mode=None, iteration=0),
        "status": "failed",
        "facts": 100000,
        "error_type": "DomainPreparationError",
        "traceback": "completion within 600 monotonic seconds: execute deadline exceeded",
    }
    weak_pressure = {**local, "candidate": candidates[2], "scenario": "event-lifecycle-anchor"}
    direct = {
        **_baseline_sample(candidates[3], route="ibis", mode=None, iteration=0),
        "backend": "postgres",
    }
    disconnected = {
        **direct,
        "backend": "mysql",
        "facts": 100000,
        "status": "failed",
        "error_type": "MaterializationError",
    }
    values: tuple[list[dict[str, object]], ...] = (
        [local, native],
        [deadline],
        [weak_pressure],
        [direct, disconnected],
        [],
    )
    directories = tuple(tmp_path / f"phase-{index}" for index in range(5))
    for directory, candidate, samples in zip(directories, candidates, values, strict=True):
        _phase(directory, candidate, samples)
    before = [
        path.read_bytes()
        for directory in directories
        for path in sorted((directory / "samples").glob("*.json"))
    ]
    report = audit(directories, snapshot)
    assert report["effective_samples"] == 4
    assert report["raw_counts"] == {"passed": 4, "failed": 2}
    historical_paths = (
        (directories[0] / "samples/sample-001.json", candidates[0]),
        (directories[2] / "samples/sample-000.json", candidates[2]),
    )
    assert arr(report["historical_samples"]) == [
        {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "status": "passed",
            "execution_candidate": candidate["content_sha256"],
        }
        for path, candidate in historical_paths
    ]
    assert report["counts"] != {"passed": 28, "failed": 0, "unverified": 0}
    assert before == [
        path.read_bytes()
        for directory in directories
        for path in sorted((directory / "samples").glob("*.json"))
    ]
    results = obj(report["results"])
    assert (
        obj(
            results[
                "R9:cost-baseline:sqlite:ordinary-table:ibis_python:1k-100k-warmup-three-samples"
            ]
        )["status"]
        == "failed"
    )
    assert (
        obj(results["R9:cost-baseline:mysql:ordinary-table:ibis:1k-100k-warmup-three-samples"])[
            "status"
        ]
        == "failed"
    )


def _followup_collectors() -> tuple[str, str]:
    old = """
def run_group(directory, *, source_routes: Sequence[str] | None = None):
    with workload(backend, profile, facts, scenario, root, patch) as work:
        for route in work.routes:
            record = {}
            result = work.source(route)
            if scenario == "baseline" and route == "ibis":
                record = cold_sample(result)
    return record
def collect(args):
    binding = candidate()
    record = {"scheduled_source_routes": args.source_routes}
    return run_group(directory, source_routes=args.source_routes)
def main():
    parser = Parser()
    parser.add_argument("--source-routes")
    return parser.parse_args()
"""
    current = (
        "from devtools.r96_cost_fixture_layout import FixtureLayout, fixture_layout\n"
        + old.replace(
            "source_routes: Sequence[str] | None = None):",
            "source_routes: Sequence[str] | None = None, skip_baseline_cold: bool = False, "
            "layout: FixtureLayout = 'unindexed', deployment: Mapping[str, object] | None = None):",
        )
        .replace(
            "    with workload(backend, profile, facts, scenario, root, patch) as work:\n",
            "    with fixture_layout(layout), workload(backend, profile, facts, scenario, root, patch) as work:\n"
            "        if deployment is not None:\n"
            "            work.environment['deployment_evidence'] = dict(deployment)\n"
            "            work.environment['deployment_cohort'] = deployment['deployment_cohort']\n",
        )
        .replace(
            "            record = {}\n",
            "            record = {}\n"
            "            if layout != 'unindexed' or deployment is not None:\n"
            "                record['environment'] = work.environment\n",
        )
        .replace(
            '            if scenario == "baseline" and route == "ibis":\n',
            '            if scenario == "baseline" and route == "ibis":\n'
            "                if skip_baseline_cold:\n"
            "                    continue\n",
        )
        .replace(
            "    binding = candidate()\n",
            "    binding = candidate()\n"
            "    deployment = None\n"
            "    if args.deployment_evidence is not None:\n"
            "        data = args.deployment_evidence.read_bytes()\n"
            "        configuration = dict(_mapping(json.loads(data)))\n"
            "        cohort = configuration.get('deployment_cohort')\n"
            "        if not isinstance(cohort, str) or not cohort:\n"
            "            raise ValueError('Deployment evidence requires a nonempty deployment_cohort')\n"
            "        deployment = {'path': str(args.deployment_evidence.resolve()),\n"
            "                      'sha256': digest(data), 'configuration': configuration,\n"
            "                      'deployment_cohort': cohort, 'usage_observation': False}\n",
        )
        .replace(
            '{"scheduled_source_routes": args.source_routes}',
            '{"scheduled_source_routes": args.source_routes, "scheduled_baseline_cold": not args.skip_baseline_cold,\n'
            '              "fixture_layout": args.fixture_layout, "deployment_evidence": deployment}',
        )
        .replace(
            "source_routes=args.source_routes)",
            "source_routes=args.source_routes, skip_baseline_cold=args.skip_baseline_cold, "
            "layout=args.fixture_layout, deployment=deployment)",
        )
        .replace(
            '    parser.add_argument("--source-routes")\n',
            '    parser.add_argument("--source-routes")\n'
            "    parser.add_argument('--skip-baseline-cold', action='store_true')\n"
            "    parser.add_argument('--fixture-layout', choices=('unindexed', 'indexed-keys'), default='unindexed')\n"
            "    parser.add_argument('--deployment-evidence', type=Path)\n",
        )
    )
    return old, current


def test_explicit_followup_controls_preserve_the_default_measurement_ast() -> None:
    old, current = _followup_collectors()
    reuse._followup_collector_unchanged(ast.parse(old), ast.parse(current))
    for replacement in (
        current.replace("skip_baseline_cold: bool = False", "skip_baseline_cold: bool = True"),
        current.replace(
            "layout: FixtureLayout = 'unindexed'", "layout: FixtureLayout = 'indexed-keys'"
        ),
        current.replace("result = work.source(route)", "result = work.source('ibis_python')"),
        current.replace("'usage_observation': False", "'usage_observation': True"),
        current.replace("record = cold_sample(result)", "record = cold_sample(different_result)"),
    ):
        with pytest.raises(ValueError):
            reuse._followup_collector_unchanged(ast.parse(old), ast.parse(replacement))


@pytest.mark.parametrize("complete", (False, True))
def test_efficiency_provisioning_proof_requires_the_existing_complete_policy(
    complete: bool,
) -> None:
    path = "tests/multisource_environment/clickhouse_analysis.py"
    full = "ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1, max_execution_time=0 CHANGEABLE_IN_READONLY"
    old = f"def setup():\n    con.command({full!r})\ndef setup_cluster():\n    con.command('ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1')\n"
    current = old.replace(
        "con.command('ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1')",
        f"con.command({full!r})"
        if complete
        else "con.command('ALTER USER analysis_reader SETTINGS readonly=1')",
    )
    if complete:
        assert reuse._efficiency_source_unchanged(path, ast.parse(old), ast.parse(current)) == [
            "setup_cluster.reader_policy"
        ]
    else:
        with pytest.raises(ValueError, match="complete reader policy"):
            reuse._efficiency_source_unchanged(path, ast.parse(old), ast.parse(current))


def test_file_authority_reads_its_c7_snapshot_after_current_binder_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "scripts/r96_cost_results.py"
    current = tmp_path / "current"
    snapshot = tmp_path / "pre-cluster"
    for root, value in ((current, "current C8 metadata"), (snapshot, "original C7 metadata")):
        (root / path).parent.mkdir(parents=True)
        (root / path).write_text(value)
    monkeypatch.setattr(reuse, "ROOT", current)
    assert reuse._file_source(path, snapshot) == b"original C7 metadata"
    assert reuse._file_source(path, None) == b"current C8 metadata"


def test_efficiency_authority_keeps_original_products_and_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = "marivo/analysis/materialization/graph_exchange.py"
    original = {product: "unchanged product\n"}
    old = _candidate(original)
    current = _candidate({product: "different product\n"})
    monkeypatch.setattr(reuse, "EFFICIENCY_ORIGINAL_SHA256", old["content_sha256"])
    with pytest.raises(ValueError, match="exact provisioning and metadata files"):
        reuse._efficiency_reuse(old, current, tmp_path)
    with pytest.raises(ValueError, match="unchanged HEAD"):
        reuse._efficiency_reuse(old, {**old, "head": "another-head"}, tmp_path)


@pytest.mark.parametrize("count", ("passed", "28"))
def test_efficiency_cli_mock_repair_retains_the_actual_partial_requirement_count(
    count: str,
) -> None:
    owner = "test_require_complete_cli_distinguishes_partial_report_from_28_id_exit"
    old = (
        f"def {owner}():\n"
        "    for passed in (27, 28):\n"
        "        report['counts'] = {'passed': passed}\n"
        "        assert results.main() == expected\n"
    )
    current = old.replace(
        "        assert results.main() == expected\n",
        f"        report['accepted_requirement_ids'] = {count}\n"
        "        assert results.main() == expected\n",
    )
    if count == "passed":
        assert reuse._efficiency_source_unchanged(
            "tests/test_r96_cost_audit.py", ast.parse(old), ast.parse(current)
        ) == [owner + ".accepted_requirement_ids_mock"]
    else:
        with pytest.raises(ValueError, match="actual accepted requirement count"):
            reuse._efficiency_source_unchanged(
                "tests/test_r96_cost_audit.py", ast.parse(old), ast.parse(current)
            )


@pytest.mark.parametrize("additional_change", (False, True))
def test_physical_binding_repair_requires_exact_reviewed_owner_ast(
    monkeypatch: pytest.MonkeyPatch, additional_change: bool
) -> None:
    path = "devtools/r96_cost_scenarios.py"
    old = "def _data():\n    return 'TIMESTAMP'\ndef _rows():\n    return 'unchanged facts'\n"
    current = old.replace("return 'TIMESTAMP'", "return 'TIMESTAMP(6)'")
    approved = {
        path: {
            "_data": (
                reuse._hash(reuse._dump(reuse._function(ast.parse(old), "_data")).encode()),
                reuse._hash(reuse._dump(reuse._function(ast.parse(current), "_data")).encode()),
            )
        }
    }
    monkeypatch.setattr(reuse, "PHYSICAL_BINDING_AST", approved)
    if additional_change:
        current = current.replace("unchanged facts", "different facts")
        with pytest.raises(ValueError, match="preserved source or oracle owner"):
            reuse._physical_binding_source_unchanged(path, ast.parse(old), ast.parse(current))
    else:
        assert reuse._physical_binding_source_unchanged(
            path, ast.parse(old), ast.parse(current)
        ) == ["_data"]


@pytest.mark.parametrize(
    ("schema", "backend", "profile", "facts", "expected"),
    (
        ("marivo.r96.cost-sample.v1", "clickhouse", "distributed", 100000, True),
        ("marivo.r96.functional-probe.v1", "clickhouse", "distributed", 1000, True),
        ("marivo.r96.physical-fixed-binding.v1", "clickhouse", "distributed", 100000, True),
        ("marivo.r96.cost-sample.v1", "trino", "non-iceberg", 100000, False),
        ("marivo.r96.cost-sample.v1", "clickhouse", "mergetree", 100000, False),
        ("marivo.r96.cost-sample.v1", "clickhouse", "distributed", 1000, False),
    ),
)
def test_physical_binding_authority_retains_only_actual_c8_distributed_scope(
    schema: str, backend: str, profile: str, facts: int, expected: bool
) -> None:
    functional = schema == "marivo.r96.functional-probe.v1"
    binding = schema == "marivo.r96.physical-fixed-binding.v1"
    sample = {
        "schema": schema,
        "candidate": {"content_sha256": reuse.PHYSICAL_BINDING_ORIGINAL_SHA256},
        "backend": backend,
        "profile": profile,
        "facts": facts,
        "scenario": "baseline",
        "acceptance_schedule": "r96-efficiency-42-v1",
        "status": "passed",
        "cost_scope": "physical",
        "requested_route": "ibis_python",
        "temperature": "functional" if functional else "warmup",
        "iteration": 0,
        "measurement_kind": "functional" if functional else "cost",
        "cost_measured": not binding,
    }
    assert reuse.eligible_physical_binding_sample(sample) is expected
    assert not reuse.eligible_physical_binding_sample(
        {**sample, "candidate": {"content_sha256": "unreviewed-predecessor"}}
    )


def test_physical_binding_reuse_preserves_products_and_actual_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = {"marivo/analysis/materialization/graph_exchange.py": "unchanged product\n"}
    old = _candidate(original)
    monkeypatch.setattr(reuse, "PHYSICAL_BINDING_ORIGINAL_SHA256", old["content_sha256"])
    with pytest.raises(ValueError, match="six exact fixture and metadata files"):
        reuse._physical_binding_reuse(old, _candidate(original), tmp_path)
    with pytest.raises(ValueError, match="unchanged HEAD"):
        reuse._physical_binding_reuse(old, {**old, "head": "different-head"}, tmp_path)


def test_remaining_1k_snapshot_overlay_keeps_actual_c8_collector_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    snapshots = tmp_path / "snapshots"
    physical = snapshots / "pre-physical-binding"
    remaining = snapshots / "pre-remaining-1k"
    physical.mkdir(parents=True)
    for path in ("devtools/analysis_r9_cost.py", "tests/test_r96_cost_schedule.py"):
        for directory, value in ((current, "current C10"), (remaining, "same C8 and C9")):
            (directory / path).parent.mkdir(parents=True, exist_ok=True)
            (directory / path).write_text(value)
    monkeypatch.setattr(reuse, "ROOT", current)
    for path in ("devtools/analysis_r9_cost.py", "tests/test_r96_cost_schedule.py"):
        assert reuse._file_source(path, physical) == b"same C8 and C9"
        assert reuse._file_source(path, remaining) == b"same C8 and C9"
        assert reuse._file_source(path, None) == b"current C10"
    path = "devtools/analysis_r9_cost.py"
    (physical / path).parent.mkdir(parents=True)
    (physical / path).write_text("original C8 snapshot")
    assert reuse._file_source(path, physical) == b"original C8 snapshot"


@pytest.mark.parametrize("additional_change", (False, True))
def test_remaining_1k_authority_requires_exact_owner_ast_and_unchanged_oracle(
    monkeypatch: pytest.MonkeyPatch, additional_change: bool
) -> None:
    path = "devtools/analysis_r9_cost.py"
    old = "def collect():\n    return 100000\ndef oracle():\n    return 'complete'\n"
    current = old.replace("return 100000", "return 1000")
    monkeypatch.setattr(
        reuse,
        "REMAINING_1K_AST",
        {
            path: {
                "collect": (
                    reuse._hash(reuse._dump(reuse._function(ast.parse(old), "collect")).encode()),
                    reuse._hash(
                        reuse._dump(reuse._function(ast.parse(current), "collect")).encode()
                    ),
                )
            }
        },
    )
    current += "REMAINING_1K_SCHEDULE = 'r96-remaining-1k-v1'\n"
    current += (
        "REMAINING_1K_PHYSICAL_PROFILES = tuple(profile for profile in "
        "EFFICIENCY_PHYSICAL_PROFILES if profile != 'clickhouse:distributed')\n"
    )
    if additional_change:
        with pytest.raises(ValueError, match="exact finite schedule"):
            reuse._remaining_1k_source_unchanged(
                path,
                ast.parse(old),
                ast.parse(current.replace("'r96-remaining-1k-v1'", "'arbitrary-scale'")),
            )
        with pytest.raises(ValueError, match="excludes only completed Distributed"):
            reuse._remaining_1k_source_unchanged(
                path,
                ast.parse(old),
                ast.parse(current.replace("'clickhouse:distributed'", "'trino:non-iceberg'")),
            )
        with pytest.raises(ValueError, match="reviewed owner"):
            reuse._remaining_1k_source_unchanged(
                path, ast.parse(old), ast.parse(current.replace("return 1000", "return 10"))
            )
        with pytest.raises(ValueError, match="preserved source or oracle owner"):
            reuse._remaining_1k_source_unchanged(
                path, ast.parse(old), ast.parse(current.replace("'complete'", "'partial'"))
            )
    else:
        assert reuse._remaining_1k_source_unchanged(path, ast.parse(old), ast.parse(current)) == [
            "collect"
        ]


def test_remaining_1k_authority_preserves_the_original_nine_repair_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def retained(*arguments: object) -> dict[str, object]:
        calls.append(arguments)
        return {"historical_candidate": "unchanged", "original_outcomes": ["passed", "failed"]}

    for owner in (
        "_harness_reuse",
        "_pressure_reuse",
        "_direct_reuse",
        "_keyed_reuse",
        "_optimization_reuse",
        "_file_reuse",
        "_efficiency_reuse",
        "_physical_binding_reuse",
        "_remaining_1k_reuse",
    ):
        monkeypatch.setattr(reuse, owner, retained)
    authorities: list[dict[str, object]] = [{"content_sha256": str(index)} for index in range(10)]
    original = reuse.validate_current_reuse(authorities[:9], tmp_path)
    calls.clear()
    current = reuse.validate_current_reuse(authorities, tmp_path)
    original_repairs = obj(original["repairs"])
    current_repairs = obj(current["repairs"])
    assert len(current_repairs) == len(original_repairs) + 1
    assert {name: current_repairs[name] for name in original_repairs} == original_repairs
    assert current["candidate"] == authorities[-1]
    assert current["execution_candidates"] == authorities
    assert current["schema"] == "marivo.r96.cost-reuse-proof.v9"
    assert calls[-2] == (
        authorities[7],
        authorities[8],
        tmp_path / "pre-physical-binding",
        tmp_path / "pre-remaining-1k",
    )
    assert calls[-1] == (authorities[8], authorities[9], tmp_path / "pre-remaining-1k")


def test_remaining_1k_reuse_rejects_product_or_snapshot_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = reuse.REMAINING_1K_CHANGED
    original = dict.fromkeys(changed, "def schedule():\n    return 100000\n")
    product = "marivo/analysis/materialization/graph_exchange.py"
    original[product] = "# unchanged product\n"
    current = {
        path: source.replace("return 100000", "return 1000") for path, source in original.items()
    }
    root = tmp_path / "current"
    snapshot = tmp_path / "pre-remaining-1k"
    for directory, files in ((root, current), (snapshot, original)):
        for path, source in files.items():
            (directory / path).parent.mkdir(parents=True, exist_ok=True)
            (directory / path).write_text(source)
    monkeypatch.setattr(reuse, "ROOT", root)
    approved = {
        path: {
            "schedule": (
                reuse._hash(
                    reuse._dump(reuse._function(ast.parse(original[path]), "schedule")).encode()
                ),
                reuse._hash(
                    reuse._dump(reuse._function(ast.parse(current[path]), "schedule")).encode()
                ),
            )
        }
        for path in changed
    }
    monkeypatch.setattr(reuse, "REMAINING_1K_AST", approved)
    for path in ("devtools/analysis_r9_cost.py", "scripts/r96_cost_results.py"):
        current[path] += "REMAINING_1K_SCHEDULE = 'r96-remaining-1k-v1'\n"
    current["devtools/analysis_r9_cost.py"] += (
        "REMAINING_1K_PHYSICAL_PROFILES = tuple(profile for profile in "
        "EFFICIENCY_PHYSICAL_PROFILES if profile != 'clickhouse:distributed')\n"
    )
    for path in ("devtools/analysis_r9_cost.py", "scripts/r96_cost_results.py"):
        (root / path).write_text(current[path])
    for name in ("REMAINING_1K_ORIGINAL_SHA256", "REMAINING_1K_CHANGED", "REMAINING_1K_AST"):
        current["scripts/r96_cost_reuse.py"] += f"{name} = None\n"
    (root / "scripts/r96_cost_reuse.py").write_text(current["scripts/r96_cost_reuse.py"])
    old, final = _candidate(original), _candidate(current)
    monkeypatch.setattr(reuse, "REMAINING_1K_ORIGINAL_SHA256", old["content_sha256"])
    proof = reuse._remaining_1k_reuse(old, final, snapshot)
    assert proof["original_candidate"] == old
    assert proof["candidate"] == final
    assert obj(proof["unchanged_boundary_sources"])[product] == _hash(original[product])
    with pytest.raises(ValueError, match="unchanged HEAD"):
        reuse._remaining_1k_reuse(old, {**final, "dependencies": {}}, snapshot)
    with pytest.raises(ValueError, match="six exact scheduling"):
        reuse._remaining_1k_reuse(
            old, _candidate({**current, product: "# changed product\n"}), snapshot
        )
    with pytest.raises(ValueError, match="actual C9 repair authority"):
        reuse._remaining_1k_reuse({**old, "content_sha256": "unknown"}, final, snapshot)
    path = "devtools/analysis_r9_cost.py"
    (snapshot / path).write_text(original[path] + "# archive drift\n")
    with pytest.raises(ValueError, match="snapshot does not bind C9"):
        reuse._remaining_1k_reuse(old, final, snapshot)
