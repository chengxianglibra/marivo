"""Reject architecture budget increases and new lint exemptions in a PR."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from configparser import ConfigParser
from importlib import import_module

tomllib = import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")

BASELINE = "scripts/architecture_baseline.json"
IMPORT_CONFIG = ".importlinter"
TOOL_CONFIG = "pyproject.toml"
PROTECTED_RUFF = {"PLC0415", "TID251"}
PROTECTED_MYPY = {"ignore-without-code", "redundant-expr", "truthy-bool", "possibly-undefined"}


def baseline_increases(before: str, after: str) -> list[str]:
    previous = json.loads(before)
    candidate = json.loads(after)
    if not isinstance(previous, dict) or not isinstance(candidate, dict):
        raise ValueError("Architecture baselines must be objects.")
    errors: list[str] = []
    for rule, entries in candidate.items():
        old_entries = previous.get(rule, {})
        if not isinstance(entries, dict) or not isinstance(old_entries, dict):
            raise ValueError(f"{rule} must be a baseline object.")
        if rule not in previous:
            errors.append(f"{BASELINE} adds rule {rule}")
        for key, allowed in entries.items():
            old = old_entries.get(key)
            unchanged_site = isinstance(old, str) and isinstance(allowed, str)
            lower_budget = type(old) is int and type(allowed) is int and 0 < allowed <= old
            if not (unchanged_site or lower_budget):
                errors.append(f"{BASELINE} adds or increases {rule} {key}")
    return errors


def _ignore_imports(config: ConfigParser) -> set[tuple[str, str, str]]:
    ignores: set[tuple[str, str, str]] = set()
    for section in config.sections():
        for line in config[section].get("ignore_imports", "").splitlines():
            if line.strip():
                source, target = line.split("->", maxsplit=1)
                ignores.add((section, source.strip(), target.strip()))
    return ignores


def import_config_relaxations(before: str, after: str) -> list[str]:
    previous = ConfigParser(interpolation=None)
    candidate = ConfigParser(interpolation=None)
    previous.read_string(before)
    candidate.read_string(after)
    errors = [
        f"{IMPORT_CONFIG} adds ignore_imports in [{section}] for {source} -> {target}"
        for section, source, target in sorted(
            _ignore_imports(candidate) - _ignore_imports(previous)
        )
    ]
    if candidate["importlinter"].getboolean("exclude_type_checking_imports", fallback=False):
        errors.append(f"{IMPORT_CONFIG} excludes TYPE_CHECKING imports")
    if previous["importlinter"].getboolean("include_external_packages", fallback=False) and not (
        candidate["importlinter"].getboolean("include_external_packages", fallback=False)
    ):
        errors.append(f"{IMPORT_CONFIG} excludes external packages")
    for section in previous.sections():
        if section not in candidate:
            # Stages can replace obsolete contracts with stricter ones.
            continue
        if candidate[section].getboolean("allow_indirect_imports", fallback=False) and not (
            previous[section].getboolean("allow_indirect_imports", fallback=False)
        ):
            errors.append(f"{IMPORT_CONFIG} relaxes indirect imports in [{section}]")
    return errors


def _ruff_exemptions(lint: dict[str, object]) -> set[tuple[str, str]]:
    patterns: dict[str, set[str]] = {}
    for key in ("per-file-ignores", "extend-per-file-ignores"):
        entries = lint.get(key, {})
        if not isinstance(entries, dict):
            raise ValueError(f"Ruff {key} must be a table.")
        for pattern, codes in entries.items():
            patterns.setdefault(pattern, set()).update(codes)
    for key in ("ignore", "extend-ignore"):
        codes = lint.get(key, [])
        if not isinstance(codes, list):
            raise ValueError(f"Ruff {key} must be an array.")
        patterns.setdefault("<global>", set()).update(codes)
    return {
        (pattern, code)
        for pattern, selectors in patterns.items()
        for code in PROTECTED_RUFF
        if any(selector == "ALL" or code.startswith(selector) for selector in selectors)
    }


def tool_config_relaxations(before: str, after: str) -> list[str]:
    previous = tomllib.loads(before)["tool"]
    candidate = tomllib.loads(after)["tool"]
    old_ruff = previous["ruff"]["lint"]
    ruff = candidate["ruff"]["lint"]
    errors = [
        f"{TOOL_CONFIG} adds Ruff {code} exemption for {pattern}"
        for pattern, code in sorted(_ruff_exemptions(ruff) - _ruff_exemptions(old_ruff))
    ]
    old_bans = old_ruff.get("flake8-tidy-imports", {}).get("banned-api", {})
    bans = ruff.get("flake8-tidy-imports", {}).get("banned-api", {})
    errors.extend(
        f"{TOOL_CONFIG} removes banned API {api}" for api in sorted(old_bans.keys() - bans.keys())
    )
    if not set(ruff.get("select", [])) >= PROTECTED_RUFF:
        errors.append(f"{TOOL_CONFIG} must select PLC0415 and TID251")
    mypy = candidate["mypy"]
    if not set(mypy.get("enable_error_code", [])) >= PROTECTED_MYPY or (
        PROTECTED_MYPY & set(mypy.get("disable_error_code", []))
    ):
        errors.append(f"{TOOL_CONFIG} disables a protected Mypy error code")
    if any(
        PROTECTED_MYPY & set(override.get("disable_error_code", []))
        for override in mypy.get("overrides", [])
    ):
        errors.append(f"{TOOL_CONFIG} adds a Mypy override for a protected error code")
    return errors


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _snapshot(revision: str, path: str) -> str:
    return _git("show", f"{revision}:{path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base commit of the pull request")
    parser.add_argument("--head", default="HEAD", help="candidate commit, usually the PR merge ref")
    args = parser.parse_args(argv)
    try:
        base = _git("rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}").strip()
        head = _git("rev-parse", "--verify", "--end-of-options", f"{args.head}^{{commit}}").strip()
        errors = import_config_relaxations(
            _snapshot(base, IMPORT_CONFIG), _snapshot(head, IMPORT_CONFIG)
        )
        after = _snapshot(head, BASELINE)
        if _git("ls-tree", "--name-only", base, "--", BASELINE).strip():
            errors.extend(baseline_increases(_snapshot(base, BASELINE), after))
            errors.extend(
                tool_config_relaxations(_snapshot(base, TOOL_CONFIG), _snapshot(head, TOOL_CONFIG))
            )
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print("Architecture governance diff passed.")
        return 0
    except subprocess.CalledProcessError as error:
        print(
            f"Could not read the governance diff from Git: {error.stderr.strip()}", file=sys.stderr
        )
    except (KeyError, ValueError) as error:
        print(f"Invalid architecture governance configuration: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
