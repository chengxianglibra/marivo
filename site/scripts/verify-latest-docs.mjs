import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { docsRoots, docsByVersion, latestOnlyDocs } from '../docs-versions.mjs';

const site = fileURLToPath(new URL('..', import.meta.url));
const pages = [...docsByVersion.latest, ...latestOnlyDocs]
  .filter((page) => !page.startsWith('release-notes/') && page !== 'contributing.mdx');
const checked = new Set();
let count = 0;
for (const edition of docsRoots) {
  for (const page of pages) {
    const relative = `${edition}/latest/${page}`.replace(/\.mdx$/, '').replace(/\/index$/, '');
    const output = join(site, 'dist', relative, 'index.html');
    assert.ok(existsSync(output), `Missing latest page: ${relative}`);
    const html = readFileSync(output, 'utf8');
    const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)?.[1];
    assert.ok(main?.includes('<h1'), `Empty latest page: ${relative}`);
    for (const [, href] of html.matchAll(/\bhref="([^"]+)"/g)) {
      if (!href.startsWith('/docs/') && !href.startsWith('/zh-cn/docs/') && !href.startsWith('/api/')) continue;
      const url = new URL(href.replaceAll('&amp;', '&'), 'https://marivo.io');
      const key = url.pathname + url.hash;
      if (checked.has(key)) continue;
      checked.add(key);
      const target = url.pathname.endsWith('.html')
        ? join(site, 'dist', decodeURIComponent(url.pathname))
        : join(site, 'dist', decodeURIComponent(url.pathname), 'index.html');
      assert.ok(existsSync(target), `${relative}: broken link ${href}`);
      if (url.hash) {
        const fragment = decodeURIComponent(url.hash.slice(1));
        const targetHtml = readFileSync(target, 'utf8');
        assert.ok(targetHtml.includes(`id="${fragment}"`), `${relative}: missing anchor ${href}`);
      }
    }
    count += 1;
  }
}
console.log(`Verified ${count} latest usage pages and ${checked.size} internal route/anchor targets.`);
