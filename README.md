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

Verify the selected environment once:

```bash
.venv/bin/python -m marivo doctor
```

On Windows, use `.venv/Scripts/python.exe -m marivo doctor`.

Then use the selected project interpreter for focused help.

```python
import marivo

marivo.help()
marivo.help("authoring")
marivo.help("analysis")
marivo.help("semantic.metric")
marivo.help("analysis.observe")
```

`marivo.help()` introduces Marivo's core concepts and routes to two secondary
roots. Use `marivo.help("authoring")` to connect data and define governed
semantics; use `marivo.help("analysis")` to discover typed analysis. Follow the
qualified routes from those pages rather than depending on short-name
disambiguation. Focused help also accepts registered public types, errors, and
member targets when they are already known or obtained from a live result.

If the project already contains `marivo.toml` and `models/`, reuse its semantic layer.
For a new project, tell the agent which datasource and business outcome you need, then
confirm the proposed metric meaning before analysis.

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

Use the repository entrypoints for checks:

```bash
make format
make lint
make typecheck
make test
make check
```

Read [`agent-guide.md`](agent-guide.md) before contributing. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.
