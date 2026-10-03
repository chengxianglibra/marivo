"""Reproduce the R8.1 static freeze without business reads or method execution."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import zlib
from pathlib import Path
from typing import TypeAlias

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
SPECS = "docs/superpowers/specs/"
API = "docs/specs/analysis/python-analysis-design.md#r81-frozen-statistical-relation-api-target"
OPS = "docs/specs/analysis/operators-and-frames.md#r81-frozen-statistical-method-rules"
RUNTIME = "docs/specs/analysis/session-state-and-runtime.md#r81-frozen-statistical-execution-and-disclosure"
TIME = "docs/specs/analysis/timezone-and-calendar-design.md#r81-frozen-statistical-grid-authority"
PLAN = SPECS + "2026-10-03-marivo-full-algebra-dsl-r8-implementation-plan.md"
LEDGER = SPECS + "2026-10-03-marivo-full-algebra-dsl-r8-migration-ledger.md"
SNAPSHOT = SPECS + "2026-10-03-marivo-r81-consumer-snapshot.json"
SNAPSHOT_DATA = SPECS + "2026-10-03-marivo-r81-consumer-snapshot-data"
CHUNK_BYTES = 800_000
TOKENS = re.compile(r"candidate|discover|driver_|association|correlat|spearman|forecast", re.I)

OWNERS = {
    "F01": API,
    "F02": API,
    "F03": OPS,
    "F04": OPS,
    "F05": OPS,
    "F06": OPS,
    "F07": OPS,
    "F08": OPS,
    "F09": OPS,
    "F10": RUNTIME,
    "F11": RUNTIME,
    "F12": RUNTIME,
    "F13": RUNTIME,
    "F14": RUNTIME,
}
MIGRATIONS: dict[str, tuple[str, ...]] = {
    "M01": ("operators/discovery.py", "operators/candidate_dataset.py", "observation/metric.py"),
    "M02": ("operators/candidate_contracts.py", "operators/candidate_values.py"),
    "M03": ("operators/driver_", "compiler/driver_candidate.py"),
    "M04": ("compiler/entity_candidate.py", "compiler/driver_numeric.py"),
    "M05": (
        "public_dsl.py",
        "core/graph.py",
        "core/rules.py",
        "methods/local.py",
        "materialization/graph_spearman_execution.py",
        "materialization/graph_composition.py",
        "materialization/graph_relation.py",
    ),
    "M06": ("operators/correlate.py", "operators/association"),
    "M07": ("compiler/correlation.py",),
    "M08": ("operators/forecast",),
    "M09": (
        "materialization/candidate_",
        "materialization/association_",
        "materialization/forecast_",
    ),
    "M10": (
        "datasets/descriptors.py",
        "materialization/storage.py",
        "materialization/source_preparation.py",
        "materialization/input_bindings_codec.py",
    ),
    "M11": (
        "operators/registry.py",
        "compiler/nodes.py",
        "compiler/normalize.py",
        "compiler/lowering.py",
        "compiler/placement.py",
        "compiler/source_admission.py",
        "observation/contracts.py",
    ),
    "M12": (
        "materialization/dataset_execution.py",
        "materialization/execution.py",
        "materialization/source_stage.py",
        "materialization/local_stage.py",
    ),
    "M13": (
        "compiler/graph_plan.py",
        "materialization/graph_observation",
        "materialization/source_preparation.py",
        "materialization/graph_local_execution.py",
        "materialization/graph_occurrence_execution.py",
    ),
    "M14": (
        "materialization/graph_protocol.py",
        "materialization/graph_exchange.py",
        "materialization/graph_storage.py",
        "materialization/graph_snapshot.py",
        "materialization/graph_store.py",
        "public_dsl.py",
        "session/core.py",
    ),
    "M15": (
        "materialization/graph_findings.py",
        "materialization/graph_publication.py",
        "materialization/store.py",
        "evidence/",
        "materialization/finding_values.py",
    ),
    "M16": ("__init__.py", "_public.py", "_capabilities/", "introspection/"),
    "M17": (),
    "M18": (),
}
METHODS = {
    "deviation.zscore@v1": ("R8.2", ("fit_inputs", "fit_state")),
    "deviation.mad@v1": ("R8.2", ("fit_inputs", "fit_state")),
    "time.runs@v1": ("R8.3", ("grid_cells", "condition_cells", "run_cells")),
    "association.pearson@v1": ("R8.4", ("pair_inputs", "association_state")),
    "association.spearman@v1": ("R8.4", ("pair_inputs", "association_state")),
    "association.kendall@v1": ("R8.4", ("pair_inputs", "association_state")),
    "forecast.naive@v1": ("R8.4", ("training_inputs", "forecast_state", "future_cells")),
    "forecast.drift@v1": ("R8.4", ("training_inputs", "forecast_state", "future_cells")),
    "forecast.seasonal_naive@v1": ("R8.4", ("training_inputs", "forecast_state", "future_cells")),
}
VS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "V01": (
        "R8.2",
        "future concrete type/identity and zero-I/O oracle",
        (
            "types_positive",
            "types_negative",
            "foreign_session",
            "mixed_closure",
            "same_label_wrong_domain",
            "construct_help_plan_zero_io",
        ),
    ),
    "V02": (
        "R8.2",
        "raw rational mean/population variance and sorted median oracle",
        ("zscore", "mad", "same_values_time_entity_different_identity"),
    ),
    "V03": (
        "R8.2",
        "independent Cell table and original/tag count enumeration",
        (
            "empty",
            "all_nondefined",
            "singleton",
            "constant",
            "mad_zero_fallback",
            "null",
            "undefined",
            "unknown",
            "nonfinite",
            "duplicate_identity",
            "false_coverage",
        ),
    ),
    "V04": (
        "R8.2",
        "raw int/Decimal/binary64-ratio and directed root/quantile oracle",
        (
            "large_nearby_int64",
            "extreme_float",
            "cancellation",
            "subnormal",
            "decimal_scale_precision",
            "half_even",
            "overflow",
            "no_input_float_cast",
        ),
    ),
    "V05": (
        "R8.2",
        "explicit original fit keys and reader/kernel spies",
        (
            "null_category",
            "composite_partition",
            "shuffle",
            "batch",
            "select_after_fit",
            "select_before_fit",
            "explicit_sharing",
            "separate_equal_nodes",
        ),
    ),
    "V06": (
        "R8.3",
        "enumeration of the original complete grid",
        (
            "true_false_true",
            "true_unavailable_true",
            "direct_negative_business_threshold_zero_score",
        ),
    ),
    "V07": (
        "R8.3",
        "independent non-short-circuit condition tree oracle",
        (
            "one_sided",
            "two_sided_opposite_sign",
            "state_predicate",
            "unavailable_sibling",
            "zero_hit",
            "all_unavailable",
            "wrong_unit",
            "wrong_domain",
            "illegal_gap",
        ),
    ),
    "V08": (
        "R8.3",
        "original UTC endpoint/tick and maximal-segment oracle",
        (
            "partial",
            "missing",
            "duplicate",
            "left_edge",
            "right_edge",
            "cross_batch",
            "dst_23h",
            "dst_25h",
            "certified_unequal_period",
            "multiple_series",
            "run_cell_mapping",
        ),
    ),
    "V09": (
        "R8.3",
        "complete grid and real Subject set-image oracle",
        (
            "where_before_runs_reject",
            "where_after_runs_no_resegment",
            "subject_members",
            "global_time_no_members",
            "duration_filter_no_rank_expansion",
        ),
    ),
    "V10": (
        "R8.4",
        "centered sums/average ranks/tau-b pair-count independent oracle",
        (
            "arity_2",
            "arity_3",
            "arity_16",
            "arity_17_reject",
            "duplicate_quantity",
            "wrong_domain",
            "pearson",
            "spearman_ties",
            "kendall_both_ties",
            "mixed_numeric_types",
        ),
    ),
    "V11": (
        "R8.4",
        "explicit coordinate pair enumeration and winning-lag tuple",
        (
            "entity",
            "category",
            "time",
            "category_time",
            "positive_lag",
            "negative_lag",
            "calendar_lag",
            "boundary_drop",
            "null_pairs",
            "insufficient_pairs",
            "constant_a",
            "constant_b",
            "constant_both",
            "invalid_lag_retained",
            "selected_ties",
            "undefined_unknown_reject",
        ),
    ),
    "V12": (
        "R8.4",
        "independent qualified-route comparison and issued-reader audit",
        (
            "native_local_same_vector",
            "no_fallback",
            "bad_coefficient",
            "candidate_4096",
            "candidate_4097",
            "large_input_no_capacity_rejection",
            "preserve_r4_fixed_spearman",
        ),
    ),
    "V13": (
        "R8.4",
        "normative innovation/df/point/variance and independent inverse-normal",
        (
            "naive_nonzero_innovation",
            "seasonal_nonzero_innovation",
            "exact_zero",
            "drift_first_last_slope",
            "drift_parameter_variance",
            "seasonal_multi_horizon",
        ),
    ),
    "V14": (
        "R8.4",
        "original training/future grid and per-series variance enumeration",
        (
            "minimum_history",
            "horizon_1",
            "horizon_1000",
            "horizon_invalid",
            "level_endpoints",
            "bool_level",
            "season_invalid",
            "null_undefined_unknown",
            "duplicate",
            "missing",
            "partial",
            "uncertified_future",
            "no_pooling",
            "bad_df_variance",
            "nonfinite_atomic",
        ),
    ),
    "V15": (
        "R8.5",
        "original/current domain, owned-view and RequiredParts/K oracle",
        (
            "where_views_sync",
            "rank_table",
            "original_scope",
            "shared_views",
            "coefficient_descriptive_only",
            "prediction_descriptive_only",
            "no_original_rollup_attribute",
            "no_interval_sum",
        ),
    ),
    "V16": (
        "R8.6",
        "actual public DAG, original contribution and source-prefix audit",
        (
            "A11_score_members_observe",
            "A11_direct_runs",
            "A11_score_runs",
            "A11_next_round",
            "A11_two_axes_and_joint",
            "A11_multi_correlation",
            "A11_three_forecasts",
            "A02",
            "A04",
            "A07",
            "J1",
            "J2",
            "J3",
            "J4",
        ),
    ),
    "V17": (
        "R8.6",
        "producer/continuation/cold process independence and receipts",
        (
            "source_new_realization",
            "shared_node",
            "fixed_exact_hit",
            "producer_process",
            "fixed_kernel_process",
            "cold_kernel_process",
            "offline_no_semantic_duckdb",
            "transport_not_kernel",
        ),
    ),
    "V18": (
        "R8.6",
        "independent corruption and atomic-failure/resource injection",
        (
            "every_required_part",
            "version",
            "receipt",
            "key",
            "scope",
            "finding_body",
            "finding_digest",
            "finding_order",
            "finding_cap",
            "finding_input",
            "empty_findings",
            "timeout_below",
            "timeout_at",
            "timeout_above",
            "cancel",
            "bad_batch",
            "close",
            "late_result",
            "transaction",
            "committed_success",
        ),
    ),
    "V19": (
        "R8.5",
        "independent public discovery/K/repair and retirement reachability",
        (
            "exports",
            "help_reachability",
            "help_budget",
            "drift",
            "repair",
            "repr_show",
            "cli",
            "api",
            "english_chinese_examples",
            "legacy_import",
            "legacy_registry",
            "legacy_codec",
            "issued_sql",
        ),
    ),
    "V20": (
        "R8.6",
        "same noneditable archive and separate installed processes",
        (
            "wheel_hash_dependencies",
            "site_packages_origin",
            "poisoned_pythonpath",
            "A11_installed",
            "A04_installed",
            "every_new_result_cold",
        ),
    ),
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked(value: object) -> Json:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [checked(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Json] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON keys must be strings")
            result[key] = checked(item)
        return result
    raise ValueError("not a closed JSON value")


def object_json(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def array_json(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return value


def read_json(path: Path) -> dict[str, Json]:
    raw: object = json.loads(path.read_text())
    return object_json(checked(raw))


def encode(value: Json) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(("git", *arguments), cwd=root)


def module_name(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def migration_ids(path: str) -> list[Json]:
    relative = path.removeprefix("marivo/analysis/")
    if path.startswith("tests/"):
        return ["M18"]
    if path.startswith(("docs/", "site/", "marivo/skills/")):
        return ["M17"]
    if path == "marivo/cli.py" or "/introspection/" in path or "/_help/" in path:
        return ["M16"]
    return [
        key for key, prefixes in MIGRATIONS.items() if any(relative.startswith(p) for p in prefixes)
    ]


def imported_module(path: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parent = path.removesuffix(".py").split("/")
    if parent[-1] != "__init__":
        parent.pop()
    prefix = parent[: len(parent) - node.level + 1]
    return ".".join((*prefix, *((node.module or "").split(".") if node.module else ())))


def scan_python(root: Path, path: str) -> dict[str, Json]:
    raw = (root / path).read_bytes()
    source = raw.decode()
    source_lines = source.splitlines(keepends=True)
    tree = ast.parse(source, filename=path)
    symbols: list[Json] = []
    imports: list[Json] = []
    calls: list[Json] = []
    branches: list[Json] = []
    strings: list[Json] = []
    tests: list[Json] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            module = imported_module(path, node)
            for alias in node.names:
                aliases[alias.asname or alias.name] = module + "." + alias.name

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        nested = scope
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = ".".join((*scope, node.name))
            body = "".join(source_lines[node.lineno - 1 : node.end_lineno])
            symbols.append(
                {
                    "symbol": qualified,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "kind": type(node).__name__,
                    "r8_candidate": bool(TOKENS.search(node.name + " " + body)),
                    "body_sha256": digest(body.encode()),
                }
            )
            nested = (*scope, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                assertions = [ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Assert)]
                tests.append(
                    {
                        "node": path + "::" + "::".join((*scope, node.name)),
                        "line": node.lineno,
                        "body_sha256": digest(body.encode()),
                        "r8_candidate": bool(TOKENS.search(path + " " + body)),
                        "assertions": list(assertions),
                    }
                )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [imported_module(path, node)]
            )
            imports.append(
                {
                    "line": node.lineno,
                    "scope": ".".join(scope),
                    "statement": ast.unparse(node),
                    "modules": list(modules),
                    "r8_candidate": bool(TOKENS.search(ast.unparse(node))),
                }
            )
        elif isinstance(node, ast.Call):
            expression = ast.unparse(node.func)
            first, _, rest = expression.partition(".")
            resolved = aliases.get(first, first) + ("." + rest if rest else "")
            if TOKENS.search(expression + " " + resolved) or TOKENS.search(path):
                calls.append(
                    {
                        "line": node.lineno,
                        "scope": ".".join(scope),
                        "expression": expression,
                        "alias_candidate": resolved,
                        "dynamic_receiver_unverified": first not in aliases,
                    }
                )
        elif isinstance(node, ast.If):
            condition = ast.unparse(node.test)
            if TOKENS.search(condition):
                branches.append(
                    {"line": node.lineno, "scope": ".".join(scope), "condition": condition}
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if TOKENS.search(node.value) or re.search(
                r"\bSELECT\b|\bCREATE\s+MACRO\b", node.value, re.I
            ):
                strings.append(
                    {
                        "line": node.lineno,
                        "scope": ".".join(scope),
                        "value": node.value,
                        "sql_candidate": bool(
                            re.search(r"\bSELECT\b|\bCREATE\s+MACRO\b", node.value, re.I)
                        ),
                    }
                )
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(tree, ())
    return {
        "path": path,
        "sha256": digest(raw),
        "bytes": len(raw),
        "migration_ids": migration_ids(path),
        "symbols": symbols,
        "imports": imports,
        "calls": calls,
        "branches": branches,
        "strings": strings,
        "tests": tests,
    }


def legacy_disposition(record: dict[str, Json], test: dict[str, Json]) -> dict[str, Json]:
    path, node = str(record["path"]), str(test["node"])
    assertions = array_json(test["assertions"])
    text = node.lower() + " " + " ".join(str(item).lower() for item in assertions)
    if "driver" in path and any(
        term in text for term in ("concentration", "score", "prefix", "screen", "top")
    ):
        disposition, owner, purpose = (
            "retire_heuristic",
            "R6 attribute",
            "old axis-screening score/prefix is actively withdrawn, not a contribution oracle",
        )
    elif any(
        term in text
        for term in (
            "precision",
            "numeric",
            "median",
            "mad",
            "variance",
            "innovation",
            "constant",
            "null",
            "overflow",
            "lag",
            "corrupt",
            "receipt",
            "schema",
            "duplicate",
            "bound",
            "finite",
            "identity",
            "coverage",
            "tie",
            "zero",
        )
    ):
        disposition = "preserve_oracle"
        owner = "R8.4" if any(term in path for term in ("correlation", "forecast")) else "R8.2/R8.3"
        purpose = "retain the exact numeric/state/key/grid/receipt counterexample; replace old entry and derive expected values from raw facts"
    else:
        disposition = "replace_entry"
        owner = (
            "R8.4"
            if any(term in path for term in ("correlation", "forecast"))
            else "R8.2/R8.3/R8.5"
        )
        purpose = "replace this exact Dataset/Candidate workflow with the owning public Relation composition"
    shared = not any(term in path for term in ("candidate", "driver", "correlation", "forecast"))
    if shared:
        disposition = "preserve_oracle"
        owner = (
            "current shared public/type/disclosure guard; R8.5 updates only statistical assertions"
        )
        purpose = "retain the original shared guard and its nonstatistical assertions; migrate only the bound statistical entry or export expectations"
    return {
        "node": node,
        "line": test["line"],
        "body_sha256": test["body_sha256"],
        "migration_id": "M18",
        "disposition": disposition,
        "replacement_owner": owner,
        "replacement_entry": "same shared test node, with only statistical expectations updated"
        if shared
        else "DifferenceRelation.attribute (explicit axes); obsolete heuristic exits"
        if disposition == "retire_heuristic"
        else "NumericRelation.forecast / ForecastResult"
        if "forecast" in path
        else "NumericRelation.correlate / AssociationResult"
        if "correlation" in path
        else "NumericRelation.deviation/runs and explicit where/rank/members/attribute",
        "remaining_shared_owner": owner
        if shared
        else "no shared owner closed by this static scan; actual caller audit required",
        "purpose": purpose,
        "original_assertions": assertions,
        "independence": "not established by static inventory; new raw-fact oracle required",
        "replacement_evidence": ["V16", "V19"]
        if disposition == "retire_heuristic"
        else ["V19", "V16", "V18"]
        if shared
        else ["V10", "V11", "V12", "V13", "V14"]
        if owner == "R8.4"
        else ["V02", "V03", "V04", "V06", "V07", "V08", "V09", "V16"],
        "deletion_condition": "retain the shared guard; only replace statistical assertions after their public replacement evidence passes"
        if shared
        else "remove only this legacy entry/harness after the retained counterexample passes through the public replacement and its imports are dynamically audited",
        "replacement_execution": "planned",
        "dynamic_unreachability": "unverified",
        "physical_deletion": "not_performed",
    }


def symbol_dispositions(source: list[Json]) -> list[Json]:
    """Bind every statistical symbol candidate to an explicit migration duty."""
    rows: list[Json] = []
    evidence: tuple[str, ...]
    caller_index: dict[str, list[Json]] = {}
    for item in source:
        consumer = object_json(item)
        for call_value in array_json(consumer["calls"]):
            call = object_json(call_value)
            references = {
                str(call["alias_candidate"]),
                module_name(str(consumer["path"])) + "." + str(call["expression"]),
            }
            for reference in references:
                caller_index.setdefault(reference, []).append(
                    {
                        "path": consumer["path"],
                        "line": call["line"],
                        "scope": call["scope"],
                        "dynamic_receiver_unverified": call["dynamic_receiver_unverified"],
                    }
                )
    for item in source:
        record = object_json(item)
        path = str(record["path"])
        groups = array_json(record["migration_ids"])
        if not groups:
            continue
        for value in array_json(record["symbols"]):
            symbol = object_json(value)
            if not symbol["r8_candidate"] and not TOKENS.search(path):
                continue
            name = str(symbol["symbol"])
            identity = module_name(path) + "." + name
            callers = caller_index.get(identity, [])
            dedicated = bool(TOKENS.search(path))
            if "forecast" in path:
                target, owner, evidence = (
                    "NumericRelation.forecast / ForecastResult / normal_residual@v1",
                    "R8.4",
                    ("V04", "V13", "V14", "V17", "V18"),
                )
            elif any(term in path for term in ("association", "correlation", "correlate")):
                target, owner, evidence = (
                    "NumericRelation.correlate / AssociationResult / graph Finding subject",
                    "R8.4",
                    ("V04", "V10", "V11", "V12", "V17", "V18"),
                )
            elif any(term in path for term in ("candidate", "discovery")):
                target, owner, evidence = (
                    "NumericRelation.deviation/runs and explicit where/rank/members/attribute",
                    "R8.2/R8.3/R8.5",
                    ("V02", "V03", "V04", "V06", "V09", "V16", "V19"),
                )
            elif "driver" in path:
                target, owner, evidence = (
                    "R6 explicit-axis DifferenceRelation.attribute; no score/prefix replacement",
                    "R8.5",
                    ("V16", "V19"),
                )
            else:
                target, owner, evidence = (
                    "remaining common graph/Store/Help owner; update only statistical variants",
                    "R8.2/R8.3/R8.4/R8.5",
                    ("V15", "V17", "V18", "V19"),
                )
            factory = path.endswith("operators/forecast_contracts.py") and name.split(".")[0] in (
                "ForecastHorizon",
                "ForecastModel",
                "periods",
                "naive",
                "drift",
                "seasonal_naive",
            )
            rows.append(
                {
                    "id": path + "::" + name,
                    "path": path,
                    "symbol": name,
                    "line": symbol["line"],
                    "body_sha256": symbol["body_sha256"],
                    "migration_ids": groups,
                    "disposition": "preserve_shared"
                    if factory or not dedicated
                    else "replace_entry",
                    "replacement_entry": target,
                    "replacement_owner": owner,
                    "replacement_evidence": list(evidence),
                    "replacement_execution": "planned",
                    "static_import_call_candidates": callers,
                    "remaining_shared_owner": "factory value contract"
                    if factory
                    else "current common owner and every retained actual caller; helper extraction is conditional",
                    "deletion_condition": "retain the symbol and its shared/factory contract; update only owned statistical cases"
                    if factory or not dedicated
                    else "replacement evidence must pass; no issued/submit/Store/codec/import/dynamic caller may remain; shared helpers stay until their last retained caller is closed",
                    "dynamic_unreachability": "unverified",
                    "physical_deletion": "not_performed",
                }
            )
    return rows


def origin_profiles(temporal: bool) -> list[dict[str, Json]]:
    rows: list[dict[str, Json]] = []
    if not temporal:
        return [
            {
                "id": "TABLE-NONE",
                "physical_form": "table",
                "source_precision": "none",
                "time_profile": "none",
            },
            {
                "id": "PARQUET-NONE",
                "physical_form": "parquet",
                "source_precision": "none",
                "time_profile": "none",
            },
        ]
    for zone_id, zone in (("UTC", "UTC"), ("NY", "America/New_York")):
        for form, precisions in (("table", ("us",)), ("parquet", ("s", "ms", "us", "ns"))):
            for precision in precisions:
                rows.append(
                    {
                        "id": form.upper() + "-" + precision.upper() + "-" + zone_id,
                        "physical_form": form,
                        "source_precision": precision,
                        "time_profile": "builtin_day:grid_us:" + zone,
                    }
                )
        for form in ("table", "parquet"):
            rows.append(
                {
                    "id": form.upper() + "-CAL-" + zone_id,
                    "physical_form": form,
                    "source_precision": "us",
                    "time_profile": "certified_unequal:grid_us:" + zone,
                }
            )
    return rows


def requirement(
    method: str,
    types: tuple[str, ...],
    domain: str,
    origin: dict[str, Json],
    phase: str,
    suffix: str,
    v: str,
    oracle: str,
    key_profile: str = "default",
) -> dict[str, Json]:
    package, roles = METHODS[method]
    native = phase == "N"
    route = "ibis" if native else "ibis_python" if phase == "S" else "artifact_python"
    proof = {
        "S": "source_kernel",
        "F": "fixed_kernel",
        "C": "fresh_process_source_offline_kernel",
        "N": "native_source_kernel",
    }[phase]
    type_id = "_".join(t.replace("(", "P").replace(",", "S").replace(")", "") for t in types)
    key = method.removesuffix("@v1").replace(".", "-")
    domain_kind = {
        "scalar": "singleton",
        "entity": "entity",
        "category": "group",
        "time": "group",
        "category_time": "group",
        "entity_time": "entity",
    }[domain]
    if key_profile == "default":
        key_profile = (
            "composite(string,int64)"
            if domain == "entity"
            else "time_cell:string"
            if domain == "time"
            else "typed_complete_tuple"
        )
    parts = [*roles, "finding_policy"]
    if "time" in domain:
        parts.append("grid_cells")
    if (method.startswith("deviation") and domain in ("entity", "entity_time")) or (
        method == "time.runs@v1" and domain == "entity_time"
    ):
        parts.append("subject_map")
    time_shape: dict[str, Json] = (
        {
            "kind": "instant",
            "unit": origin["source_precision"],
            "timezone": str(origin["time_profile"]).split(":")[-1],
        }
        if "time" in domain
        else {"kind": "NoTime"}
    )
    shape: dict[str, Json] = {"kind": "FixedShape", "time": time_shape}
    if phase in ("S", "N"):
        shape = {
            "kind": "SourceShape",
            "backend": "duckdb",
            "form": origin["physical_form"],
            "table_kind": "native" if origin["physical_form"] == "table" else "parquet",
            "time": time_shape,
        }
    return {
        "id": f"Q-R8-{suffix}-{key}-{type_id}-{domain}-{origin['id']}-{phase}",
        "method": method,
        "state_version": "v1",
        "numeric_policy": "r8_numeric_v1",
        "precision_contract": "exact" if method == "time.runs@v1" else "certified_statistical",
        "input_types": list(types),
        "domain": domain,
        "input_domains": [domain_kind] * len(types),
        "key_profile": key_profile,
        "required_parts": list(dict.fromkeys(parts)),
        "implementation_contract_version": "v1",
        "implementation_id": f"r8-{key}-{route}-v1",
        "qualification_key": {
            "method": method,
            "input_types": list(types),
            "input_domains": [domain_kind] * len(types),
            "shape": shape,
            "route": route,
        },
        "route": route,
        "backend": "duckdb",
        "physical_form": origin["physical_form"],
        "source_precision": origin["source_precision"],
        "time_profile": origin["time_profile"],
        "origin_profile": origin["id"],
        "proof_class": proof,
        "required": True,
        "responsibility": package,
        "V": v,
        "oracle": oracle,
        "status": "blocked" if native else "planned",
        "blocked_reason": "exact expanded native contract/retained-input/numeric qualification not registered"
        if native
        else None,
    }


def qualification_targets() -> list[Json]:
    rows: list[Json] = []
    types = (
        "int64",
        "float64",
        "decimal(9,2)",
        "decimal(18,6)",
        "decimal(38,0)",
        "decimal(38,6)",
        "decimal(38,18)",
        "decimal(38,38)",
    )
    domains = ("scalar", "entity", "category", "time", "category_time", "entity_time")
    admitted_domains: tuple[str, ...]
    inputs: list[tuple[str, ...]]
    origins: list[dict[str, Json]]
    for method in METHODS:
        if method.startswith("association"):
            admitted_domains = ("entity", "category", "time", "category_time")
            inputs = [(kind, kind) for kind in types]
            inputs.extend(
                (left, right)
                for left in ("int64", "float64", "decimal(38,6)")
                for right in ("int64", "float64", "decimal(38,6)")
                if left != right
            )
            inputs.extend((("decimal(9,2)", "decimal(38,18)"), ("decimal(38,18)", "decimal(9,2)")))
            v = "V10"
        elif method.startswith("forecast"):
            admitted_domains, inputs, v = (
                ("time", "category_time"),
                [(kind,) for kind in types],
                "V13",
            )
        elif method == "time.runs@v1":
            admitted_domains, inputs, v = (
                ("time", "category_time", "entity_time"),
                [(kind,) for kind in types],
                "V08",
            )
        else:
            admitted_domains, inputs, v = domains, [(kind,) for kind in types], "V02"
        for domain in admitted_domains:
            for input_types in inputs:
                for origin in origin_profiles("time" in domain):
                    for phase in ("S", "F", "C"):
                        key_profiles = (
                            (("KS", "string"), ("KI", "int64"), ("KC", "composite(string,int64)"))
                            if domain in ("entity", "entity_time")
                            else (
                                (
                                    "KT",
                                    "time_cell:string"
                                    if domain == "time"
                                    else "typed_complete_tuple",
                                ),
                            )
                        )
                        for key_id, key_profile in key_profiles:
                            rows.append(
                                requirement(
                                    method,
                                    input_types,
                                    domain,
                                    origin,
                                    phase,
                                    "INTEGRATED-" + key_id,
                                    v,
                                    VS[v][1],
                                    key_profile,
                                )
                            )
        if method in ("association.pearson@v1", "association.spearman@v1"):
            for domain in admitted_domains:
                for left in ("int64", "float64"):
                    for right in ("int64", "float64"):
                        for origin in origin_profiles("time" in domain):
                            rows.append(
                                requirement(
                                    method,
                                    (left, right),
                                    domain,
                                    origin,
                                    "N",
                                    "NATIVE",
                                    "V12",
                                    VS["V12"][1],
                                )
                            )
        if method != "time.runs@v1":
            domain = "time" if method.startswith("forecast") else "entity"
            origins = [
                {
                    "id": "TABLE-US-UTC" if domain == "time" else "TABLE-NONE",
                    "physical_form": "table",
                    "source_precision": "us" if domain == "time" else "none",
                    "time_profile": "builtin_day:grid_us:UTC" if domain == "time" else "none",
                },
                {
                    "id": "PARQUET-US-UTC" if domain == "time" else "PARQUET-NONE",
                    "physical_form": "parquet",
                    "source_precision": "us" if domain == "time" else "none",
                    "time_profile": "builtin_day:grid_us:UTC" if domain == "time" else "none",
                },
            ]
            for precision in range(1, 39):
                for scale in range(precision + 1):
                    kind = f"decimal({precision},{scale})"
                    operands = (kind, kind) if method.startswith("association") else (kind,)
                    for origin in origins:
                        for phase in ("S", "F", "C"):
                            rows.append(
                                requirement(
                                    method,
                                    operands,
                                    domain,
                                    origin,
                                    phase,
                                    "DECIMAL-LAW",
                                    "V04",
                                    VS["V04"][1],
                                )
                            )
    return rows


def obligation_targets() -> list[Json]:
    rows: list[Json] = []
    inputs: tuple[str, ...]
    for v, (package, oracle, scenarios) in VS.items():
        methods = list(METHODS)
        if v in ("V02", "V03", "V05"):
            methods = [m for m in METHODS if m.startswith("deviation")]
        elif v in ("V06", "V07", "V08", "V09"):
            methods = ["time.runs@v1"]
        elif v in ("V10", "V11", "V12"):
            methods = [m for m in METHODS if m.startswith("association")]
        elif v in ("V13", "V14"):
            methods = [m for m in METHODS if m.startswith("forecast")]
        for method in methods:
            for scenario in scenarios:
                if v == "V16":
                    allowed = {
                        "A11_score_members_observe": "deviation",
                        "A11_direct_runs": "time.runs",
                        "A11_next_round": "time.runs",
                        "A11_two_axes_and_joint": "deviation",
                        "A11_multi_correlation": "association",
                        "A11_three_forecasts": "forecast",
                        "A04": "association.spearman",
                        "J4": "association.spearman",
                    }
                    if scenario in allowed and not method.startswith(allowed[scenario]):
                        continue
                    if scenario == "A11_score_runs" and not method.startswith(
                        ("deviation", "time.runs")
                    ):
                        continue
                if v == "V15":
                    if scenario == "coefficient_descriptive_only" and not method.startswith(
                        "association"
                    ):
                        continue
                    if scenario in (
                        "prediction_descriptive_only",
                        "no_interval_sum",
                    ) and not method.startswith("forecast"):
                        continue
                domain = (
                    "time"
                    if method.startswith("forecast") or method == "time.runs@v1"
                    else "entity"
                )
                if scenario in (
                    "time",
                    "positive_lag",
                    "negative_lag",
                    "calendar_lag",
                    "boundary_drop",
                    "invalid_lag_retained",
                    "same_values_time_entity_different_identity",
                    "A11_score_runs",
                ):
                    domain = "time"
                elif scenario == "category":
                    domain = "category"
                elif scenario in (
                    "category_time",
                    "multiple_series",
                    "no_pooling",
                    "A11_multi_correlation",
                    "A11_three_forecasts",
                ):
                    domain = "category_time"
                elif scenario in ("subject_members", "A11_next_round"):
                    domain = "entity_time"
                temporal = "time" in domain
                zone = "America/New_York" if scenario in ("dst_23h", "dst_25h") else "UTC"
                profile = (
                    "certified_unequal"
                    if scenario in ("calendar_lag", "certified_unequal_period")
                    else "builtin_day"
                )
                origin: dict[str, Json] = {
                    "id": "TABLE-US-" + zone.replace("/", "-") + "-" + profile
                    if temporal
                    else "TABLE-NONE",
                    "physical_form": "table",
                    "source_precision": "us" if temporal else "none",
                    "time_profile": profile + ":grid_us:" + zone if temporal else "none",
                }
                inputs = ("int64", "int64") if method.startswith("association") else ("int64",)
                if scenario in ("arity_3", "A11_multi_correlation"):
                    inputs = ("int64", "float64", "decimal(38,6)")
                elif scenario in ("arity_16", "arity_17_reject"):
                    inputs = ("int64",) * (16 if scenario == "arity_16" else 17)
                elif scenario == "mixed_numeric_types":
                    inputs = ("int64", "decimal(38,6)")
                phases = (
                    ("S", "F", "C")
                    if v == "V16"
                    else ("C",)
                    if v == "V20"
                    else ("F",)
                    if scenario
                    in ("fixed_exact_hit", "fixed_kernel_process", "preserve_r4_fixed_spearman")
                    else ("C",)
                    if scenario in ("cold_kernel_process", "offline_no_semantic_duckdb")
                    else ("S",)
                )
                for phase in phases:
                    row = requirement(
                        method, inputs, domain, origin, phase, v + "-" + scenario, v, oracle
                    )
                    row.update(
                        {
                            "responsibility": package,
                            "scenario": scenario,
                            "proof_class": "installed_wheel"
                            if v == "V20"
                            else "future_typing_target"
                            if scenario in ("types_positive", "types_negative")
                            else "public_runtime_obligation",
                            "kernel_proof_class": row["proof_class"],
                            "assertion_scope": scenario,
                            "execution_command": None,
                        }
                    )
                    rows.append(row)
    return rows


def prepared_journey_targets() -> list[Json]:
    """Keep the time/Decimal prepared-observation gaps mandatory and visible."""
    rows: list[Json] = []
    for method in ("deviation.zscore@v1", "deviation.mad@v1"):
        for kind in (
            "int64",
            "float64",
            "decimal(9,2)",
            "decimal(18,6)",
            "decimal(38,0)",
            "decimal(38,6)",
            "decimal(38,18)",
            "decimal(38,38)",
        ):
            for domain in ("entity", "entity_time"):
                for key_id, key_profile in (
                    ("KS", "string"),
                    ("KI", "int64"),
                    ("KC", "composite(string,int64)"),
                ):
                    for origin in origin_profiles(domain == "entity_time"):
                        for phase in ("S", "F", "C"):
                            row = requirement(
                                method,
                                (kind,),
                                domain,
                                origin,
                                phase,
                                "F11-" + key_id,
                                "V16",
                                VS["V16"][1],
                                key_profile,
                            )
                            row.update(
                                {
                                    "scenario": "A11_score_members_observe",
                                    "responsibility": "R8.2",
                                    "proof_class": "public_runtime_obligation",
                                    "kernel_proof_class": row["proof_class"],
                                    "downstream_observation": {
                                        "operation": "observe",
                                        "reduction": "sum",
                                        "value_type": kind,
                                        "grid_window": domain == "entity_time",
                                    },
                                    "downstream_required_facts": [
                                        "original Subject support",
                                        "captured contributions",
                                        "path/version facts",
                                        "time envelope",
                                        "original components",
                                        "full typed member keys",
                                    ],
                                    "execution_command": None,
                                }
                            )
                            if domain == "entity_time" or kind.startswith("decimal"):
                                row["status"] = "blocked"
                                row["blocked_reason"] = (
                                    "current prepared observation rejects grid_window or Decimal amount_type; complete preparation/transport/kernel must connect before qualification"
                                )
                            rows.append(row)
    return rows


def probes(root: Path) -> dict[str, Json]:
    code = """import importlib.metadata as metadata
