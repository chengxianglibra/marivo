# Marivo Python API reference (Sphinx)

This directory is the Sphinx source for the generated Python API reference. It
documents the public surface — `marivo.datasource`, `marivo.semantic`, and
`marivo.analysis` — from each module's `__all__` exports and Google-style
docstrings.

## Build

From the repository root, with the `docs` extra installed:

```bash
.venv/bin/pip install -e ".[docs]"
make docs-api
```

`make docs-api` runs `sphinx-build` into `site/public/api/` (gitignored), which
the Astro site serves at `/api/`. A full site build regenerates it
automatically via the site's npm `prebuild` step:

```bash
cd site && npm run build
```

The build uses `-W`, so any unresolved reference or malformed docstring fails
the build. The publish pipeline must run on a Python-capable host.
