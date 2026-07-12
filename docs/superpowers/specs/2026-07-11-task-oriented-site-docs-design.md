# Task-oriented site documentation design

**Status:** proposed and approved for specification review
**Scope:** `site/src/content/docs/{en,zh-cn}/latest/` and the latest-version
sidebar only. Historical version trees remain unchanged.

## Goal

Make the latest Marivo documentation easier to enter, scan, and follow. The
documentation should read like a practical technical guide rather than product
copy or a translated API reference. The primary model is the Polars user guide:
an obvious first path, concepts grouped by purpose, and detail introduced only
when the reader needs it.

## Information architecture

The latest sidebar will have three user-facing groups:

1. **Get started / 开始使用**
   - Home
   - Installation
   - Quick Start
2. **Core concepts / 核心概念**
   - Overview
   - Semantic layer
   - Analysis workflow
   - Readiness
   - Evidence
3. **Reference and maintenance / 参考与维护**
   - Project configuration
   - Deployment
   - Local telemetry
   - Contributing
   - Python API reference

The existing installation page is split so that the first-use route stays
short. Cross-project semantic roots, telemetry, and deployment become focused
reference pages. S3 publishing is removed from the site documentation rather
than moved. The remaining behavior and claims remain unchanged.

## Page responsibilities

### Home

The home page answers only four questions:

1. What is Marivo?
2. What does it provide for an AI agent?
3. What does one analysis look like?
4. Where should a new reader go next?

It keeps the agreed four capabilities in this order: Semantic Layer, Typed
Analysis DSL, Analysis Session, and Evidence Engine. It replaces the
warehouse-versus-harness marketing table with a compact, concrete analysis
sequence and a small example. The page ends with three links: installation,
quick start, and semantic layer.

### Installation

Installation is a setup page, not a product or operations reference. It has two
paths: the recommended installer and pip. Both lead directly to `marivo init`,
then to Quick Start. A brief project-tree explanation identifies the files a
new user will work with. Configuration and operational material moves out.

### Quick Start

Quick Start remains the first end-to-end tutorial. Its opening is shortened to
the smallest complete path: create a project, declare a datasource and one
metric, load the catalog, run readiness, and execute an observation. Advanced
agent authoring guidance and long operational notes move to their concept or
reference pages.

### Semantic layer

The semantic-layer page becomes a guided concept page before it becomes a
reference page.

1. State that an agent starts from a trusted metric, not a warehouse table.
2. Explain what a metric needs to be usable: business definition, calculation,
   allowed dimensions and time axis, and guardrails.
3. Show the object graph: datasource → domain → entity → fields → metric.
4. Walk through one concise orders/revenue example.
5. Present detailed authoring material in this order: datasource; domain and
   entity; dimensions and time dimensions; measures; metric builders; joins,
   versioning, and advanced metadata.

Existing parameter tables and correctness constraints are retained, but move
under the concept they document instead of appearing before readers understand
why they exist.

## Writing rules

- Lead with the reader's task and the result, then explain the mechanism.
- Use short paragraphs, concrete verbs, and one claim per sentence.
- Write Chinese independently rather than translating English syntax.
- Use established Chinese technical terms: 智能体, 语义层, 指标, 度量, 维度,
  分析会话, 证据链, 就绪检查. Preserve API names, code, and unambiguous
  product names in English.
- Avoid empty qualifiers such as "可信", "收敛", "赋能", and "面向" unless the
  following sentence specifies the concrete behavior or constraint.
- Do not add product claims beyond what current implementation and tests
  establish.

## Validation

- Run `npm run verify:content` in `site/`.
- Run `npx astro check` in `site/`.
- Run `npx astro build` in `site/`.
- Confirm the generated sidebar exposes the three latest-version groups and
  that English and Chinese navigation have matching routes.
- Review the changed Chinese pages for untranslated prose and direct
  sentence-structure translation.

## Non-goals

- Do not change historical versioned documentation.
- Do not change Marivo runtime behavior, CLI behavior, or public APIs.
- Do not redesign the visual theme or introduce a custom documentation UI.
- Do not document `marivo publish` or S3 publishing in the latest site.
