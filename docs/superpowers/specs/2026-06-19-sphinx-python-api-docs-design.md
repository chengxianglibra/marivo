# Sphinx Python API Docs Design

Date: 2026-06-19

Status: approved scope, pending written-spec review

## Problem

The Marivo docs site (Astro + Starlight, see
`2026-06-17-marivo-static-site-design.md`) ships hand-written conceptual pages
(installation, quick start, concepts) but has no generated reference for the
public Python surface. The public API is three modules — `marivo.datasource`,
`marivo.semantic`, `marivo.analysis` — each exposing a large, `__all__`-pinned
set of symbols with rich Google-style docstrings (130+ `Args:` blocks, 110+
`Returns:` blocks across the surface). Agents and humans currently have to read
source or `help()`/`describe()` output to learn signatures and parameters.

We want a generated, browsable API reference built with Sphinx and published at
an appropriate location inside the existing site so it ships with every site
build.

## Goals

- Generate an HTML API reference for the three public modules with Sphinx,
  driven by the existing `__all__` lists and Google-style docstrings.
- Publish it as part of the existing Astro/Starlight site at `/api/`.
- Keep generated HTML out of git; build it from tracked Sphinx source.
- Make the reference reachable from the site navigation.

## Non-Goals

- No per-version API trees (`/api/latest`, `/api/v0.1`). First cut is
  latest-only. Versioning can be layered on later if needed.
- No internationalization of the API reference. Docstrings are English-only by
  repository rule, so the reference is English-only even though the surrounding
  Starlight site is bilingual.
- No attempt to re-render Sphinx output into Starlight MDX. The reference is a
  standalone Sphinx-themed subtree.
- No new deploy pipeline/CI changes beyond what is needed to build locally; the
  site's existing deploy mechanism is unchanged (it must run the build on a
  Python-capable host — see Constraints).

## Approach

Standalone Sphinx HTML served as a static subtree at `/api/`.

Astro copies `site/public/` verbatim into its build output, so Sphinx builds
into `site/public/api/` and is served at `<site>/api/`. Sphinx keeps its own
PyData theme; there is no conversion step into Starlight's content collection.

Alternative considered and rejected: convert Sphinx output to MDX and embed it
natively in Starlight (e.g. `sphinx-markdown-builder` / MyST). That loses
autodoc signatures, cross-references, and typehint rendering — significantly
more work for a worse reference. Embedding a standalone Sphinx subtree is the
standard way to combine Sphinx with a JAMstack site.

## Components

### 1. Sphinx source (tracked) — `docs/api/`

The conventional Sphinx project home, kept outside the Astro `src/` tree so
Astro never tries to process `.rst` files.

- `docs/api/conf.py`
  - Extensions: `sphinx.ext.autodoc`, `sphinx.ext.autosummary`,
    `sphinx.ext.napoleon` (with `napoleon_google_docstring = True`),
    `sphinx.ext.viewcode`, `sphinx.ext.intersphinx`.
  - `html_theme = "pydata_sphinx_theme"`.
  - Project version resolved from `importlib.metadata.version("marivo")`, the
    same source `marivo/__init__.py` uses.
  - `intersphinx_mapping` for python and pandas (both publish a stable
    `objects.inv`; omit targets without a reliable inventory to keep the `-W`
    gate from failing on unresolved external references).
  - `autodoc_typehints` configured so annotations render in signatures.
- `docs/api/index.rst` — landing page plus a `toctree` to the three module
  pages.
- `docs/api/datasource.rst`, `semantic.rst`, `analysis.rst` — each one
  `.. automodule:: marivo.<module>` with `:members:` (and
  `:show-inheritance:`). Because each module `__init__` defines `__all__`,
  autodoc documents exactly the public re-exported surface, including imported
  members, and excludes internals.
- `docs/api/README.md` — short note on how to build (points at `make
  docs-api`).

### 2. Dependencies — `pyproject.toml`

New optional-dependency group:

```toml
[project.optional-dependencies]
docs = [
    "sphinx>=7",
    "pydata-sphinx-theme>=0.15",
]
```

Installed into the existing venv via `.venv/bin/pip install -e ".[docs]"`.

### 3. Build wiring

- `Makefile` target `docs-api`:
  - Guard with `./scripts/require-venv.sh sphinx-build`.
  - Remove any stale `site/public/api`, then
    `$(VENV_BIN)/sphinx-build -W --keep-going -b html docs/api site/public/api`.
  - `-W --keep-going` turns warnings (missing/broken docstrings, bad
    references) into a failing build while still reporting all of them at once.
- `site/package.json` — add a `prebuild` script: `cd .. && make docs-api`.
  npm runs `prebuild` automatically before `build`, so `npm run build` produces
  the complete site (API reference + Astro output) in one command.

### 4. Output (gitignored) — `site/public/api/`

Add `site/public/api/` to `.gitignore`. The directory is build output and is
never committed; only the Sphinx source under `docs/api/` is tracked.

### 5. Navigation — `site/astro.config.mjs`

Add a top-level Starlight sidebar entry
`{ label: 'API Reference (Python)', link: '/api/' }`, with a `zh-CN`
translation label, shown in both locales. The linked content itself is English.

### 6. Documentation note

Add a short "Building the API reference" section to `CONTRIBUTING.md` documenting
`make docs-api`, the `[docs]` extra, and the requirement that the publish build
runs on a Python-capable host.

## Data Flow

`make docs-api` → `sphinx-build` imports `marivo` from `.venv` → reads each
module's `__all__` and Google-style docstrings → writes HTML to
`site/public/api/` → `astro build` copies `public/` into `site/dist/api/` →
served at `/api/`, reachable from the sidebar link.

## Constraints

- The build model is build-time with gitignored output. The deploy host must be
  Python-capable and run the full build (e.g. `npm run build`, which triggers
  `make docs-api`). A Node-only deploy host would not regenerate the reference.
  This trade-off was chosen deliberately over committing generated HTML.
- Repository Python rules apply: no bare `python`/`sphinx-build`; the Makefile
  uses `.venv/bin/...` via the existing `require-venv.sh` guard. The npm
  `prebuild` shells out to `make`, not to a bare interpreter.

## Success Criteria

- `make docs-api` builds `site/public/api/index.html` documenting all three
  modules with zero warnings under `-W`.
- `cd site && npm run build` runs Sphinx then Astro; `site/dist/api/index.html`
  exists in the output.
- The Starlight sidebar shows the "API Reference (Python)" link to `/api/`.
- `site/public/api/` is gitignored and no generated HTML is committed.
- The `docs` optional dependency group installs cleanly.

## Testing And Verification

There are no Python unit tests for this build integration. Verification is
running the two build commands above and confirming the success criteria, plus
`make lint`/`make typecheck` remaining green (no Python source under `marivo/`
changes, but `pyproject.toml` and `Makefile` are touched).
