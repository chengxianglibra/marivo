# Agent-user documentation design

**Status:** proposed and approved for specification review
**Scope:** `site/src/content/docs/{en,zh-cn}/latest/` and the latest-version
sidebar only. Historical versions remain unchanged.

## Goal

Make the latest Marivo documentation serve a person who uses a coding agent to
build and run governed data analysis. The default reader is not expected to
write `ms.*` or `session.*` calls directly. Instead, they should learn how to
give an agent the right business context, review the semantic contract it
proposes, approve the parts only a business owner can decide, and inspect the
resulting analysis and evidence.

## Product boundary

Marivo helps an agent build and use a semantic layer, but it does not replace
business ownership.

| Responsibility | User | Agent with Marivo |
| --- | --- | --- |
| State the business question and intended decision | Provides the goal, scope, and context | Clarifies ambiguous requests |
| Define business meaning | Confirms metric meaning, filters, exclusions, units, and acceptable breakdowns | Drafts semantic objects from data and supplied context |
| Establish technical evidence | Reviews material uncertainties and approves the intended definition | Inspects data, samples it with an explicit scope, previews definitions, and runs readiness |
| Run and assess analysis | Checks whether the conclusion answers the business question | Executes typed analysis steps and records evidence |

Readiness is a structural and runtime-evidence gate. It does not establish that
a business definition has been approved. The documentation must say this
explicitly wherever it introduces readiness.

## Default user journey

```text
business question
  → user supplies business context and constraints
  → agent drafts semantic objects from bounded data evidence
  → user verifies and adjusts the business contract
  → agent verifies, previews, and checks readiness
  → agent runs analysis
  → user reviews conclusion and evidence
```

Every task-oriented page uses this three-part pattern:

1. **What you need to decide** — business information that cannot be inferred
   safely.
2. **What to ask the agent to do** — a copyable natural-language instruction
   that invokes the installed Marivo skill or describes the desired outcome.
3. **What to review before continuing** — an observable artifact, definition,
   readiness result, or evidence trail.

## Information architecture

The latest sidebar is organized for the agent-user journey:

1. **Get started / 开始使用**
   - Home
   - Prepare a project for an agent
   - First agent-guided semantic layer
   - First agent-guided analysis
2. **Work with an agent / 与 agent 协作**
   - Give the agent a business question
   - Semantic contract and user review
   - Run an analysis
   - Readiness and evidence review
3. **Integration and reference / 集成与参考**
   - Project configuration
   - Deployment
   - Local telemetry
   - Semantic and analysis runtime contracts
   - Python API reference

The first two groups are the primary documentation path. Integration and API
material remains available, but is not presented as a parallel getting-started
route.

## Page responsibilities

### Home

The home page describes one collaboration loop using a concrete business
question, such as a revenue decline. It shows the user providing constraints,
the agent preparing and checking a semantic contract, and the user reviewing
evidence-backed findings. The existing four capability names remain in their
agreed order: Semantic Layer, Typed Analysis DSL, Analysis Session, Evidence
Engine. They are explained as safeguards in that collaboration loop, not as
API modules.

### Prepare a project for an agent

The installation route becomes a setup checklist for a coding agent: create a
project, install Marivo, initialize it, provide datasource credentials through
the approved environment-backed configuration, and identify the tables or
datasets to model. It ends with a copyable request for the user's agent, not a
Python declaration tutorial.

### First agent-guided semantic layer

The current Quick Start becomes the default semantic-authoring tutorial. It
asks the user to provide the source, business goal, known filters, exclusions,
and unresolved questions. It then supplies a copyable instruction that asks an
agent to use `marivo-semantic`. The page describes the expected staged output:
bounded inspection, a proposed entity and metric, preview, readiness, and a
user review of the resulting business definition and guardrails.

Python snippets may show what the agent creates, but are labeled as generated
project artifacts rather than instructions the user must type.

### First agent-guided analysis

The analysis workflow page starts with a copyable `marivo-analysis` request:
a question, selected metric, comparison window, intended breakdowns, and a
request for evidence. It describes the agent's `observe → compare → attribute`
loop in terms of inputs, intermediate artifacts, and outputs the user can
review. Operator tables and frame contracts move below the collaboration flow
or into the reference section.

### Semantic contract and user review

The semantic-layer page begins by calling a metric a business contract. Before
any object model or builder detail, it presents the responsibility table and a
metric review checklist:

- what the metric measures and does not measure;
- the row population and required filters;
- exclusions, cancellations, refunds, and other business adjustments;
- time axis, units, additivity, and valid breakdowns;
- known limitations and the responsible owner.

The agent may draft these items from data and context, but the page requires a
user to confirm or revise them. Object graph, metric-builder choice, and API
reference follow as supporting material.

### Readiness and evidence

Readiness describes the pre-analysis technical gate: static checks, fresh
snapshot metadata, and a matching preview. It explicitly distinguishes this
from a user's approval of metric semantics. Evidence describes how a user can
trace a conclusion back to the observations and analysis steps, then decide
whether to accept it or send the agent back to correct the semantic contract.

## Writing rules

- Address the reader as the business owner working with an agent, not as an
  API programmer.
- Lead with decisions, prompts, and review artifacts. Put generated Python and
  API detail after the user task it supports.
- Use natural technical Chinese: 智能体, 业务口径, 语义契约, 指标, 度量, 维度,
  分析会话, 证据链, 就绪检查. Preserve API identifiers, code, and established
  product names in English.
- Never imply that an agent can infer or approve a business definition solely
  from schema or sampled data.
- Never imply that readiness constitutes business approval.
- Preserve all technical claims and examples that reflect current runtime
  behavior; move reference detail rather than deleting necessary contracts.

## Validation

- Verify the latest English and Chinese sidebars express the same user journey.
- Verify each primary route contains a user-decision section, a copyable agent
  request, and a review/acceptance section.
- Confirm that semantic-layer, readiness, and evidence pages distinguish user
  business approval from agent-generated technical evidence.
- Run `npm run verify:content`, `npx astro check`, `npx astro build`, and
  `npm run postbuild` from `site/`.
- Confirm historical versioned docs have no diff and that current API examples
  remain technically accurate.

## Non-goals

- Do not change Marivo runtime behavior, skills, CLI behavior, or public APIs.
- Do not make coding-agent integration a competing first-use route.
- Do not remove integration and API reference material needed by developers.
- Do not modify historical versioned documentation.
