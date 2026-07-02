# Multi Semantic Layer Roots Design

## Status

Accepted design for implementation planning.

Date: 2026-07-02

## Context

Marivo currently treats one project root as the semantic-layer boundary:

- datasources are loaded from `<project>/models/datasources/`;
- semantic objects are loaded from `<project>/models/semantic/<domain>/`;
- `ms.load()` returns a single ready `SemanticCatalog`;
- `SemanticProject` remains the internal project/load boundary behind the
  public catalog surface.

In enterprise use, different business domains may manage their semantic-layer
objects in different GitLab projects. A central analysis project should be able
to load those authored semantic-layer packages without copying their files into
the current repository.

The current `models/` directory in the active project must continue to load by
default. External layer configuration should extend that default, not replace
it.

## Decision

Add project-level configuration in `marivo.toml`:

```toml
[semantic]
layer_paths = [
  "../sales-domain/models",
  "/opt/company/finance-domain/models",
]
```

Each path points to an authored Marivo `models/` root. Relative paths are
resolved relative to the current project root, the directory containing
`marivo.toml`. Absolute paths are used as-is.

The local project's `<project>/models` root is always loaded first. Configured
external roots are loaded after it in `layer_paths` order.

Each configured external root must contain both:

```text
<models-root>/datasources/
<models-root>/semantic/
```

The roots are combined into one datasource registry, one semantic registry, one
sidecar map, and one public catalog. User-visible semantic refs do not gain a
project namespace. Existing refs such as `metric.sales.revenue` remain the
agent-facing contract.

Any datasource, domain, or semantic object name collision fails the load. Marivo
does not merge domains, does not override objects by configuration order, and
does not accept matching duplicate datasource declarations.

## Goals

- Allow a project to load semantic-layer objects from multiple Git-managed
  `models/` directories.
- Preserve `ms.load()` and `catalog.load()` as the only normal public load
  surfaces.
- Preserve the current local `models/` default without requiring config.
- Keep loaded object refs stable and business-oriented, not repository-oriented.
- Fail closed on all duplicate datasource, domain, and semantic object names.
- Produce errors that identify both the conflicting name and the source paths
  involved.
- Keep analysis sessions and readiness working against one unified catalog.

## Non-Goals

- Do not introduce a user-visible project namespace such as
  `project.domain.object`.
- Do not load external `.marivo/` state or external `marivo.toml` files.
- Do not support per-layer enablement, labels, or metadata in this iteration.
- Do not support multiple config shapes. `layer_paths` is a list of strings.
- Do not make `layer_paths` replace the current project's default `models/`.
- Do not allow silent fallback when a configured external root is invalid.

## Configuration Contract

`marivo.toml [semantic].layer_paths` is optional.

When omitted, Marivo behaves as it does today: only the current project's
`models/` root is loaded.

When present, `layer_paths` must be a list of strings. Any other shape is a
typed load error. Empty lists are allowed and are equivalent to omitting the
setting.

Each configured path is normalized to an absolute path:

- absolute path: use the path directly;
- relative path: resolve against the current project root.

Duplicate normalized roots are load errors, including duplicates of the local
project root. This avoids executing the same declaration files twice.

Configured roots are external layer roots. Unlike the local project root, they
must already contain both `datasources/` and `semantic/`. Missing directories,
non-directory paths, and nonexistent paths are load errors.

## Loading Architecture

Introduce an internal models-root abstraction:

```text
root 0: <workspace>/models
root 1: <resolved layer path 1>
root 2: <resolved layer path 2>
```

Each root contributes:

```text
<models-root>/datasources/
<models-root>/semantic/
```

`SemanticProject` still represents the active workspace. During
`SemanticProject.load()`, it resolves `marivo.toml`, builds the ordered models
root list, and passes it to a multi-root loader.

The multi-root loader should perform one aggregate load and one aggregate
validation:

1. Load datasource declarations from every root's `datasources/` directory.
2. Discover domain directories under every root's `semantic/` directory.
3. Execute each domain's `_domain.py` and sibling semantic files with synthetic
   module prefixes that remain isolated per semantic root.
4. Collect all `LoaderContext` objects.
5. Build one `Registry` and one `Sidecar`.
6. Detect duplicate datasource names, duplicate domain names, and duplicate
   semantic ids with source-path diagnostics.
