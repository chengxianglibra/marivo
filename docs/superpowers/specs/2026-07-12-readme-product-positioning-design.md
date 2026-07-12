# README Product Positioning Design

**Date:** 2026-07-12
**Status:** Approved for planning

## Context

The root README pair predates the latest site documentation structure. Both files describe Marivo accurately, but they lead with the older “metric-centered analysis runtime” category, mix product positioning with API reference, and require a new visitor to read installation tables and Python analysis code before understanding the intended agent-facing workflow.

The Chinese README also carries more untranslated implementation terminology than the latest site documentation. Its sentence structure often follows the English version instead of reading like native Chinese product documentation.

The README is the GitHub project landing page. It should help a new visitor answer four questions quickly:

1. What is Marivo?
2. Why does an AI agent need it?
3. What capabilities does it provide?
4. How do I try it through an agent?

Detailed installation options, semantic authoring examples, runtime API reference, and troubleshooting belong in the site documentation.

## Goal

Rewrite `README.md` and `README.zh-CN.md` as concise, product-first introductions aligned with the latest site documentation.

The first screen should establish that Marivo is a data analysis Harness for AI agents. The README should then explain that Marivo gives an agent a governed semantic contract, typed analysis operations, persistent analysis sessions, and auditable evidence instead of relying on free-form SQL generation.

The main usage path must be agent-facing: initialize a project, reuse or create its semantic layer with an agent, then state a business question and let `marivo-analysis` handle the analysis workflow.

## Audience

Primary audience:

- developers and data-platform engineers evaluating Marivo from GitHub;
- teams building AI agents that analyze business data;
- technical decision-makers comparing Marivo with direct Text-to-SQL approaches.

The README does not assume that the reader already knows Marivo APIs or semantic-layer implementation details.

## Chosen Approach: Product Positioning First

The README will be a compact product landing page rather than a condensed API manual.

It will retain enough commands to let a reader start, but route detail to `marivo.io`. It will not attempt to duplicate the site installation page, semantic-layer tutorial, analysis operator reference, or deployment guidance.

## Positioning

Use these primary titles:

- English: **A Data Analysis Harness for AI Agents**
- Chinese: **面向 AI 智能体的数据分析 Harness 框架**

The opening explanation should establish:

- Marivo is a Python framework used where the agent runs;
- it helps an agent analyze business data through approved semantics and typed operations;
- it keeps the investigation and evidence reviewable;
- it is not a hosted chat UI and not a Text-to-SQL wrapper.

The README should describe the outcome before implementation details. Ibis expressions, typed frames, semantic refs, findings, propositions, and assessments may appear only where they materially clarify a capability; they should not dominate the opening.

## Information Architecture

### 1. Header and One-Sentence Positioning

Keep the reciprocal language links near the top. Follow them with the primary positioning line and a short two-paragraph explanation of the problem and outcome.

The reader should understand the product before encountering commands or API names.

### 2. Why Marivo

Use one short section rather than the current large comparison table.

Explain that giving an agent raw schemas and asking it to generate SQL leaves metric meaning, joins, filters, analysis steps, and evidence implicit. Marivo moves those decisions into shared contracts and bounded analysis operations that can be reused and reviewed.

Avoid adversarial marketing language or absolute reliability claims. State the practical difference precisely.

### 3. Four Core Capabilities

Present exactly these four product capabilities:

1. **Semantic Layer** — code-managed business definitions, datasource bindings, relationships, guardrails, and stable metric/dimension references. The agent assists with drafting; the user or business owner confirms business meaning.
2. **Typed Analysis DSL** — typed operators such as observe, compare, and attribute replace free-form analysis steps and return explicit result objects.
3. **Analysis Session** — one project-local investigation keeps the question, results, intermediate artifacts, and history together.
4. **Evidence Engine** — material findings and judgments remain connected to their source results so conclusions can be reviewed and audited.

Readiness remains an important safeguard but is not presented as a fifth core capability. Explain it briefly as the technical handoff that stops incomplete semantic objects before analysis.

