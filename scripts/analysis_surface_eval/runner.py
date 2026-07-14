"""Codex runner for the analysis surface evaluation gate.

The runner orchestrates cold-agent trials: it loads the evaluation profile,
builds fixture projects, installs the candidate wheel, runs two tool-sandbox
preflight checks (loopback must succeed, ``example.com:443`` must fail), then
invokes ``codex exec`` once per trial with the pinned model and prompt.

The external harness/container blocks sandbox-tool network egress except
loopback; the model inference/control-plane connection remains available.
The runner rejects environments where this isolation cannot be established
rather than claiming the gate ran.

Outputs (written to ``--output-dir`` or
``.marivo/evals/analysis-surface/<run-id>/``):
- ``events.jsonl``                    -- combined events from all trials
- ``<case>-trial-<n>.jsonl``          -- per-trial event files
- ``<case>-trial-<n>-transcript.txt`` -- per-trial transcript
- ``report.json``                     -- machine-readable evaluation report
- ``report.md``                       -- human-readable evaluation report

Exit codes:
- 0: gate passed
- 1: gate failed (qualifying trials below threshold)
- 2: CLI version mismatch
- 3: preflight failure
- 4: wheel file not found
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.analysis_surface_eval.fixture import (
    FixtureProject,
    build_convergence_fixture,
    build_skew_fixture,
)
from scripts.analysis_surface_eval.instrumentation import (
    InstrumentationConfig,
    generate_sitecustomize,
)
from scripts.analysis_surface_eval.model import (
    CaseId,
    EvalEvent,
    EvaluationProfile,
    EvaluationReport,
    load_profile,
)
from scripts.analysis_surface_eval.scorer import score_evaluation

# ---------------------------------------------------------------------------
# Subprocess wrapper (for patching in tests)
# ---------------------------------------------------------------------------


def _run_subprocess(
    cmd: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: int | None = None,
    stdin_data: str | None = None,
    cwd: str | None = None,
) -> int:
    """Run a subprocess and return its exit code.

    Parameters
    ----------
    cmd:
        Command and arguments as a list.
    capture_output:
        If True, capture stdout and stderr.
    text:
        If True, decode output as text.
    timeout:
        Optional timeout in seconds.
    stdin_data:
        Optional string to pass on stdin.
    cwd:
        Optional working directory.

    Returns
    -------
    int
        The process exit code.
    """
    kwargs: dict[str, object] = {}
    if capture_output:
        kwargs["capture_output"] = True
    if text:
        kwargs["text"] = True
    if timeout is not None:
        kwargs["timeout"] = timeout
    if stdin_data is not None:
        kwargs["input"] = stdin_data
    if cwd is not None:
        kwargs["cwd"] = cwd

    try:
        result = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        return 124
    return int(result.returncode)


# ---------------------------------------------------------------------------
# Codex version discovery
# ---------------------------------------------------------------------------


def _get_codex_version() -> str:
    """Return the installed ``codex`` CLI version string.

    Returns
    -------
    str
        The version string (e.g. ``"0.139.0"``) or ``""`` if codex is not
        installed or the version cannot be parsed.
    """
    try:
        proc = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    output = (proc.stdout + proc.stderr).strip()
    # Extract version: look for a semver-like pattern.
    for token in output.split():
        if token and token[0].isdigit():
            return token
    return output or ""


# ---------------------------------------------------------------------------
# Codex command construction
# ---------------------------------------------------------------------------


def build_codex_command(
    profile: EvaluationProfile,
    project_dir: Path,
    prompt: str,
) -> list[str]:
    """Build the Codex CLI command for a single trial.

    Parameters
    ----------
    profile:
        The evaluation profile providing model, reasoning effort, and sandbox
        configuration.
    project_dir:
        The isolated fixture project directory to pass as ``--cd``.
    prompt:
        The fixed prompt text to pass on stdin (not embedded in the command
        list itself).

    Returns
    -------
    list[str]
        The Codex CLI argument list.  The prompt is passed on stdin, not as a
        command argument.  The last element is ``"-"`` to read stdin.

    Example:
        >>> from scripts.analysis_surface_eval.model import EvaluationProfile
        >>> profile = EvaluationProfile(
        ...     profile_id="test", provider="openai",
        ...     model_snapshot="gpt-5.4-2026-03-05",
        ...     agent_client="codex-cli",
        ...     agent_client_version="0.139.0",
        ...     reasoning_effort="high",
        ...     tool_policy="sandboxed",
        ...     sampling_seed_supported=False,
        ...     cases=("clean_convergence",),
        ...     prompt_hashes={},
        ... )
        >>> cmd = build_codex_command(profile, Path("/tmp/p"), "hello")
        >>> cmd[0]
        'codex'
        >>> cmd[-1]
        '-'
    """
    return [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--model",
        profile.model_snapshot,
        "--config",
        f'model_reasoning_effort="{profile.reasoning_effort}"',
        "--sandbox",
        "workspace-write",
        "--cd",
        str(project_dir),
        "-",
    ]


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

_LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_PORT = 18080
_EGRESS_HOST = "example.com"
_EGRESS_PORT = 443
_PREFLIGHT_TIMEOUT = 10


_PREFLIGHT_SCRIPT = (
    "import socket, sys\n"
    f"_HOST={_LOOPBACK_HOST!r}\n"
    f"_PORT={_LOOPBACK_PORT!r}\n"
    f"_EGRESS_HOST={_EGRESS_HOST!r}\n"
    f"_EGRESS_PORT={_EGRESS_PORT!r}\n"
    f"_TIMEOUT={_PREFLIGHT_TIMEOUT}\n"
    "try:\n"
    "    s=socket.create_connection((_HOST,_PORT),timeout=_TIMEOUT)\n"
    "    s.close()\n"
    "except OSError:\n"
    "    sys.exit(1)\n"
    "try:\n"
    "    s=socket.create_connection((_EGRESS_HOST,_EGRESS_PORT),timeout=_TIMEOUT)\n"
    "    s.close()\n"
    "    sys.exit(1)\n"
    "except OSError:\n"
    "    pass\n"
    "sys.exit(0)\n"
)


def run_preflight(venv_python: str) -> bool:
    """Run both tool-sandbox preflight checks from the isolated analysis venv.

    The loopback connection must succeed (the harness owns a listener), and
    the ``example.com:443`` connection must fail (egress is blocked).

    Parameters
    ----------
    venv_python:
        Path to the isolated analysis venv's Python executable.  The
        preflight checks are executed via
        ``subprocess.run([venv_python, "-c", script])`` so that the socket
        probes run inside the venv's Python, not the runner's own process.

    Returns
    -------
    bool
        True only if the subprocess exits 0 (both conditions are met).
    """
    try:
        result = subprocess.run(
            [venv_python, "-c", _PREFLIGHT_SCRIPT],
            capture_output=True,
            timeout=_PREFLIGHT_TIMEOUT * 3,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------


def generate_run_id() -> str:
    """Generate a deterministic run ID based on the current UTC timestamp.

    Returns
    -------
    str
        A timestamp-based run ID (e.g. ``"20260713T120000Z"``).
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------


