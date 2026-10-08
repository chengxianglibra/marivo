import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = fileURLToPath(new URL('..', import.meta.url));
const hash = (text) => createHash('sha256').update(text).digest('hex');

export function materializeSharedDocs(root, entries, { check = false } = {}) {
  const contentRoot = resolve(root, 'src/content/docs');
  const targets = new Set(entries.map((entry) => entry.target));
  if (targets.size !== entries.length) throw new Error('Duplicate shared document target');
  const pathFor = (name) => {
    const path = resolve(contentRoot, name);
    const local = relative(contentRoot, path);
    if (isAbsolute(local) || local.startsWith('..') || !local.endsWith('.mdx')) {
      throw new Error(`Invalid shared document path: ${name}`);
    }
    return path;
  };
  // Validate the complete batch before writing any generated page.
  const pages = entries.map((entry) => {
    if (targets.has(entry.source)) throw new Error('Shared document sources must be authored');
    const source = readFileSync(pathFor(entry.source), 'utf8');
    const match = source.match(/^---\s*\n[\s\S]*?\n---\s*\n([\s\S]*)$/);
    if (!match || hash(match[1]) !== entry.bodySha256) {
      throw new Error(`Shared document body changed: ${entry.source}`);
    }
    const text = entry.frontmatter + match[1];
    if (hash(text) !== entry.pageSha256) throw new Error(`Shared document metadata changed: ${entry.target}`);
    const path = pathFor(entry.target);
    if (existsSync(path) && readFileSync(path, 'utf8') !== text) {
      throw new Error(`Refusing to overwrite edited shared document: ${entry.target}`);
    }
    if (check && !existsSync(path)) throw new Error(`Missing shared document: ${entry.target}`);
    return { path, text };
  });
  if (!check) for (const { path, text } of pages) if (!existsSync(path)) writeFileSync(path, text);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const entries = JSON.parse(readFileSync(resolve(siteRoot, 'shared-docs.json'), 'utf8'));
  materializeSharedDocs(siteRoot, entries, { check: process.argv.includes('--check') });
}
