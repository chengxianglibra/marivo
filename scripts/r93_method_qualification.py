"""Capture bounded R9.3 invocation evidence without granting scenario passes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from scripts import r9_qualification_requirements as freeze


def run(
    directory: Path,
    selection: str = "",
    test_paths: tuple[str, ...] = ("tests/test_r93_method_consumers.py",),
    all_statistical_backends: bool = False,
) -> int:
    directory.mkdir(parents=True, exist_ok=False)
    payload = freeze.build()
    freeze.save(payload, directory / "freeze")
    requirements = [
        row for row in freeze.arr(payload["requirements"]) if freeze.obj(row)["gap_owner"] == "R9.3"
    ]
    (directory / "inventory.json").write_bytes(
        freeze.encode(
            {
                "required": len(requirements),
                "requirements": requirements,
                "authority": "Frozen obligations; invocation observations do not grant complete scenario qualification",
            }
        )
    )
    command = [
        str(freeze.ROOT / ".venv/bin/pytest"),
        "-q",
        "-n",
        "0",
        "-m",
        "runtime",
        *test_paths,
        "--tb=short",
        "--maxfail=5",
        f"--junitxml={directory / 'junit.xml'}",
    ]
    if selection:
        command.extend(("-k", selection))
    started = datetime.now(timezone.utc).isoformat()
    environment = dict(os.environ, MARIVO_R93_EVIDENCE_DIR=str(directory))
    phases: list[freeze.Json] = []
    exit_code = 0
    if all_statistical_backends:
        command = [
            sys.executable,
            "-m",
            "scripts.r93_method_qualification",
            "--directory",
            str(directory),
            "--all-statistical-backends",
        ]
        environment.update(
            {
                "MARIVO_" + backend + "_ANALYSIS_TEST": "1"
                for backend in ("POSTGRES", "MYSQL", "TRINO", "CLICKHOUSE")
            }
        )
        merged = ET.Element("testsuites")
        for backend, paths, selector in (
            (
                "trino",
                (
                    "tests/test_r93_retained_numeric.py",
                    "tests/test_r93_source_deadline.py",
                    "tests/test_r93_method_consumers.py",
                ),
                "(retained or duckdb or sqlite or postgres or trino or expiry or interrupt or unconfirmed) and not clickhouse",
            ),
            (
                "clickhouse",
                (
                    "tests/test_r93_source_deadline.py",
                    "tests/test_r93_method_consumers.py",
                ),
                "mysql or clickhouse",
            ),
        ):
            service = ["bash", "tests/multisource_environment/manage.sh", "start", backend]
            subprocess.run(service, cwd=freeze.ROOT, env=environment, check=True)
            phase_command = [
                str(freeze.ROOT / ".venv/bin/pytest"),
                "-q",
                "-n",
                "0",
                "-m",
                "runtime",
                *paths,
                "--tb=short",
                "--maxfail=5",
                f"--junitxml={directory / ('junit-' + backend + '.xml')}",
                "-k",
                selector,
            ]
            phase_started = datetime.now(timezone.utc).isoformat()
            with (directory / ("test-" + backend + ".log")).open("wb") as log:
                observed = subprocess.run(
                    phase_command,
                    cwd=freeze.ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            phases.append(
                {
                    "backend": backend,
                    "service_command": freeze.checked(service),
                    "command": freeze.checked(phase_command),
                    "started": phase_started,
                    "finished": datetime.now(timezone.utc).isoformat(),
                    "exit_code": observed.returncode,
                }
            )
            tree = ET.parse(directory / ("junit-" + backend + ".xml")).getroot()
            merged.extend(list(tree) if tree.tag == "testsuites" else [tree])
            exit_code = observed.returncode
            if exit_code:
                break
        ET.ElementTree(merged).write(
            directory / "junit.xml", encoding="utf-8", xml_declaration=True
        )
        (directory / "test.log").write_text(
            "\n".join(
                (directory / ("test-" + str(freeze.obj(phase)["backend"]) + ".log")).read_text()
                for phase in phases
            )
        )
    else:
        with (directory / "test.log").open("wb") as log:
            observed = subprocess.run(
                command,
                cwd=freeze.ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        exit_code = observed.returncode
    if freeze.candidate()["content_sha256"] != freeze.obj(payload["candidate"])["content_sha256"]:
        raise ValueError("Candidate changed; retain observations without qualification")
    (directory / "run.json").write_bytes(
        freeze.encode(
            {
                "candidate_sha256": freeze.obj(payload["candidate"])["content_sha256"],
                "command": freeze.checked(command),
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "exit_code": exit_code,
                "phases": phases,
                "attachments": {
                    file.name: freeze.digest(file.read_bytes())
                    for file in sorted(directory.iterdir())
                    if file.is_file()
                },
                "boundary": "Bounded selected invocations only; complete R9.3 scenario statuses are not granted",
            }
        )
    )
    print((directory / "test.log").read_text())
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--selection", default="")
    parser.add_argument("--tests", nargs="+", default=["tests/test_r93_method_consumers.py"])
    parser.add_argument("--all-statistical-backends", action="store_true")
    args = parser.parse_args()
    if args.all_statistical_backends and (
        args.selection or args.tests != ["tests/test_r93_method_consumers.py"]
    ):
        parser.error("all-statistical-backends owns its closed phase selections")
    raise SystemExit(
        run(
            args.directory.resolve(),
            args.selection,
            tuple(args.tests),
            args.all_statistical_backends,
        )
    )
