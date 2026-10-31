<p align="center">
  <a href="https://marivo.io" target="_blank">
    <img src="https://raw.githubusercontent.com/chengxianglibra/marivo/main/site/src/assets/marivo-mark.svg" alt="Marivo" width="128">
  </a>
</p>

<h1 align="center">Marivo</h1>

<p align="center">
  <em>A data analysis harness for AI agents that keeps business meaning, analytical steps, session state, and evidence connected.</em>
</p>

<p align="center">
  <a href="https://marivo.io/docs/latest/" target="_blank"><strong>Docs</strong></a> ·
  <a href="https://marivo.io/docs/latest/first-analysis/" target="_blank"><strong>First Analysis</strong></a> ·
  <a href="https://discord.gg/8WqCzeaYk" target="_blank"><strong>Discord</strong></a> ·
  <a href="https://marivo.io/blog/" target="_blank"><strong>Blog</strong></a>
</p>

<p align="center">
  <b>English</b>
  <b> | </b>
  <a href="README.zh-CN.md"><b>简体中文</b></a>
</p>

<p align="center">
  <a href="https://github.com/chengxianglibra/marivo/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/chengxianglibra/marivo/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/marivo/"><img src="https://img.shields.io/pypi/v/marivo" alt="PyPI version"></a>
  <a href="https://pypi.org/project/marivo/"><img src="https://img.shields.io/pypi/pyversions/marivo" alt="Python versions"></a>
  <a href="https://github.com/chengxianglibra/marivo/blob/main/LICENSE"><img src="https://img.shields.io/github/license/chengxianglibra/marivo" alt="License"></a>
</p>

**Marivo** is a Python framework that helps AI agents analyze business data through
shared semantics, typed analysis operations, persistent sessions, and recorded
evidence. It turns an open-ended business question into a reviewable investigation.

**Highlights**

- 🧭 **shared semantics:** define metrics, dimensions, relationships, and guardrails once instead of rebuilding them in every query
- 🧮 **typed analysis:** move through explicit operators with bounded inputs, outputs, and failure modes
- 🗂️ **persistent sessions:** keep the question, intermediate results, and artifacts together so an investigation can resume
- 🔎 **traceable evidence:** keep findings connected to source results, analytical scope, and limitations

Marivo is not a hosted chat UI or a Text-to-SQL wrapper. The agent works with
declared business meaning and bounded analytical operations instead of rebuilding
metrics, joins, and analysis logic in every SQL query.

## Why Marivo

Giving an agent raw schemas and asking it to generate SQL leaves important choices
implicit: what a metric means, which records belong in it, how tables relate, which
comparison is valid, and what evidence supports the answer. Those choices can drift
between prompts and are difficult to review after the fact.

Marivo makes them explicit and reusable. Business definitions live in a code-managed
semantic layer, analysis proceeds through typed operations, and material results stay
connected to the session and evidence that produced them.

## Four core capabilities

### Semantic Layer

Python declarations define datasource bindings, entities, relationships, metrics,
dimensions, and guardrails under stable references. An agent can inspect evidence
and draft definitions; the user or business owner confirms their business meaning.

### Typed Analysis DSL

Typed operators such as `observe`, `compare`, and `attribute` give the agent explicit
analytical actions and return typed result objects. Invalid or unsupported steps fail
through the contract instead of being hidden inside free-form SQL.

### Analysis Session

Each project-local investigation keeps its question, intermediate results, artifacts,
and history together. The agent can continue an analysis without recreating context
or repeating completed work.

### Evidence Engine

Deterministic typed findings remain connected to their source results and are
projected into bounded, operator-specific digests. Marivo does not use an LLM or
make cross-artifact judgments: the agent owns synthesis and next-step choice, while
typed inference boundaries, omissions, and exact audit reads keep the conclusion
reviewable.

Before analysis starts, readiness checks the technical handoff for the required
semantic objects. It blocks incomplete definitions without treating technical
readiness as approval of their business meaning.

## How you use Marivo

1. **Install and initialize a project.** Marivo creates the project structure and
   makes the bundled `marivo-semantic` and `marivo-analysis` skills available to
   compatible agents.
2. **Prepare the semantic layer.** Reuse the definitions in an existing project, or
   let an agent use `marivo-semantic` to draft what a new project needs.
3. **State the business question.** The agent uses `marivo-analysis` to check
   readiness, choose typed analysis steps, preserve evidence, and return the
   conclusion and limitations.

You confirm choices that materially affect business meaning or how the conclusion
will be used. You do not need to write Python, select operators, manage the analysis
session, or specify evidence fields.

## Quick Start

Marivo requires Python 3.10 or newer. Enter the directory that will contain the
project, then run:

```bash
curl -fsSL https://marivo.io/install.sh | bash
```

