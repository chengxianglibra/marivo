# Site maintenance

Run `npm run dev`, `npm run verify:content`, or `npm run build` from this directory.
These entrypoints materialize the ignored page copies in `shared-docs.json` before
loading content. Direct `astro` invocation requires running
`node scripts/materialize-shared-docs.mjs` first.

Each shared page keeps its original path and frontmatter. Its body comes from an
authored page in this repository; body and complete-page hashes bind the original
bytes. Generation rejects modified copies, changed source bodies, duplicate
targets, generated sources, and paths outside the content tree. It validates the
whole manifest before writing any page and does not access the network.

For a version-specific edit, remove the target from the manifest and its ignore
entry, then track and edit the already materialized page. Do not edit a shared
source to change only one historical version. An intentional shared-body update
must update the manifest hashes and independently review the affected page
baselines in `scripts/docs-page-baseline.json`.

`docs-versions.mjs` owns version/page lists for navigation and content checks.
`npm run test:shared-docs` verifies cold and warm generation, original shared-page bytes,
and rejection of edits or malformed manifests. `npm run build` additionally
checks Astro types, compiles every route, verifies all original documentation
routes, and builds the Python API reference. The route baseline permits normal
content edits to authored pages without freezing their text.
