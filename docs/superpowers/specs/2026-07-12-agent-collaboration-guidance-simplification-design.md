# Agent Collaboration Guidance Simplification Design

**Date:** 2026-07-12
**Status:** Approved for planning

## Context

The latest site documentation is organized around users working with Marivo through an agent. The **Work with an agent** sidebar group currently includes:

- Business question
- Semantic layer
- Analysis workflow
- Readiness
- Evidence

These pages correctly preserve user ownership of business meaning, but their user-facing sections ask for too many detailed inputs, approvals, checks, and workflow instructions. Several copyable prompts also repeat behavior already owned by `marivo-semantic` or `marivo-analysis`. This makes normal use feel like operating Marivo manually through an agent instead of delegating analysis to an agent with appropriate business oversight.

## Goal

Make the collaboration guidance easier to use without weakening the division of responsibility:

- the user explains the business goal and confirms material business meaning;
- the agent uses the bundled skills to choose and execute the technical workflow;
- Marivo supplies bounded semantic, readiness, analysis, and evidence contracts;
- the user decides whether the resulting conclusion is suitable for the intended decision.

The documentation should primarily remind users how to work with the agent well. It should not require them to memorize Marivo APIs, prescribe the agent's workflow, or complete an exhaustive form before beginning.

## Chosen Approach: Light Entry, Deep Reference

Each page will have a short user-oriented entry section followed, where necessary, by deeper technical reference material.

The entry section answers only:

1. When should the user use this page or capability?
2. What information is useful to give the agent?
3. What should the user pay attention to when reviewing the result?

The reference section may continue to document object models, APIs, reports, and evidence structures for readers who need implementation detail. User guidance and technical reference must be visually and structurally distinct.

This approach preserves the existing five-page navigation and technical depth while reducing the perceived burden of ordinary use.

## Page Changes

### Business Question

Replace the mandatory worksheet with a small set of helpful inputs:

- the decision or question;
- the metric or business outcome, if known;
- the comparison, scope, or area of concern, if relevant.

Users may start with incomplete information. The agent should clarify only material ambiguity as it arises. The copyable request should state the business question naturally and should not instruct the agent to browse catalogs, call particular APIs, stop at named checkpoints, or reproduce skill workflow.

The page should remind users that they need to confirm choices that materially change the interpretation of the result, not approve a fixed checklist before every analysis.

### Semantic Layer

Keep the distinction between agent-assisted drafting and user approval of business meaning. Simplify the user-facing responsibility from a field-by-field approval table to a short principle:

- confirm that the metric represents the intended business outcome;
- call out important inclusion, exclusion, time, unit, or usage rules;
- correct the agent when a proposed definition does not match the business.

The agent remains responsible for inspecting available evidence, drafting Python objects, validating them, previewing them, and reporting unresolved semantic decisions. Metric organization, Git maintenance, object reference, and API details remain available as deeper reference.

### Analysis Workflow

Remove expectations that users arrange sessions, select typed operators, request particular `show()` or `contract()` calls, or manage checkpoints. Explain that the agent organizes the investigation using `marivo-analysis`.

The user should participate when a choice could materially change the conclusion, such as changing the metric, population, comparison, or interpretation. Routine operator selection and session management stay with the skill.

### Readiness

Lead with the practical meaning of readiness: the agent may pause before analysis because the required semantic objects are incomplete or lack current technical evidence.

Tell the user how to respond:

- clarify an unresolved business rule when asked;
- approve or correct a proposed semantic meaning;
- allow the agent to repair technical declarations and rerun checks.

Remove the long user prompt that restates the semantic skill's validation and readiness sequence. Preserve the detailed `ReadinessReport`, issue kinds, and API behavior as technical reference.

### Evidence

Replace exhaustive user review lists with three core reminders:

- does the conclusion answer the business question?
- are the important claims supported by visible Marivo evidence?
- do limitations or data-quality issues change how the conclusion may be used?

The agent should present the conclusion, evidence, and limitations without the user listing every artifact or evidence field in a prompt. Preserve the evidence model, session knowledge, and audit API as deeper reference.

## Prompt Policy

Copyable prompts in this group should:

- use natural business language;
- mention the appropriate Marivo skill only when routing would otherwise be unclear;
- include only user intent, known constraints, and desired output;
- rely on the skill for catalog browsing, readiness checks, operator selection, session discipline, evidence collection, and handoff behavior.

Prompts must not duplicate static contracts from `md.help`, `ms.help`, or `mv.help`, and must not restate workflow already defined by `marivo-semantic` or `marivo-analysis`.

## Information Architecture and Style

- Keep the existing **Work with an agent** navigation group and its five pages.
- Put practical guidance before technical explanation.
- Use short paragraphs and small reminder lists; avoid forms, approval matrices, and exhaustive checklists in the user entry path.
- In Chinese, use natural product documentation language and keep `Harness` untranslated.
- Keep English and Chinese latest documentation structurally aligned, while writing each language idiomatically rather than translating sentence by sentence.
- Do not modify historical versioned documentation.

## Scope

In scope:

- `site/src/content/docs/en/latest/guides/business-question.mdx`
- `site/src/content/docs/zh-cn/latest/guides/business-question.mdx`
- latest English and Chinese semantic-layer collaboration guidance
- latest English and Chinese analysis-workflow collaboration guidance
- latest English and Chinese readiness collaboration guidance
- latest English and Chinese evidence collaboration guidance
- small navigation or cross-link wording changes required for coherence
- site content, Astro, and build verification

Out of scope:

- changing Marivo runtime behavior or public APIs;
- changing the bundled skill contracts;
- removing technical reference material solely to shorten pages;
- changing historical documentation versions;
- restructuring the entire site navigation.

## Acceptance Criteria

- A user can begin by stating a business question without completing a mandatory worksheet.
- User-facing sections do not require knowledge of Marivo operators, sessions, artifacts, or readiness APIs.
- User responsibilities are framed as practical review reminders rather than exhaustive approval procedures.
- Copyable prompts do not reproduce bundled skill workflows.
- The semantic-layer page still makes clear that the agent only assists with drafting and the user or business owner confirms metric meaning.
- Technical reference for semantic objects, readiness reports, and evidence remains available.
- English and Chinese latest pages stay aligned in structure and intent.
- Historical documentation is unchanged.
- Site content verification, Astro check, and Astro build pass.
