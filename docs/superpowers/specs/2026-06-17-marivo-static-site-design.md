# Marivo Static Site Design

- **Date:** 2026-06-17
- **Status:** Approved (pending spec review)
- **Scope:** Initial `site/` static website skeleton for Marivo using Astro +
  Starlight, with English and Simplified Chinese content and isolated
  documentation versions.

## Context

Marivo is a Python library for metric-centered analysis workflows driven by AI
agents. The current repository already has strong README-level onboarding and
internal design documentation:

- `README.md` explains the project, installation, quick start, semantic layer,
  analysis session flow, readiness, and evidence tracing.
- `docs/specs/` contains maintained technical specs for current Python semantic
  and analysis contracts.
- `docs/superpowers/` contains design records, discussions, and implementation
  plans for repository work.
- `CONTRIBUTING.md` exists, but some content appears older than the current
  package layout and should not be published without revision.

The public website should become the user-facing entry point for learning,
installing, using, and contributing to Marivo. It should not expose internal
agent execution plans or design scratch space as public product docs.

## Goals

- Add an independent `site/` static website project using Astro + Starlight.
- Publish the first website slice only:
  - home
  - installation
  - quick start
  - concepts
  - contributing
- Support English and Simplified Chinese from the first commit.
- Support isolated documentation versions from the first commit.
- Keep website dependencies and lockfile inside `site/`, not in the repository
  root Node project.
- Reuse the current README narrative where accurate, but split it into
  navigable website pages.
- Keep internal specs and superpowers artifacts out of the public navigation.

## Non-goals

- No blog, release notes, API reference generation, search customization,
  analytics, custom theme system, or deployment workflow in the initial
  skeleton.
- No changes to Marivo Python behavior.
- No rewrite of repository-wide contribution policy beyond the first public
  contributing page.
- No automatic version extraction from git tags or release branches in this
  phase.

## Chosen approach

Use Astro + Starlight with a versioned content tree:

```text
site/src/content/docs/
  en/
    latest/
    v0.1/
  zh-cn/
    latest/
    v0.1/
```

Routes are explicit and symmetric:

```text
/en/latest/
/en/v0.1/
/zh-cn/latest/
/zh-cn/v0.1/
```

This keeps language and version boundaries visible in URLs, avoids hidden
fallback rules for the default language, and makes each release snapshot a
separate directory. Starlight provides built-in internationalization support;
versioned docs are modeled by the project directory structure and sidebar
configuration rather than by a Starlight first-class versioning feature.

## Alternatives considered

### A. Filesystem-isolated versions

Each language/version pair has its own content directory. Release snapshots are
created by copying `latest` into `vX.Y`.

This is the chosen approach. It is explicit, easy to review in git, and does
not require custom build-time content generation.

### B. Generate version directories from git tags

Build the site by checking out release tags or branches and materializing docs
into the Starlight content tree.

This is more automated, but too heavy for the first skeleton. It also requires a
clear release branch policy that Marivo does not yet need for the website.

### C. Use Docusaurus

Docusaurus has mature versioned-docs workflows, but the requested stack is
Astro + Starlight. Switching stacks would trade away Starlight's smaller static
site footprint and built-in docs experience for a capability that can be
modeled explicitly in this phase.

## Information architecture

The initial navigation is deliberately small:

- **Home:** project promise, core capabilities, install CTA, docs CTA.
- **Installation:** Python version, base package install, backend extras, local
  development note.
- **Quick Start:** create a project, declare datasource, declare semantic
  objects, load catalog, run first analysis.
- **Concepts:** compact conceptual pages for the first release:
  - Overview
  - Semantic layer
  - Analysis workflow
  - Readiness
  - Evidence
- **Contributing:** public contributor setup, repository entrypoints, testing
  expectations, documentation update expectations, and PR guidance.

The website should not link to `docs/superpowers/plans/` or
`docs/superpowers/specs/` from public navigation. Stable details can be
re-authored into user-facing docs when they become product-level contracts.

## File structure

