"""Verify the finite R10.1 retirement ledger without executing business sources."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.r101_package_contents import RETIRED_PATHS

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = Path("docs/superpowers/specs/2026-10-06-marivo-r101-inventory")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected an exact ledger object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected ledger text")
    return value


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected ledger array")
    return list(value)


def _load(path: Path) -> object:
    value: object = json.loads(path.read_text())
    return value


def audit(root: Path = ROOT) -> dict[str, str | int | dict[str, int]]:
    """Check exact inventory reconstruction, test dispositions and current owners."""
    inventory = root / INVENTORY
    index = _object(_load(inventory / "baseline-tests-index.json"))
    records: list[dict[str, object]] = []
    for item in _array(index["shards"]):
        shard = _object(item)
        name = _text(shard["path"])
        if Path(name).name != name:
            raise ValueError("shard name must be local to the inventory")
        data = (inventory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != shard["sha256"]:
            raise ValueError("baseline shard hash differs")
        selected = [_object(json.loads(line)) for line in data.splitlines()]
        if len(selected) != shard["count"]:
            raise ValueError("baseline shard count differs")
        records.extend(selected)
    if len(records) != index["count"] or len(records) != 4224:
        raise ValueError("baseline function denominator differs")
    groups = _object(_load(inventory / "invariant-groups.json"))
    for paths in groups.values():
        if any(not (root / _text(path)).is_file() for path in _array(paths)):
            raise ValueError("current invariant owner is absent")
    definitions: dict[str, dict[str, str]] = {}
    seen: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for row in records:
        path, name = _text(row["path"]), _text(row["name"])
        if (path, name) in seen or row["invariant_group"] not in groups:
            raise ValueError("duplicate test identity or unowned invariant")
        seen.add((path, name))
        if path not in definitions:
            source = root / path
            text = source.read_text() if source.is_file() else ""
            definitions[path] = {
                node.name: hashlib.sha256(
                    (ast.get_source_segment(text, node) or "").encode()
                ).hexdigest()
                for node in ast.walk(ast.parse(text))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            }
        current = definitions[path].get(name)
        disposition = _text(row["disposition"])
        expected = (
            "retired_legacy_assertion"
            if current is None
            else "retained"
            if current == row["sha256"]
            else "updated_current_consumer"
        )
        if disposition != expected or current != row["current_sha256"]:
            raise ValueError(f"stale test disposition: {path}::{name}")
        counts[disposition] += 1
    retired = [_object(item) for item in _array(_load(inventory / "retired-modules.json"))]
    paths = {_text(row["path"]) for row in retired}
    if paths != RETIRED_PATHS or len(paths) != len(retired):
        raise ValueError("retired module denominator differs")
    if any((root / path).exists() for path in paths):
        raise ValueError("retired module returned")
    return {
        "status": "passed",
        "evidence_kind": "finite_static_disposition_only",
        "baseline": _text(index["baseline"]),
        "baseline_functions": len(records),
        "retired_modules": len(paths),
        "dispositions": dict(sorted(counts.items())),
    }


if __name__ == "__main__":
    print(json.dumps(audit(), sort_keys=True))
