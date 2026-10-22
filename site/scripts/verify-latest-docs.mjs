import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { docsRoots, docsByVersion, latestOnlyDocs } from '../docs-versions.mjs';
import { usageDocs, movedGuides } from '../usage-docs.mjs';

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

for (const page of usageDocs) {
  const examples = docsRoots.map((edition) => {
    const source = readFileSync(join(site, 'src/content/docs', edition, 'latest', page), 'utf8');
    return [...source.matchAll(/```(?:python|bash|toml)\n([\s\S]*?)```/g)].map((match) => match[0]);
  });
  assert.deepEqual(examples[0], examples[1], `Bilingual code drift: ${page}`);
}
for (const edition of docsRoots) {
  for (const [oldPage, newPage] of Object.entries(movedGuides)) {
    const html = readFileSync(join(site, 'dist', edition, 'latest', oldPage.replace(/\/index$/, ''), 'index.html'), 'utf8');
    const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)?.[1] ?? "";
    const destination = newPage === "index" ? `/${edition}/latest/` : `/${edition}/latest/${newPage}/`;
    assert.ok(main.includes(destination), `Missing migration destination: ${edition}/${oldPage}`);
    assert.ok(/name="robots" content="noindex"/.test(html), `Migration page indexed: ${oldPage}`);
  }
}
console.log(`Verified bilingual executable blocks and ${Object.keys(movedGuides).length * docsRoots.length} migration pages.`);
