"""Check Marivo's architectural boundaries against a shrinking debt baseline."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from importlib.util import resolve_name
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypeAlias

import grimp

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "architecture_baseline.json"
RULES = ("ARCH001", "ARCH002", "ARCH003", "ARCH004", "ARCH005")
Baseline: TypeAlias = dict[str, dict[str, int | str]]
Findings: TypeAlias = dict[str, Counter[str]]
ImportTarget: TypeAlias = tuple[str, str]

SHARED_PACKAGES = {"_kernel", "_definition", "_authoring", "_help"}
SPI_MODULES = {
    "marivo.datasource.spi",
    "marivo.semantic.spi",
    "marivo.analysis.read_spi",
    "marivo.analysis.session.read_spi",
}
ENGINES = {"duckdb", "postgres", "postgresql", "mysql", "trino", "clickhouse"}
ENGINE_PACKAGE = "marivo.datasource.engines."
DYNAMIC_CALLS = {"importlib.import_module", "builtins.__import__", "builtins.eval", "builtins.exec"}
ESCAPE_CALLS = {
    "typing.cast": "cast",
    "typing_extensions.cast": "cast",
    "builtins.getattr": "getattr",
    "builtins.object.__setattr__": "object.__setattr__",
}
CALL_ATTRIBUTES = {name.rpartition(".")[2] for name in DYNAMIC_CALLS | ESCAPE_CALLS.keys()}
ANY_NAMES = {"typing.Any", "typing_extensions.Any"}
STDLIB_OWNERS = {"builtins", "importlib", "typing", "typing_extensions"}
BUILTIN_NAMES = {
    name: f"builtins.{name}" for name in ("__import__", "eval", "exec", "getattr", "object")
}

COLD_IMPORTS = {
    "marivo": frozenset(),
    "marivo.datasource": frozenset({"datasource"}),
    "marivo.semantic": frozenset({"datasource", "semantic"}),
    "marivo.analysis": frozenset({"analysis"}),
    "marivo.ontology": frozenset({"datasource", "semantic", "ontology"}),
}
DOMAIN_NAMES = {
    "datasource",
    "semantic",
    "analysis",
    "ontology",
    "server",
    "cli",
    "__main__",
}


def import_targets(node: ast.Import | ast.ImportFrom, package: str) -> Iterator[ImportTarget]:
    """Yield a module and optional symbol, resolving relative imports."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name, ""
        return
    module = node.module or ""
    if node.level:
        module = resolve_name("." * node.level + module, package)
    for alias in node.names:
        yield module, alias.name


def private_edge(source: str, target: ImportTarget) -> str | None:
    module, symbol = target
    if module == "marivo" and symbol:
        destination = f"{module}.{symbol}"
    elif module.startswith("marivo."):
        destination = module
    else:
        return None

    partitions = destination.split(".")[1:]
    source_top = source.partition(".")[2].partition(".")[0]
    if partitions[0] == source_top:
        return None
    if partitions[0] in SHARED_PACKAGES or module in SPI_MODULES:
        return None
    if symbol.startswith("_") or any(part.startswith("_") for part in partitions):
        return f"{source} -> {module}{' : ' + symbol if symbol else ''}"
    return None


def engine_import(target: ImportTarget) -> bool:
    module, symbol = target
    name = f"{module}.{symbol}" if symbol else module
    return name.startswith(ENGINE_PACKAGE) and name[len(ENGINE_PACKAGE) :].split(".")[0] in ENGINES


def engine_literal(value: ast.AST) -> bool:
    if isinstance(value, ast.Constant):
        return isinstance(value.value, str) and value.value.casefold() in ENGINES
    if isinstance(value, (ast.List, ast.Set, ast.Tuple)):
        return any(engine_literal(item) for item in value.elts)
    return False


def binding(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute) and (owner := binding(node.value, aliases)):
        return f"{owner}.{node.attr}"
    return None


