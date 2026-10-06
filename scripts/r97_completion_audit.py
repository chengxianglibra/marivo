"""Bind original R9 obligations without rewriting historical executions."""

from __future__ import annotations

import argparse
import gzip
import importlib.metadata
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from scripts import r9_qualification_requirements as freeze

Json = freeze.Json
SPECS = Path("docs/superpowers/specs")
ROUTING = SPECS / "evidence/r97/original-id-adoption-routing-08.json"
SQLITE_DECIMAL = "R9:source-types:sqlite:ordinary-table:ibis:decimal-precision-scale"
EXECUTION_FIELDS = (
    "candidate_sha256",
    "source_candidate_sha256",
    "command",
    "started",
    "finished",
    "test_node",
    "exit_code",
)


def pointer(value: Json, path: str) -> Json:
    """Resolve a JSON pointer, including requirement IDs containing slashes."""
    if not path:
        return value
    if not path.startswith("/"):
        raise ValueError("Invalid JSON pointer")
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else freeze.obj(value)[token]
    return value


class Evidence:
    """Read hash-bound evidence and retain the exact files actually inspected."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.used: dict[str, Json] = {}
        self.cached_bytes: dict[str, bytes] = {}
        self.verified_documents: set[tuple[str, str]] = set()

    def data(self, path: Path) -> bytes:
        key = str(path.resolve())
        if key in self.cached_bytes:
            return self.cached_bytes[key]
        if path.is_file():
            data = path.read_bytes()
        else:
            manifest = next(
                (
                    path.parent / name
                    for name in ("packed-attachments.json", "attachment-transport.json")
                    if (path.parent / name).is_file()
                ),
                None,
            )
            if manifest is None:
                raise FileNotFoundError(path)
            self.reference(manifest)
            index = freeze.read(manifest)
            packed = freeze.obj(freeze.obj(index.get("attachments", index))[path.name])
            chunks = []
            for raw in freeze.arr(packed["parts"]):
                part = freeze.obj(raw)
                part_path = manifest.parent / str(part["path"])
                if (
                    self.reference(part_path)["sha256"] != part["sha256"]
                    or part_path.stat().st_size != part["bytes"]
                ):
                    raise ValueError("Packed attachment shard mismatch")
                chunks.append(part_path.read_bytes())
            compressed = b"".join(chunks)
            if "packed_sha256" in packed and freeze.digest(compressed) != packed["packed_sha256"]:
                raise ValueError("Packed attachment digest mismatch")
            data = gzip.decompress(compressed)
            if len(data) != packed["bytes"] or freeze.digest(data) != packed["sha256"]:
                raise ValueError("Reconstructed attachment digest mismatch")
        self.cached_bytes[key] = data
        return data

    def reference(self, path: Path) -> dict[str, Json]:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Evidence path escapes repository")
        data = self.data(resolved)
        result: dict[str, Json] = {
            "path": str(resolved.relative_to(self.root)),
            "sha256": freeze.digest(data),
        }
        self.used[str(result["path"])] = result
        return result

    def read(self, reference: dict[str, Json]) -> tuple[Path, dict[str, Json]]:
        path = self.root / str(reference["path"])
        observed = self.reference(path)
        if observed["sha256"] != reference["sha256"]:
            raise ValueError(f"Evidence digest mismatch: {reference['path']}")
        return path, freeze.obj(freeze.checked(json.loads(self.data(path))))

    def authority(self, document: dict[str, Json], name: str) -> tuple[Path, dict[str, Json]]:
        return self.read(freeze.obj(freeze.obj(document["authorities"])[name]))

    def attachments(self, value: Json, directory: Path) -> None:
        """Validate concrete paths; original unavailable /tmp source labels stay labels."""
        if isinstance(value, list):
            for item in value:
                self.attachments(item, directory)
        elif isinstance(value, dict):
            invocation = freeze.obj(value.get("original_invocation", {}))
            package = value.get("original_package", value.get("package", invocation.get("package")))
            if package:
                directory = directory.parent / str(package)
            if (
                "sha256" in value
                and "line" not in value
                and ("path" in value or "attachment" in value)
            ):
                if "path" in value:
                    path = self.root / str(value["path"])
                else:
                    origin = freeze.obj(value.get("origin", {}))
                    base = Path(str(origin["directory"])) if "directory" in origin else directory
                    path = base / str(value["attachment"])
                try:
                    reference = self.reference(path)
                except FileNotFoundError:
                    # Archived binders retain original basename references. Resolve
                    # only identical bytes in the original evidence family.
                    family = next(
                        (
                            parent
                            for parent in directory.parents
                            if parent.name.endswith("-evidence")
                        ),
                        directory,
                    )
                    candidates = sorted(
                        set(family.rglob(path.name))
                        | {
                            candidate.with_name(path.name)
                            for candidate in family.rglob(path.name + ".gz.part-001")
                        }
                    )
                    matching = [
                        candidate
                        for candidate in candidates
                        if freeze.digest(self.data(candidate)) == value["sha256"]
                    ]
                    if not matching:
                        raise
                    reference = self.reference(matching[0])
                if reference["sha256"] != value["sha256"]:
                    raise ValueError(f"Attachment digest mismatch: {path}")
            for item in value.values():
                self.attachments(item, directory)


def current_candidate(root: Path) -> dict[str, Json]:
    files: dict[str, Json] = {
        str(path.relative_to(root)): freeze.digest(path.read_bytes())
        for directory in ("marivo", "devtools", "scripts", "tests")
        for path in sorted((root / directory).rglob("*.py"))
    }
    encoded = json.dumps(files, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "content_sha256": freeze.digest(encoded),
        "source_sha256": files,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("ibis-framework", "duckdb", "pyarrow", "pandas", "numpy", "pytest")
        },
    }


def adoption(
    evidence: Evidence, routing: dict[str, Json], delivery: dict[str, Json]
) -> dict[str, Json]:
    _, proof = evidence.authority(routing, "concrete_eight_authority_proof")
    historical = freeze.obj(proof["candidate"])
    old_files, new_files = (
        freeze.obj(historical["source_sha256"]),
        freeze.obj(delivery["source_sha256"]),
    )
    old_product = {path: value for path, value in old_files.items() if path.startswith("marivo/")}
    current_product = {
        path: value for path, value in new_files.items() if path.startswith("marivo/")
    }
    if not old_product or old_product != current_product:
        raise ValueError("Product owner drift requires a new precise impact witness")
    if historical["dependencies"] != delivery["dependencies"]:
        raise ValueError("Dependency drift requires a new precise impact witness")
    for path, expected in current_product.items():
        if freeze.digest((evidence.root / path).read_bytes()) != expected:
            raise ValueError(f"Current product digest mismatch: {path}")
    return {
        "historical_content_candidate": historical["content_sha256"],
        "delivery_content_candidate": delivery["content_sha256"],
        "unchanged_product_owners": len(old_product),
        "dependencies_equal": True,
        "dependency_names": freeze.checked(list(freeze.obj(historical["dependencies"]))),
        "nonproduct_source_delta": freeze.checked(
            sorted(
                path
                for path in old_files.keys() | new_files.keys()
                if old_files.get(path) != new_files.get(path)
            )
        ),
        "historical_execution_candidates_rewritten": False,
    }


def r93_record(
    evidence: Evidence, owner_path: Path, identity: str, record: dict[str, Json]
) -> tuple[dict[str, Json], dict[str, Json]]:
    """Resolve raw-file and results-index locators retained by implementation acceptance."""
    package = owner_path.parent.parent / str(record["source_package"])
    if "record" in record:
        path = package / str(record["record"])
        return evidence.read(
            {"path": str(path.relative_to(evidence.root)), "sha256": record["record_sha256"]}
        )[1], evidence.reference(path)
    if "results_index_sha256" in record:
        path, results = evidence.read(
            {
                "path": str((package / "results-index.json").relative_to(evidence.root)),
                "sha256": record["results_index_sha256"],
            }
        )
        raw = freeze.obj(freeze.obj(results["overrides"])[identity])
        if freeze.digest(freeze.encode(raw)) != record["record_sha256"]:
            raise ValueError("Original result-record digest mismatch")
        return raw, {
            **evidence.reference(path),
            "json_pointer": "/overrides/" + identity.replace("~", "~0").replace("/", "~1"),
        }
    matches = [
        path
        for path in package.glob("*.json")
        if freeze.digest(path.read_bytes()) == record["record_sha256"]
    ]
    if len(matches) != 1:
        raise ValueError("Unresolved or ambiguous original R9.3 record locator")
    return freeze.read(matches[0]), evidence.reference(matches[0])


def historical_binding(
    evidence: Evidence,
    original: dict[str, Json],
    routing: dict[str, Json],
    row: dict[str, Json],
    route: dict[str, Json],
) -> dict[str, Json]:
    path, authority = evidence.authority(original, str(row["authority"]))
    record = freeze.obj(pointer(authority, str(row["pointer"])))
    source_reference: dict[str, Json] = {**evidence.reference(path), "json_pointer": row["pointer"]}
    raw = record
    if row["gap_owner"] == "R9.3":
        if record.get("record_sha256") != row.get("original_record_sha256"):
            raise ValueError("Original R9.3 acceptance record digest drift")
        raw, source_reference = r93_record(evidence, path, str(row["id"]), record)
    sqlite_refusal = (
        row["id"] == SQLITE_DECIMAL
        and row["effective_expectation"] == "rejection"
        and row.get("owning_status") == "passed_exact_authorized_refusal"
    )
    if (
        row.get("owning_status") not in ("passed", "scoped_implementation_closed")
        and not sqlite_refusal
    ):
        raise ValueError("Original owner does not assert a scoped closure")
    if record.get("status", record.get("owner_assertion_status", "passed")) != "passed":
        raise ValueError("Original owning assertion did not pass")
    if record.get("id", row["id"]) != row["id"] and row["gap_owner"] != "R9.5":
        raise ValueError("Original owning assertion identity drift")
    if "required_proofs" in record and set(freeze.arr(record["required_proofs"])) != set(
        freeze.arr(row["required_proofs"])
    ):
        raise ValueError("Original owning assertion required-proof drift")
    if sqlite_refusal:
        if (
            record.get("amended_expectation") != "rejection"
            or set(freeze.obj(record["proof_status"])) != set(freeze.arr(row["required_proofs"]))
            or any(value != "passed" for value in freeze.obj(record["proof_status"]).values())
        ):
            raise ValueError("Authorized SQLite refusal proof is incomplete")
        evidence.attachments(authority.get("attachments", []), path.parent)
    if row["gap_owner"] == "R9.5" and row["id"] not in freeze.arr(record["original_mapping"]):
        raise ValueError("Original SQL mapping identity drift")
    locator = (str(source_reference["path"]), str(source_reference.get("json_pointer", "")))
    if locator not in evidence.verified_documents:
        evidence.attachments(raw, (evidence.root / str(source_reference["path"])).parent)
        evidence.verified_documents.add(locator)
    effective = str(row["effective_expectation"])
    expected = str(row["original_expectation"])
    if effective != expected and (
        row["id"] != SQLITE_DECIMAL or expected != "success" or effective != "rejection"
    ):
        raise ValueError("Unapproved original expectation amendment")
    binding = freeze.obj(route["adoption_binding"])
    impact_path, impact = evidence.authority(routing, str(binding["authority"]))
    pointer(impact, str(binding["json_pointer"]))
    proofs: dict[str, Json] = {}
    for proof_name in freeze.arr(row["required_proofs"]):
        name = str(proof_name)
        proof: dict[str, Json] = {
            "status": "bound_historical_owner",
            "original_owner": {**evidence.reference(path), "json_pointer": row["pointer"]},
            "original_record": source_reference,
            "current_impact": {
                **evidence.reference(impact_path),
                "json_pointer": binding["json_pointer"],
            },
        }
        if "proofs" in raw and isinstance(raw["proofs"], dict):
            original_proof = freeze.obj(raw["proofs"]).get(name)
            if original_proof is None:
                raise ValueError(f"Missing original required proof: {name}")
            if (
                isinstance(original_proof, dict)
                and original_proof.get("status", "passed") != "passed"
            ):
                raise ValueError(f"Original required proof did not pass: {name}")
            proof["original_proof"] = original_proof
        if row["gap_owner"] == "R9.4":
            impact_row = freeze.obj(pointer(impact, str(binding["json_pointer"])))
            proposed = freeze.obj(freeze.obj(impact_row["proof_bindings_proposed"])[name])
            for packet in freeze.arr(proposed["family_packet_authorities"]):
                ref = freeze.obj(freeze.obj(impact["authorities"])[str(packet)])
                if evidence.reference(evidence.root / str(ref["path"]))["sha256"] != ref["sha256"]:
                    raise ValueError("Family packet digest mismatch")
            proof["finite_owner_binding"] = proposed
        if row["gap_owner"] == "R9.5":
            receipts = [freeze.obj(value) for value in freeze.arr(authority["native_receipts"])]
            static = freeze.obj(authority["static_audit"])
            if authority["status"] != "scoped_implementation_closed" or freeze.arr(
                freeze.obj(static["report"])["failures"]
            ):
                raise ValueError("SQL owning classification exit is incomplete")
            public_receipts = [
                value for value in receipts if value.get("public_owner_witness") is True
            ]
            if (
                len(public_receipts) != 6
                or {value["backend"] for value in public_receipts} != set(freeze.PROFILES)
                or any(
                    freeze.obj(value["original_receipt"]).get("unknown_submissions") != 0
                    for value in receipts
                )
            ):
                raise ValueError("SQL actual native witnesses are incomplete")
            proof["finite_sql_witnesses"] = {
                **evidence.reference(path),
                "json_pointer": "/native_receipts" if name == "submission" else "/static_audit",
                "scope": "Original six public native receipts and scoped static owner classification only.",
            }
        proofs[name] = proof
    return {
        "status": "adopted_finite_owner_proofs",
        "historical_owner_status": row["owning_status"],
        "effective_expectation": effective,
        "original_record": source_reference,
        "historical_execution": {field: raw.get(field) for field in EXECUTION_FIELDS},
        "unrecorded_execution_fields": [field for field in EXECUTION_FIELDS if field not in raw],
        "proofs": proofs,
        "scope": raw.get("scope", raw.get("boundary", record.get("assertion_scope"))),
    }


def scoped_binding(
    evidence: Evidence, routing: dict[str, Json], identity: str, route: dict[str, Json]
) -> dict[str, Json]:
    binding = freeze.obj(route["adoption_binding"])
    path, data = evidence.authority(routing, str(binding["authority"]))
    bound = pointer(data, str(binding["json_pointer"]))
    proof_map: dict[str, Json] = {
        str(name): {
            "status": "bound_scoped_owner",
            "binding": {**evidence.reference(path), "json_pointer": binding["json_pointer"]},
        }
        for name in freeze.arr(route["required_proofs"])
    }
    if isinstance(bound, list):
        named = [freeze.obj(value) for value in bound if freeze.obj(value).get("id") == identity]
        if {str(value["proof"]) for value in named} != set(proof_map):
            raise ValueError("Missing exact scoped disclosure proof")
    elif freeze.obj(bound).get("id") == identity:
        if {str(freeze.obj(bound)["proof"])} != set(proof_map):
            raise ValueError("Missing exact scoped disclosure proof")
    elif set(freeze.obj(bound)) != set(proof_map):
        raise ValueError("Missing exact scoped V14 proof")
    selected = (
        named
        if isinstance(bound, list)
        else [freeze.obj(bound)]
        if freeze.obj(bound).get("id") == identity
        else [freeze.obj(value) for value in freeze.obj(bound).values()]
    )
    if any(
        value.get("status") not in ("closed_scoped_C8_adoption", "supported_scoped_current_C8")
        for value in selected
    ):
        raise ValueError("Scoped proof owner did not assert supported closure")
    for reference in freeze.obj(data["authorities"]).values():
        ref = freeze.obj(reference)
        if evidence.reference(evidence.root / str(ref["path"]))["sha256"] != ref["sha256"]:
            raise ValueError("Scoped authority digest mismatch")
    return {
        "status": "adopted_finite_owner_proofs",
        "proofs": proof_map,
        "historical_candidates_rewritten": False,
    }


def engineering_binding(
    evidence: Evidence, routing: dict[str, Json], delivery: dict[str, Json], path: Path
) -> dict[str, Json]:
    run = freeze.read(path)
    evidence.reference(path)
    if (
        run.get("exit_code") != 0
        or run.get("candidate") != delivery
        or run.get("candidate_after") != delivery
        or run.get("candidate_unchanged") is not True
        or run.get("command") != ["make", "check-agent"]
    ):
        raise ValueError(
            "Final engineering command did not pass under the unchanged delivery candidate"
        )
    log = path.parent / "command.log"
    if evidence.reference(log)["sha256"] != run.get("log_sha256"):
        raise ValueError("Final engineering log digest mismatch")
    junit = path.parent / "junit.xml"
    if evidence.reference(junit)["sha256"] != run.get("junit_sha256"):
        raise ValueError("Final engineering JUnit digest mismatch")
    root = ElementTree.fromstring(evidence.data(junit))
    cases = {
        str(case.get("classname")) + "::" + str(case.get("name")): case
        for case in root.iter("testcase")
    }
    _, disclosed = evidence.authority(routing, "disclosure_current_C8")
    _, previous = evidence.authority(disclosed, "original_disclosure_binding")
    classes = freeze.obj(freeze.obj(previous["scoped_independent_disclosure_junit"])["classes"])
    names = {
        module + "::" + str(name)
        for module, values in classes.items()
        for name in freeze.arr(values)
    }
    names.update(
        str(name).partition("::")[0].removesuffix(".py").replace("/", ".")
        + "::"
        + str(name).partition("::")[2]
        for name in freeze.arr(
            freeze.obj(previous["optional_driver_and_extension_boundaries"])[
                "actual_current_six_nodes"
            ]
        )
    )
    if any(
        name not in cases
        or any(cases[name].find(kind) is not None for kind in ("failure", "error", "skipped"))
        for name in names
    ):
        raise ValueError(
            "Final engineering JUnit lacks a passed original disclosure or boundary node"
        )
    if any(case.find(kind) is not None for case in cases.values() for kind in ("failure", "error")):
        raise ValueError("Final engineering JUnit contains failures")
    return {
        "status": "passed",
        "actual_run": evidence.reference(path),
        "log": evidence.reference(log),
        "junit": evidence.reference(junit),
        "passed_disclosure_and_boundary_nodes": len(names),
        "original_candidate": run["candidate"],
        "skips_grant_runtime": False,
    }


def audit(
    root: Path,
    frozen: dict[str, Json],
    routing_path: Path,
    delivery: dict[str, Json],
    *,
    skip_remaining_cost: bool = False,
    cost_results: Path | None = None,
    engineering_run: Path | None = None,
) -> dict[str, Json]:
    """Audit finite owner bindings; absent evidence remains explicitly unverified."""
    evidence = Evidence(root)
    routing = freeze.read(routing_path)
    evidence.reference(routing_path)
    _, original = evidence.authority(routing, "original_routing")
    required = {
        str(freeze.obj(row)["id"]): freeze.obj(row) for row in freeze.arr(frozen["requirements"])
    }
    routes = freeze.obj(routing["requirements"])
    rows = {
        str(freeze.obj(row)["id"]): freeze.obj(row) for row in freeze.arr(original["requirements"])
    }
    if (
        len(freeze.arr(frozen["requirements"])) != 394
        or len(freeze.arr(original["requirements"])) != 394
        or len(required) != 394
        or len(rows) != 394
        or required.keys() != rows.keys()
        or required.keys() != routes.keys()
    ):
        raise ValueError("Original 394-ID denominator drift")
    for identity, requirement in required.items():
        if (
            rows[identity]["required_proofs"] != requirement["required_proofs"]
            or freeze.obj(routes[identity])["required_proofs"] != requirement["required_proofs"]
        ):
            raise ValueError("Original required-proof drift")
        if rows[identity]["original_expectation"] != requirement["expectation"]:
            raise ValueError("Original expectation drift")
        effective = rows[identity]["effective_expectation"]
        if effective != requirement["expectation"] and (
            identity != SQLITE_DECIMAL
            or requirement["expectation"] != "success"
            or effective != "rejection"
        ):
            raise ValueError("Unapproved original expectation amendment")
    product_adoption = adoption(evidence, routing, delivery)
    cost_data = freeze.read(cost_results) if cost_results else None
    if cost_results:
        evidence.reference(cost_results)
        if (
            cost_data is None
            or cost_data.get("schema") != "marivo.r97.cost-baseline-binding.v1"
            or set(freeze.obj(cost_data["results"]))
            != {identity for identity, row in rows.items() if row["family"] == "cost-baseline"}
        ):
            raise ValueError("Independent cost baseline inventory drift")
        evidence.attachments(cost_data.get("validator_sources", []), cost_results.parent)
    results: dict[str, Json] = {}
    defects: list[Json] = []
    for identity, row in rows.items():
        route = freeze.obj(routes[identity])
        try:
            if row["gap_owner"] == "R9.6":
                scenario_cost = row["family"] in ("cost-scenario", "V17")
                supplied = (
                    freeze.obj(cost_data.get("results", {})).get(identity) if cost_data else None
                )
                if scenario_cost and skip_remaining_cost:
                    result: dict[str, Json] = {
                        "status": "authorized_skipped",
                        "reason": "User explicitly skipped the remaining 40 authorized 1k cost groups.",
                        "proofs": {
                            str(name): {
                                "status": "unverified",
                                "reason": "Remaining collection or combined V17 exit was not executed.",
                            }
                            for name in freeze.arr(row["required_proofs"])
                        },
                    }
                    if row["family"] == "V17":
                        result["unexecuted_physical_producer_bindings"] = {
                            "count": 4,
                            "status": "unverified",
                            "user_waiver_inferred": False,
                        }
                elif supplied is not None:
                    result = freeze.obj(supplied)
                    if result.get("status") != "passed" or any(
                        freeze.obj(value).get("status") != "passed"
                        for value in freeze.obj(result["proofs"]).values()
                    ):
                        raise ValueError("Cost baseline result did not independently pass")
                    if set(freeze.obj(result["proofs"])) != {
                        str(name) for name in freeze.arr(row["required_proofs"])
                    }:
                        raise ValueError("Cost result required-proof drift")
                    evidence.attachments(result, cost_results.parent if cost_results else root)
                    result = {
                        **result,
                        "status": "adopted_finite_owner_proofs",
                        "independent_cost_status": "passed",
                        "new_current_candidate_measurement": False,
                    }
                else:
                    result = {
                        "status": "unverified",
                        "reason": "Complete ordinary material awaits the independent cost result binding.",
                        "proofs": {
                            str(name): {"status": "unverified"}
                            for name in freeze.arr(row["required_proofs"])
                        },
                    }
            elif route["status"] == "closed_scoped_current_C8":
                result = scoped_binding(evidence, routing, identity, route)
            else:
                result = historical_binding(evidence, original, routing, row, route)
        except (ValueError, KeyError, IndexError, OSError) as error:
            result = {
                "status": "unverified",
                "reason": str(error),
                "proofs": {
                    str(name): {"status": "unverified"}
                    for name in freeze.arr(row["required_proofs"])
                },
            }
            defects.append({"id": identity, "reason": str(error)})
        result["required_proofs"] = row["required_proofs"]
        results[identity] = result
    engineering: dict[str, Json] = {
        "status": "unverified",
        "reason": "No actual final delivery engineering command supplied.",
    }
    if engineering_run:
        try:
            engineering = engineering_binding(evidence, routing, delivery, engineering_run)
        except (ValueError, KeyError, OSError, ElementTree.ParseError) as error:
            engineering = {"status": "unverified", "reason": str(error)}
    counts = dict(Counter(str(freeze.obj(value)["status"]) for value in results.values()))
    complete = not defects and not counts.get("unverified", 0) and engineering["status"] == "passed"
    return {
        "schema": "marivo.r97.completion-audit.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate": delivery,
        "original_denominator": 394,
        "results": results,
        "counts": freeze.checked(counts),
        "defects": defects,
        "current_owner_adoption": product_adoption,
        "engineering": engineering,
        "authorized_skip_remaining_cost": skip_remaining_cost,
        "requested_scope": "Final evidence audit and R10 handoff with residual obligations disclosed",
        "requested_audit_scope_complete": complete,
        "qualification_complete": False,
        "unresolved_non_cost_obligations": [
            {
                "profile": name,
                "status": "unverified",
                "user_waiver_inferred": False,
                "owning_exit": "V17 cost-reuse producer binding",
            }
            for name in ("duckdb/csv", "duckdb/parquet", "duckdb/local-json", "trino/non-iceberg")
        ],
        "full_r9_complete": False,
        "raw_current_candidate_runtime_passes_granted": 0,
        "separate_additional_obligations": routing["separate_additional_obligations"],
        "attachments": list(evidence.used.values()),
        "boundary": "Finite historical proof adoption and explicit authorized skips; original candidates, failures and omissions remain unchanged. Installed wheel, real Agent and release remain R10 work.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routing", type=Path, default=freeze.ROOT / ROUTING)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-results", type=Path)
    parser.add_argument("--engineering", "--engineering-run", dest="engineering_run", type=Path)
    parser.add_argument("--skip-remaining-cost", action="store_true")
    args = parser.parse_args()
    report = audit(
        freeze.ROOT,
        freeze.load(freeze.OUTPUT),
        args.routing,
        current_candidate(freeze.ROOT),
        skip_remaining_cost=args.skip_remaining_cost,
        cost_results=args.cost_results,
        engineering_run=args.engineering_run,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        json.dumps(
            {
                "counts": report["counts"],
                "requested_audit_scope_complete": report["requested_audit_scope_complete"],
                "full_r9_complete": False,
            },
            sort_keys=True,
        )
    )
    return int(
        bool(report["defects"])
        or bool(args.engineering_run and not report["requested_audit_scope_complete"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
