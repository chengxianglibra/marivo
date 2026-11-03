# Marivo Python API Reference (Sphinx)

This directory contains the Sphinx source for the public Python API reference.
It documents `marivo.datasource`, `marivo.semantic`, `marivo.analysis`, and
optional `marivo.ontology` from each module's `__all__` exports and docstrings.

Build from the repository root with the docs extra installed:

```bash
.venv/bin/pip install -e ".[docs]"
make docs-api
```

`make docs-api` deletes the previous output and rebuilds with warnings as
errors. That is the build `make check`, CI, and release use. `make check-fast`
calls `make docs-api-incremental`, which keeps the output directory and builds
with `-j auto`. The incremental build is only a local inner loop.

The generated site is written to `site/public/api/`; the Astro site serves it
at `/api/`. A full site build also runs the API documentation build.
