# Marivo Installer Script Design

## Status

Accepted design for implementation planning.

Date: 2026-07-10

## Context

Marivo requires Python 3.12 or newer. The documented installation flow asks a
user to select a suitable interpreter, create and activate a virtual
environment, install Marivo and its datasource extras, and run `marivo init`.
Each step can fail for a different reason, and using an unqualified `pip` or
`marivo` command can silently target the wrong Python environment.

Add one repository script that performs this setup from the directory where it
is invoked. The script is an onboarding helper for a Marivo project, not a
development-environment bootstrapper for the Marivo source checkout.

## Decision

Add an executable Bash script:

```text
scripts/install-marivo.sh
```

The script supports macOS, mainstream Linux distributions, and Windows through
WSL. It does not claim native Windows, Git Bash, MSYS2, Cygwin, or PowerShell
support. Native Windows requires a separate PowerShell entrypoint because the
uv installer, virtualenv executable layout, and path handling differ.

The current working directory is always the target project directory. The
script does not change to its own repository directory. It creates or reuses
`<current-directory>/.venv`, installs the newest available `marivo[all]` into
that environment, and initializes the current directory with that environment's
`marivo` executable.

## Goals

- Produce a usable project-local `.venv` backed by Python 3.12 or newer.
- Reuse a suitable local Python interpreter when possible.
- Install an isolated uv-managed Python 3.12 when no suitable interpreter is
  available, without replacing the system Python.
- Upgrade an existing Marivo installation in the target `.venv`.
- Run `marivo init` with the executable installed in the target `.venv`.
- Validate every stage and report the failed stage, observed state, and a
  concrete recovery action.
- Make destructive replacement of an invalid `.venv` explicit.
- Remain safe to rerun.

## Non-Goals

- Do not install Marivo development dependencies or install this checkout in
  editable mode.
- Do not activate `.venv` or modify the caller's shell startup files.
- Do not replace, uninstall, or change the operating system's default Python.
- Do not install Python through Homebrew, apt, dnf, yum, or source compilation.
- Do not support native Windows from this Bash script.
- Do not pass `--force` to `marivo init` or overwrite existing project content.
- Do not configure datasources, credentials, semantic models, or telemetry.

## Command Interface

```bash
./scripts/install-marivo.sh [--yes]
```

With no arguments, the script prompts before deleting an invalid or
incompatible `.venv`. `--yes` accepts that one destructive action and is the
supported non-interactive mode. Unknown arguments fail before any mutation.

The script prints the absolute target directory at startup and a numbered stage
label before each operation. It uses `set -Eeuo pipefail` and an error trap so
unexpected failures identify the active stage.

## Platform and Tool Preflight

The preflight validates:

- Bash is executing the script;
- the operating system reported by `uname -s` is Darwin or Linux;
- the current directory exists, is writable, and can host `.venv`;
- either `curl` or `wget` is available if uv may need to be installed;
- `uname`, `mktemp`, and the basic POSIX file utilities used by the script are
  available.

WSL is handled as Linux. Native Windows-like `uname` values such as `MINGW`,
`MSYS`, and `CYGWIN` fail with a message recommending WSL and explaining that
the Bash script does not support the Windows virtualenv layout.

Network-dependent operations do not get a separate connectivity probe. The
actual uv or pip operation is the authoritative probe and its failure is
reported with the exact stage and rerun guidance.

## Python Selection

Python selection is deterministic:

1. If `.venv` contains an executable Python, inspect its version and prefix.
2. If it is Python 3.12 or newer and identifies `.venv` as its environment,
   reuse the environment.
3. Otherwise treat `.venv` as invalid and request permission to delete it.
4. Search supported local executable names for a Python 3.12-or-newer
   interpreter. Use an interpreter only after executing a version check with
   it; command naming alone is not evidence.
5. If no suitable interpreter is found, reuse an existing working `uv` or
   install uv with its official macOS/Linux standalone installer.
6. Use uv to install a managed Python 3.12 and resolve the resulting
   interpreter path.

The minimum version check compares `sys.version_info` rather than parsing
human-readable `--version` output. A local Python newer than 3.12 is accepted.
The uv fallback requests the 3.12 line to keep behavior predictable across
machines.

After installing uv, the script locates the executable from both the current
`PATH` and the installer's standard user-local destination, then requires
`uv --version` to succeed before continuing.

## Virtual Environment Lifecycle

A valid existing `.venv` is preserved. Marivo and pip are upgraded in place.

An invalid `.venv` includes any of the following:

- no executable environment Python;
- Python older than 3.12;
- an interpreter that cannot execute the version/prefix probe;
- an interpreter whose prefix does not resolve to the target `.venv`.

Before removing such a directory, the script prints its absolute path and asks
for confirmation. It deletes only the exact `.venv` entry under the current
working directory. If standard input is not interactive, deletion is refused
unless `--yes` was passed.

