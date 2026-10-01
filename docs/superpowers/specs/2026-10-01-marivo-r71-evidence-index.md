# R7.1 static freeze evidence index

Date: 2026-10-01. Status: R7.1 documentation/static freeze complete; no new
Runtime, installed-wheel, backend, source or real-Agent qualification.
Baseline `panda`, `10724b1d5c54019e175a3a1c3e12a19ec9c4a118`, clean at entry.

## Durable artifacts and baseline

- [Migration/qualification ledger](2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md): F01-F14, M01-M16, V01-V18, A09/A10/A13 and blockers.
- [Consumer snapshot](2026-10-01-marivo-r71-consumer-snapshot.json): SHA256 `901c2811828008216bab6c953f137cd90e6cfb1b376d86f5e6296c5eb43c42cb`.
- Snapshot owner_inputs preserves ten exact baseline document hashes. The complete
  source, test/worker/fixture, disclosure, consumer-disposition and requirement
  records are zlib+base64 encoded under inventory_payload with a bound raw SHA256;
  per-file hashes remain in those records. A decode command follows below.
- Current contract owners: [API](../../specs/analysis/python-analysis-design.md#r71-frozen-domain-api-target), [operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules), [Semantic](../../specs/semantic/semantic-object-model.md#r71-frozen-event-and-statemodel-handoff),
  [Runtime](../../specs/analysis/session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state) and [timezone](../../specs/analysis/timezone-and-calendar-design.md#r71-frozen-occurrence-and-relative-window-time).

Static totals: 221 source files,
513 relevant import statements,
1015 alias/receiver call candidates,
308 source SQL-text candidates,
251 test/worker/fixture files,
2313 test definition nodes and
51 current disclosure files.
These are exact scoped scan counts, not dynamic execution/reachability counts.
The encoded inventory payload decompresses to
`2413847` bytes (SHA256
`b4d9f12746827c660f43820ddc0ca2012ae7353bf616d9b1dfed7eec1eda0063`). Decode and verify it with:

```sh
.venv/bin/python -c 'import base64,hashlib,json,zlib; from pathlib import Path; p=json.loads(Path("docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json").read_text())["inventory_payload"]; b=zlib.decompress(base64.b64decode(p["data"])); assert len(b)==p["uncompressed_bytes"] and hashlib.sha256(b).hexdigest()==p["sha256_uncompressed"]; Path("/tmp/marivo-r71-inventory-payload.json").write_bytes(b)'
```

The 13500 target cells and
236 migration dispositions are planned,
not executed test or Runtime pass counts.

Import-only probes confirm four old Event/Lifecycle public Dataset classes still
resolve, private funnel classes are not public exports, and the new Journey/
History/Anchor/Retention classes are absent. Pure legacy migration-stage probes
return 7 for Event/Lifecycle/funnel, 8 for statistical consumers and None for
retired Metric comparison. Source admission's existing blocked branch is inspected;
no Session, datasource, Artifact, Run or test is executed by these probes.

## Source pushdown and ClickHouse feasibility

The user requests source pushdown and efficient native methods. Prefer qualified
source implementations; minimize exchanged support/rows/bytes and record the
reason for each irreducible local operation. The Runtime owner defines this rule.

[ClickHouse's parametric-function reference](https://clickhouse.com/docs/reference/functions/aggregate-functions/parametric-functions)
describes windowFunnel as longest depth, sequenceCount as nonoverlapping chains,
sequenceMatch as existence, and sequenceMatchEvents as matching timestamps.
These are candidates, not full canonical Journey assignments. Shared-tick ordering,
condition limits and exact time units require server-version-specific qualification.

| Candidate | Inferred qualification obligation; no implementation pass |
| --- | --- |
| windowFunnel | Reconcile sliding/max-depth semantics with absolute follow-up and canonical first/shared/exclusive assignment; preserve exact witness keys |
| sequenceMatch | Existential truth alone cannot provide assignments, negative coverage or Journey multiplicity |
| sequenceCount | Nonoverlapping chains are not automatically every_start exclusive final-only reservation with reusable intermediate events |
| sequenceMatchEvents | Timestamp arrays do not establish occurrence identity or canonical earliest assignment |

The table's contract mismatches are inferences from the native descriptions and
the accepted Marivo owner, not performance measurements or claims that every
restricted native implementation is impossible. Candidate use may combine a
native stage with source-side canonical witnesses when the full contract proves
equivalent. R9 retains actual ClickHouse MergeTree/Distributed/server qualification.

[Ibis aggregate builtin documentation](https://ibis-project.org/reference/aggregate-udfs)
provides typed aggregate builtins, but that general API does not establish support
for this parametric syntax. The local Ibis 12.0.0 compile-only probe and installed
compiler-file hashes are in source_pushdown_probe. Passing window=60 to a builtin
named windowFunnel renders an ordinary one-list call, not the required parametric
two-list form. No database was contacted and the output is not executed.
The binding gap requires supported public Ibis lowering or an upstream supported
operator/version; SQL embedded in function names, private visitors, compiler
monkeypatches and post-compilation string/AST rewrites remain disallowed.

## Reproduction and limitations

Use repository `.venv/bin/python`; no pytest collection or Runtime is involved.
Extract the exact collector below to a temporary file, then run:

```sh
.venv/bin/python /tmp/marivo-r71-inventory.py 10724b1d5c54019e175a3a1c3e12a19ec9c4a118 /tmp/marivo-r71-reproduced.json
```

The collector reads tracked source/test/disclosure files and baseline owner blobs,
runs import-only and Ibis compile-only probes, and writes only the requested JSON
artifact. Reproduction must use unchanged captured product/test/disclosure inputs
and the recorded Ibis/compiler versions; newer code is a new snapshot, not a pass
for this baseline. The embedded collector SHA256 is
`223bba71e5a3ed3e3108181ebca9ebfb255c75b80253a3d288d4e5460b4e04d0`. Generated JSON is deterministic for these inputs.
Test nodes are AST definitions without parametrized instance collection. Alias
resolution is file-wide, receiver/SQL matches are candidates, and dynamic calls
require later Runtime/registry/codec/worker verification. Mandatory S/F/C targets
remain planned even when an old related private test exists.

<!-- R71 inventory collector -->
```python
"""Reproduce the R7.1 static inventory; no business reads or test execution."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zlib

ROOT = Path.cwd()
BASELINE = sys.argv[1]
OUTPUT = Path(sys.argv[2])

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)

def sha(data):
    return hashlib.sha256(data).hexdigest()

paths = sorted(git("ls-files").decode().splitlines())
pattern = re.compile(r"event|lifecycle|funnel|finding|business_order|anchor|retention", re.I)
sql_calls = {"statement", "submit", "execute", "_compile_sql", "compile_event_bundle",
             "compile_event_expression", "integrity_sql", "integrity_queries", "parse_one",
             "batches", "compile_read", "collect_bounded"}
owners = ["docs/specs/analysis/python-analysis-design.md",
          "docs/specs/analysis/operators-and-frames.md",
          "docs/specs/analysis/session-state-and-runtime.md",
          "docs/specs/analysis/timezone-and-calendar-design.md",
          "docs/specs/semantic/semantic-object-model.md",
          "docs/superpowers/specs/2026-10-01-marivo-full-algebra-dsl-r7-implementation-plan.md",
          "docs/superpowers/specs/2026-09-26-marivo-full-refactor-acceptance.md",
          "docs/superpowers/specs/2026-10-01-marivo-r67-evidence-index.md",
          "docs/superpowers/specs/2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md",
          "docs/superpowers/specs/2026-09-26-marivo-full-refactor-r0-sql-ledger.md"]
source, tests, disclosure = [], [], []
for path in paths:
    file = ROOT / path
    if not file.is_file():
        continue
    if path.startswith(("marivo/", "tests/")) and path.endswith(".py"):
        data = file.read_bytes()
        body = data.decode()
        shared = path.startswith(("marivo/analysis/core/", "marivo/analysis/methods/",
                                  "marivo/analysis/materialization/graph_")) or path in (
            "marivo/analysis/compiler/graph_plan.py", "marivo/analysis/compiler/graph_lowering.py",
            "marivo/datasource/adapters.py", "marivo/cli.py",
            "tests/test_analysis_help_resolution.py", "tests/test_analysis_dsl_public_static.py",
            "tests/test_unified_help.py", "tests/typing/analysis_dsl_public_contract.py",
            "tests/test_cli.py", "tests/installed_r6_journeys.py",
            "tests/test_analysis_recovery_r67.py", "tests/test_analysis_graph_publication_r44.py")
        if not shared and not pattern.search(path + "\n" + body):
            continue
        tree = ast.parse(body)
        imports, calls, definitions, strings, nodes, candidates = [], [], [], [], [], []
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                if module.startswith("marivo"):
                    imports.append({"line": node.lineno, "module": module,
                                    "names": names, "level": node.level})
                    for alias in node.names:
                        aliases[alias.asname or alias.name] = module + "." + alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("marivo"):
                        imports.append({"line": node.lineno, "module": alias.name,
                                        "names": [], "level": 0})
                        aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions.append({"line": node.lineno, "name": node.name,
                                    "kind": type(node).__name__})
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if len(value) <= 180 and pattern.search(value):
                    strings.append({"line": node.lineno, "value": value})
                if re.match(r"\s*(SELECT|WITH|CREATE|SET|ROLLBACK|BEGIN|COMMIT|PRAGMA)\b", value, re.I):
                    candidates.append({"line": node.lineno, "sha256": sha(value.encode()),
                                       "prefix": value.strip()[:100]})
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            first = name.split(".")[0]
            resolved = aliases.get(first)
            qualified = resolved + name[len(first):] if resolved else name
            if pattern.search(qualified) or name.split(".")[-1] in sql_calls:
                calls.append({"line": node.lineno, "callee": name[:180],
                              "resolved_alias": qualified[:220] if resolved else None})
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                nodes.append(path + "::" + node.name)
            elif isinstance(node, ast.ClassDef):
                nodes.extend(path + "::" + node.name + "::" + member.name for member in node.body
                             if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and member.name.startswith("test_"))
        imports = [item for item in imports
                   if pattern.search(item["module"] + " " + " ".join(item["names"]))]
        record = {"path": path, "sha256": sha(data), "definitions": definitions,
                  "imports": imports, "calls": calls,
                  "domain_string_candidates": strings,
                  "sql_text_candidates": candidates}
        if path.startswith("tests/"):
            del record["definitions"]
            record["test_definition_nodes"] = nodes
            record["worker_or_fixture"] = not bool(nodes)
            tests.append(record)
        else:
            source.append(record)
    elif path.startswith(("site/src/content/docs/", "marivo/skills/", "docs/api/")):
        if path.endswith((".md", ".mdx", ".rst")):
            data = file.read_bytes()
            body = data.decode()
            if ("/latest/" in path or path.startswith(("marivo/skills/", "docs/api/"))) and pattern.search(body):
                disclosure.append({"path": path, "sha256": sha(data),
                                   "matching_lines": [i for i, line in enumerate(body.splitlines(), 1)
                                                      if pattern.search(line)]})

probe_source = '''import json
import marivo.analysis as mv
from marivo.analysis.operators.registry import legacy_source_migration_stage
names = ("LogicalEventDataset", "MaterializedEventDataset", "LogicalLifecycleDataset",
         "MaterializedLifecycleDataset", "LogicalFunnelDeltaDataset", "LogicalFunnelAttributionDataset",
         "LogicalJourneyResult", "LogicalHistoryResult", "LogicalAnchorDomain", "LogicalRetentionResult",
         "step", "sequence", "first_per_subject", "every_start", "from_inception", "dropped_before",
         "funnel_loss_rate", "select_subjects")
exports = []
for name in names:
    try:
        value = getattr(mv, name)
        exports.append({"name": name, "in_all": name in mv.__all__,
                        "resolves": True, "owner_module": getattr(value, "__module__", None)})
    except AttributeError:
        exports.append({"name": name, "in_all": name in mv.__all__, "resolves": False})
print(json.dumps({"exports": exports, "legacy_migration_stage": {
    name: legacy_source_migration_stage(name) for name in
    ("session.events.match", "event.funnel", "event.time_to_event", "event.compare",
     "funnel_delta.attribute", "session.lifecycle.replay", "lifecycle.dwell",
     "metric.compare", "forecast.fit", "association.correlate")}}))
'''
probe = subprocess.run([sys.executable, "-c", probe_source], cwd=ROOT,
                       text=True, capture_output=True, check=True)
pushdown_source = '''import hashlib
import json
from pathlib import Path
import ibis
@ibis.udf.agg.builtin(name="windowFunnel", window=60)
def wf(timestamp: int, a: bool, b: bool) -> int:
    ...
t = ibis.table({"ts": "uint64", "a": "boolean", "b": "boolean"}, name="events")
files = ("expr/operations/udf.py", "backends/sql/compilers/base.py", "backends/sql/compilers/clickhouse.py")
root = Path(ibis.__file__).parent
print(json.dumps({"ibis_version": ibis.__version__,
                  "package_files": [{"path": name, "sha256": hashlib.sha256((root/name).read_bytes()).hexdigest()} for name in files],
                  "compiled_sql": str(ibis.to_sql(t.aggregate(level=wf(t.ts,t.a,t.b)), dialect="clickhouse")),
                  "observation": "The builtin window keyword does not render a parametric first argument list in this probe",
                  "scope": "Compile-only feasibility; no connection, execution, semantic parity or performance qualification"}))
'''
pushdown = subprocess.run([sys.executable, "-c", pushdown_source], cwd=ROOT,
                         text=True, capture_output=True, check=True)
key_profiles = [{"id": f"K{s}{o}", "subject": subject, "occurrence": occurrence}
                for s, subject in enumerate(("string", "int64", "composite(string,int64)"), 1)
                for o, occurrence in enumerate(("string", "int64", "composite(string,int64)"), 1)]
time_profiles = []
for form, units in (("duckdb_native_table", ("us",)), ("duckdb_local_parquet", ("s", "ms", "us", "ns"))):
    for unit in units:
        for zone in ("UTC", "America/New_York"):
            time_profiles.append({"id": f"T{len(time_profiles)+1:02}", "source_form": form,
                                  "occurrence_unit": unit, "aware_instant": True,
                                  "report_timezone": zone, "snapshot_and_validity_axes": True})
method_profiles = []
def profile(code, method, variant, route, package, public_route, output_type):
    method_profiles.append({"id": code, "method": method, "semantic_version": 1,
                            "state_version": 1, "implementation_version": 1,
                            "variant": variant, "source_route": route,
                            "implementation_identity": f"r7.{method}.{route}@v1",
                            "fixed_implementation_identity": f"r7.{method}.artifact_python@v1",
                            "fixed_route": "artifact_python", "resource_profile": "r7_execute_v1",
                            "implementation_package": package, "public_route": public_route,
                            "output_type": output_type,
                            "source_native_alternative": "unverified; requires full-contract parity and qualified public Ibis lowering",
                            "requirements": ["V01", "V02", "V03", "V13", "V14", "V15", "V16", "V17", "V18"] + (
                                ["V04", "V05", "V06"] if method.startswith("journey.") or method == "row.mean" else
                                ["V07", "V08"] if method.startswith("funnel") else
                                ["V09", "V10", "V11"] if method.startswith("history.") else
                                ["V06", "V12"] if method.startswith(("anchor.", "retention.")) else ["V06"])})
profile("P01", "occurrence.prepare", "integer/enum/precedence or closed tie case", "ibis", "R7.2", "session.events.match / session.lifecycle.replay / session.anchors", "exact typed occurrence rows")
for code, policy, route in (("P02", "first_per_subject", "ibis"), ("P03", "every_start(shared)", "ibis"), ("P04", "every_start(exclusive)", "ibis_python")):
    profile(code, "journey.match", policy, route, "R7.3", "session.events.match", "JourneyResult")
profile("P05", "journey.duration", "all five statuses; adjacent/nonadjacent exact steps", "ibis", "R7.3", "journeys.time_to_event", "Duration(unit)")
profile("P06", "journey.dropped_before", "first_per_subject; complete/unknown", "ibis", "R7.3", "journeys.read(dropped_before)", "Boolean Cell")
profile("P07", "funnel.reduce", "ungrouped/complete axes/null axis/dense steps", "ibis", "R7.4", "journeys.funnel", "checked int64 counts/float64 rates")
profile("P08", "funnel.compare", "funnel-period/full outer/owned read", "ibis", "R7.4", "funnel.compare", "checked int64 count pairs/float64 rates and difference")
profile("P09", "funnel_ratio_mix", "joint/hierarchy/common Top-K/Other/zero delta", "ibis", "R7.4", "change.attribute(funnel_loss_rate)", "float64 allocated sides/contributions with exact count parts")
profile("P10", "history.replay", "from_inception/NotStarted/Unknown/self/zero-duration/terminal", "ibis_python", "R7.5", "session.lifecycle.replay", "canonical HistoryResult")
for code, method, output in (("P11", "in_state", "Boolean Cell"), ("P12", "distribution", "int64 counts/float64 conditional share"), ("P13", "transitions", "int64 counts/float64 share"), ("P14", "violations", "exact occurrence/Category/Temporal relations"), ("P15", "intervals", "exact interval/Duration relations"), ("P16", "dwell", "int64 counts/Duration(unit) mean,median,p90")):
    profile(code, "history." + method, "retained complete domain and canonical parts", "ibis_python", "R7.6", "history." + ("read(in_state)" if method == "in_state" else method), output)
profile("P17", "anchor.bind", "Event role starts", "ibis", "R7.7", "session.anchors(Event role)", "AnchorDomain")
profile("P18", "anchor.bind", "exclusive/local canonical Journey starts", "ibis_python", "R7.7", "session.anchors(JourneyResult)", "AnchorDomain")
profile("P19", "anchor.retention", "elapsed positive/negative/unknown/shared", "ibis", "R7.8", "anchors.retention(elapsed)", "Boolean status/int64 counts/float64 deterministic bounds")
profile("P20", "anchor.retention", "calendar_days/DST/unique deadline/shared", "ibis_python", "R7.8", "anchors.retention(calendar_days)", "Boolean status/int64 counts/float64 deterministic bounds")
for code, rule in (("P21", "any_anchor"), ("P22", "every_anchor")):
    profile(code, "retention.by_subject", rule, "ibis_python", "R7.8", "retention.by_subject", "Subject Boolean status/int64 counts/float64 bounds")
profile("P23", "row.mean", "source-computable completed Journey current rows; HALF_EVEN", "ibis", "R7.3", "duration.summarize(mean)", "Duration(unit)")
profile("P24", "parts_transport", "where/completed/read/members; actual Subject image and empty selection", "ibis_python", "R7.3/R7.6/R7.7/R7.8", "owned read / where / completed / members", "exact transported scalar/domain variant")
for i, metric in enumerate(("count:int64", "sum:int64", "sum:float64", "sum:decimal(38,6)", "sum:duration(unit)",
                            "original_ratio:int64", "original_ratio:float64", "original_ratio:decimal(38,6)",
                            "original_ratio:duration(unit)", "multi_root_runtime_linear:float64",
                            "multi_root_runtime_ratio:int64"), 25):
    profile(f"P{i:02}", "anchor.observe", metric + "; Event-role origin/elapsed; exact path,scope,null/empty policies",
            "ibis", "R7.7", "anchors.observe(Metric or RuntimeMetricExpr)", "original Metric physical output/components")
    profile(f"P{i+12:02}", "anchor.observe", metric + "; calendar or local Journey origin; exact path,scope,null/empty policies",
            "ibis_python", "R7.7", "anchors.observe(Metric or RuntimeMetricExpr)", "original Metric physical output/components")
profile("P36", "bind_project", "local dropout/History selection then same-Run Metric count:int64 and sum:float64",
        "ibis_python", "R7.3/R7.6", "where -> members -> observe within one Logical DAG", "original Metric physical output/components")
profile("P48", "anchor.bind", "source-computable first/shared canonical Journey starts", "ibis", "R7.7", "session.anchors(JourneyResult)", "AnchorDomain")
profile("P49", "row.mean", "local Journey or observed interval current rows; HALF_EVEN", "ibis_python", "R7.3/R7.6", "duration.summarize(mean)", "Duration(unit)")
profile("P50", "parts_transport", "source-computable where/completed/read/members", "ibis", "R7.3/R7.4/R7.7/R7.8", "owned read / where / completed / members", "exact transported scalar/domain variant")
method_profiles.sort(key=lambda item: item["id"])
cells = []
for item in method_profiles:
    for key in key_profiles:
        for time in time_profiles:
            for phase, route in (("S", item["source_route"]), ("F", "artifact_python"), ("C", "artifact_python")):
                cells.append(["Q-R7-" + "-".join((item["id"], key["id"], time["id"], phase)),
                              item["id"], key["id"], time["id"], phase, route, "planned", True])
resource_limits = {"execute_seconds": 600}
resource_requirements = [
    {"requirement_id": f"Q-R7-V15-EXECUTE-{boundary}", "resource": "execute_seconds", "limit": 600,
     "boundary": boundary, "expected": "atomic failure" if boundary == "above" else "admitted within the single deadline",
     "status": "planned", "owner": "R7.9", "implementation_contributors": "each producing package",
     "route": "ibis/ibis_python/artifact_python",
     "scope": "single execute invocation from entry through successful return; all stages share remaining time"}
    for boundary in ("below", "at", "above")]
dispositions = []
for item in tests:
    path = item["path"]
    if not re.search(r"(?:test_lazy_(?:(?:postgres|trino|clickhouse)_)?(?:event|lifecycle)|lazy_(?:(?:remote|postgres|trino|clickhouse)_)?(?:event|lifecycle))", path):
        continue
    remote = bool(re.search(r"postgres|trino|clickhouse|remote", path))
    package = "R7.6" if "lifecycle_reducer" in path else "R7.5" if "lifecycle" in path else "R7.4" if "comparison" in path else "R7.3"
    nodes = item["test_definition_nodes"] or [path + "::<worker-or-fixture>"]
    for node in nodes:
        dispositions.append({"node": node, "source_sha256": item["sha256"],
                             "replacement_package": package, "backend_qualification_owner": "R9" if remote else package,
                             "disposition": "preserve independent business/numeric oracle; replace legacy entry, producer or codec assertions before retirement",
                             "replacement_status": "planned", "retirement_status": "not retired"})
inventory_payload = {
    "source_files": source, "test_files": tests, "disclosure_files": disclosure,
    "legacy_test_dispositions": dispositions, "qualification_cells": cells,
    "resource_requirements": resource_requirements}
payload_bytes = json.dumps(inventory_payload, separators=(",", ":"), ensure_ascii=True).encode()
snapshot = {
    "schema": "marivo.r71.static-consumer-snapshot/v2",
    "scope": "Static AST/text inventory and import-only probes; no dynamic reachability or Runtime qualification",
    "baseline": {"branch": "panda", "head": BASELINE,
                 "initial_status_porcelain": "", "tracked_diff": "empty at entry",
                 "owner_inputs": [{"path": path, "sha256": sha(git("show", BASELINE + ":" + path))}
                                  for path in owners]},
    "collector": {"sha256": sha(Path(__file__).read_bytes()),
                  "selection_regex": pattern.pattern,
                  "limitations": ["Alias resolution is file-wide, not scope-aware",
                                   "Receiver calls and SQL text matches are candidates, not execution proofs",
                                   "Test nodes identify AST definitions, not collected parameter instances",
                                   "Files outside the regex and explicit shared-owner/test selection are excluded"]},
    "inventory_payload": {"encoding": "zlib-rfc1950+base64",
                          "sha256_uncompressed": sha(payload_bytes),
                          "uncompressed_bytes": len(payload_bytes),
                          "records": "source_files, test_files, disclosure_files, legacy_test_dispositions, qualification_cells, resource_requirements",
                          "data": base64.b64encode(zlib.compress(payload_bytes, 9)).decode("ascii")},
    "public_import_probe": json.loads(probe.stdout),
    "source_pushdown_probe": json.loads(pushdown.stdout),
    "qualification_target": {
        "scope": "Mandatory target cells, not registry declarations or execution evidence",
        "key_profiles": key_profiles, "time_profiles": time_profiles, "method_profiles": method_profiles,
        "columns": ["requirement_id", "method_profile", "key_profile", "time_profile", "phase", "route", "status", "mandatory"],
        "phases": {"S": "independent source execution", "F": "verified fixed continuation",
                   "C": "fresh-process source-offline recovery and actual K execution"},
        "cells": "Every expanded cell and planned route/status is stored in inventory_payload.qualification_cells.",
        "resource_profile": {"id": "r7_execute_v1", "limits": resource_limits, "clock": "monotonic", "scope": "single execute invocation, all stages and routes"},
        "resource_requirements": "Stored in inventory_payload.resource_requirements.",
        "route_rules": ["Source route is the initial target for its parameter profile, not passed qualification",
                        "Prefer a fully qualified equivalent source-native alternative before admitting an ibis_python realization",
                        "A route change needs explicit versioned owner/ledger evidence; never drop a mandatory requirement",
                        "History/local-predecessor consumers prepare all source dependencies first; no source query follows local output",
                        "P01/P24 private transport cells are exercised through the named public producer/continuation; no extra public constructor"]},
    "totals": {"source_files": len(source), "test_files": len(tests),
               "test_definition_nodes": sum(len(item["test_definition_nodes"]) for item in tests),
               "disclosure_files": len(disclosure),
               "source_imports": sum(len(item["imports"]) for item in source),
               "source_calls": sum(len(item["calls"]) for item in source),
               "source_sql_text_candidates": sum(len(item["sql_text_candidates"]) for item in source),
               "qualification_cells": len(cells), "legacy_test_dispositions": len(dispositions)}
}
OUTPUT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n")
```

## Final documentation/static validation receipt

The latest execute-budget instruction supersedes the initial multi-quota draft.
The final snapshot uses only r7_execute_v1: 600 seconds per execute invocation,
shared across all stages/routes. Its three time-boundary requirements remain
planned. No candidate snapshot supplies Runtime passing evidence.

Executed static commands:

```sh
.venv/bin/python /tmp/marivo-r71-reproduce-collector.py 10724b1d5c54019e175a3a1c3e12a19ec9c4a118 /tmp/marivo-r71-reproduced.json
.venv/bin/python /tmp/marivo-r71-validate.py
git diff --check
```

The collector extracted from this document reproduced the JSON byte for byte;
SHA256 `901c2811828008216bab6c953f137cd90e6cfb1b376d86f5e6296c5eb43c42cb`. Validation passed F01-F14/M01-M16/V01-V18,
533 baseline/captured file hashes, 2313 actual AST test definitions, 236 legacy
dispositions, 13,500 unique planned qualification IDs and three planned time-budget
IDs. All source/fixed/cold statuses remain planned. New JSON/Markdown content
is English; the three new files are not ignored and their whitespace is checked.
Local link/anchor validation passed 40 references; the final added sections and
new documents contain 12 structurally valid tables. `git diff --check` passed.

Scope is seven modified tracked documents plus three new non-ignored artifacts;
no files are staged. The full tracked dirty patch is bound separately from the
new files: `git diff --binary -- <seven paths below>` SHA256
`36b7c224ce01a8b6cacde31f4dc90ca23411d49167fa4745ff89896282b45cc2`. HEAD/branch remain at the clean-entry baseline. No product,
tests, AGENTS.md or packaged skill file changes. Runtime/default tests, wheel,
site build, typecheck/lint, release-check and MinIO are excluded, not passed;
no commit, push or publication occurred. The acceptance owner marks R7.1 static
freeze complete and R7.2-R7.9 pending.

| Final owner/artifact path | SHA256 |
| --- | --- |
| `docs/specs/analysis/operators-and-frames.md` | `c515207aea06e5c43a8602c067f0c507dea7ca0b169399958e7afc72bfc36d36` |
| `docs/specs/analysis/python-analysis-design.md` | `f472bd581e21834ff4caa90dad9d56c904a63bb8f9af459a663cc99d28dc1646` |
| `docs/specs/analysis/session-state-and-runtime.md` | `10ce8339f302e4b959c174815471f35376277f37554cb46e0f0a5b50243811ce` |
| `docs/specs/analysis/timezone-and-calendar-design.md` | `ea81234da492af06104d889443ff5c1b46fbc8d357e987e17c29801bcb0bcca0` |
| `docs/specs/semantic/semantic-object-model.md` | `29355b12cccd12a0fc4cbccfe1f8a95bdd28cc4fcfef5420f5258b0e4be3859b` |
| `docs/superpowers/specs/2026-09-26-marivo-full-refactor-acceptance.md` | `b1f077884696b72db5b67fb05382dcdbb1743d593a1cb975bbefd55a7a4b38d8` |
| `docs/superpowers/specs/2026-10-01-marivo-full-algebra-dsl-r7-implementation-plan.md` | `54b4f70916851300ac3c3ae4ec53c2c465143d07a4f9461236b61b0d2c18f932` |
| `docs/superpowers/specs/2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md` | `f8d4db597aa009ec8c2ac5c421e0b9635ea375de4c5d6bd7c8349e51481ad044` |
| `docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json` | `901c2811828008216bab6c953f137cd90e6cfb1b376d86f5e6296c5eb43c42cb` |

This evidence index avoids a circular self-hash; its embedded validation source
is SHA256 `2fafb32baf8b3b49914554efe106205e56de806bfbcf7ffbfe6661648cd08193`. Recreate the validator by extracting the
fenced Python block immediately after the following marker into
/tmp/marivo-r71-validate.py. The collector must likewise be extracted into
/tmp/marivo-r71-inventory.py for its source-hash check. Run from the unchanged
repository baseline/diff and captured dependency environment.

<!-- R71 static validator -->
```python
"""Validate R7.1 documentation/static artifacts without executing tests."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zlib
from urllib.parse import unquote

root = Path.cwd()
folder = Path('docs/superpowers/specs')
ledger = folder / '2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md'
evidence = folder / '2026-10-01-marivo-r71-evidence-index.md'
snapshot = folder / '2026-10-01-marivo-r71-consumer-snapshot.json'
allowed = {
 'docs/specs/analysis/python-analysis-design.md',
 'docs/specs/analysis/operators-and-frames.md',
 'docs/specs/analysis/session-state-and-runtime.md',
 'docs/specs/analysis/timezone-and-calendar-design.md',
 'docs/specs/semantic/semantic-object-model.md',
 str(folder / '2026-10-01-marivo-full-algebra-dsl-r7-implementation-plan.md'),
 str(folder / '2026-09-26-marivo-full-refactor-acceptance.md'),
 str(ledger), str(evidence), str(snapshot)}

def git(*args):
    return subprocess.check_output(['git', *args], text=True)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def unfence(body):
    return re.sub(r'^```[^\n]*\n.*?^```\s*$', '', body, flags=re.M | re.S)

def anchors(body):
    headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*$', unfence(body), re.M)
    seen, result = {}, set()
    for heading in headings:
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if not count else f'{slug}-{count}')
    result.update(re.findall(r'<a\s+(?:id|name)=[\'"]([^\'"]+)', body))
    return result

status = git('status', '--porcelain').splitlines()
changed = {line[3:] for line in status}
assert changed == allowed, sorted(changed - allowed)
assert git('diff', '--cached', '--name-only') == ''
assert git('branch', '--show-current').strip() == 'panda'
def contains_cjk(value):
    if isinstance(value, str):
        return bool(re.search(r'[\u4e00-\u9fff]', value))
    if isinstance(value, dict):
        return any(contains_cjk(k) or contains_cjk(v) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_cjk(v) for v in value)
    return False

s = json.loads(snapshot.read_text())
assert s['schema'] == 'marivo.r71.static-consumer-snapshot/v2'
compressed = s['inventory_payload']
payload_bytes = zlib.decompress(base64.b64decode(compressed['data']))
assert len(payload_bytes) == compressed['uncompressed_bytes']
assert sha(payload_bytes) == compressed['sha256_uncompressed']
assert len(snapshot.read_bytes()) < 1000000
payload = json.loads(payload_bytes)
assert git('rev-parse', 'HEAD').strip() == s['baseline']['head']
text = ledger.read_text()
for prefix, count in [('F', 14), ('M', 16), ('V', 18)]:
    rows = re.findall(r'^\| (' + prefix + r'\d{2}) \|', text, re.M)
    assert rows == [f'{prefix}{i:02}' for i in range(1, count+1)], (prefix, rows)

hash_count = 0
all_nodes, test_paths = set(), set()
for group in ('source_files', 'test_files', 'disclosure_files'):
    for record in payload[group]:
        path = Path(record['path'])
        assert sha(path.read_bytes()) == record['sha256'], str(path)
        hash_count += 1
        if group == 'test_files':
            test_paths.add(str(path))
            tree = ast.parse(path.read_text())
            actual = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                    actual.add(str(path) + '::' + node.name)
                elif isinstance(node, ast.ClassDef):
                    actual.update(str(path) + '::' + node.name + '::' + member.name for member in node.body
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith('test_'))
            assert actual == set(record['test_definition_nodes']), str(path)
            all_nodes.update(actual)
for record in s['baseline']['owner_inputs']:
    blob = subprocess.check_output(['git', 'show', s['baseline']['head'] + ':' + record['path']])
    assert sha(blob) == record['sha256'], record['path']
    hash_count += 1
for record in payload['legacy_test_dispositions']:
    node = record['node']
    assert node in all_nodes or node.endswith('::<worker-or-fixture>') and node.split('::')[0] in test_paths
    assert record['replacement_status'] == 'planned' and record['retirement_status'] == 'not retired'
for row in re.findall(r'^\| V\d{2} \|.*$', text, re.M):
    old = re.findall(r'`([^`]+)`', row)[-1]
    assert old in all_nodes if '::' in old else old in test_paths, old

q = s['qualification_target']
assert len(q['method_profiles']) == 50 and len(q['key_profiles']) == 9 and len(q['time_profiles']) == 10
profiles = {p['id']: p for p in q['method_profiles']}
keys = {p['id'] for p in q['key_profiles']}
times = {p['id'] for p in q['time_profiles']}
ids = set()
for rid, method, key, time, phase, route, state, mandatory in payload['qualification_cells']:
    assert rid == '-'.join(('Q-R7', method, key, time, phase)) and rid not in ids
    ids.add(rid)
    assert method in profiles and key in keys and time in times and phase in {'S', 'F', 'C'}
    assert route == (profiles[method]['source_route'] if phase == 'S' else 'artifact_python')
    assert state == 'planned' and mandatory is True
assert len(ids) == 13500
assert len(payload['resource_requirements']) == 3
assert len({r['requirement_id'] for r in payload['resource_requirements']}) == 3
assert all(r['status'] == 'planned' for r in payload['resource_requirements'])
assert q['resource_profile']['limits'] == {'execute_seconds': 600}
assert q['resource_profile']['id'] == 'r7_execute_v1'
for profile in profiles.values():
    assert profile['implementation_version'] == profile['state_version'] == profile['semantic_version'] == 1
    assert profile['resource_profile'] == q['resource_profile']['id']
    assert profile['implementation_identity'] == f"r7.{profile['method']}.{profile['source_route']}@v1"
    assert all(re.fullmatch(r'V(?:0[1-9]|1[0-8])', v) for v in profile['requirements'])
assert sha(Path('/tmp/marivo-r71-inventory.py').read_bytes()) == s['collector']['sha256']
embedded = evidence.read_text().split('<!-- R71 inventory collector -->\n```python\n', 1)[1].split('\n```', 1)[0] + '\n'
assert sha(embedded.encode()) == s['collector']['sha256']
assert sha(snapshot.read_bytes()) in evidence.read_text()
assert Path(snapshot).stat().st_size < 1000000
for path in (ledger, evidence, snapshot):
    body = path.read_text()
    assert body.endswith('\n') and not body.endswith('\n\n'), str(path)
    assert all(line == line.rstrip() for line in body.splitlines()), str(path)

assert not contains_cjk(s)
if '<!-- R71 static validator -->\n```python\n' in evidence.read_text():
    receipt = evidence.read_text().split('## Final documentation/static validation receipt', 1)[1]
    validation_source = receipt.split('<!-- R71 static validator -->\n```python\n', 1)[1].split('\n```', 1)[0] + '\n'
    assert sha(validation_source.encode()) == sha(Path(__file__).read_bytes())
    for name, expected in re.findall(r'^\| `([^`]+)` \| `([0-9a-f]{64})` \|$', receipt, re.M):
        assert sha(Path(name).read_bytes()) == expected, name
    tracked = git('diff', '--name-only').splitlines()
    patch = subprocess.check_output(['git', 'diff', '--binary', '--', *tracked])
    assert sha(patch) in receipt


links = tables = 0
for path in sorted(changed - {str(snapshot)}):
    file = Path(path)
    body = file.read_text()
    diff = git('diff', '--unified=0', '--', path)
    added = '\n'.join(line[1:] for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++')) if diff else body
    scan = unfence(added)
    assert not re.search(r'\bTBD\b|\bTODO\b', scan), path
    if path in {str(ledger), str(evidence)}:
        assert not re.search(r'[\u4e00-\u9fff]', body), path
    for label, target in re.findall(r'\[([^\]\n]+)\]\(([^\)\n]+)\)', scan):
        if re.match(r'\w+://|mailto:', target):
            continue
        target = unquote(target.strip('<>'))
        name, _, fragment = target.partition('#')
        dest = (file.parent / name).resolve() if name else file.resolve()
        assert dest.is_file(), (path, target)
        assert dest.is_relative_to(root), (path, target)
        if fragment:
            assert fragment in anchors(dest.read_text()), (path, target, sorted(anchors(dest.read_text()))[-10:])
        links += 1
    section = body.split('## R7.1 ', 1)[-1] if path.startswith('docs/specs/') else body if path in {str(ledger), str(evidence)} else added
    expected = None
    for line in unfence(section).splitlines():
        if not line.startswith('|'):
            expected = None
            continue
        columns = len(re.findall(r'(?<!\\)\|', line)) - 1
        if expected is None:
            expected = columns
            tables += 1
        assert columns == expected, (path, line[:100], columns, expected)
for path in (str(ledger), str(evidence), str(snapshot)):
    ignored = subprocess.run(['git', 'check-ignore', '--quiet', path])
    assert ignored.returncode == 1, path
subprocess.run(['git', 'diff', '--check'], check=True)
print(json.dumps({'status': 'passed', 'changed_docs_and_json': len(changed),
    'coverage': {'F': 14, 'M': 16, 'V': 18}, 'file_hashes': hash_count,
    'test_definition_nodes': len(all_nodes), 'legacy_dispositions': len(payload['legacy_test_dispositions']),
    'planned_qualification_cells': len(ids), 'planned_budget_requirements': 3,
    'local_links_and_anchors': links, 'tables': tables, 'staged_files': 0,
    'snapshot_sha256': sha(snapshot.read_bytes())}, sort_keys=True))
```