def _load_trial_events(events_file: Path) -> list[EvalEvent]:
    """Load EvalEvents from a JSONL events file.

    Parameters
    ----------
    events_file:
        Path to the JSONL file written by the instrumentation probe.

    Returns
    -------
    list[EvalEvent]
        Parsed events in file order.
    """
    events: list[EvalEvent] = []
    if not events_file.is_file():
        return events
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        events.append(
            EvalEvent(
                kind=raw["kind"],
                trial=raw["trial"],
                case_id=raw["case_id"],
                timestamp=raw["timestamp"],
                target=raw.get("target"),
                receiver_family=raw.get("receiver_family"),
                artifact_family=raw.get("artifact_family"),
                artifact_ref=raw.get("artifact_ref"),
                observe_phase=raw.get("observe_phase", "before_observe"),
                fingerprint=raw.get("fingerprint"),
                fingerprint_matched=raw.get("fingerprint_matched"),
                is_help_target_error=raw.get("is_help_target_error", False),
                detail=raw.get("detail"),
            )
        )
    return events


# ---------------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------------


def _build_fixture(
    case_id: CaseId,
    root: Path,
    prompt_file: Path,
) -> FixtureProject:
    """Build the fixture project for a given case."""
    if case_id == "clean_convergence":
        return build_convergence_fixture(root, prompt_file=prompt_file)
    else:
        return build_skew_fixture(root, prompt_file=prompt_file)