```text
site/
  package.json
  package-lock.json
  astro.config.mjs
  src/
    content.config.ts
    content/
      docs/
        en/
          latest/
            index.mdx
            installation.mdx
            quick-start.mdx
            concepts/
              index.mdx
              semantic-layer.mdx
              analysis-workflow.mdx
              readiness.mdx
              evidence.mdx
            contributing.mdx
          v0.1/
            index.mdx
            installation.mdx
            quick-start.mdx
            concepts/
              index.mdx
              semantic-layer.mdx
              analysis-workflow.mdx
              readiness.mdx
              evidence.mdx
            contributing.mdx
        zh-cn/
          latest/
            index.mdx
            installation.mdx
            quick-start.mdx
            concepts/
              index.mdx
              semantic-layer.mdx
              analysis-workflow.mdx
              readiness.mdx
              evidence.mdx
            contributing.mdx
          v0.1/
            index.mdx
            installation.mdx
            quick-start.mdx
            concepts/
              index.mdx
              semantic-layer.mdx
              analysis-workflow.mdx
              readiness.mdx
              evidence.mdx
            contributing.mdx
      i18n/
        en.json
        zh-cn.json
    styles/
      custom.css
  public/
    favicon.svg
```

The `site/` package owns its own lockfile. The root `package.json` currently has
unrelated tooling dependencies and should not become the website package.

## Language policy

- English and Simplified Chinese pages use mirrored paths.
- Every page added to one language must have the same path in the other
  language.
- English is the source language for technical contract precision.
- Chinese pages should be full translations for the initial skeleton, not empty
  placeholders.
- If future translation lags are unavoidable, the missing localized page should
  still exist and clearly state that it is temporarily following the English
  source for that version.

## Version policy

- `latest` tracks documentation for the current `main` branch behavior.
- `v0.1` is the first release snapshot for the current package version.
- A release snapshot is created by copying the matching `latest` tree into
  `vX.Y` and then freezing it.
- Old release directories receive only errata and security-relevant
  corrections, not routine main-branch documentation updates.
- Cross-version links should include both locale and version explicitly.
- The version selector in the first skeleton can be a normal nav/sidebar link
  group. A custom dropdown is not required in this phase.

## Starlight configuration

The initial `astro.config.mjs` should configure:

- `title: "Marivo"`
- `defaultLocale: "en"`
- `locales.en.label = "English"`
- `locales["zh-cn"].label = "简体中文"`
- `locales["zh-cn"].lang = "zh-CN"`
- GitHub social link to `https://github.com/lumendata/marivo`
- Sidebar groups for `latest` and `v0.1` in each locale:
  - Home
  - Installation
  - Quick Start
  - Concepts
  - Contributing

The sidebar should be manually configured in the initial skeleton. Automatic
sidebars are useful later, but manual configuration prevents accidental exposure
of internal or draft pages.

## Content boundaries

Initial pages may reuse accurate README material, but each page should be
edited for website use:

- Installation should keep PyPI and backend extras visible.
- Quick Start should show the core write-run-read flow without becoming an API
  reference.
- Concepts should explain Marivo vocabulary without copying internal design
  records wholesale.
- Contributing should be public-facing and aligned to repository entrypoints:
  `make test`, `make typecheck`, `make lint`, and `make format`.

The existing `CONTRIBUTING.md` should not be copied verbatim because it includes
older structure and command examples that are not fully aligned with current
repository guidance.

## Build and validation

Initial scripts:

```json
{
  "scripts": {
    "dev": "astro dev",
    "build": "astro check && astro build",
    "preview": "astro preview"
  }
}
```

Validation for the implementation:

```bash
npm install --prefix site
npm run build --prefix site
```

Manual preview after implementation:

```bash
npm run dev --prefix site
```

The implementation should verify that the following route families build:

```text
/en/latest/
/en/v0.1/
/zh-cn/latest/
/zh-cn/v0.1/
```

Python tests are not required for the static-site skeleton unless a later change
touches Python package behavior.

## Deployment model

The first skeleton only needs a static build artifact:

```text
site source -> npm install --prefix site -> npm run build --prefix site -> site/dist
```

Any static host can publish `site/dist`: GitHub Pages, Cloudflare Pages, Netlify,
Vercel, or an object store/CDN. A GitHub Actions workflow is a follow-up, not
part of the first skeleton.

## Follow-up phases

After the skeleton is working:

1. Add release notes under the same language/version discipline.
2. Add blog pages outside release-versioned docs, with language mirroring.
3. Add generated API reference from public Python surfaces.
4. Add a richer version selector once the manual sidebar becomes cumbersome.
5. Add deployment automation once the hosting target is chosen.

## Acceptance criteria

- `site/` is an independent Astro + Starlight project.
- English and Simplified Chinese content both exist.
- `latest` and `v0.1` docs are separate directories.
- Initial navigation includes only home, installation, quick start, concepts,
  and contributing.
- Build succeeds with the website package scripts.
- No Python behavior changes are introduced.