class ModuleScan(ast.NodeVisitor):
    """Collect one module's imports, selected expressions, and scope sizes."""

    def __init__(self, path: str, module: str, package: str, findings: Findings) -> None:
        self.path = path
        self.module = module
        self.package = package
        self.findings = findings
        self.scope: list[str] = []
        self.engine_scope = module.partition(".")[2].partition(".")[0] in {"analysis", "semantic"}
        self.aliases = BUILTIN_NAMES.copy()
        self.calls: list[tuple[str, ast.Call]] = []
        self.names: Counter[str] = Counter()
        self.any_attributes: list[ast.Attribute] = []

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        targets = tuple(import_targets(node, self.package))
        for target, alias in zip(targets, node.names, strict=True):
            module, symbol = target
            if module in STDLIB_OWNERS:
                qualified = f"{module}.{symbol}" if symbol else module
                self.aliases[alias.asname or alias.name] = qualified
            if edge := private_edge(self.module, target):
                self.findings["ARCH001"][edge] += 1
            if self.engine_scope and engine_import(target):
                self.findings["ARCH002"][self.path] += 1
        if self.scope and any(
            module == "marivo" or module.startswith("marivo.") for module, _ in targets
        ):
            self.findings["ARCH005"][f"{self.path}: local Marivo imports"] += 1

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.visit_Import(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.engine_scope and any(
            engine_literal(item) for item in (node.left, *node.comparators)
        ):
            self.findings["ARCH002"][self.path] += 1
        self.generic_visit(node)

    def visit_MatchValue(self, node: ast.MatchValue) -> None:
        if self.engine_scope and engine_literal(node.value):
            self.findings["ARCH002"][self.path] += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) or (
            isinstance(node.func, ast.Attribute) and node.func.attr in CALL_ATTRIBUTES
        ):
            self.calls.append((".".join(self.scope) or "<module>", node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Alias resolution happens after the module scan.
        self.names[node.id] += 1

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "Any":
            self.any_attributes.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        lines = (node.end_lineno or node.lineno) - node.lineno + 1
        if lines > 120:
            self.findings["ARCH005"][f"{self.path}: function {'.'.join(self.scope)} lines"] += lines
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        methods = {
            item.name
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if len(methods) > 30:
            self.findings["ARCH005"][f"{self.path}: class {'.'.join(self.scope)} methods"] += len(
                methods
            )
        self.generic_visit(node)
        self.scope.pop()

    def scan(self, text: str) -> None:
        tree = ast.parse(text, filename=self.path, type_comments=True)
        self.visit(tree)
        budgets = self.findings["ARCH005"]
        file_lines = len(text.splitlines())
        if file_lines > 800:
            budgets[f"{self.path}: file lines"] = file_lines
        budgets["type: ignore"] += len(tree.type_ignores)
        budgets["undocumented type: ignore"] += sum(
            re.fullmatch(r"\[[a-z0-9_, -]+\]\s*#\s*\S.*", ignore.tag) is None
            for ignore in tree.type_ignores
        )
        budgets["Any"] += sum(
            self.names[name] for name, target in self.aliases.items() if target in ANY_NAMES
        )
        budgets["Any"] += sum(
            binding(node, self.aliases) in ANY_NAMES for node in self.any_attributes
        )
        for scope, node in self.calls:
            name = binding(node.func, self.aliases)
            if name in DYNAMIC_CALLS:
                self.findings["ARCH003"][f"{self.module}:{scope}: {ast.unparse(node)}"] += 1
            elif name is not None and name in ESCAPE_CALLS:
                budgets[ESCAPE_CALLS[name]] += 1


def scan_sources(root: Path) -> Findings:
    findings: Findings = {rule: Counter() for rule in RULES}
    for path in sorted((root / "marivo").rglob("*.py")):
        relative = path.relative_to(root)
        parts = relative.with_suffix("").parts
        package = ".".join(parts[:-1])
        module = package if path.name == "__init__.py" else ".".join(parts)
        ModuleScan(relative.as_posix(), module, package, findings).scan(
            path.read_text(encoding="utf-8")
        )
    return findings


def cyclic_edges(graph: grimp.ImportGraph) -> set[tuple[str, str]]:
    """Return all edges internal to a strongly connected component (Tarjan)."""
    adjacency = {
        module: graph.find_modules_directly_imported_by(module) for module in graph.modules
    }
    positions: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    pending: set[str] = set()
    edges: set[tuple[str, str]] = set()

    def visit(module: str) -> None:
        positions[module] = low[module] = len(positions)
        stack.append(module)
        pending.add(module)
        for dependency in adjacency[module]:
            if dependency not in positions:
                visit(dependency)
                low[module] = min(low[module], low[dependency])
            elif dependency in pending:
                low[module] = min(low[module], positions[dependency])
        if low[module] == positions[module]:
            component: set[str] = set()
            while module not in component:
                member = stack.pop()
                pending.remove(member)
                component.add(member)
            edges.update(
                (member, target) for member in component for target in adjacency[member] & component
            )

    for module in sorted(adjacency):
        if module not in positions:
            visit(module)
    return edges


def scan_cycles() -> Counter[str]:
    cycles: Counter[str] = Counter()
    for graph_name, skip_type_checking in (("runtime", True), ("full", False)):
        graph = grimp.build_graph(
            "marivo", exclude_type_checking_imports=skip_type_checking, cache_dir=None
        )
        for source, target in cyclic_edges(graph):
            cycles[f"{graph_name}: {source} -> {target}"] = 1
    return cycles


_COLD_PROBE = """
from importlib import import_module
from pathlib import Path
import json
import sys

root = Path.cwd()
sys.path.insert(0, str(root))
import_module(sys.argv[1])
if Path(sys.modules["marivo"].__file__).resolve() != root / "marivo" / "__init__.py":
    raise RuntimeError("cold probe imported a different checkout")
print(json.dumps(sorted(name for name in sys.modules if name.startswith("marivo."))))
"""


def _cold_import(root: Path, surface: str) -> list[str]:
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _COLD_PROBE, surface],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        )
    except subprocess.TimeoutExpired:
        return [f"ARCH006 {surface} cold import timed out"]
    if result.returncode != 0:
        return [f"ARCH006 {surface} cold import failed:\n{result.stderr.strip()}"]
    modules: list[str] = json.loads(result.stdout.splitlines()[-1])
    forbidden = DOMAIN_NAMES - COLD_IMPORTS[surface]
    return [
        f"ARCH006 {surface} cold import loaded {module}"
        for module in modules
        if module.split(".")[1] in forbidden
    ]


def check_cold_imports(root: Path) -> list[str]:
    """Probe each public surface from this checkout in a fresh interpreter."""
    with ThreadPoolExecutor(max_workers=len(COLD_IMPORTS)) as executor:
        probes = [executor.submit(_cold_import, root, surface) for surface in COLD_IMPORTS]
        return [error for probe in probes for error in probe.result()]


def load_baseline(path: Path) -> Baseline:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.keys() != set(RULES):
        raise ValueError(f"{path} must contain the five ratcheted rule maps")
    baseline: Baseline = {}
    for rule in RULES:
        entries = parsed[rule]
        if not isinstance(entries, dict):
            raise ValueError(f"{path}: {rule} must be an object")
        baseline[rule] = {}
        for key, budget in entries.items():
            if rule == "ARCH003":
                valid = isinstance(budget, str) and bool(budget.strip())
            else:
                valid = type(budget) is int and budget > 0
            if not valid:
                raise ValueError(f"{path}: invalid {rule} allowance {key!r}")
            baseline[rule][key] = budget
    return baseline


def compare(findings: Findings, baseline: Baseline) -> tuple[list[str], Baseline]:
    errors: list[str] = []
    remaining: Baseline = {rule: {} for rule in RULES}
    for rule in RULES:
        for key, count in sorted(findings[rule].items()):
            if not count:
                continue
            allowed = baseline[rule].get(key, 0)
            if isinstance(allowed, str):
                if count == 1:
                    remaining[rule][key] = allowed
                    continue
                allowed = 1
            if count > allowed:
                errors.append(f"{rule} {key} ({count}, baseline {allowed})")
            else:
                remaining[rule][key] = count
    return errors, remaining


def write_baseline(path: Path, baseline: Baseline) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as buffer:
        temporary = Path(buffer.name)
        try:
            json.dump(baseline, buffer, indent=2, sort_keys=True)
            buffer.write("\n")
            buffer.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--ratchet-down", action="store_true", help="remove retired baseline allowances"
    )
    mode.add_argument("--strict-ratchet", action="store_true", help="fail if allowances can shrink")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(BASELINE_PATH)
        with ThreadPoolExecutor(max_workers=1) as executor:
            cold_imports = executor.submit(check_cold_imports, ROOT)
            findings = scan_sources(ROOT)
            findings["ARCH004"] = scan_cycles()
            errors, remaining = compare(findings, baseline)
            errors.extend(cold_imports.result())
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        if remaining != baseline:
            if args.ratchet_down:
                write_baseline(BASELINE_PATH, remaining)
                print("Architecture baseline reduced.")
            else:
                print(
                    "Architecture baseline can shrink. Run scripts/check_architecture.py --ratchet-down."
                )
                if args.strict_ratchet:
                    return 1
        print("Architecture checks passed (ARCH001–ARCH006).")
        return 0
    except (ImportError, OSError, SyntaxError, ValueError) as error:
        print(f"Architecture check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
