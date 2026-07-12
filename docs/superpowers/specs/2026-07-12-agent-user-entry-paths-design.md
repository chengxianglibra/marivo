# Agent-user entry paths documentation design

**Status:** approved for specification review
**Scope:** `site/src/content/docs/{en,zh-cn}/latest/` only. Historical versions
remain unchanged.

## Goal

Make the first Marivo experience shorter and unambiguous for a person working
through a coding agent. The documentation must separate alternative setup and
project-entry paths, leave workflow mechanics to the installed Marivo skills,
and tell the user only what they need to decide, ask for, and review.

## Documentation principles

- Present mutually exclusive choices as choices. Never make the automatic and
  manual installation paths look cumulative.
- Treat an existing semantic-layer project as reusable project state. Cloning
  such a project must not send the user through first-time authoring again.
- Keep prompts outcome-oriented. A prompt names the relevant Marivo skill, the
  business task, and task-specific decisions; the skill owns catalog browsing,
  readiness, sessions, operator selection, evidence capture, and stop rules.
- The agent may inspect evidence and draft semantic objects. The user or
  business owner still confirms and adjusts metric meaning and other business
  definitions.
- Keep English and Chinese `latest` pages aligned. The Chinese prose uses
  `Harness` directly rather than translating the term.

## Home page

The Chinese home-page title and hero tagline are exactly:

> 面向 AI 智能体的数据分析 Harness 框架

The corresponding English title is:

> A Data Analysis Harness for AI Agents

The four capability cards remain, in this order:

1. Semantic Layer
2. Typed Analysis DSL
3. Analysis Session
4. Evidence Engine

Their descriptions explain how each capability helps an agent produce
reviewable analysis. The page sends a new reader to installation, then to the
project-state fork described below.

## Installation paths

The installation page starts with a clear choice between two complete paths.

### Automatic installation

The recommended script is a complete setup path. The page tells the user to
enter the intended project directory first, then states what the script does:
create or reuse `.venv`, install Marivo and packaged backends, and initialize
the current directory as a Marivo project.
After running it, the user goes directly to the shared verification section.
The page must not tell this user to run `pip install` or `marivo init` again.

### Manual installation

This path is for users who want to control the virtual environment, installed
backend extras, and initialization. It contains the ordered manual steps:

1. create and activate a Python 3.12+ environment;
2. install `marivo` with the required backend extra;
3. verify the installed version;
4. run `marivo init` once in the project directory.

Both paths converge on one short verification section covering generated
project files, skill links, backend availability, and environment-variable
references. The optional agent request asks only for a project-readiness
check; it does not repeat installer behavior or ask the agent to recreate the
project.

## Project-state fork

Quick Start begins by asking which project state the user has.

### Existing semantic-layer project

The default route is:

```text
clone → install project dependencies → configure local environment variables
→ ask the agent to inspect project and semantic-layer status → analyze
```

The agent reuses the committed semantic objects. It enters
`marivo-semantic` authoring only when it finds a missing object, a readiness
blocker that requires a model change, or a requested business-definition
change. Existing definitions are still reviewed for applicability to the
current task, but they are not rebuilt by default.

### New project

The default route is:

```text
initialize → identify datasource and business goal → agent drafts semantic
objects → user confirms or adjusts business definitions → agent verifies and
checks readiness → analyze
```

The page keeps a compact example of the generated project artifacts, but does
not require the user to reproduce the full authoring workflow or its Python
calls.

## Prompt boundary

Every prompt in the affected primary routes is checked against
`marivo-semantic` or `marivo-analysis` before publication.

Remove instructions already owned by a skill, including:

- exact `help`, catalog-browsing, inspection, sampling, preview, readiness, or
  recovery call sequences;
- one-object-at-a-time mechanics and other workflow-internal stop rules;
- analysis session creation, operator selection, artifact inspection, and
  evidence-recording mechanics;
- generic safety rules already guaranteed by the skill.

Retain only information specific to the user's task:

- the relevant skill name;
- the datasource or existing project to use;
- the business goal or question;
- known metric meaning, scope, comparison period, and useful breakdowns;
- explicit business decisions the user wants the agent to ask about;
- the review outcome the user expects before work continues.

The Quick Start prompt therefore asks the agent to build or repair the semantic
layer for a stated business goal, ask when a business decision is unresolved,
and stop for approval before analysis. The First Analysis prompt states the
question, confirmed metric, comparison, and optional investigation focus. The
installed `marivo-analysis` skill supplies the rest of the workflow.

## Git-managed semantic layer

The semantic-layer concept page adds a user-facing collaboration section:

- semantic objects are ordinary Python source files under `models/` and are
  committed with the rest of the project;
- teams clone the project to reuse the same approved definitions;
- business-definition changes use a branch and pull-request review so the
  changed meaning, owner, guardrails, and affected metrics are visible;
- reviewers compare semantic declarations and their business context, not only
  whether Python syntax passes;
- datasource declarations reference approved environment-variable names;
  credential values, `.marivo/` runtime state, local caches, and generated
  secrets are not committed;
- after clone or merge, the agent checks project configuration and scoped
  readiness before analysis.

This section explains maintenance and sharing without turning the page into a
Git tutorial.

## First analysis

The first-analysis page assumes a ready existing project whenever possible.
It removes the long prerequisite checklist and replaces it with one sentence:
use an approved metric from the project; if the project is not ready or the
metric meaning is unresolved, ask the agent to route the gap through
`marivo-semantic` first.

The primary request is short and task-shaped. It does not prescribe
`observe`, `compare`, `attribute`, session handling, readiness calls, or result
inspection. The page explains those actions afterward as an optional view of
what Marivo does, not as instructions the user must supply.

The user's final review remains explicit: confirm the metric meaning, time and
population scope, comparison, important evidence, and stated limitations.

## Files and scope

Primary files to update in both locales:

- `site/src/content/docs/{en,zh-cn}/latest/index.mdx`
- `site/src/content/docs/{en,zh-cn}/latest/installation.mdx`
- `site/src/content/docs/{en,zh-cn}/latest/quick-start.mdx`
- `site/src/content/docs/{en,zh-cn}/latest/first-analysis.mdx`
- `site/src/content/docs/{en,zh-cn}/latest/concepts/semantic-layer.mdx`

Other `latest` pages may receive narrowly scoped link or wording corrections
only when needed to keep these paths consistent. Navigation groups and
historical version trees remain unchanged unless a broken latest link requires
repair.

## Validation

- Verify the exact Chinese home title and aligned English title.
- Verify installation presents two complete, mutually exclusive paths and only
  the manual path requires a separate `marivo init` step.
- Verify Quick Start exposes both existing-project and new-project routes.
- Compare every affected prompt against the applicable skill and remove
  duplicated workflow instructions.
- Verify the semantic-layer page documents Git-based reuse, maintenance, and
  sharing while excluding credentials and local runtime state.
- Verify First Analysis can be started with one short business request.
- Confirm English and Chinese `latest` content remains structurally aligned.
- Confirm historical versioned documentation has no diff.
- Run `npm run verify:content`, `npx astro check`, `npx astro build`, and
  `npm run postbuild` from `site/`.

## Non-goals

- Do not change Marivo runtime, installer, skill, CLI, or public API behavior.
- Do not rewrite historical documentation versions.
- Do not add a general Git tutorial or prescribe a hosting provider.
- Do not duplicate skill-owned workflow in prose under a different heading.