7. Run assembly validation once over the aggregate registry and sidecar.

This is intentionally not "load each root independently and merge registries."
Cross-layer references should be visible to normal validation, and all readiness
and analysis paths should continue to consume one project registry.

## Conflict Rules

Conflicts are fatal load errors:

- same datasource name in two datasource files;
- same domain name in two `semantic/<domain>/` directories;
- same semantic id in two authored semantic objects, including entities,
  dimensions, time dimensions, measures, metrics, and relationships.

The error message must include:

- the conflicting name;
- the conflict kind;
- both source paths when available;
- the next step: rename or remove one declaration.

Matching duplicate datasource declarations are still rejected. The loader should
not compare declarations and decide that duplicates are safe.

## Domain Filtering

`ms.load(domains=...)` and `catalog.load(domains=...)` continue to filter by
domain name.

Filtering applies across all configured roots. For example, if `sales` lives in
an external root, `ms.load(domains=["sales"])` loads that external domain and
the datasource declarations from all valid models roots. Datasources remain
project-level declarations, not domain-scoped declarations.

Requested missing domains still produce a warning instead of a hard error, as
the current single-root loader does.

## Error Handling

Config and root-shape failures should surface through the existing semantic load
failure path. `ms.load()` continues to raise `SemanticLoadFailed` when the
project cannot produce a ready catalog.

Required failures:

- `[semantic].layer_paths` is present but is not a list of strings.
- A configured root does not exist.
- A configured root exists but is not a directory.
- A configured root is missing `datasources/`.
- A configured root is missing `semantic/`.
- A normalized configured root is duplicated.
- A normalized configured root equals the local `<project>/models` root.
- A datasource, domain, or semantic object name conflicts across roots.

The current loader records too little object provenance for high-quality
duplicate diagnostics. Implementation should preserve or attach source paths to
loaded contexts or pending objects so duplicate errors are auditable.

## Public Surface

The public load surface remains:

```python
catalog = ms.load()
catalog.load()
catalog = ms.load(domains=["sales"])
```

No new public loader API is introduced.

`catalog.list()` still returns top-level domains and datasources. It does not add
a repository or project layer to browsing. Future work may expose source paths
in `details()` or load diagnostics, but this iteration does not change catalog
output unless needed for load errors or tests.

## Tests

Add focused tests for:

- no `[semantic]` config loads only local `models/`;
- relative `layer_paths` resolve against the project root;
- absolute `layer_paths` work;
- non-list and non-string config values fail;
- configured root missing `datasources/` fails;
- configured root missing `semantic/` fails;
- duplicate configured roots fail;
- local root repeated in `layer_paths` fails;
- local plus external roots load two domains and two datasources into one
  catalog;
- external semantic objects can be retrieved through `catalog.get(...)`;
- duplicate datasource names fail with both source paths;
- duplicate domain names fail with both source paths;
- duplicate semantic ids fail with both source paths;
- `ms.load(domains=...)` filters across local and external roots;
- `catalog.load()` reloads configured external roots and sees newly authored
  objects;
- at least one analysis/session smoke path can resolve an external layer object
  through `SemanticProject(workspace_dir=project_root)`.

Use repository entrypoints for verification, starting with targeted tests and
then broadening based on touched surfaces.

## Documentation

Update active/latest documentation only:

- English and Chinese installation or project layout docs should describe
  optional `[semantic].layer_paths`.
- English and Chinese semantic-layer concept docs should show that the current
  project's `models/` root is always loaded and external roots can be added.
- Any runtime help or `describe(ms.load)` text that states the load contract
  should mention configured layer paths.

Do not update historical site versions for this change.

`agent-guide.md` does not need an update because this is product behavior, not a
repository-wide coding or testing rule.

## Acceptance Criteria

- A project with no `[semantic]` config behaves the same as today.
- A project can load current `models/` plus one or more external `models/` roots.
- External roots contribute both datasources and semantic objects.
- All refs remain unchanged and do not include project names.
- Invalid config and invalid roots fail closed with actionable load errors.
- Duplicate datasource, domain, and semantic object names fail closed.
- Existing analysis flows consume the unified catalog without a new public API.
