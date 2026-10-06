"""Audit every product Python module against the SQL ownership SQL ownership boundary.

This is a static inventory, not driver execution evidence. SQL text clues never
grant submission authority. Only exact function and call identities below do.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

Json: TypeAlias = None | bool | int | str | list["Json"] | dict[str, "Json"]
ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = frozenset(
    {
        "duckdb_execution",
        "postgres_execution",
        "mysql_execution",
        "sqlite_execution",
        "trino_execution",
        "clickhouse_execution",
        "scalar_sql_execution",
        "dataset_execution",
        "source_stage",
        "source_preparation",
        "local_stage",
        "validation",
    }
)
SUBMIT_NAMES = frozenset(
    {
        "execute",
        "executemany",
        "executescript",
        "raw_sql",
        "sql",
        "query",
        "command",
        "query_rows_stream",
    }
)
ALIAS_NAMES = SUBMIT_NAMES | {"compile", "to_sql", "statement", "parse_one", "parse_sql"}
SQL_TEXT = re.compile(
    r"\A\s*(?:SELECT\b|WITH\b|INSERT\b|UPDATE\b|DELETE\b|CREATE\b|DROP\b|ALTER\b|"
    r"PRAGMA\b|SET\b|SHOW\b|DESCRIBE\b|BEGIN\b|COMMIT\b|ROLLBACK\b)"
)


@dataclass(frozen=True)
class Owner:
    category: str
    justification: str


Manifest: TypeAlias = dict[tuple[str, str, str], Owner]


def _manifest() -> Manifest:
    result: Manifest = {}

    def add(path: str, symbols: tuple[str, ...], calls: tuple[str, ...], owner: Owner) -> None:
        for symbol in symbols:
            for call in calls:
                result[(f"marivo/{path}.py", symbol, call)] = owner

    store = Owner(
        "store_sqlite_persistence",
        "SessionStore-owned SQLite connection and schema transactions; no datasource reads or algorithms.",
    )
    add(
        "analysis/materialization/store",
        (
            "_enable_wal",
            "_rows",
            "SessionStore._connection",
            "SessionStore._initialize",
            "SessionStore._read",
            "SessionStore._write",
            "SessionStore.create_session",
            "SessionStore.activate",
            "SessionStore.reserve",
            "SessionStore._delete_resources",
            "SessionStore.fail",
        ),
        ("conn.execute",),
        store,
    )
    add("analysis/materialization/store", ("SessionStore._initialize",), ("read.execute",), store)
    add(
        "analysis/materialization/graph_store",
        ("admit", "publish"),
        ("conn.execute", "conn.executemany"),
        store,
    )
    add("analysis/materialization/graph_findings", ("collection",), ("conn.execute",), store)
    add(
        "doctor",
        ("_state_section",),
        ("conn.execute",),
        Owner(
            "store_sqlite_persistence",
            "Read-only diagnostic connection inspects the project SessionStore schema.",
        ),
    )
    add(
        "telemetry/__init__",
        ("_session_creation_attributes",),
        ("connection.execute",),
        Owner(
            "store_sqlite_persistence",
            "Read-only v7 SessionStore connection reads the owned sessions question.",
        ),
    )

    governed = Owner(
        "ibis_governed_read_check_probe",
        "Selected SourceSession or its native probe submits exact Ibis compilation at the driver boundary.",
    )
    add(
        "datasource/adapters",
        ("_native_cursor",),
        ("cursor.execute", "connection.execute", "connection.query_rows_stream"),
        governed,
    )
    compile_owner = Owner(
        "ibis_compilation",
        "Ibis expression compilation is inventoried separately from driver submission.",
    )
    add("datasource/adapters", ("_probe_backend",), ("backend.compile",), compile_owner)
    add(
        "datasource/adapters", ("SourceSession.compile",), ("self._backend.compile",), compile_owner
    )
    add("datasource/adapters", ("SourceSession.collect_bounded",), ("self.compile",), compile_owner)
    add(
        "analysis/materialization/graph_source_execution",
        ("_issue",),
        ("source.compile",),
        compile_owner,
    )
    add(
        "semantic/_aggregate_accuracy",
        ("aggregate_repair.supported",),
        ("ibis.to_sql",),
        compile_owner,
    )

    provider = Owner(
        "approved_provider_statement",
        "Closed provider statement registry validates backend, purpose and parameters; fixed templates have independent snapshots.",
    )
    add(
        "datasource/capabilities",
        ("execute_provider_statement",),
        ("backend.raw_sql", "render_provider_statement"),
        provider,
    )
    add("datasource/capabilities", ("render_provider_statement",), ("re.sub",), provider)
    for backend in ("duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"):
        add(f"datasource/engines/{backend}", ("<module>",), ("ProviderStatement",), provider)
    add(
        "datasource/manage",
        ("raw_sql",),
        ("backend.raw_sql",),
        Owner(
            "terminal_user_raw_sql",
            "Public md.raw_sql submits the caller's original statement and returns a terminal bounded result.",
        ),
    )

    typed = Owner(
        "non_sql_typed_execution",
        "This exact call executes a typed graph or local result, not a SQL statement constructor or driver.",
    )
    add(
        "analysis/materialization/admission",
        ("DatasetRuntime._execute_graph",),
        ("marivo.analysis.materialization.graph_publication.execute",),
        typed,
    )
    add(
        "analysis/materialization/graph_dataset",
        ("GraphDataset.verified",),
        (
            "marivo.analysis.materialization.runs_execution.execute",
            "marivo.analysis.materialization.statistical_execution.execute",
        ),
        typed,
    )
    local_executors = (
        "statistical_execution",
        "runs_execution",
        "deviation_execution",
        "retention_execution",
        "history_views",
        "funnel_execution",
        "journey_views",
        "journey_execution",
    )
    add(
        "analysis/materialization/graph_local_execution",
        ("execute_verified_fixed",),
        tuple(f"marivo.analysis.materialization.{module}.execute" for module in local_executors),
        typed,
    )
    add(
        "analysis/materialization/graph_preparation",
        ("execute",),
        tuple(
            f"marivo.analysis.materialization.{module}.execute"
            for module in (*local_executors, "history_execution")
        ),
        typed,
    )
    add(
        "analysis/materialization/graph_source_execution",
        ("execute_source_graph",),
        ("marivo.analysis.materialization.graph_preparation.execute",),
        typed,
    )
    add(
        "analysis/materialization/graph_relation",
        ("Relation.execute",),
        ("self.binding.graph.execute",),
        typed,
    )
    add(
        "analysis/public_dsl",
        ("_Value._run", "LogicalTable.execute"),
        ("self._node.execute",),
        typed,
    )
    add(
        "datasource/engines/trino",
        ("_trino_table_comment_from_show_create",),
        ("match.group().replace",),
        Owner(
            "non_submit_metadata_decode",
            "Decode an escaped comment from the approved SHOW CREATE TABLE result; never rewrite submitted SQL.",
        ),
    )
    return result


MANIFEST = _manifest()


@dataclass(frozen=True)
class Record:
    path: str
    line: int
    symbol: str
    surface: str
    call: str
    category: str
    justification: str
    failed: bool = False
    text_sha256: str | None = None

    def json(self) -> dict[str, Json]:
        return {
            "path": self.path,
            "line": self.line,
            "symbol": self.symbol,
            "surface": self.surface,
            "call": self.call,
            "category": self.category,
            "owner_justification": self.justification,
            "failed": self.failed,
            "text_sha256": self.text_sha256,
        }


@dataclass(frozen=True)
class Report:
    scanned_files: tuple[str, ...]
    source_digests: tuple[tuple[str, str], ...]
    records: tuple[Record, ...]

    @property
    def failures(self) -> tuple[Record, ...]:
        return tuple(record for record in self.records if record.failed)

    def json(self) -> dict[str, Json]:
        manifest: list[Json] = [
            {
                "path": path,
                "symbol": symbol,
                "call": call,
                "category": owner.category,
                "owner_justification": owner.justification,
            }
            for (path, symbol, call), owner in sorted(MANIFEST.items())
        ]
        return {
            "schema": "marivo.r95.sql-audit.v1",
            "evidence_kind": "static_ast_inventory",
            "scope": "all marivo/**/*.py",
            "scanned_files": list(self.scanned_files),
            "source_sha256": dict(self.source_digests),
            "manifest": manifest,
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest(),
            "retired_entries": [
                f"marivo/analysis/materialization/{name}.py" for name in sorted(RETIRED_MODULES)
            ],
            "records": [record.json() for record in self.records],
            "failures": [record.json() for record in self.failures],
            "status": "failed" if self.failures else "passed",
        }


def _identity(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_identity(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            return f"{_identity(node.args[0])}.{node.args[1].value}"
        return f"{_identity(node.func)}()"
    return ast.unparse(node)


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scopes: list[str] = []
        self.aliases: list[dict[str, str]] = [{}]
        self.sql_values: list[set[str]] = [set()]
        self.records: list[Record] = []

    @property
    def symbol(self) -> str:
        return ".".join(self.scopes) or "<module>"

    def _record(self, node: ast.expr | ast.stmt, surface: str, call: str) -> None:
        owner = MANIFEST.get((self.path, self.symbol, call))
        if owner is not None and owner.category == "non_sql_typed_execution":
            surface = "typed_execution"
        self.records.append(
            Record(
                self.path,
                node.lineno,
                self.symbol,
                surface,
                call,
                owner.category if owner else "unclassified",
                owner.justification
                if owner
                else "No approved exact function/call owner; resolve before acceptance.",
                failed=owner is None,
            )
        )

    def _call(self, node: ast.AST) -> str:
        value = _identity(node)
        for aliases in reversed(self.aliases):
            if value in aliases:
                return aliases[value]
            for alias, original in aliases.items():
                if value.startswith(alias + "."):
                    return original + value[len(alias) :]
        return value

    def _sql(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and SQL_TEXT.match(child.value)
            ):
                return True
            if isinstance(child, ast.Name) and (
                child.id == "sql"
                or child.id.endswith("_sql")
                or any(child.id in values for values in self.sql_values)
            ):
                return True
            if isinstance(child, ast.Attribute) and child.attr in {"sql", "template"}:
                return True
            if (
                isinstance(child, ast.Call)
                and self._call(child.func) != "re.compile"
                and self._call(child.func).rsplit(".", 1)[-1] in {"compile", "to_sql"}
            ):
                return True
        return False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes.append(node.name)
        if any(
            "sqlglot" in _identity(base).lower() or "sqlcompiler" in _identity(base).lower()
            for base in node.bases
        ):
            self._record(node, "sql_ast_compiler_class", node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node.name)
        self.aliases.append({})
        self.sql_values.append(set())
        if node.name == "statement" or (
            node.name.startswith("visit_")
            and any("sql" in _identity(base).lower() for base in node.decorator_list)
        ):
            self._record(node, "legacy_statement_or_compiler_hook", node.name)
        self.generic_visit(node)
        self.sql_values.pop()
        self.aliases.pop()
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind(node, target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._bind(node, node.target, node.value)
        self.generic_visit(node)

    def _bind(self, node: ast.Assign | ast.AnnAssign, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            identity = self._call(value)
            if identity.rsplit(".", 1)[-1] in ALIAS_NAMES:
                self.aliases[-1][target.id] = identity
            if self._sql(value):
                self.sql_values[-1].add(target.id)
        if isinstance(target, ast.Attribute) and (
            target.attr.startswith("visit_") or "compiler" in _identity(target).lower()
        ):
            self._record(node, "compiler_patch", _identity(target))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._sql(node.target):
            self._record(node, "sql_text_rewrite", _identity(node.target))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            self._import(node, name.name)
            if name.asname is not None and name.name in {"re", "ibis", "sqlglot"}:
                self.aliases[-1][name.asname] = name.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._import(node, module)
        for name in node.names:
            self._import(node, f"{module}.{name.name}")
            if name.name in ALIAS_NAMES:
                self.aliases[-1][name.asname or name.name] = f"{module}.{name.name}"

    def _import(self, node: ast.Import | ast.ImportFrom, name: str) -> None:
        if name.startswith("sqlglot") or (".compiler" in name and name.startswith("ibis.backends")):
            self._record(node, "sql_ast_or_compiler_import", name)
        if set(name.split(".")) & RETIRED_MODULES and "materialization" in name:
            self._record(node, "retired_entry_import", name)

    def visit_Call(self, node: ast.Call) -> None:
        call = self._call(node.func)
        leaf = call.rsplit(".", 1)[-1]
        if leaf in SUBMIT_NAMES:
            self._record(node, "sql_submission_candidate", call)
        elif leaf in {"statement", "ProviderStatement", "render_provider_statement"}:
            self._record(node, "sql_statement_constructor", call)
        elif leaf in {"compile", "to_sql"} and call != "re.compile":
            self._record(node, "compilation", call)
        elif leaf in {"parse_one", "parse_sql", "to_sqlglot"} or call.startswith("sqlglot."):
            self._record(node, "sql_ast_operation", call)
        elif leaf in {"replace", "sub", "subn", "transform"} and self._sql(node):
            self._record(node, "sql_text_rewrite", call)
        elif leaf == "from_sql":
            self.records.append(
                Record(
                    self.path,
                    node.lineno,
                    self.symbol,
                    "sql_provenance",
                    call,
                    "non_submit_provenance",
                    "Provenance value construction is a clue only and grants no execution authority.",
                )
            )
        if (
            leaf == "setattr"
            and len(node.args) >= 2
            and "compiler" in _identity(node.args[0]).lower()
        ):
            self._record(node, "compiler_patch", call)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and SQL_TEXT.match(node.value):
            self.records.append(
                Record(
                    self.path,
                    node.lineno,
                    self.symbol,
                    "sql_text_clue",
                    "literal",
                    "non_submit_text_clue",
                    "SQL-shaped literal requires owner review; this clue grants no submission authority.",
                    text_sha256=hashlib.sha256(node.value.encode()).hexdigest(),
                )
            )


def audit(root: Path = ROOT) -> Report:
    """Return deterministic static classifications for the complete product tree."""
    paths = sorted((root / "marivo").rglob("*.py"))
    records: list[Record] = []
    names: list[str] = []
    digests: list[tuple[str, str]] = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        names.append(name)
        source = path.read_bytes()
        digests.append((name, hashlib.sha256(source).hexdigest()))
        if path.stem in RETIRED_MODULES and "materialization" in path.parts:
            records.append(
                Record(
                    name,
                    1,
                    "<module>",
                    "retired_entry_file",
                    path.stem,
                    "unclassified",
                    "Retired production entry must be physically absent.",
                    failed=True,
                )
            )
        scanner = _Scanner(name)
        try:
            scanner.visit(ast.parse(source, filename=name))
        except SyntaxError as error:
            records.append(
                Record(
                    name,
                    error.lineno or 1,
                    "<module>",
                    "syntax_error",
                    "ast.parse",
                    "unclassified",
                    str(error),
                    failed=True,
                )
            )
        records.extend(scanner.records)
    if root.resolve() == ROOT:
        seen = {(record.path, record.symbol, record.call) for record in records}
        for owner_path, symbol, call in sorted(MANIFEST.keys() - seen):
            records.append(
                Record(
                    owner_path,
                    1,
                    symbol,
                    "manifest_owner_missing",
                    call,
                    "unclassified",
                    "Closed manifest entry no longer matches the product; review ownership drift.",
                    failed=True,
                )
            )
    return Report(
        tuple(names),
        tuple(digests),
        tuple(sorted(records, key=lambda r: (r.path, r.line, r.surface, r.call))),
    )


class _Arguments(argparse.Namespace):
    root: Path
    output: Path | None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    arguments = _Arguments()
    parser.parse_args(namespace=arguments)
    report = audit(arguments.root)
    rendered = json.dumps(report.json(), sort_keys=True, indent=2) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered)
    return int(bool(report.failures))


if __name__ == "__main__":
    raise SystemExit(main())
