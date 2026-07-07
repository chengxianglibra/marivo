# Marivo Doctor Design

## Status

Accepted design for implementation planning.

Date: 2026-07-07

## Context

Marivo already has focused diagnostic surfaces:

- `md.test(...)` checks a datasource connection and persists validated
  environment-sourced secrets on success.
- `catalog.readiness()` and `ms.readiness()` check semantic readiness for
  analysis handoff.
- `python -m marivo.semantic.check --readiness` exposes semantic load and
  readiness diagnostics in text or JSON, and can be reused behind
  `marivo doctor --semantic`.
- Datasource and analysis typed errors render concrete `Fix:` snippets.

Those surfaces are useful after an agent knows which layer is failing. They are
less effective as the first action when the environment is uncertain. Common
failure classes span the current interpreter, installed backend extras, project
root resolution, datasource declarations, secret configuration, and persistent
`.marivo/` state. Agents currently have to compose those checks manually.

`marivo doctor` provides one safe first diagnostic command for environment and
project setup failures. It extends the existing "errors teach" philosophy to the
environment layer without replacing semantic readiness or datasource testing.

## Decision

Add a new CLI command:

```bash
marivo doctor [--project-root PATH] [--format text|json] [--fix-snap]
              [--semantic] [--connect] [--datasource NAME]
```

Default `marivo doctor` is static, read-only, and bounded. It checks the current
Python executable, installed Marivo package, project root, `marivo.toml`,
expected project directories, datasource declarations, backend extra
importability, secret references and presence, secret cache permissions, and
existing analysis state readability.

Default `marivo doctor` does not:

- connect to datasources;
- instantiate APIs that create `.marivo/analysis` state;
- persist secrets;
- run semantic readiness;
- execute repairs.

Live datasource checks require `--connect`. Semantic load/readiness checks
require `--semantic`. `--fix-snap` prints paste-ready commands derived from the
current report but never executes them.

## Goals

- Give agents one deterministic first action for environment and setup failures.
- Keep the default command genuinely read-only.
- Preserve existing public diagnosis surfaces instead of replacing them.
- Report current interpreter, package path, and project root so wrong-venv
  failures are visible.
- Generate actionable fixes from current state without embedding stale command
  catalogs.
- Provide human-readable text by default and stable JSON for automation.
- Keep semantic readiness and live datasource connectivity opt-in.

## Non-Goals

- Do not add public `md.doctor()` or `ms.doctor()` APIs.
- Do not make doctor a semantic authoring gate.
- Do not make doctor a replacement for `catalog.readiness()`.
- Do not remove domain-specific checks such as `md.test(...)`,
  `catalog.readiness()`, or the semantic check runner. `marivo doctor
  --semantic` becomes the documented user and agent entrypoint for semantic
  diagnostics, while `python -m marivo.semantic.check` remains available as a
  compatibility/internal runner.
- Do not default to network, database, or auth checks.
- Do not write secrets, create sessions, modify `.marivo/`, or repair files.
- Do not add a daemon, service, or background process.
- Do not support a broad `--fix` command in this iteration.

## Architecture

Introduce one implementation module, `marivo/doctor.py`, and wire it into the
existing `marivo` CLI in `marivo/cli.py`.

`marivo/cli.py` remains thin:

1. Parse `doctor` flags.
2. Call the doctor module.
3. Render text or JSON.
4. Return the proper exit code.

The doctor module owns check execution, result DTOs, rendering, JSON conversion,
and fix-snapshot generation.

The first iteration exposes only the CLI command. Internal helpers can be
structured by subsystem, but they are not added to datasource, semantic, or
analysis public exports.

## Result Model

Use small typed result objects:

```text
DoctorStatus = ok | warning | fail | skipped

DoctorCheck
  id: stable check id
  label: human-readable check label
  status: DoctorStatus
  summary: bounded one-line summary
  details: optional bounded data map
  fix: optional ordered list of paste-ready commands or next steps

DoctorSection
  id: stable section id
  label: human-readable section label
  checks: ordered checks

DoctorReport
  status: ok | warning | fail
  project_root: optional path
  python_executable: path
  marivo_version: string
  marivo_package_path: path
  sections: ordered sections
```

Overall status is derived from checks:

- any `fail` check makes the report `fail`;
- otherwise any `warning` check makes the report `warning`;
- otherwise the report is `ok`;
- skipped checks do not fail the report.

The JSON schema follows the DTO shape. It is stable enough for agents and CI to
parse by section id, check id, status, summary, details, and fix list.

## Default Check Groups

### Installation

Report:

- `sys.executable`;
- Marivo version;
- Marivo package path;
- whether the installed command is running from the expected package import.