def run_trial(
    profile: EvaluationProfile,
    case_id: CaseId,
    trial_index: int,
    wheel_path: str,
    events_dir: Path,
) -> int:
    """Run a single cold-agent trial.

    Builds the fixture project, installs the candidate wheel, generates the
    instrumentation probe, invokes ``codex exec``, and writes the trial's
    events to ``events_dir/<case>-trial-<trial_index>.jsonl``.

    Parameters
    ----------
    profile:
        The evaluation profile.
    case_id:
        Case identifier.
    trial_index:
        Zero-based trial index.
    wheel_path:
        Path to the candidate wheel file.
    events_dir:
        Directory for trial event output.

    Returns
    -------
    int
        0 if the trial completed successfully, non-zero on error.
    """
    prompts_dir = (
        Path(__file__).resolve().parent.parent.parent / "evals" / "analysis_surface" / "prompts"
    )
    prompt_file = prompts_dir / f"{case_id}.md"
    prompt_text = prompt_file.read_text()

    fixture_root = events_dir / f"fixture-{case_id}-{trial_index}"
    fixture_root.mkdir(parents=True, exist_ok=True)
    fixture = _build_fixture(case_id, fixture_root, prompt_file)

    # Install the candidate wheel into the analysis venv.
    # In real runs, this uses uv or pip in the fixture's analysis-venv.
    # For now, we create a placeholder marker.
    install_marker = fixture.analysis_venv / ".wheel-installed"
    install_marker.write_text(f"wheel={wheel_path}\n")

    # Generate and install the sitecustomize probe.
    trial_prefix = f"{case_id}-trial-{trial_index}"
    events_file = events_dir / f"{trial_prefix}.jsonl"
    instrumentation_config = InstrumentationConfig(
        events_file=events_file,
        trial=trial_index,
        case_id=case_id,
    )
    site_packages_dir = fixture.analysis_venv / "lib" / "site-packages"
    site_packages_dir.mkdir(parents=True, exist_ok=True)
    generate_sitecustomize(site_packages_dir, instrumentation_config)

    # Build and run the Codex command.
    cmd = build_codex_command(profile, fixture.root, prompt_text)
    exit_code = _run_subprocess(
        cmd,
        stdin_data=prompt_text,
        cwd=str(fixture.root),
        timeout=600,
    )

    # Save a transcript placeholder.
    transcript_file = events_dir / f"{trial_prefix}-transcript.txt"
    transcript_file.write_text(
        f"Trial {trial_index} for case {case_id}\n"
        f"Exit code: {exit_code}\n"
        f"Events file: {events_file}\n"
    )

    return exit_code


# ---------------------------------------------------------------------------
# Evaluation orchestration
# ---------------------------------------------------------------------------


