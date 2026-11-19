"""Assign default-suite test modules to duration-balanced CI shards.

Pytest still collects only the files in the selected shard, so shard runtime
tracks measured file duration instead of paying a full-suite collection on
every machine. Files pytest would collect that are absent from the map are
reported by ``--check`` and are not dropped: shard 1 runs them too.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml


def discover_test_files(root: Path) -> set[str]:
    """Return repo-relative modules the default pytest file patterns collect."""
    tests = root / "tests"
    discovered: set[str] = set()
    for pattern in ("test_*.py", "*_test.py"):
        for path in tests.rglob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                discovered.add(path.relative_to(root).as_posix())
    return discovered


def pack_files(durations: Mapping[str, float], shard_count: int) -> dict[int, list[str]]:
    """Greedy longest-first packing. Shard numbers are 1-based."""
    if shard_count < 1:
        raise ValueError(f"shard_count must be positive; received {shard_count}")
    totals = dict.fromkeys(range(1, shard_count + 1), 0.0)
    shards: dict[int, list[str]] = {shard: [] for shard in totals}
    for path, duration in sorted(durations.items(), key=lambda item: (-item[1], item[0])):
        shard = min(totals, key=lambda number: (totals[number], number))
        shards[shard].append(path)
        totals[shard] += duration
    for files in shards.values():
        files.sort()
    return shards


def load_shards(path: Path) -> dict[int, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("shards"), dict):
        raise ValueError(f"{path} must be an object with a shards object")
    shards: dict[int, list[str]] = {}
    for key, value in payload["shards"].items():
        shard = int(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"shard {key} must be a list of paths")
        shards[shard] = list(value)
    return shards


def unassigned_files(discovered: set[str], shards: Mapping[int, Sequence[str]]) -> list[str]:
    assigned = {path for files in shards.values() for path in files}
    return sorted(discovered - assigned)


def validate_map(
    root: Path, shards: Mapping[int, Sequence[str]], *, expected_shards: set[int]
) -> list[str]:
    discovered = discover_test_files(root)
    errors: list[str] = []
    if shards.keys() != expected_shards:
        errors.append(
            f"shard map IDs {sorted(shards)} do not match CI matrix IDs {sorted(expected_shards)}"
        )
    seen: dict[str, int] = {}
    for shard, files in sorted(shards.items()):
        for path in files:
            if path in seen:
                errors.append(f"{path} is assigned to shards {seen[path]} and {shard}")
            else:
                seen[path] = shard
            if path not in discovered:
                errors.append(f"shard {shard} lists {path}, which pytest would not collect")
    missing = unassigned_files(discovered, shards)
    if missing:
        preview = ", ".join(missing[:8])
        suffix = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
        errors.append(f"{len(missing)} test modules are not in the shard map: {preview}{suffix}")
    return errors


def files_for_shard(
    root: Path,
    shards: Mapping[int, Sequence[str]],
    shard: int,
) -> list[str]:
    if shard not in shards:
        raise SystemExit(f"shard {shard} is not in the map; expected one of {sorted(shards)}")
    selected = list(shards[shard])
    if shard == min(shards):
        selected.extend(unassigned_files(discover_test_files(root), shards))
    return sorted(selected)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--map", type=Path, default=None)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    map_path = args.map or (root / "scripts" / "ci_shards.json")
    shards = load_shards(map_path)
    if args.check:
        workflow_path = root / ".github" / "workflows" / "ci.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        expected_shards = set(workflow["jobs"]["tests"]["strategy"]["matrix"]["shard"])
        errors = validate_map(root, shards, expected_shards=expected_shards)
        if errors:
            print("CI shard map is stale:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        print(f"CI shard map covers {len(discover_test_files(root))} test modules")
        return 0
    if args.shard is None:
        raise SystemExit("pass --shard or --check")
    for path in files_for_shard(root, shards, args.shard):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