This does not try to infer the user's intended virtualenv. It makes the current
runtime explicit so wrong-environment failures are visible.

### Project

Resolve project root from:

1. `--project-root` when provided;
2. `MARIVO_PROJECT_ROOT`;
3. nearest ancestor containing `marivo.toml`;
4. current directory if no project root is found.

Checks:

- `marivo.toml` exists and parses as TOML;
- `[project].name` is present when `marivo.toml` exists;
- `models/` exists;
- `models/datasources/` existence is reported;
- `models/semantic/` existence is reported;
- `.marivo/` existence is reported but not created.

Missing project files can be failures or warnings depending on the context:

- an explicit `--project-root` that does not exist is a failure;
- no detected `marivo.toml` is a failure for project health and suggests
  changing directory, setting `MARIVO_PROJECT_ROOT`, passing `--project-root`,
  or running `marivo init`;
- missing optional subdirectories are warnings when the project can still be
  inspected.

### Datasources

Statically load datasource declarations from `models/datasources/*.py` without
opening backend connections.

For each datasource:

- report name and backend type;
- report unsupported backend types as failures;
- probe the backend extra by importing the required Ibis backend support or the
  minimal module needed by Marivo for that backend;
- report missing extras with a fix using the current interpreter:

```bash
<sys.executable> -m pip install "marivo[trino]"
```

Backend extra probes must not connect to a database.

If `--datasource NAME` is supplied, datasource checks narrow to that datasource.
An unknown datasource name is a failure.

### Secrets

Inspect secret references without rendering secret values.

For each datasource env ref:

- check whether the referenced environment variable is present and non-empty;
- if absent, check whether `~/.marivo/secrets.toml` contains a non-empty value;
- report missing secret refs as failures;
- generate fix steps with placeholders, not secret values.

Also inspect conventional sensitive env names that Marivo supports, such as
`MARIVO_<DATASOURCE>_<FIELD>`, as informational presence checks.

If `~/.marivo/secrets.toml` exists, check that permissions are safe. Insecure
permissions are failures with:

```bash
chmod 600 ~/.marivo/secrets.toml
```

Doctor must not write the cache file. It must not call helpers that persist
environment-sourced secrets.

### Analysis State

Inspect existing `.marivo/analysis` state only if it already exists.

Checks:

- `.marivo/analysis/` exists or is skipped;
- `session_store.db` exists or is skipped;
- if the SQLite database exists, it can be opened read-only and basic expected
  tables can be inspected.

Doctor must not instantiate `SessionStore()` because that creates the database
and parent directories. Use file-level checks and read-only SQLite connection
URIs instead.

Unreadable or corrupted state is reported with conservative manual guidance.
Doctor does not delete or repair state.

## Opt-In Check Groups

### `--semantic`

Run the existing semantic load/readiness pathway through
`marivo.semantic.check.run_check(..., readiness=True)`.

Semantic checks remain a separate section. `--semantic` may make the command
exit non-zero when semantic load or readiness fails. This is opt-in so default
doctor does not conflate environment health with normal authoring blockers.

`marivo doctor --semantic` becomes the recommended user and agent command for
semantic diagnostics. The existing `python -m marivo.semantic.check` command is
kept as a compatibility wrapper and reusable implementation seam, but
agent-facing docs should route semantic diagnostics through doctor.

### `--connect`

Run live datasource connection checks for all datasources, or only the
datasource named by `--datasource`.

This must not call `md.test(...)` as-is because `md.test(...)` persists
validated environment-sourced secrets on success. Implementation should add a
no-persist connectivity helper or expose a no-persist internal option for doctor
to use.

Connectivity checks should:

- respect backend missing-extra failures and avoid attempting live connection
  when the backend cannot import;
- use a minimal round-trip such as `SELECT 1`;
- always disconnect opened backends;
- clearly label live checks in text and JSON.

## Output Contract

Default text output is concise and scan-friendly:

```text
Marivo doctor: fail
Python: /path/to/.venv/bin/python
Marivo: 0.2.8.dev0 (/path/to/site-packages/marivo)
Project: /path/to/project

[installation] ok
[project] ok
[datasources] warning 1 missing backend extra
[secrets] fail TRINO_AUTH is not set and not cached
[state] ok existing analysis store is readable

Fix:
  /path/to/.venv/bin/python -m pip install "marivo[trino]"
  export TRINO_AUTH="secret_value"
  marivo doctor --datasource warehouse --connect
```

`--fix-snap` prints only the fix block plus enough context to know which report
it came from. It never executes commands.

`--format json` emits the report dictionary. JSON output must not include secret
values or tracebacks by default.

## Exit Codes