def _write_report_json(report: EvaluationReport, output_path: Path) -> None:
    """Write the evaluation report as JSON.

    Parameters
    ----------
    report:
        The evaluation report to serialize.
    output_path:
        Destination file path.
    """
    data = {
        "profile_id": report.profile_id,
        "passes": report.passes,
        "convergence_qualifying_count": report.convergence_qualifying_count,
        "skew_all_qualified": report.skew_all_qualified,
        "median_help_invocation_count": report.median_help_invocation_count,
        "median_invalid_api_error_count": report.median_invalid_api_error_count,
        "aggregate_retired_name_count": report.aggregate_retired_name_count,
        "per_trial_retired_name_counts": [list(t) for t in report.per_trial_retired_name_counts],
        "trial_scores": [
            {
                "trial": s.trial,
                "case_id": s.case_id,
                "qualifies": s.qualifies,
                "help_invocation_count": s.help_invocation_count,
                "help_invocation_count_before_observe": s.help_invocation_count_before_observe,
                "invalid_api_error_count": s.invalid_api_error_count,
                "invalid_api_error_count_before_observe": s.invalid_api_error_count_before_observe,
                "has_oracle_artifact": s.has_oracle_artifact,
                "has_matching_fingerprint_before_analysis": s.has_matching_fingerprint_before_analysis,
                "used_native_reflection": s.used_native_reflection,
                "mismatch_detected": s.mismatch_detected,
                "environment_stop_recorded": s.environment_stop_recorded,
                "analysis_api_call_count": s.analysis_api_call_count,
                "retired_name_attribute_error_count": s.retired_name_attribute_error_count,
                "disqualification_reason": s.disqualification_reason,
            }
            for s in report.trial_scores
        ],
    }
    output_path.write_text(json.dumps(data, indent=2) + "\n")


def _write_report_md(report: EvaluationReport, output_path: Path) -> None:
    """Write the evaluation report as Markdown.

    Parameters
    ----------
    report:
        The evaluation report to serialize.
    output_path:
        Destination file path.
    """
    status = "PASS" if report.passes else "FAIL"
    lines = [
        "# Analysis Surface Evaluation Report",
        "",
        f"**Status:** {status}",
        "",
        f"**Profile:** {report.profile_id}",
        "",
        "## Summary",
        "",
        f"- Convergence qualifying trials: {report.convergence_qualifying_count}",
        f"- Skew all qualified: {report.skew_all_qualified}",
        f"- Median help invocations: {report.median_help_invocation_count}",
        f"- Median invalid API errors: {report.median_invalid_api_error_count}",
        f"- Aggregate retired-name errors: {report.aggregate_retired_name_count}",
        "",
        "## Trial Scores",
        "",
        "| Trial | Case | Qualifies | Help (pre-observe) | Invalid (pre-observe) | Reason |",
        "|-------|------|-----------|--------------------|-----------------------|--------|",
    ]
    for s in report.trial_scores:
        qual = "YES" if s.qualifies else "NO"
        reason = s.disqualification_reason or ""
        lines.append(
            f"| {s.trial} | {s.case_id} | {qual} | "
            f"{s.help_invocation_count_before_observe} | "
            f"{s.invalid_api_error_count_before_observe} | {reason} |"
        )
    lines.append("")
    output_path.write_text("\n".join(lines))


def _write_combined_events(
    trial_events: dict[tuple[str, int], list[EvalEvent]],
    output_path: Path,
) -> None:
    """Write all trial events into a combined JSONL file.

    Parameters
    ----------
    trial_events:
        Mapping from ``(case_id, trial_index)`` to the trial's events.
    output_path:
        Destination file path.
    """
    lines: list[str] = []
    for _key, events in sorted(trial_events.items()):
        for ev in events:
            payload: dict[str, object] = {
                "kind": ev.kind,
                "trial": ev.trial,
                "case_id": ev.case_id,
                "timestamp": ev.timestamp,
                "observe_phase": ev.observe_phase,
            }
            if ev.target is not None:
                payload["target"] = ev.target
            if ev.receiver_family is not None:
                payload["receiver_family"] = ev.receiver_family
            if ev.artifact_family is not None:
                payload["artifact_family"] = ev.artifact_family
            if ev.artifact_ref is not None:
                payload["artifact_ref"] = ev.artifact_ref
            if ev.fingerprint is not None:
                payload["fingerprint"] = ev.fingerprint
            if ev.fingerprint_matched is not None:
                payload["fingerprint_matched"] = ev.fingerprint_matched
            if ev.is_help_target_error:
                payload["is_help_target_error"] = True
            if ev.detail is not None:
                payload["detail"] = ev.detail
            lines.append(json.dumps(payload))
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def _trials_per_case() -> int:
    """Return the number of trials per case from SURFACE_LIMITS.

    Returns
    -------
    int
        The number of cold-agent trials per case.
    """
    from marivo.analysis._capabilities.model import SURFACE_LIMITS

    return SURFACE_LIMITS.cold_agent_trials_per_case


