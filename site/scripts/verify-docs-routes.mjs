import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const { routes } = JSON.parse(readFileSync(new URL('./docs-page-baseline.json', import.meta.url), 'utf8'));
for (const [page, route] of Object.entries(routes)) {
  assert.equal(existsSync(join(root, 'dist', route, 'index.html')), true, `Missing route for ${page}: /${route}/`);
}
console.log(`Verified ${Object.keys(routes).length} original documentation routes.`);