Default static doctor uses environment-health semantics:

- `0`: no default static failure;
- `1`: one or more default static failures;
- usage errors remain argparse errors.

Warnings do not fail by default.

When `--semantic` is supplied, semantic load/readiness failures can make the
command exit `1`.

When `--connect` is supplied, live datasource failures can make the command exit
`1`.

Do not add multi-level exit codes in the first iteration. They are harder for
agents to use and unnecessary for the default "one safe first action" contract.

## Error Handling

Each check catches expected local failures and converts them into failed checks.
One failed check should not prevent unrelated sections from running.

Unexpected internal exceptions become failed checks with bounded messages:

```text
Unexpected RuntimeError while checking datasource warehouse: <message>
```

Do not print tracebacks unless a future debug flag is added.

Do not leak:

- secret values;
- full environment dumps;
- credentials embedded in URLs;
- arbitrary contents of `~/.marivo/secrets.toml`.

## Fix Generation

Fix commands are generated from current state:

- Missing backend extra:
  `<sys.executable> -m pip install "marivo[<backend>]"`
- Missing env secret:
  `export <ENV_VAR>="secret_value"`
- Insecure secret cache:
  `chmod 600 ~/.marivo/secrets.toml`
- Missing project:
  suggest `cd`, `MARIVO_PROJECT_ROOT`, `--project-root`, or `marivo init`
  depending on available context.
- Live validation after static repair:
  `marivo doctor --datasource <name> --connect`

Fixes are next steps, not hidden actions. They must be safe to show, bounded,
and paste-ready.

## Documentation And Skill Updates

`marivo doctor` owns environment and setup triage. It does not take ownership of
semantic readiness or datasource validation.

Keep domain-specific checks available and documented where they are the precise
tool:

- `catalog.readiness()` and `ms.readiness()` remain the semantic handoff gate.
- `md.test(...)` remains the explicit datasource live round-trip helper, with
  its secret-persistence semantics called out where relevant.
- `python -m marivo.semantic.check --readiness --format json` remains available
  as a compatibility/internal runner for CI and scripted semantic validation,
  but the recommended documented user and agent entrypoint is
  `marivo doctor --semantic --format json`.

Update user-facing guidance where agents look first so the first triage path is
consistent:

- README quick-start or troubleshooting section: mention `marivo doctor` as the
  first environment diagnostic command.
- `marivo-semantic` datasource reference: if datasource setup fails, run
  `marivo doctor` before live discovery.
- `marivo-analysis` backend setup reference: if backend resolution fails, run
  `marivo doctor`; use `--connect` only when checking live reachability.

Remove scattered ad hoc environment-debugging recipes from agent-facing docs
when `marivo doctor` covers the same first-triage need. Replace direct
agent-facing references to `python -m marivo.semantic.check` with
`marivo doctor --semantic` unless the documentation is explicitly about the
compatibility runner or CI internals. Do not remove domain-specific checks from
API docs or usage guidance when they are the post-triage tool for a specific
layer.

Do not duplicate backend parameter tables or error catalogs in docs. Doctor
reports and existing typed errors own current repair details.

## Tests

Add `tests/test_doctor.py` for module behavior:

- DTO status derivation and JSON shape;
- default text rendering;
- `--fix-snap` fix extraction;
- project root resolution with explicit path, env, cwd ancestor, and missing
  project;
- static datasource declaration checks;
- backend extra missing diagnostics with current-interpreter pip fix;
- secret env/cache missing diagnostics without secret leakage;
- secret cache permission diagnostics;
- existing state read-only inspection;
- default doctor does not create `.marivo/analysis/session_store.db`;
- default doctor does not write `~/.marivo/secrets.toml`.

Extend `tests/test_cli.py` for CLI integration:

- `marivo doctor` command parsing;
- `--format json`;
- exit code success/failure;
- `--semantic`, `--connect`, and `--datasource` flag wiring with fakes.

Optional live connection tests should use fakes or monkeypatches. Do not require
real database services.

## Verification

Focused implementation gate:

```bash
make test TESTS='tests/test_cli.py tests/test_doctor.py'
make lint
make typecheck
```

If docs or packaged skills are edited, also run:

```bash
make examples-check
```

Run the broader `make test` only if implementation touches shared datasource,
semantic, analysis session, or error machinery beyond the doctor support seams.

## Open Follow-Ups

Potential future work after the first iteration:

- `--strict` to make warnings fail.
- Debug mode that includes tracebacks.
- Additional doctor sections for publish/S3 configuration.
- Richer no-persist datasource connectivity API exposed internally for other
  maintenance commands.

These are intentionally deferred to keep the first version narrow.