Environment creation first uses the selected interpreter's standard
`-m venv` support. If that interpreter is otherwise suitable but cannot create
a seeded environment, the script installs or reuses uv and creates `.venv`
through uv instead. Creation is successful only when `.venv/bin/python` exists,
reports Python 3.12 or newer, and has a resolved prefix equal to `.venv`.

## Marivo Installation

All package operations are tied to the environment interpreter:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --upgrade "marivo[all]"
```

The script never invokes an unqualified `pip`. If pip is absent, it first tries
the environment interpreter's `ensurepip`; if that fails, it rebuilds the new
environment through uv with seed packages rather than mutating another Python
installation.

Installation validation requires:

- `.venv/bin/python -m pip --version` succeeds and reports a location under
  `.venv`;
- importing `marivo` succeeds with `.venv/bin/python`;
- `.venv/bin/marivo --version` succeeds;
- the resolved `marivo` executable is the one under `.venv/bin`.

The script prints the selected Python version, Python path, pip version, Marivo
version, and Marivo path without printing pip configuration or credentials.

## Project Initialization

Initialization uses the environment-qualified command from the unchanged
target directory:

```bash
.venv/bin/marivo init
```

`marivo init` is already idempotent, so rerunning the installer preserves
existing project files and adds only missing artifacts. The installer does not
use `marivo init --force`.

After the command succeeds, the installer requires the core initialized
artifacts to exist:

- `marivo.toml`;
- `models/`;
- `.marivo/`.

Agent skill links remain governed by `marivo init`. Because link creation is a
documented best-effort operation, missing optional links are reported as
warnings rather than making an otherwise valid project installation fail.

## Error Handling

Expected failures use concise, stage-specific messages and a non-zero exit
status. Important cases include:

| Condition | Behavior |
| --- | --- |
| Unsupported operating system | Fail before mutation and list supported platforms. |
| Target directory is not writable | Fail before installation. |
| No download tool when uv is required | Fail and request `curl` or `wget`. |
| uv installation or validation fails | Fail with the official installer URL and rerun guidance. |
| Invalid `.venv` replacement is declined | Exit without modifying it. |
| `.venv` cannot be removed or created | Fail and report the exact path. |
| Python in `.venv` remains below 3.12 | Fail before pip installation. |
| pip upgrade or Marivo installation fails | Preserve `.venv`, report the failing command category, and suggest rerunning. |
| Marivo validation fails | Fail before initialization and show the environment-qualified validation command. |
| `marivo init` fails | Preserve installed `.venv` and any init artifacts; do not roll back user files. |
| Required init artifact is missing | Fail with the missing path and suggest rerunning `.venv/bin/marivo init`. |

The script does not attempt transactional rollback. A successfully created
`.venv` is useful state for diagnosing or retrying later stages, while project
initialization may have safely created a subset of idempotent artifacts.

## Testing

Add focused tests for the installer under `tests/test_install_marivo_script.py`.
Tests run the Bash script in temporary target directories with controlled fake
executables and no real network or global environment changes.

The test harness supplies fake Python, uv, pip, and Marivo behavior through a
temporary `PATH`. It records command invocation and creates only the minimum
filesystem artifacts needed to exercise the script as a black-box command.

Required scenarios:

- rejects unknown arguments before mutation;
- rejects unsupported native Windows-like environments;
- reuses a valid Python 3.12-or-newer `.venv`;
- accepts a local Python newer than 3.12;
- replaces an old or broken `.venv` only after confirmation or `--yes`;
- refuses destructive replacement in non-interactive mode without `--yes`;
- installs and validates uv when no suitable Python is available;
- creates `.venv` using uv when standard `venv` creation fails;
- uses only `.venv/bin/python -m pip` for package installation;
- includes `--upgrade` and `marivo[all]` in the package install;
- stops before initialization when package validation fails;
- runs `.venv/bin/marivo init` from the original current directory;
- validates required init artifacts;
- succeeds when rerun against an already initialized project.

Verification proceeds from narrow to broad:

```bash
make test TESTS='tests/test_install_marivo_script.py'
bash -n scripts/install-marivo.sh
git diff --check
```

If ShellCheck is available in the repository environment, run it as an
additional diagnostic. It is not introduced as a new required project
dependency in this change.

## Documentation Scope

The implementation adds equivalent short source-checkout usage notes to
`README.md` and `README.zh-CN.md`. Both notes document macOS, Linux, and WSL
support; `.venv` creation or reuse; `marivo[all]` installation or upgrade;
`marivo init`; the `--yes` replacement behavior; and the native Windows shell
boundary. The notes do not present the script as part of the installed wheel,
because the root `scripts/` directory is not package data. The public PyPI
installation commands remain valid and unchanged.