import inspect
import json
import marivo.analysis as mv
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.operators.registry import legacy_source_migration_stage
names = ("LogicalDeviationResult", "MaterializedDeviationResult", "LogicalTimeRunResult", "MaterializedTimeRunResult", "LogicalForecastResult", "MaterializedForecastResult", "LogicalCoefficientRelation")
classes = ("LogicalNumericRelation", "MaterializedNumericRelation", "LogicalDifferenceRelation", "MaterializedDifferenceRelation", "LogicalAssociationResult", "MaterializedAssociationResult")
print(json.dumps({"exports": list(mv.__all__), "current_correlate_signatures": {name: str(inspect.signature(getattr(mv, name).correlate)) for name in ("LogicalNumericRelation", "MaterializedNumericRelation")}, "target_symbols": {name: hasattr(mv, name) for name in names}, "methods": {name: {method: hasattr(getattr(mv, name), method) for method in ("deviation", "runs", "correlate", "forecast")} for name in classes}, "help_targets": list(REGISTRY.canonical_ids()), "legacy_stage": {name: legacy_source_migration_stage(name) for name in ("metric.correlate", "metric.forecast", "candidate.where")}, "versions": {name: metadata.version(name) for name in ("ibis-framework", "duckdb", "pyarrow", "pandas", "numpy", "scipy")}}))"""
    completed = subprocess.run(
        (sys.executable, "-c", code), cwd=root, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    value: object = json.loads(completed.stdout)
    return {
        "proof_class": "import_only_no_session_run_or_business_io",
        "exit_code": completed.returncode,
        "command": [".venv/bin/python", "-c", code],
        "result": checked(value),
    }


def collect(root: Path, baseline: Path) -> dict[str, Json]:
    tracked = {name for name in git(root, "ls-files", "-z").decode().split("\0") if name}
    working = {
        name
        for name in git(root, "ls-files", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
        if name
    }
    paths = sorted(tracked | working)
    source: list[Json] = []
    test_files: list[Json] = []
    disclosure: list[Json] = []
    dispositions: list[Json] = []
    fixture_workers: list[Json] = []
    for path in paths:
        file = root / path
        if not file.is_file():
            continue
        if path.endswith(".py") and path.startswith(("marivo/", "tests/")):
            record = scan_python(root, path)
            if path.startswith("marivo/"):
                source.append(record)
            else:
                test_files.append(record)
                relevant = [
                    object_json(t)
                    for t in array_json(record["tests"])
                    if object_json(t)["r8_candidate"]
                ]
                if not path.endswith("test_analysis_contract_freeze_r81.py"):
                    dispositions.extend(legacy_disposition(record, test) for test in relevant)
                if TOKENS.search(path) and not relevant:
                    fixture_workers.append(
                        {
                            "path": path,
                            "sha256": record["sha256"],
                            "migration_id": "M18",
                            "disposition": "replace_harness_after_public_oracle",
                            "replacement_entry": "public Relation/Result source/fixed/cold harness for the same bound statistical family",
                            "replacement_owner": "R8.2/R8.3/R8.4/R8.6",
                            "replacement_evidence": ["V16", "V17", "V18", "V20"],
                            "deletion_condition": "replacement raw-fact oracle must pass; retain all shared imports/helpers until their last actual consumer closes",
                            "symbols": record["symbols"],
                            "imports": record["imports"],
                            "remaining_shared_owner": "inspect actual import candidates; no filename deletion",
                            "replacement_execution": "planned",
                            "dynamic_unreachability": "unverified",
                            "physical_deletion": "not_performed",
                        }
                    )
        elif path.startswith(("docs/api/", "marivo/skills/")) or (
            path.startswith("site/src/content/docs/") and "/latest/" in path
        ):
            if file.suffix not in (".md", ".mdx", ".rst", ".py"):
                continue
            raw = file.read_bytes()
            lines: list[Json] = [
                {"line": i, "text": line}
                for i, line in enumerate(raw.decode().splitlines(), 1)
                if TOKENS.search(line)
            ]
            disclosure.append(
                {
                    "path": path,
                    "sha256": digest(raw),
                    "migration_ids": migration_ids(path),
                    "matches": lines,
                }
            )
    baseline_data = read_json(baseline)
    owner_paths = sorted(
        {str(name).partition("#")[0] for name in OWNERS.values()}
        | {
            TIME.partition("#")[0],
            PLAN,
            LEDGER,
            SPECS + "2026-09-01-lazy-analysis-typed-operators-design.md",
            SPECS + "2026-09-24-marivo-semantic-analysis-dsl-interface-design.md",
            SPECS + "2026-09-24-marivo-analysis-dsl-architecture-design.md",
            SPECS + "2026-09-26-marivo-full-refactor-r0-capability-ledger.md",
            SPECS + "2026-09-26-marivo-full-refactor-r0-sql-ledger.md",
        }
    )
    owner_inputs: dict[str, Json] = {
        path: digest((root / path).read_bytes()) for path in owner_paths
    }
    requirements = qualification_targets() + obligation_targets() + prepared_journey_targets()
    ids = [str(object_json(row)["id"]) for row in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate immutable requirement ID")
    payload: dict[str, Json] = {
        "source_files": source,
        "symbol_dispositions": symbol_dispositions(source),
        "test_files": test_files,
        "disclosure_files": disclosure,
        "legacy_test_dispositions": dispositions,
        "fixture_worker_dispositions": fixture_workers,
        "requirements": requirements,
        "V_obligations": {
            key: {"owner": value[0], "oracle": value[1], "scenarios": list(value[2])}
            for key, value in VS.items()
        },
        "migration_groups": {key: list(value) for key, value in MIGRATIONS.items()},
        "static_limitations": [
            "AST definitions are not parametrized pytest collection",
            "file-wide import alias resolution is a candidate, not dynamic call proof",
            "no new Runtime/backend/wheel/Agent qualification",
            "positive integrated profiles and full Decimal numeric-law sweep are distinct; no unlisted Cartesian combination is qualified",
        ],
    }
    raw = encode(payload)
    compressed = zlib.compress(raw, 9)
    data_dir = root / SNAPSHOT_DATA
    data_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Json] = []
    for index, offset in enumerate(range(0, len(compressed), CHUNK_BYTES)):
        part = compressed[offset : offset + CHUNK_BYTES]
        relative_path = f"{SNAPSHOT_DATA}/part-{index:03}.bin"
        (root / relative_path).write_bytes(part)
        chunks.append({"path": relative_path, "bytes": len(part), "sha256": digest(part)})
    for stale in data_dir.glob("part-*.bin"):
        if stale.name not in {Path(str(object_json(item)["path"])).name for item in chunks}:
            stale.unlink()
    return {
        "schema": "marivo.r81.static_freeze/v1",
        "status": "documentation_static_freeze_complete",
        "baseline": baseline_data,
        "capture_head": git(root, "rev-parse", "HEAD").decode().strip(),
        "owner_inputs": owner_inputs,
        "decision_owners": dict(OWNERS),
        "support_owners": {"temporal": TIME},
        "collector": {
            "path": "scripts/r81_static_freeze.py",
            "sha256": digest((root / "scripts/r81_static_freeze.py").read_bytes()),
            "python_version": sys.version,
        },
        "counts": {
            "source_files": len(source),
            "symbol_dispositions": len(array_json(payload["symbol_dispositions"])),
            "test_files": len(test_files),
            "disclosure_files": len(disclosure),
            "legacy_test_dispositions": len(dispositions),
            "fixture_worker_dispositions": len(fixture_workers),
            "requirements": len(requirements),
            "planned": sum(object_json(r)["status"] == "planned" for r in requirements),
            "blocked": sum(object_json(r)["status"] == "blocked" for r in requirements),
            "executed": 0,
            "qualified": 0,
        },
        "probes": probes(root),
        "inventory_payload": {
            "encoding": "zlib_chunks_v1",
            "compressed_bytes": len(compressed),
            "chunks": chunks,
            "uncompressed_bytes": len(raw),
            "sha256_uncompressed": digest(raw),
        },
    }


def decode_payload(root: Path, snapshot: dict[str, Json]) -> dict[str, Json]:
    payload = object_json(snapshot["inventory_payload"])
    compressed_parts: list[bytes] = []
    for item in array_json(payload["chunks"]):
        record = object_json(item)
        path = (root / str(record["path"])).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("snapshot chunk path escapes the project root")
        part = path.read_bytes()
        if len(part) != record["bytes"] or digest(part) != record["sha256"]:
            raise ValueError("snapshot chunk digest/length mismatch")
        compressed_parts.append(part)
    compressed = b"".join(compressed_parts)
    if len(compressed) != payload["compressed_bytes"]:
        raise ValueError("snapshot compressed length mismatch")
    raw = zlib.decompress(compressed)
    if len(raw) != payload["uncompressed_bytes"] or digest(raw) != payload["sha256_uncompressed"]:
        raise ValueError("snapshot payload digest/length mismatch")
    value: object = json.loads(raw)
    return object_json(checked(value))


def verify_current(root: Path, snapshot: dict[str, Json]) -> None:
    inventory = decode_payload(root, snapshot)
    for group in ("source_files", "test_files", "disclosure_files"):
        for item in array_json(inventory[group]):
            record = object_json(item)
            path = root / str(record["path"])
            if not path.is_file() or digest(path.read_bytes()) != record["sha256"]:
                raise ValueError("current input differs: " + str(record["path"]))
    for owner_path, expected in object_json(snapshot["owner_inputs"]).items():
        if digest((root / owner_path).read_bytes()) != expected:
            raise ValueError("current owner differs: " + owner_path)
    collector = object_json(snapshot["collector"])
    if digest((root / str(collector["path"])).read_bytes()) != collector["sha256"]:
        raise ValueError("current collector differs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=Path(SNAPSHOT))
    parser.add_argument("--verify-current", action="store_true")
    arguments = parser.parse_args()
    root: Path = arguments.root.resolve()
    output: Path = arguments.output
    if arguments.verify_current:
        verify_current(root, read_json(output))
        print("R8.1 captured input hashes verified; static evidence only")
        return
    baseline: Path | None = arguments.baseline
    if baseline is None:
        parser.error("--baseline is required when capturing the freeze")
    snapshot = collect(root, baseline)
    output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(json.dumps(snapshot["counts"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
