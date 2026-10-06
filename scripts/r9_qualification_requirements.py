"""Freeze R9 targets and verify static evidence without opening a datasource."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sysconfig
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TypeAlias

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
ROOT = Path(__file__).resolve().parents[1]
SPECS = "docs/superpowers/specs/"
OUTPUT = ROOT / SPECS / "2026-10-04-marivo-r9-evidence"
SEED = SPECS + "2026-10-04-marivo-r86-evidence/r9-targets.json.gz"
CAPABILITY = SPECS + "2026-09-26-marivo-full-refactor-r0-capability-ledger.md"
SQL = SPECS + "2026-09-26-marivo-full-refactor-r0-sql-ledger.md"
PLAN = SPECS + "2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md"
PROFILES = {
    "duckdb": (
        "table",
        "view",
        "csv",
        "parquet",
        "local-json",
        "http-json-public",
        "http-json-auth",
    ),
    "postgres": ("table", "view", "namespace-table", "namespace-view"),
    "mysql": ("innodb-table", "view"),
    "sqlite": ("main-table", "main-view"),
    "trino": ("iceberg", "non-iceberg"),
    "clickhouse": ("mergetree", "distributed"),
}
SHARD_BYTES = 512 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Json) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def checked(value: object) -> Json:
    if value is None or isinstance(value, (str, bool, int, float)):
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
    raise ValueError("invalid JSON value")


def obj(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def arr(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return value


def read(path: Path) -> dict[str, Json]:
    return obj(checked(json.loads(path.read_bytes())))


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def owner_paths() -> list[str]:
    paths = {
        CAPABILITY,
        SQL,
        PLAN,
        SEED,
        "pyproject.toml",
        "uv.lock",
        SPECS + "2026-09-26-marivo-full-refactor-acceptance.md",
    }
    paths.update(
        str(p.relative_to(ROOT)) for p in (ROOT / SPECS).glob("*r[1-8]*implementation-plan.md")
    )
    paths.update(
        str(p.relative_to(ROOT)) for p in (ROOT / SPECS).glob("*r[5-8]*migration-ledger.md")
    )
    paths.update(
        [
            "docs/specs/semantic/datasource-layer.md",
            "docs/specs/semantic/semantic-object-model.md",
            "docs/specs/analysis/python-analysis-design.md",
            "docs/specs/analysis/operators-and-frames.md",
            "docs/specs/analysis/session-state-and-runtime.md",
            "docs/specs/analysis/timezone-and-calendar-design.md",
            "tests/multisource_environment/README.md",
            "tests/multisource_environment/manage.sh",
        ]
    )
    return sorted(p for p in paths if (ROOT / p).is_file())


def candidate() -> dict[str, Json]:
    files = sorted(
        {
            str(p.relative_to(ROOT))
            for directory in ("marivo", "scripts", "tests")
            for p in (ROOT / directory).rglob("*.py")
        }
    )
    hashes: dict[str, Json] = {p: digest((ROOT / p).read_bytes()) for p in files}
    dependencies: dict[str, Json] = {}
    installed = {
        distribution.metadata["Name"].lower(): distribution.version
        for distribution in importlib.metadata.distributions(path=[sysconfig.get_path("purelib")])
        if distribution.metadata["Name"]
    }
    for name in (
        "marivo",
        "ibis-framework",
        "duckdb",
        "pyarrow",
        "pandas",
        "numpy",
        "scipy",
        "pytest",
    ):
        dependencies[name] = installed.get(name, "not-installed")
    return {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "branch": git("branch", "--show-current").decode().strip(),
        "dirty_diff_sha256": digest(
            git("diff", "--binary", "HEAD", "--", "marivo", "scripts", "tests")
        ),
        "untracked": {
            p: digest((ROOT / p).read_bytes())
            for p in git(
                "ls-files", "--others", "--exclude-standard", "--", "marivo", "scripts", "tests"
            )
            .decode()
            .splitlines()
            if (ROOT / p).is_file()
        },
        "files": hashes,
        "content_sha256": digest(encode(hashes)),
        "dependencies": dependencies,
    }


def source_rows(paths: list[str]) -> dict[str, Json]:
    records: dict[str, Json] = {}
    for path in paths:
        if not path.endswith(".md"):
            continue
        for line, text in enumerate((ROOT / path).read_text().splitlines(), 1):
            if not text.startswith("| ") or re.match(r"\| [-:]", text):
                continue
            key = f"{path}:{line}:{digest(text.encode())[:16]}"
            records[key] = {
                "path": path,
                "line": line,
                "sha256": digest(text.encode()),
                "capabilities": sorted(set(re.findall(r"C\d{2}(?:\.[a-z]\d?)?", text))),
            }
    return records


def serial(value: object) -> Json:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: serial(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [serial(item) for item in value]
    return checked(value)


def consumers() -> dict[str, Json]:
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.semantics import CONNECTED_METHODS

    methods: dict[str, Json] = {}
    for semantics in CONNECTED_METHODS:
        methods[str(semantics.key)] = {
            "semantics": serial(semantics),
            "implementations": serial(implementations(semantics.key)),
            "authority": "static declaration only; does not grant R9 runtime qualification",
        }
    nodes: dict[str, Json] = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text())
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        if names:
            nodes[str(path.relative_to(ROOT))] = checked(names)
    return {
        "methods": methods,
        "test_functions": nodes,
        "binding": "Test function inventory is not proof of per-requirement assertions",
    }


def build() -> dict[str, Json]:
    owners = owner_paths()
    seeds = obj(checked(json.loads(gzip.decompress((ROOT / SEED).read_bytes()))))
    profiles = obj(seeds["profiles"])
    if len(profiles) != 9109 or seeds["status"] != "unverified":
        raise ValueError("R8.6 seed authority changed; retain an explicit scope revision")
    for key, value in profiles.items():
        if digest(encode(value)) != key:
            raise ValueError("seed identity mismatch")
    origins = source_rows(owners)
    requirements: list[Json] = []
    mappings: dict[str, Json] = {}

    def add(
        family: str,
        backend: str,
        profile: str,
        route: str,
        scenario: str,
        owner: str,
        proofs: tuple[str, ...],
        expectation: str = "success",
    ) -> str:
        identity = f"R9:{family}:{backend}:{profile}:{route}:{scenario}"
        requirements.append(
            {
                "id": identity,
                "family": family,
                "backend": backend,
                "profile": profile,
                "route": route,
                "scenario": scenario,
                "expectation": expectation,
                "gap_owner": owner,
                "required_proofs": list(proofs),
                "execution_binding": "unverified: bind an actual consumer, exact test/oracle/environment",
            }
        )
        return identity

    # Algorithm risks are verified once on complete retained inputs. Backend capture
    # is separate, rather than multiplying every numerical risk by every physical form.
    method_targets: dict[str, list[str]] = {}
    for method in sorted({str(obj(value)["method"]) for value in profiles.values()}):
        ids = []
        for backend in PROFILES:
            ids.append(
                add(
                    method,
                    backend,
                    "ordinary-table",
                    "ibis_python",
                    "complete-source-capture",
                    "R9.3",
                    ("submission", "numeric_state", "type_domain_parts", "resource_cancel"),
                )
            )
        for scenario in (
            "int64-near-extremes",
            "finite-float64",
            "decimal-exact",
            "complete-identity-domain",
            "empty-null-unavailable",
        ):
            ids.append(
                add(
                    method,
                    "none",
                    "retained-input",
                    "artifact_python",
                    scenario,
                    "R9.3",
                    ("numeric_state", "type_domain_parts"),
                )
            )
        if method.startswith("deviation."):
            risks = ("original-fit-domain", "zero-scale", "decimal-center-scale-extremes")
        elif method == "time.runs@v1":
            risks = (
                "full-grid-dst-adjacency-duration",
                "cross-batch-long-run",
                "unavailable-breaks",
            )
        elif method.startswith("association."):
            risks = ("complete-pair-lag-order", "invalid-constant-pairs", "ties-average-rank-tau-b")
            if method == "association.pearson@v1":
                risks = (*risks[:2], "centered-numeric-extremes")
        else:
            risks = ("complete-training", "future-grid-interval", "model-innovation-variance")
        for scenario in risks:
            ids.append(
                add(
                    method,
                    "none",
                    "retained-input",
                    "artifact_python",
                    scenario,
                    "R9.3",
                    ("numeric_state", "type_domain_parts"),
                )
            )
        ids.append(
            add(
                method,
                "none",
                "producer-bound-receipt",
                "artifact_python",
                "continue-cold-exact-hit",
                "R9.4",
                ("numeric_state", "type_domain_parts", "publication_recovery"),
            )
        )
        method_targets[method] = ids
    for key, value in profiles.items():
        mappings[key] = list(method_targets[str(obj(value)["method"])])

    # R0 still owns native/preparation routes. Each family has six source baselines
    # and a single fixed goal; profile differences are independently checked below.
    capability_targets: dict[str, list[str]] = {}
    sql_lines = (ROOT / SQL).read_text().splitlines()
    for origin, raw in origins.items():
        record = obj(raw)
        if record["path"] != SQL:
            continue
        cells = sql_lines[int(str(record["line"])) - 1].split("|")[1:-1]
        if len(cells) != 7 or not cells[0].strip().startswith("C"):
            continue
        family = cells[0].strip().split()[0]
        if family.startswith("C14"):
            names = [
                name
                for name in method_targets
                if (".a1" in family and name.startswith("deviation."))
                or (".a2" in family and name == "time.runs@v1")
                or (".b" in family and name.startswith("association."))
                or (".c" in family and name.startswith("forecast."))
            ]
            ids = [identity for name in names for identity in method_targets[name]]
        elif family.startswith("C16"):
            ids = [
                add(
                    "C16-C17",
                    "none",
                    "current-disclosure",
                    "static",
                    "affected-help-errors-cli-dependencies",
                    "R9.7",
                    ("disclosure",),
                )
            ]
        else:
            ids = []
            has_fixed = False
            for backend, cell in zip(PROFILES, cells[1:], strict=True):
                tokens = re.split(r"[^IPFT/+]+", cell.strip())[0]
                has_fixed = has_fixed or "F" in tokens
                for token, route in (("I", "ibis"), ("P", "ibis_python"), ("T", "terminal")):
                    if token in tokens:
                        ids.append(
                            add(
                                family,
                                backend,
                                "ordinary-table",
                                route,
                                "representative-positive-and-data-checks",
                                "R9.3",
                                (
                                    "submission",
                                    "numeric_state",
                                    "type_domain_parts",
                                    "resource_cancel",
                                ),
                            )
                        )
            if has_fixed:
                ids.append(
                    add(
                        family,
                        "none",
                        "producer-bound-receipt",
                        "artifact_python",
                        "continue-cold-exact-hit",
                        "R9.4",
                        ("numeric_state", "type_domain_parts", "publication_recovery"),
                    )
                )
        mappings[origin] = checked(ids)
        for capability in arr(record["capabilities"]):
            capability_targets.setdefault(str(capability), []).extend(ids)
    for origin, raw in origins.items():
        if origin in mappings:
            obj(raw)["disposition"] = "matrix-authority"
            continue
        record = obj(raw)
        ids = sorted(
            {
                identity
                for cap in arr(record["capabilities"])
                for key, targets in capability_targets.items()
                if key.split(".")[0] == str(cap).split(".")[0]
                for identity in targets
            }
        )
        mappings[origin] = checked(ids)
        record["disposition"] = "capability-reference" if ids else "historical-owner-reference"
        record["note"] = "Traceability only; historical rows do not add acceptance goals"

    for backend, forms in PROFILES.items():
        for form in forms:
            identity = add(
                "source-profile",
                backend,
                form,
                "ibis",
                "metadata-read-decode-range-empty-close-fault",
                "R9.2",
                ("submission", "type_domain_parts", "resource_cancel"),
            )
            mappings[f"profile:{backend}:{form}"] = [identity]
        for risk in (
            "int64-overflow-nullable",
            "decimal-precision-scale",
            "native-parsed-time-dst",
            "composite-versioned-identity",
        ):
            identity = add(
                "source-types",
                backend,
                "ordinary-table",
                "ibis",
                risk,
                "R9.2",
                ("submission", "type_domain_parts", "resource_cancel"),
                "rejection"
                if backend == "sqlite" and risk == "decimal-precision-scale"
                else "success",
            )
            mappings[f"source-risk:{backend}:{risk}"] = [identity]
    for prefix, limit in (("DS", 22), ("AN", 33)):
        for number in range(1, limit + 1):
            origin = f"{prefix}{number:02}"
            mappings[origin] = [
                add(
                    origin,
                    "all",
                    "sql-owner",
                    "audit",
                    "classification-reachability-submission",
                    "R9.5",
                    ("submission", "disclosure"),
                )
            ]
    for origin, raw in origins.items():
        record = obj(raw)
        if record["path"] == SQL:
            line = sql_lines[int(str(record["line"])) - 1]
            match = re.match(r"\| ((?:DS|AN)\d{2}) \|", line)
            if match:
                mappings[origin] = mappings[match.group(1)]
                record["disposition"] = "sql-authority"
    for number in range(1, 18):
        origin = f"V{number:02}"
        mappings[origin] = [
            add(
                origin,
                "shared",
                "scenario-owner",
                "audit",
                "contract-counterexamples",
                "R9.6" if number == 17 else "R9.4",
                ("disclosure",)
                if number == 16
                else ("cost",)
                if number == 17
                else (
                    "numeric_state",
                    "type_domain_parts",
                    "resource_cancel",
                    "publication_recovery",
                ),
            )
        ]
    for backend in PROFILES:
        for route in ("ibis", "ibis_python"):
            origin = f"cost-baseline:{backend}:{route}"
            mappings[origin] = [
                add(
                    "cost-baseline",
                    backend,
                    "ordinary-table",
                    route,
                    "1k-100k-warmup-three-samples",
                    "R9.6",
                    ("cost", "numeric_state", "resource_cancel"),
                )
            ]
    mappings["cost-fixed"] = [
        add(
            "cost-baseline",
            "none",
            "producer-bound-receipt",
            "artifact_python",
            "1k-100k-kernel-and-exact-hit",
            "R9.6",
            ("cost", "numeric_state", "publication_recovery"),
        )
    ]
    for scenario in (
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
    ):
        mappings[f"cost:{scenario}"] = [
            add(
                "cost-scenario",
                "shared",
                "applicable-routes",
                "cost",
                scenario,
                "R9.6",
                ("cost", "numeric_state"),
            )
        ]
    for refusal in (
        "mixed-live-fixed",
        "cross-session",
        "cross-datasource",
        "missing-required-parts",
        "no-static-route",
    ):
        mappings[refusal] = [
            add(
                "refusal",
                "shared",
                "pre-run-pre-read",
                "refusal",
                refusal,
                "R9.4",
                ("type_domain_parts", "disclosure"),
                "rejection",
            )
        ]
    return {
        "schema": "marivo.r9.scenario-freeze/v1",
        "candidate": candidate(),
        "consumers": consumers(),
        "owners": {p: digest((ROOT / p).read_bytes()) for p in owners},
        "seed_sha256": digest((ROOT / SEED).read_bytes()),
        "seeds": profiles,
        "source_rows": origins,
        "mappings": mappings,
        "requirements": requirements,
        "method_targets": checked(method_targets),
        "physical_profiles": {key: list(forms) for key, forms in PROFILES.items()},
        "defaults": {
            "status": "unverified",
            "executed": False,
            "test_node": None,
            "oracle": None,
            "release_condition": "Bind the scenario consumer, collected test, oracle and concrete environment",
        },
        "profile_gaps": {
            "trino/non-iceberg": "R9.2: bind accepted connector, catalog, table type and service version",
            "clickhouse/distributed": "R9.2: bind actual shard/replica topology and remote observations",
            "postgres/namespace": "R9.2: bind database/schema/search-path collision fixture",
        },
        "coverage_policy": {
            "basis": "User accepted method-family and critical-risk scenarios instead of Cartesian seed expansion",
            "seed_mapping": "Many-to-many traceability; representative scenarios do not qualify every seed",
            "fixed": "One backend-free goal; attach schema/receipt/parts/offline evidence for every real producer",
            "extension": "Append an exact scenario when backend/profile differences change semantics, decoding or resources",
            "historical": "Historical owner rows are references, outside the acceptance denominator",
        },
        "exclusions": {
            "C16.b": "R10 owns installed-package and real-Agent acceptance; R9 retains disclosure deltas",
            "research": "Unaccepted bootstrap, causal and survival inference are outside the owning contract",
        },
        "results": {"default": "unverified", "overrides": {}, "runtime_passed": 0},
    }


def validate(payload: dict[str, Json]) -> None:
    rows = [obj(row) for row in arr(payload["requirements"])]
    ids = [str(row["id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate requirement ID")
    mapping = obj(payload["mappings"])
    mapped = [str(identity) for values in mapping.values() for identity in arr(values)]
    if set(mapped) != set(ids):
        raise ValueError("missing target or orphan mapping")
    if any(
        len(arr(values)) != len({str(item) for item in arr(values)}) for values in mapping.values()
    ):
        raise ValueError("duplicate mapping edge")
    if not set(obj(payload["seeds"])) <= set(mapping) or not set(
        obj(payload["source_rows"])
    ) <= set(mapping):
        raise ValueError("unmapped original target")
    if obj(payload["results"]) != {"default": "unverified", "overrides": {}, "runtime_passed": 0}:
        raise ValueError("static freeze cannot grant execution qualification")
    if obj(payload["defaults"])["executed"] is not False:
        raise ValueError("static freeze cannot claim execution")
    for path, expected in obj(payload["owners"]).items():
        if digest((ROOT / path).read_bytes()) != expected:
            raise ValueError(f"owner digest changed: {path}")
    if digest((ROOT / SEED).read_bytes()) != payload["seed_sha256"]:
        raise ValueError("seed digest changed")
    # Regeneration independently checks the expected expansion and route obligations.
    expected = build()
    for field in (
        "seeds",
        "consumers",
        "source_rows",
        "requirements",
        "mappings",
        "physical_profiles",
        "method_targets",
        "coverage_policy",
        "defaults",
        "profile_gaps",
        "exclusions",
    ):
        if payload[field] != expected[field]:
            raise ValueError(f"frozen target drift: {field}")
    frozen_candidate = obj(payload["candidate"])
    current = candidate()
    for field in ("files", "dependencies", "content_sha256"):
        if frozen_candidate[field] != current[field]:
            raise ValueError(f"candidate drift: {field}")


def verify_results(payload: dict[str, Json], result_path: Path) -> dict[str, Json]:
    """Check evidence structure and identity; assertions remain with test owners."""
    results = read(result_path)
    authority = obj(payload["candidate"])["content_sha256"]
    if results.get("candidate_sha256") != authority:
        raise ValueError("result candidate mismatch")
    required = {str(obj(row)["id"]): obj(row) for row in arr(payload["requirements"])}
    counts: dict[str, Json] = {"passed": 0, "failed": 0, "blocked": 0, "unverified": len(required)}
    overrides = obj(results["overrides"])
    nodes = obj(obj(payload["consumers"])["test_functions"])
    for identity, raw in overrides.items():
        if identity not in required:
            raise ValueError("orphan result")
        record = obj(raw)
        status = str(record["status"])
        if status not in counts:
            raise ValueError("invalid qualification status; skipped is an execution attribute")
        if status == "blocked" and not all(
            record.get(k) for k in ("reason", "owner", "release_condition")
        ):
            raise ValueError("incomplete blocker")
        if status == "passed":
            if (
                record.get("proof_class") != "runtime"
                or record.get("exit_code") != 0
                or record.get("skipped")
            ):
                raise ValueError("static/compile/skip cannot grant runtime qualification")
            if record.get("expectation") != required[identity]["expectation"]:
                raise ValueError("success/rejection mismatch")
            if record.get("owner_sha256") != payload["owners"]:
                raise ValueError("result owner mismatch")
            for field in (
                "environment",
                "input_sha256",
                "oracle",
                "command",
                "started",
                "finished",
            ):
                if not record.get(field):
                    raise ValueError(f"missing evidence binding: {field}")
            node = str(record.get("test_node", ""))
            path, _, function = node.partition("::")
            if path not in nodes or function.split("[")[0] not in arr(nodes[path]):
                raise ValueError("uncollected test owner")
            proofs = obj(record["proofs"])
            for obligation_raw in arr(required[identity]["required_proofs"]):
                obligation = str(obligation_raw)
                proof = obj(proofs.get(obligation))
                if (
                    proof.get("status") != "passed"
                    or proof.get("requirement_id") != identity
                    or proof.get("candidate_sha256") != authority
                ):
                    raise ValueError("incomplete or incorrectly bound proof")
                relative = Path(str(proof["attachment"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("invalid attachment path")
                attachment = result_path.parent / relative
                if not attachment.resolve().is_relative_to(result_path.parent.resolve()):
                    raise ValueError("attachment escapes evidence package")
                if digest(attachment.read_bytes()) != proof["sha256"]:
                    raise ValueError("attachment digest mismatch")
        if status != "unverified":
            counts["unverified"] = int(str(counts["unverified"])) - 1
            counts[status] = int(str(counts[status])) + 1
    if sum(int(str(count)) for count in counts.values()) != len(required):
        raise ValueError("qualification denominator mismatch")
    return counts


def save(payload: dict[str, Json], destination: Path) -> None:
    if (destination / "index.json").exists():
        raise ValueError("Freeze already exists; use a new directory to preserve historical IDs")
    destination.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(encode(payload), mtime=0)
    parts: list[Json] = []
    for offset in range(0, len(compressed), SHARD_BYTES):
        name = f"requirements.json.gz.part-{len(parts) + 1:03}"
        chunk = compressed[offset : offset + SHARD_BYTES]
        (destination / name).write_bytes(chunk)
        parts.append({"path": name, "bytes": len(chunk), "sha256": digest(chunk)})
    index: dict[str, Json] = {
        "schema": payload["schema"],
        "parts": parts,
        "sha256": digest(compressed),
        "payload_sha256": digest(encode(payload)),
        "required": len(arr(payload["requirements"])),
        "runtime_passed": 0,
    }
    (destination / "index.json").write_bytes(encode(index) + b"\n")
    keep = {str(obj(part)["path"]) for part in parts}
    for obsolete in destination.glob("requirements.json.gz.part-*"):
        if obsolete.name not in keep:
            obsolete.unlink()


def load(directory: Path) -> dict[str, Json]:
    index = read(directory / "index.json")
    chunks: list[bytes] = []
    names: set[str] = set()
    for raw in arr(index["parts"]):
        part = obj(raw)
        name = str(part["path"])
        if Path(name).name != name or name in names:
            raise ValueError("invalid or duplicate shard path")
        names.add(name)
        chunk = (directory / name).read_bytes()
        if len(chunk) != part["bytes"] or digest(chunk) != part["sha256"]:
            raise ValueError("shard digest mismatch")
        chunks.append(chunk)
    if {p.name for p in directory.glob("requirements.json.gz.part-*")} != names:
        raise ValueError("orphan shard")
    compressed = b"".join(chunks)
    if digest(compressed) != index["sha256"]:
        raise ValueError("bundle digest mismatch")
    data = gzip.decompress(compressed)
    if digest(data) != index["payload_sha256"]:
        raise ValueError("payload digest mismatch")
    payload = obj(checked(json.loads(data)))
    if len(arr(payload["requirements"])) != index["required"] or index["runtime_passed"] != 0:
        raise ValueError("index count mismatch")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "verify", "summary", "verify-results"))
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    if args.action == "generate":
        save(build(), args.directory)
    payload = load(args.directory)
    if args.action == "verify":
        validate(payload)
    if args.action == "verify-results":
        validate(payload)
        if args.results is None:
            parser.error("verify-results requires --results")
        print(json.dumps(verify_results(payload, args.results), sort_keys=True))
        return
    print(
        json.dumps(
            {
                "required": len(arr(payload["requirements"])),
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "unverified": len(arr(payload["requirements"])),
                "executed": 0,
                "authority": "static only",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
