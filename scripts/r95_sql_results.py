"""Bind scoped R9.5 SQL ownership evidence without replaying business methods."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from marivo.datasource.capabilities import ProviderStatement, provider_statement_catalog
from marivo.datasource.engines import ENGINE_PROFILES
from scripts import r9_qualification_requirements as freeze
from scripts.r95_sql_audit import RETIRED_MODULES

BACKENDS = tuple(sorted(freeze.PROFILES))
ORIGINAL_IDS = tuple(
    f"{prefix}{number:02}"
    for prefix, limit in (("DS", 22), ("AN", 33))
    for number in range(1, limit + 1)
)
LEDGER = Path("docs/superpowers/specs/2026-10-06-marivo-r95-sql-ledger.md")
REQUIRED_CATEGORIES = frozenset(
    {"governed_ibis", "provider", "raw_sql_terminal", "store", "ibis_metadata_preparation"}
)
CATEGORIES = REQUIRED_CATEGORIES | {"native_driver_setup", "test_administration"}
CONTROL = "clickhouse.analysis.cancel_owned_query"


def _text(value: freeze.Json) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a nonempty string")
    return value


def _strings(value: freeze.Json) -> list[str]:
    return [_text(item) for item in freeze.arr(value)]


def _label(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _source(path: Path, root: Path) -> dict[str, freeze.Json]:
    data = path.read_bytes()
    return {"path": _label(path, root), "bytes": len(data), "sha256": freeze.digest(data)}


def _static(path: Path, root: Path) -> dict[str, freeze.Json]:
    report = freeze.read(path)
    if (
        report.get("schema") != "marivo.r95.sql-audit.v1"
        or report.get("status") != "passed"
        or freeze.arr(report["failures"])
    ):
        raise ValueError("A passed R9.5 static SQL audit is required")
    files = _strings(report["scanned_files"])
    actual_files = sorted(p.relative_to(root).as_posix() for p in (root / "marivo").rglob("*.py"))
    hashes = freeze.obj(report["source_sha256"])
    if files != actual_files or set(hashes) != set(files) or not files:
        raise ValueError("The audit must cover the complete current product Python tree")
    if any(hashes[name] != freeze.digest((root / name).read_bytes()) for name in files):
        raise ValueError("The static audit must bind the current product source")
    retired = sorted(f"marivo/analysis/materialization/{name}.py" for name in RETIRED_MODULES)
    if _strings(report["retired_entries"]) != retired or any(
        (root / name).exists() for name in retired
    ):
        raise ValueError("All inventoried legacy adapters must be physically retired")
    manifest = freeze.arr(report["manifest"])
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    if report["manifest_sha256"] != freeze.digest(manifest_bytes):
        raise ValueError("The static manifest digest must bind its owner inventory")
    return {
        "source": _source(path, root),
        "report": report,
        "product_sha256": freeze.digest(freeze.encode(hashes)),
    }


def _ledger(path: Path, root: Path) -> tuple[dict[str, dict[str, freeze.Json]], str]:
    rows: dict[str, dict[str, freeze.Json]] = {}
    lines = path.read_text().splitlines()
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"\| ((?:DS|AN)\d{2}) \| ([GVDTX/]+) \| (.+) \|", line)
        if match is None:
            continue
        identity, disposition, owner = match.groups()
        if identity in rows:
            raise ValueError("Duplicate current SQL ledger obligation")
        rows[identity] = {
            "id": identity,
            "disposition": disposition,
            "current_owner": owner,
            "physical_retirement": "X" in disposition.split("/"),
            "ledger_source": {
                "path": _label(path, root),
                "line": number,
                "sha256": freeze.digest(line.encode()),
            },
        }
    if set(rows) != set(ORIGINAL_IDS):
        raise ValueError("The current ledger must retain all 55 original DS/AN obligations")
    document = "\n".join(lines)
    start = document.find("DS23 is appended")
    end = document.find("\n\nStore SQLite", start)
    control = document[start:end] if start >= 0 and end > start else ""
    if CONTROL not in control or "2026-10-06" not in control:
        raise ValueError("The appended DS23 approval must remain in the current ledger")
    return rows, control


def _original(
    payload: dict[str, freeze.Json], rows: dict[str, dict[str, freeze.Json]]
) -> list[freeze.Json]:
    requirements = [freeze.obj(item) for item in freeze.arr(payload["requirements"])]
    by_id = {_text(item["id"]): item for item in requirements}
    if len(requirements) != 394 or len(by_id) != 394:
        raise ValueError("The original 394-requirement freeze must remain unchanged")
    mappings = freeze.obj(payload["mappings"])
    origins = freeze.obj(payload["source_rows"])
    bound: list[freeze.Json] = []
    for identity in ORIGINAL_IDS:
        expected = f"R9:{identity}:all:sql-owner:audit:classification-reachability-submission"
        mapped = _strings(mappings[identity])
        if mapped != [expected] or expected not in by_id:
            raise ValueError("Every original SQL obligation requires its exact frozen mapping")
        requirement = by_id[expected]
        if requirement["family"] != identity or requirement["gap_owner"] != "R9.5":
            raise ValueError("Original SQL requirement ownership changed")
        source_rows = {
            key: value
            for key, value in origins.items()
            if freeze.obj(value)["path"] == freeze.SQL and mappings.get(key) == mapped
        }
        if not source_rows:
            raise ValueError("Original R0 SQL ledger provenance is required for every mapping")
        bound.append(
            {
                **rows[identity],
                "original_mapping": freeze.checked(mapped),
                "original_requirement": requirement,
                "original_source_rows": source_rows,
            }
        )
    return bound


def _catalog() -> tuple[Mapping[str, Mapping[str, ProviderStatement]], dict[str, freeze.Json]]:
    for backend in BACKENDS:
        ENGINE_PROFILES[backend]
    catalog = provider_statement_catalog()
    encoded: dict[str, freeze.Json] = {}
    for backend, statements in sorted(catalog.items()):
        encoded[backend] = {
            identity: {
                "template": statement.template,
                "literal_slots": freeze.checked(sorted(statement.literal_slots)),
                "identifier_slots": freeze.checked(sorted(statement.identifier_slots)),
                "parameterized": statement.parameterized,
                "allowed_purposes": freeze.checked(sorted(statement.allowed_purposes)),
                "integer_ranges": [
                    [name, lower, upper] for name, lower, upper in statement.integer_ranges
                ],
            }
            for identity, statement in sorted(statements.items())
        }
    control = catalog["clickhouse"][CONTROL]
    if (
        control.template != "KILL QUERY WHERE query_id={id:String} AND user={user:String} SYNC"
        or not control.parameterized
        or control.allowed_purposes != {"analysis.cancel_owned_query"}
    ):
        raise ValueError("DS23 must retain the approved exact query-ID/reader-bound control")
    return catalog, encoded


def _provider(
    record: dict[str, freeze.Json], catalog: Mapping[str, Mapping[str, ProviderStatement]]
) -> None:
    backend, owner = _text(record["backend"]), _text(record["owner"])
    statement = catalog.get(backend, {}).get(owner)
    if statement is None or record["purpose"] not in statement.allowed_purposes:
        raise ValueError(
            "Each native provider submission requires an exact current catalog purpose"
        )
    names = _strings(record["parameter_names"])
    sql = _text(record["sql"])
    if statement.parameterized:
        expected_names = ["id", "user"] if owner == CONTROL else []
        if sql != statement.template or sorted(names) != expected_names:
            raise ValueError(
                "Parameterized provider submissions must retain the exact template and parameter names"
            )
        return
    if names:
        raise ValueError("A rendered provider statement cannot carry bound parameter names")
    integers = {item[0] for item in statement.integer_ranges}
    quote = re.escape(ENGINE_PROFILES[backend].identifier_quote)
    identifier = rf"{quote}(?:{quote}{quote}|[^{quote}])*{quote}"
    parts: list[str] = []
    offset = 0
    for match in re.finditer(r"\{([^{}]+)\}", statement.template):
        parts.append(re.escape(statement.template[offset : match.start()]))
        slot = match.group(1)
        if slot in integers:
            parts.append(r"[0-9]+")
        elif slot in statement.literal_slots:
            parts.append(r"'(?:''|[^'])*'")
        elif slot in statement.identifier_slots:
            parts.append(rf"{identifier}(?:\.{identifier})*")
        else:
            raise ValueError("A provider template contains an undeclared slot")
        offset = match.end()
    parts.append(re.escape(statement.template[offset:]))
    if re.fullmatch("".join(parts), sql) is None:
        raise ValueError("Native provider text must match its current fixed template")


def _receipt(
    path: Path, root: Path, catalog: Mapping[str, Mapping[str, ProviderStatement]]
) -> dict[str, freeze.Json]:
    payload = freeze.read(path)
    environment = freeze.obj(payload["environment"])
    backend = _text(environment["backend"])
    if backend not in BACKENDS:
        raise ValueError("A native receipt requires one of the six selected source backends")
    records = [freeze.obj(item) for item in freeze.arr(payload["submissions"])]
    counts: dict[str, int] = {}
    serialized_origins: set[bool] = set()
    for record in records:
        category = _text(record["category"])
        if category not in CATEGORIES:
            raise ValueError("Unknown native SQL submissions cannot close R9.5")
        counts[category] = counts.get(category, 0) + 1
        origin_fields = {"expected_sql", "source_identity", "expression_identity", "schema"}
        present = origin_fields & set(record)
        if present and present != origin_fields:
            raise ValueError("Serialized native origin fields must use a complete captured schema")
        serialized_origins.add(bool(present))
        sql = _text(record["sql"])
        owner = _text(record["owner"])
        _text(record["boundary"])
        connection = record["connection"]
        if type(connection) is not int or connection <= 0:
            raise ValueError("Native submissions must carry the witnessed connection identity")
        if record["state"] not in ("submitted", "succeeded", "failed"):
            raise ValueError("Native submission state is required")
        if category in ("governed_ibis", "provider", "raw_sql_terminal"):
            if record["backend"] != backend or (present and record["expected_sql"] != sql):
                raise ValueError(
                    "The actual native argument must equal its source-issued or original terminal text"
                )
            _text(record["purpose"])
        if category == "provider":
            _provider(record, catalog)
        elif category == "governed_ibis":
            if owner == "SourceSession.batches":
                if present:
                    _text(record["source_identity"])
                    expression = record["expression_identity"]
                    if (
                        type(expression) is not int
                        or expression <= 0
                        or not freeze.arr(record["schema"])
                    ):
                        raise ValueError(
                            "Governed source reads require their bound expression and schema"
                        )
            elif (
                owner != "datasource.adapters._probe_backend"
                or record["purpose"] != "datasource.connectivity"
            ):
                raise ValueError(
                    "Governed SQL must have the current source-session or Ibis probe owner"
                )
        elif category == "raw_sql_terminal" and owner != "datasource.manage.raw_sql":
            raise ValueError("Terminal SQL must retain the public original-text owner")
        elif category == "store" and (
            owner != "analysis.materialization.store.SessionStore"
            or record["backend"] != "sqlite"
            or record["purpose"] != "persistence"
        ):
            raise ValueError(
                "Store SQL requires its independently identified SQLite connection owner"
            )
        elif category == "ibis_metadata_preparation" and (
            not owner.startswith("backends/") or not record["purpose"]
        ):
            raise ValueError("Ibis preparation must retain its installed-backend owner")
        elif category == "native_driver_setup" and (
            owner != "clickhouse_connect.Client._init_common_settings"
            or record["purpose"] != "connection_metadata"
        ):
            raise ValueError("Native driver setup must retain its closed metadata owner")
    if (
        not records
        or len(serialized_origins) != 1
        or payload["unknown_submissions"] != 0
        or payload["classification_counts"] != counts
    ):
        raise ValueError("Native receipts require complete counts and zero unknown submissions")
    assertions = freeze.obj(payload["assertions"])
    public = "public_probe" in assertions
    if public:
        for name in (
            "public_probe",
            "public_metadata",
            "store_connection_identity",
            "resources_released",
        ):
            if assertions.get(name) is not True:
                raise ValueError(
                    "The public native witness must bind its successful probe, inspection, Store and release assertions"
                )
        members = assertions.get("public_members")
        if (
            type(members) is not int
            or members <= 0
            or assertions.get("business_methods_replayed") is not False
        ):
            raise ValueError("A small members witness must preserve the no-business-replay scope")
        if not set(counts) >= REQUIRED_CATEGORIES or any(
            not any(
                item["category"] == category
                and (
                    item["state"] == "succeeded"
                    or (
                        category == "store"
                        and item["state"] == "submitted"
                        and item["boundary"] == "sqlite3.Connection.set_trace_callback"
                    )
                )
                for item in records
            )
            for category in REQUIRED_CATEGORIES
        ):
            raise ValueError(
                "Each public backend receipt requires all five observed SQL owners and successful public assertions"
            )
        governed_owners = {
            _text(item["owner"])
            for item in records
            if item["category"] == "governed_ibis" and item["state"] == "succeeded"
        }
        if governed_owners != {"SourceSession.batches", "datasource.adapters._probe_backend"}:
            raise ValueError(
                "The public probe and members witness require both governed native owners"
            )
        terminals = [item for item in records if item["category"] == "raw_sql_terminal"]
        if len(terminals) != 1 or terminals[0]["sql"] != _text(
            assertions["terminal_original_text"]
        ):
            raise ValueError(
                "The terminal witness must verify its single original text at the native boundary"
            )
        if environment.get("profile") != {"trino": "iceberg", "clickhouse": "mergetree"}.get(
            backend, "table"
        ):
            raise ValueError(
                "The native witness requires its selected representative physical profile"
            )
    return {
        "source": _source(path, root),
        "backend": backend,
        "public_owner_witness": public,
        "native_origin_binding": {
            "serialized_optional_origin_fields": True in serialized_origins,
            "authority": "Captured native classifier compares actual SQL to issued source/probe, provider or original terminal text before recording. Earlier receipts omit optional serialized origin fields; their original capture is preserved without rerun or invented fields.",
        },
        "original_receipt": payload,
    }


def _supplement(path: Path, root: Path) -> dict[str, freeze.Json]:
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    if not files:
        raise ValueError("A historical supplement must retain a source file or package")
    retained: dict[str, freeze.Json] = {}
    for item in files:
        if item.suffix == ".json" and (
            not path.is_dir() or item.name in ("run.json", "index.json")
        ):
            retained[_label(item, root)] = freeze.read(item)
    return {
        "path": _label(path, root),
        "authority": "original historical execution package; candidate, time and scope unchanged",
        "files": [_source(item, root) for item in files],
        "original_metadata": retained,
    }


def _witness_key(witness: dict[str, freeze.Json]) -> tuple[str, str]:
    return _text(witness["backend"]), _text(freeze.obj(witness["source"])["path"])


def _provider_evidence(
    witnesses: list[dict[str, freeze.Json]], statement_ids: set[str], purpose: str
) -> list[freeze.Json]:
    evidence: list[freeze.Json] = []
    for witness in sorted(witnesses, key=_witness_key):
        receipt = freeze.obj(witness["original_receipt"])
        for number, raw in enumerate(freeze.arr(receipt["submissions"]), 1):
            record = freeze.obj(raw)
            if (
                record["category"] == "provider"
                and _text(record["owner"]) in statement_ids
                and record["purpose"] == purpose
                and record["state"] == "succeeded"
            ):
                evidence.append(
                    {"source": witness["source"], "native_record": number, "record": record}
                )
    if not evidence:
        raise ValueError(f"A successful current native provider receipt is required for {purpose}")
    return evidence


def build(
    audit: Path,
    receipts: Sequence[Path],
    supplements: Sequence[Path] = (),
    *,
    root: Path = freeze.ROOT,
    frozen: dict[str, freeze.Json] | None = None,
    ledger: Path | None = None,
) -> dict[str, freeze.Json]:
    """Return a deterministic scoped index; never alter the original freeze or runs."""
    payload = freeze.load(freeze.OUTPUT) if frozen is None else frozen
    static = _static(audit, root)
    ledger_path = root / LEDGER if ledger is None else ledger
    rows, control = _ledger(ledger_path, root)
    obligations = _original(payload, rows)
    catalog, encoded = _catalog()
    witnesses = [_receipt(path, root, catalog) for path in receipts]
    public = [_text(item["backend"]) for item in witnesses if item["public_owner_witness"] is True]
    if sorted(public) != list(BACKENDS):
        raise ValueError(
            "Exactly one public native SQL owner receipt is required for each of the six backends"
        )
    control_evidence = _provider_evidence(witnesses, {CONTROL}, "analysis.cancel_owned_query")
    if any(
        freeze.obj(freeze.obj(item)["record"])["boundary"] != "clickhouse_connect.query"
        for item in control_evidence
    ):
        raise ValueError("DS23 must be observed at the native ClickHouse query boundary")
    http_evidence = _provider_evidence(
        witnesses,
        {"duckdb.http_secret_bearer", "duckdb.http_secret_headers"},
        "datasource.http_credentials",
    )
    instrumentation: dict[str, freeze.Json] = {
        name: freeze.digest((root / name).read_bytes())
        for name in (
            "scripts/r95_sql_audit.py",
            "scripts/r95_sql_results.py",
            "tests/r95_driver_audit.py",
        )
        if (root / name).is_file()
    }
    result: dict[str, freeze.Json] = {
        "schema": "marivo.r95.sql-closeout/v1",
        "status": "scoped_implementation_closed",
        "scope": {
            "original_sql_obligations": 55,
            "appended_control_obligations": 1,
            "native_backends": freeze.checked(list(BACKENDS)),
            "unknown_native_submissions": 0,
            "business_methods_replayed": False,
            "all_profile_final_candidate_qualification": False,
            "full_r9_qualification": False,
        },
        "original_freeze": {
            "payload_sha256": freeze.digest(freeze.encode(payload)),
            "requirement_count": 394,
            "requirements_sha256": freeze.digest(freeze.encode(payload["requirements"])),
            "candidate": payload["candidate"],
            "original_results": payload["results"],
        },
        "ledger_source": _source(ledger_path, root),
        "original_sql_mappings": obligations,
        "appended_control": {
            "id": "DS23",
            "disposition": "V",
            "approval": control,
            "statement_id": CONTROL,
            "statement": freeze.obj(encoded["clickhouse"])[CONTROL],
            "native_evidence": control_evidence,
            "authority": "approved registered control; no new physical-profile qualification",
        },
        "static_audit": static,
        "provider_catalog": encoded,
        "provider_catalog_sha256": freeze.digest(freeze.encode(encoded)),
        "ds15_scoped_http_native_evidence": http_evidence,
        "instrumentation_sha256": instrumentation,
        "native_receipts": freeze.checked(sorted(witnesses, key=_witness_key)),
        "historical_supplements": [_supplement(path, root) for path in sorted(set(supplements))],
    }
    result["binding_sha256"] = freeze.digest(freeze.encode(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = build(arguments.audit, arguments.receipt, arguments.supplement)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(freeze.encode(result) + b"\n")
    print(
        "R9.5 scoped implementation closed: 55 original SQL mappings, DS23, six native owners; original 394 unchanged"
    )


if __name__ == "__main__":
    main()
