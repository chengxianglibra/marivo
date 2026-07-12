# Analysis-Harness Documentation Design

**Date:** 2026-07-11
**Status:** Approved for implementation
**Scope:** The current English and Simplified-Chinese documentation only:
`site/src/content/docs/{en,zh-cn}/latest/{index,installation}.mdx` and
`concepts/semantic-layer.mdx`.

## Goal

Make the documentation answer a new visitor's first questions in the right
order: why agentic data analysis needs a harness, what Marivo constrains, how
to start a project, and how a trusted metric becomes an analysis input. The
documentation must establish **Analysis Harness for AI Agents** as Marivo's
primary category without changing the public Python or CLI contract.

## Non-goals

- Do not change the runtime, CLI, packaged skills, or semantic API.
- Do not edit historical `v0.1`, `v0.2`, or `v0.3` documentation snapshots.
- Do not add a demo application, benchmark, video, or marketing site outside
  the current documentation pages.
- Do not present Marivo as a hosted product or a chat interface.

## Narrative

The shared message is:

> Marivo is an analysis harness for AI agents. It turns open-ended warehouse
> exploration into a constrained, reviewable analysis loop: semantic meaning
> bounds what an agent can understand, typed analysis DSL bounds what it can do,
> sessions preserve the investigation, and evidence makes conclusions auditable.

"Metric-centered analysis runtime" remains an accurate implementation
description, not the top-level category. The docs must avoid claiming that
Marivo merely blocks SQL generation: it executes approved semantics through
Ibis and gives agents typed analytical operations instead of treating generated
SQL as the analysis contract.

## Home page

### Entry experience

- Change the title, description, and hero tagline to **Analysis Harness for AI
  Agents**.
- Start with the concrete failure of giving an agent raw warehouse access:
  meaning is guessed, action paths are unconstrained, and a confident result is
  not enough evidence to review.
- State that Marivo is a Python library, not a hosted service or chat UI, after
  the category statement rather than as the opening message.

### Four core capabilities

Use exactly these four first-class cards and descriptions:

1. **Semantic Layer** — gives trusted names, business definitions, and
   guardrails; it bounds the semantic search space.
2. **Typed Analysis DSL** — supplies typed intents and frames for analysis; it
   bounds the actions an agent can take.
3. **Analysis Session** — preserves the question, semantic context, artifacts,
   and recovery path across an investigation.
4. **Evidence Engine** — records findings and assessments with their inputs so
   a conclusion is inspectable and reproducible.

Readiness is explained as a gate within the semantic-layer story, not promoted
to a competing fifth capability.

### Page flow

1. Problem and category definition.
2. The four capabilities.
3. A compact contrast between free-form warehouse/SQL exploration and the
   harnessed path.
4. The lifecycle: `question -> trusted metric -> typed intents -> persisted
   session -> evidence-backed conclusion`.
5. One short, readable analysis example and clear calls to Install, Quick Start,
   and Semantic Layer.

The page must remain skimmable. Detailed API tables belong in the concept pages.

## Installation page

### Entry experience

Organize the page as a path to an agent-ready analysis project, not as a package
reference:

1. State the Python requirement and offer the recommended bootstrap command for
   macOS, Linux, and WSL.
2. Offer `pip` plus one backend extra as the alternative installation route.
3. Run `marivo init` and immediately direct the user to Quick Start.
4. Explain in plain language that initialization creates the project state and
   installs the semantic and analysis guidance that an in-project coding agent
   can use.

### Reference material

Keep backend extras, the generated project layout, rerun/`--force` behavior,
external semantic packages, telemetry, deployment, S3 publishing, and local
development accurate but place them after the first-run path under descriptive
sections. No command semantics may change.

## Semantic-layer page

### Entry experience

Open with the semantic layer as the human-authored contract that bounds what an
agent is allowed to mean before it analyzes. Introduce these questions before
the API surface:

- Which business object does the request refer to?
- What does the selected metric mean and when may it be used?
- Which dimensions and time axis may segment it?
- Is the object ready for analysis?

Give the reader a dependency model before individual declaration reference:

```text
Datasource -> Domain -> Entity -> fields
                                |- dimensions: how to segment
                                |- time dimension: when to analyze
                                |- measures: row-level quantities
                                `- metric: trusted, analysis-ready business number
                                           -> session.observe(...)
```

Keep semantic refs, `ai_context`, datasource declarations, entities, dimensions,
time dimensions, measures, relationships, authoring evidence, and readiness
contracts. Reorder their introductions only when this supports the dependency
model and does not alter technical content.

### Metrics as the center of analysis

Replace the flat list of metric builders with a metric-first narrative:

1. **What makes a metric trusted and analysis-ready** — its business definition,
   calculation, time behavior, applicable dimensions, guardrails, and readiness.
2. **How to choose an authoring form** — a decision table that routes common
   requirements to `ms.count`, `ms.aggregate`, `ms.ratio`,
   `ms.weighted_average`, `ms.linear`, `ms.cumulative`, or finally
   `@ms.metric` for a genuinely custom Ibis expression.
3. **How an agent consumes a metric** — scoped discovery through the catalog,
   `details().show()` to read meaning and restrictions, scoped readiness, and
   `session.observe(...)` as the first analysis action.
4. **Advanced correctness rules** — additivity, cumulative compatibility, and
   SQL provenance/parity checks, each tied back to why analysis results may be
   correct or misleading.

Use a complete running revenue example consistently across the metric section.
The page must make the recommended simple form visible first and retain all
current advanced builders and constraints as reference material.

## Localization and links

- English and Simplified-Chinese pages use the same information architecture,
  headings, code examples, links, and contract facts.
- Chinese prose is idiomatic product documentation, not a literal translation.
- In-page and cross-page links point only at `/en/latest/` or `/zh-cn/latest/`
  equivalents as appropriate.

## Validation

- Run `npm run verify:content` in `site/` to validate content structure and
  cross-links.
- Run `npm run build` in `site/` to render the bilingual site and detect MDX or
  navigation errors.
- Inspect the generated diff to confirm only the six scoped latest pages change
  during implementation and that no command/API behavior is claimed beyond the
  current documentation and implementation contract.
