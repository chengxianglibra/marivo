"""Prepare and verify installed-package public journey evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fixture import Journey, prepare
from oracle import expected, verify

JOURNEYS: tuple[Journey, ...] = ("j1", "j2", "j3", "j4")


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _child_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MARIVO_PROJECT_ROOT"] = str(root.resolve())
    env["MARIVO_TELEMETRY"] = "off"
    return env


def _run(python: Path, script: Path, root: Path, *args: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(python), str(script), str(root), *args],
        cwd=root,
        env=_child_env(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"{script.name} failed in {root} with {completed.returncode}:\n{completed.stderr}"
        )
    payload: dict[str, Any] = json.loads(completed.stdout)
    return payload


def prepare_projects(workspace: Path, evidence: Path) -> None:
    projects = [
        prepare(workspace / kind / journey, journey)
        for kind in ("user", "agent")
        for journey in JOURNEYS
    ]
    for index in range(len(JOURNEYS)):
        assert projects[index]["facts_sha256"] == projects[index + 4]["facts_sha256"]
    _write(evidence / "projects.json", projects)


def run_scripts(workspace: Path, evidence: Path, python: Path, wheel: Path) -> None:
    metadata = subprocess.run(
        [
            str(python),
            "-c",
            "import json,marivo,marivo.analysis,importlib.metadata as m;"
            "print(json.dumps({'version':m.version('marivo'),'module':marivo.__file__,"
            "'analysis_module':marivo.analysis.__file__,"
            "'direct_url':m.distribution('marivo').read_text('direct_url.json')}))",
        ],
        cwd=workspace,
        env=_child_env(workspace),
        capture_output=True,
        text=True,
        check=True,
    )
    installed = json.loads(metadata.stdout)
    assert str(python.parent.parent.resolve()) in installed["module"]
    installed["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    installed["wheel"] = str(wheel)
    _write(evidence / "installation.json", installed)

    directory = Path(__file__).resolve().parent
    for journey in JOURNEYS:
        root = workspace / "user" / journey
        result = _run(python, directory / f"{journey}.py", root)
        oracle = expected(root, journey)
        verify(result, oracle)
        _write(evidence / "user" / f"{journey}.json", result)
        _write(evidence / "oracle" / f"{journey}.json", oracle)

        database = root / "warehouse.duckdb"
        offline = root / "warehouse.offline"
        database.rename(offline)
        try:
            recovery = _run(
                python,
                directory / "recover.py",
                root,
                journey,
                result["session_id"],
                result["artifacts"]["fixed"],
            )
        finally:
            offline.rename(database)
        assert recovery["session_id"] == result["session_id"]
        assert recovery["artifact_ref"] == result["artifacts"]["fixed"]
        continuation_name = {
            "j1": "fixed_rollup",
            "j2": "decliners",
            "j3": "fixed_rollup",
            "j4": "negative",
        }[journey]
        assert recovery["continuation"] == result[continuation_name]
        if journey == "j1":
            assert recovery["failed_source"] is not None
            assert any(
                word in recovery["failed_source_detail"].lower()
                for word in ("duckdb", "source", "warehouse", "database")
            )
        _write(evidence / "recovery" / f"{journey}.json", recovery)
        print(f"{journey}: public script, independent oracle, and cold recovery passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "scripts"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare_projects(args.workspace, args.evidence)
    else:
        if args.python is None or args.wheel is None:
            parser.error("scripts requires --python and --wheel")
        run_scripts(args.workspace, args.evidence, args.python, args.wheel)


if __name__ == "__main__":
    main()