### 4. How Users Work With Marivo

Describe the default path in three steps:

1. Install and initialize a Marivo project.
2. Reuse an existing semantic layer, or ask an agent using `marivo-semantic` to draft the missing definitions for a new project.
3. State a business question; the agent uses `marivo-analysis` to check readiness, choose typed analysis steps, preserve evidence, and return the conclusion and limitations.

User responsibility should be expressed as a practical reminder, not an approval checklist: confirm choices that materially affect business meaning or how the conclusion will be used.

Do not ask the user to write Python, choose operators, manage sessions, or prescribe evidence fields in the README path.

### 5. Quick Start

Show one recommended installation path:

```bash
curl -fsSL https://marivo.io/install.sh | bash
```

Explain in one sentence that it prepares the local environment and initializes the current project. Link to the site installation page for manual setup, datasource extras, supported platforms, doctor commands, and troubleshooting.

After installation, provide one natural-language request that demonstrates the product, for example asking the agent to use an approved revenue metric to explain a period-over-period decline, show key evidence, and state limitations.

The request must remain concise and must not repeat `marivo-semantic` or `marivo-analysis` workflow.

### 6. Documentation and Development

Keep a short documentation section linking to:

- Installation
- Quick Start
- First agent-guided analysis
- Semantic Layer
- Analysis Workflow
- Evidence

Keep a compact development section with the existing supported repository entrypoints. Development commands are not part of the product story and should appear last.

## Content to Remove

Remove from both README files:

- the backend-extra installation table;
- detailed installer flags and native-Windows explanation;
- the multi-command doctor walkthrough;
- the full Python semantic/analysis code example;
- the Publish/S3 section;
- low-level implementation explanation that duplicates concept reference pages;
- repetitive user approval lists.

The removed information remains available in the latest site documentation when it is still part of the supported product contract. Publish/S3 guidance must not be routed from the README because it was intentionally removed from the product documentation path.

## Bilingual Writing Rules

- Keep `README.md` and `README.zh-CN.md` aligned in structure, product claims, commands, and links.
- Write each language independently; do not translate sentence by sentence.
- Chinese prose should use established terms such as “语义层”“类型化分析”“分析会话”“证据链”“就绪检查” and “业务口径”.
- Keep `Harness` untranslated in Chinese.
- Keep API names, skill names, paths, and commands in code formatting.
- Avoid unnecessary mixtures such as “Python library”“typed frames”“backend extras” in normal Chinese prose when a clear Chinese term exists.
- Preserve reciprocal English/Chinese links near the top.

## Scope

In scope:

- `README.md`
- `README.zh-CN.md`
- links from those files to current latest site routes

Out of scope:

- changing site documentation;
- changing runtime behavior, public APIs, packaging, installer behavior, or bundled skills;
- changing historical documentation versions;
- adding badges, diagrams, screenshots, benchmarks, or new marketing claims;
- publishing or pushing the result.

## Verification

Verify:

- both README files contain reciprocal language links;
- both use the approved positioning title;
- both present exactly the four named core capabilities;
- both use the same recommended installer command;
- both include an agent-facing first-analysis request;
- neither contains the backend installation table, full Python analysis example, doctor walkthrough, or Publish/S3 section;
- all `marivo.io` links use valid latest English or Chinese routes;
- `git diff --check` passes;
- the worktree contains only the intended README pair before commit.

## Acceptance Criteria

- A new GitHub visitor can identify Marivo as a data analysis Harness for AI agents from the opening section.
- The reader understands how Marivo differs from direct Text-to-SQL without reading API reference.
- The four core capabilities are prominent and use the same names as the site homepage.
- The default usage path is agent-facing and does not require the user to write Python or manage Marivo workflow details.
- Installation and the first agent request are visible without a long setup section.
- The README pair is materially shorter than the current version while preserving accurate product boundaries.
- Chinese reads as native technical product documentation rather than translated English.
- English and Chinese content remain aligned.
