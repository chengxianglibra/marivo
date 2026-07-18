# Marivo

[简体中文](README.zh-CN.md) | English

## A Data Analysis Harness for AI Agents

Marivo is a Python framework that helps AI agents analyze business data through
shared semantics, typed analysis operations, persistent sessions, and recorded
evidence. It runs alongside the agent and turns an open-ended data question into
a reviewable investigation.

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

Marivo requires Python 3.12 or newer. Enter the directory that will contain the
project, then run:

```bash
curl -fsSL https://marivo.io/install.sh | bash
```

The installer prepares the local environment and initializes the current directory.
For manual installation, datasource extras, supported platforms, and troubleshooting,
see [Installation](https://marivo.io/en/latest/installation/).

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

- [Installation](https://marivo.io/en/latest/installation/)
- [Quick Start](https://marivo.io/en/latest/quick-start/)
- [First agent-guided analysis](https://marivo.io/en/latest/first-analysis/)
- [Semantic Layer](https://marivo.io/en/latest/concepts/semantic-layer/)
- [Analysis Workflow](https://marivo.io/en/latest/concepts/analysis-workflow/)
- [Evidence](https://marivo.io/en/latest/concepts/evidence/)

## Development

```bash
uv venv --python 3.12 --seed
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