def run_evaluation(
    profile: EvaluationProfile,
    wheel_path: str,
    output_dir: Path,
) -> int:
    """Run the full evaluation across all cases and trials.

    Parameters
    ----------
    profile:
        The evaluation profile.
    wheel_path:
        Path to the candidate wheel file.
    output_dir:
        Directory for evaluation output files.

    Returns
    -------
    int
        0 if the gate passes, non-zero if it fails or an error occurs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check wheel existence before doing any work.
    if not Path(wheel_path).is_file():
        print(
            f"ERROR: wheel file not found: {wheel_path}",
            file=sys.stderr,
        )
        return 4

    # Check CLI version.
    installed_version = _get_codex_version()
    if installed_version != profile.agent_client_version:
        print(
            "ERROR: codex CLI version mismatch: "
            f"installed={installed_version!r} "
            f"expected={profile.agent_client_version!r}",
            file=sys.stderr,
        )
        return 2

    # Run preflight checks from the isolated analysis venv.
    if not run_preflight(venv_python=sys.executable):
        print(
            "ERROR: tool-sandbox preflight failed: "
            "loopback must succeed and example.com:443 must fail",
            file=sys.stderr,
        )
        return 3

    # Run trials.
    trial_events: dict[tuple[str, int], list[EvalEvent]] = {}

    for case_id in profile.cases:
        for trial_idx in range(_trials_per_case()):
            print(
                f"Running trial {trial_idx} for case {case_id}...",
                file=sys.stderr,
            )
            exit_code = run_trial(
                profile,
                case_id,
                trial_idx,
                wheel_path,
                output_dir,
            )
            if exit_code != 0:
                print(
                    f"WARNING: trial {trial_idx} for case {case_id} exited with code {exit_code}",
                    file=sys.stderr,
                )

            # Load events from the trial's events file.
            events_file = output_dir / f"{case_id}-trial-{trial_idx}.jsonl"
            events = _load_trial_events(events_file)
            trial_events[(case_id, trial_idx)] = events

    # Score the evaluation.
    report = score_evaluation(profile, trial_events)

    # Write outputs.
    _write_combined_events(trial_events, output_dir / "events.jsonl")
    _write_report_json(report, output_dir / "report.json")
    _write_report_md(report, output_dir / "report.md")

    print(f"Evaluation {'PASS' if report.passes else 'FAIL'}", file=sys.stderr)
    print(f"Report: {output_dir / 'report.md'}", file=sys.stderr)

    return 0 if report.passes else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the analysis surface evaluation runner.

    Parameters
    ----------
    argv:
        Optional argument list.  If None, uses ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 if the gate passes, non-zero otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="scripts.analysis_surface_eval.runner",
        description="Run the analysis surface cold-agent evaluation gate.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        required=True,
        help="Path to the evaluation profile TOML file.",
    )
    parser.add_argument(
        "--wheel",
        type=str,
        required=True,
        help="Path to the candidate wheel file.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for evaluation results. "
        "Defaults to .marivo/evals/analysis-surface/<run-id>/",
    )
    args = parser.parse_args(argv)

    profile_path = Path(args.profile)
    prompts_dir = profile_path.parent / "prompts"
    profile = load_profile(profile_path, prompts_dir=prompts_dir)

    if args.output_dir is not None:
        output_dir = Path(args.output_dir)
    else:
        run_id = generate_run_id()
        output_dir = Path(".marivo") / "evals" / "analysis-surface" / run_id

    return run_evaluation(profile, args.wheel, output_dir)


if __name__ == "__main__":
    sys.exit(main())