The installer uses `uv` to prepare a project-local environment and initializes the
current directory. It runs on macOS, Linux, WSL, and Windows through Git Bash,
MSYS2, or Cygwin.
For manual installation, datasource extras, supported platforms, and troubleshooting,
see [Installation](https://marivo.io/docs/latest/installation/).

Marivo also works directly in an empty current directory after package installation.
`marivo init` only materializes optional configuration, empty project directories,
and agent skill links; it is not a runtime prerequisite.

Discover capabilities with the selected project interpreter:

```bash
.venv/bin/python -m marivo help analysis
```

On Windows, use `.venv/Scripts/python.exe -m marivo help analysis`.
Use `marivo doctor` when interpreter, package or project identity is uncertain.

CLI and Python share the same exact Help targets. Python also accepts registered
public types and bound methods:

```python
import marivo

marivo.help()
marivo.help("authoring")
marivo.help("analysis")
marivo.help("semantic.metric.define")
marivo.help("analysis.session.observe")
```

`marivo.help()` introduces Marivo's core concepts and routes to two secondary
roots. Use `marivo.help("authoring")` to connect data and define governed
semantics; use `marivo.help("analysis")` to discover typed analysis. Follow the
current information gap: `analysis.inputs` for definitions and scope,
`analysis.source` for explicit observations or new source facts, `analysis.frame`
for calculations over represented data, `analysis.execution` for fixing or
saving values, `analysis.results` for inspection and audit, and
`analysis.sessions` for context and recovery. These routes are choices, not
required steps. Focused help also accepts registered public types, errors, and
member targets when they are already known or obtained from a live result.

If the project already contains `marivo.toml` and `models/`, reuse its semantic layer.
For a new empty project, tell the agent which datasource and business outcome you need;
Marivo creates authored and runtime directories when they are first needed. Confirm the
proposed metric meaning before analysis.

Once a metric is ready, ask a business question naturally:

> Use Marivo to explain why the approved `sales.revenue` metric decreased last
> quarter compared with the same period a year earlier. Start with regional
> differences, then give me the conclusion, key evidence, and limitations.

The bundled skills handle catalog inspection, readiness, operator selection, session
management, and evidence collection.

## Documentation

- [Installation](https://marivo.io/docs/latest/installation/)
- [Quick Start](https://marivo.io/docs/latest/quick-start/)
- [First agent-guided analysis](https://marivo.io/docs/latest/first-analysis/)
- [Semantic Layer](https://marivo.io/docs/latest/concepts/semantic-layer/)
- [Analysis Workflow](https://marivo.io/docs/latest/concepts/analysis-workflow/)
- [Evidence](https://marivo.io/docs/latest/concepts/evidence/)

## Development

```bash
uv venv --python 3.10 --seed
uv pip install --python .venv/bin/python -e ".[dev,duckdb,trino]"
```

Tests protect current behavior with concrete failure cases and independently justified
expected results. Review additions for value beyond existing coverage, and identify retained
owners before merging or removing cases. Test counts and a universal coverage percentage are
not quality gates; see the testing requirements in [CONTRIBUTING.md](CONTRIBUTING.md#测试).
The default suite includes unit and local integration tests and does not establish Runtime
or installed-package qualification.

Use the repository entrypoints for checks:

```bash
make lint
make typecheck
make test
make check-fast
make check
```

Before final checks, apply Ruff fixes and formatting only to the Python files
owned by the change. `make format` formats the whole checkout.
`make test` runs the daily local regression suite, including slow-marked cases
unless they are also marked `release`. Measured high-cost subprocess, CPU-bound,
typing, and query/recovery cases keep their assertions in `make release-check`;
the daily runtime target is under three minutes. The Make target uses one
pytest-xdist worker per logical CPU (`nproc` on Linux, `sysctl -n hw.ncpu` on
macOS), capped at 8. Use `PYTEST_WORKERS=<count> make test` to override. A single
selected node runs serially. Native thread budgets default to 1 for the test process
and its children; override with `PYTEST_NATIVE_THREADS=<count>`. Output is compact,
tracebacks are short, and the run
stops after five failures. Runtime and release cases use separate entrypoints.
`make typecheck` also uses compact output; all commands preserve check failures
and exit status. `make check-fast` is the local inner loop: it reruns lint,
typecheck, only the tests pytest-testmon sees as affected by edits, and an
incremental API docs build, in parallel. It does not track subprocess-executed
code or SQL and data file changes. `make check` remains the full gate — the
whole local suite, static checks, and a clean API docs build — and must pass
before pushing. Ordinary commits keep their
installed hooks and do not run the full Runtime suite.

The local suite includes embedded DuckDB, local files, loopback HTTP fixtures,
static native compilation/decoding contracts, and extended worker and recovery
coverage. Runtime tests require ready external datasource services
and cover basic source operations plus backend-specific risks; they do not repeat
the entire local analysis matrix. Service containers are shared across cases and
invocations, while each case owns its tables, schemas and connections.

`make release-check` adds Runtime and packaging checks to the local daily gate.
See [CONTRIBUTING.md](CONTRIBUTING.md) for release prerequisites. Use
`make runtime-test TESTS='tests/datasource/test_source_profiles.py'` for focused
native source checks when needed; daily development does not automatically run
the full Runtime suite. `make runtime-test` uses compact output and defaults to
two workers to bound shared-service load; use `RUNTIME_WORKERS=4` to override
after measuring available host capacity.
An explicit test node id containing `::` runs serially. The limit applies per
invocation, so concurrent test tasks share the host's remaining capacity.
Tests use work-stealing across workers to avoid slow-module tails.
Typing remains strict and incremental; cache misses run the full check.
Installed-environment fingerprints run with the packaging checks in
`make release-test`. Statistical dependencies load when their operation runs,
so ordinary worker startup does not import SciPy's statistics package.

Read [`AGENTS.md`](AGENTS.md) before contributing. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.
