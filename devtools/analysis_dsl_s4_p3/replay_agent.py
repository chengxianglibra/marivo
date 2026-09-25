"""Replay one archived Agent answer against its installed public wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    evidence = args.evidence.resolve()
    answer = evidence / "answer.py"
    if not answer.is_file():
        parser.error(f"Missing archived Agent answer: {answer}")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MARIVO_PROJECT_ROOT"] = str(project)
    env["MARIVO_TELEMETRY"] = "off"
    completed = subprocess.run(
        [str(args.python.absolute()), str(answer)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    (evidence / "replay.stdout.txt").write_text(completed.stdout)
    (evidence / "replay.stderr.txt").write_text(completed.stderr)
    metadata = {
        "project": str(project),
        "python": str(args.python.absolute()),
        "answer_sha256": hashlib.sha256(answer.read_bytes()).hexdigest(),
        "returncode": completed.returncode,
    }
    (evidence / "replay.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metadata, sort_keys=True))


if __name__ == "__main__":
    main()
