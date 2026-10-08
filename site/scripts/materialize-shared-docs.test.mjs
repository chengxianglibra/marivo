import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { materializeSharedDocs } from './materialize-shared-docs.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const entries = JSON.parse(readFileSync(join(root, 'shared-docs.json'), 'utf8'));
const baseline = JSON.parse(readFileSync(new URL('./docs-page-baseline.json', import.meta.url), 'utf8'));
const hash = (text) => createHash('sha256').update(text).digest('hex');

test('cold and warm generation preserve every original page byte', () => {
  const temporary = mkdtempSync(join(tmpdir(), 'marivo-docs-'));
  try {
    for (const entry of entries) {
      const source = join(temporary, 'src/content/docs', entry.source);
      mkdirSync(join(source, '..'), { recursive: true });
      mkdirSync(join(temporary, 'src/content/docs', entry.target, '..'), { recursive: true });
      writeFileSync(source, readFileSync(join(root, 'src/content/docs', entry.source)));
    }
    materializeSharedDocs(temporary, entries);
    materializeSharedDocs(temporary, entries);
    materializeSharedDocs(temporary, entries, { check: true });
    for (const entry of entries) {
      assert.equal(hash(readFileSync(join(temporary, 'src/content/docs', entry.target))), baseline.sharedPageHashes[entry.target]);
    }
    const first = entries[0];
    const target = join(temporary, 'src/content/docs', first.target);
    writeFileSync(target, 'local edit');
    assert.throws(() => materializeSharedDocs(temporary, entries), /overwrite edited/);
    assert.equal(readFileSync(target, 'utf8'), 'local edit');
    rmSync(target);
    assert.throws(() => materializeSharedDocs(temporary, entries, { check: true }), /Missing/);
    const source = join(temporary, 'src/content/docs', first.source);
    writeFileSync(source, readFileSync(source, 'utf8') + '\nchanged');
    assert.throws(() => materializeSharedDocs(temporary, entries), /body changed/);
    assert.equal(existsSync(target), false);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test('all original pages remain present and shared copies preserve their bytes', () => {
  materializeSharedDocs(root, entries);
  for (const path of Object.keys(baseline.routes)) {
    assert.equal(existsSync(join(root, 'src/content/docs', path)), true, path);
  }
  for (const [path, expected] of Object.entries(baseline.sharedPageHashes)) {
    assert.equal(hash(readFileSync(join(root, 'src/content/docs', path))), expected, path);
  }
});

test('manifest rejects duplicate targets, generated sources and escaping paths', () => {
  assert.throws(() => materializeSharedDocs(root, [entries[0], entries[0]]), /Duplicate/);
  assert.throws(() => materializeSharedDocs(root, [{ ...entries[0], source: entries[0].target }]), /sources must be authored/);
  assert.throws(() => materializeSharedDocs(root, [{ ...entries[0], source: '../outside.mdx' }]), /Invalid/);
});
