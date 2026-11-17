"""Fail a preserved ``check (<python>)`` job unless that version's CI passed.

The test shards and per-version typecheck are separate jobs so they can run
in parallel. This gate is what branch protection continues to see as
``check (3.10)``, ``check (3.12)``, ``check (3.13)`` and ``check (3.14)``.
Lint and docs run once, as steps of the static Python version's typecheck
job, and every version's gate requires those steps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobView:
    name: str
    conclusion: str | None
    run_attempt: int
    steps: tuple[tuple[str, str | None], ...]


def _latest_jobs(jobs: Sequence[JobView]) -> dict[str, JobView]:
    chosen: dict[str, JobView] = {}
    for job in jobs:
        current = chosen.get(job.name)
        if current is None or job.run_attempt >= current.run_attempt:
            chosen[job.name] = job
    return chosen


def gate_errors(
    jobs: Sequence[JobView],
    *,
    python_version: str,
    shard_count: int,
    static_python: str,
) -> list[str]:
    """Return concrete failures. An empty list means this version's gate passed."""
    if shard_count < 1:
        raise ValueError(f"shard_count must be positive; received {shard_count}")
    latest = _latest_jobs(jobs)
    errors: list[str] = []

    def require_job(name: str) -> JobView | None:
        job = latest.get(name)
        if job is None:
            errors.append(f"missing job {name!r}")
            return None
        if job.conclusion != "success":
            errors.append(f"job {name!r} concluded {job.conclusion!r}; expected 'success'")
        return job

    for shard in range(1, shard_count + 1):
        require_job(f"tests ({python_version} shard {shard})")
    require_job(f"typecheck ({python_version})")

    static_name = f"typecheck ({static_python})"
    static_job = latest.get(static_name)
    if static_job is None:
        errors.append(f"missing job {static_name!r}")
    else:
        step_conclusions = dict(static_job.steps)
        for step_name in ("Lint", "Docs"):
            conclusion = step_conclusions.get(step_name)
            if conclusion != "success":
                errors.append(
                    f"step {step_name!r} on {static_name!r} concluded {conclusion!r}; "
                    "expected 'success'"
                )
    return errors


def jobs_from_api_payload(payload: Mapping[str, object]) -> list[JobView]:
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError("jobs payload is missing a jobs list")
    jobs: list[JobView] = []
    for raw in raw_jobs:
        if not isinstance(raw, Mapping):
            raise ValueError("jobs payload contains a non-object job")
        name = raw.get("name")
        attempt = raw.get("run_attempt")
        if not isinstance(name, str) or not isinstance(attempt, int):
            raise ValueError(f"job is missing a name or run_attempt: {raw!r}")
        conclusion = raw.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, str):
            raise ValueError(f"job {name!r} has a non-text conclusion")
        raw_steps = raw.get("steps") or []
        if not isinstance(raw_steps, list):
            raise ValueError(f"job {name!r} has a non-list steps field")
        steps: list[tuple[str, str | None]] = []
        for step in raw_steps:
            if not isinstance(step, Mapping):
                raise ValueError(f"job {name!r} contains a non-object step")
            step_name = step.get("name")
            step_conclusion = step.get("conclusion")
            if not isinstance(step_name, str):
                raise ValueError(f"job {name!r} has a step without a name")
            if step_conclusion is not None and not isinstance(step_conclusion, str):
                raise ValueError(f"step {step_name!r} has a non-text conclusion")
            steps.append((step_name, step_conclusion))
        jobs.append(
            JobView(
                name=name,
                conclusion=conclusion,
                run_attempt=attempt,
                steps=tuple(steps),
            )
        )
    return jobs


def fetch_jobs(repository: str, run_id: str, token: str) -> list[JobView]:
    jobs: list[JobView] = []
    page = 1
    while True:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs"
            f"?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("jobs response was not an object")
        page_jobs = jobs_from_api_payload(payload)
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            return jobs
        page += 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--static-python", default="3.12")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--jobs-json", help="Read a jobs API payload from this file instead.")
    args = parser.parse_args(argv)

    if args.jobs_json:
        payload = json.loads(Path(args.jobs_json).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SystemExit("jobs JSON must be an object")
        jobs = jobs_from_api_payload(payload)
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not args.repository or not args.run_id or not token:
            raise SystemExit("repository, run id, and GITHUB_TOKEN are required")
        jobs = fetch_jobs(args.repository, args.run_id, token)

    errors = gate_errors(
        jobs,
        python_version=args.python_version,
        shard_count=args.shards,
        static_python=args.static_python,
    )
    if errors:
        print("CI gate failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"CI gate passed for Python {args.python_version}: "
        f"{args.shards} test shards, typecheck, lint, and docs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
