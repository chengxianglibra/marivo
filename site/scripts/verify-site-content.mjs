import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = fileURLToPath(new URL('..', import.meta.url));

const locales = ['en', 'zh-cn'];
const commonDocs = [
  'index.mdx',
  'installation.mdx',
  'quick-start.mdx',
  'concepts/index.mdx',
  'concepts/semantic-layer.mdx',
  'concepts/analysis-workflow.mdx',
  'concepts/readiness.mdx',
  'concepts/evidence.mdx',
  'contributing.mdx',
];
const docsByVersion = {
  latest: [
    ...commonDocs,
    'release-notes/0.2.7.mdx',
    'release-notes/0.2.6.mdx',
    'release-notes/0.2.5.mdx',
    'release-notes/0.2.4.mdx',
    'release-notes/0.2.3.mdx',
    'release-notes/0.2.2.mdx',
    'release-notes/0.2.1.mdx',
    'release-notes/0.2.0.mdx',
    'release-notes/0.1.0.mdx',
  ],
  'v0.2': [
    ...commonDocs,
    'release-notes/0.2.7.mdx',
    'release-notes/0.2.6.mdx',
    'release-notes/0.2.5.mdx',
    'release-notes/0.2.4.mdx',
    'release-notes/0.2.3.mdx',
    'release-notes/0.2.2.mdx',
    'release-notes/0.2.1.mdx',
    'release-notes/0.2.0.mdx',
    'release-notes/0.1.0.mdx',
  ],
  'v0.1': [...commonDocs, 'release-notes/0.1.0.mdx'],
};

const requiredFiles = [
  'package.json',
  'package-lock.json',
  'astro.config.mjs',
  'src/content.config.ts',
  'src/assets/marivo-mark.svg',
  'src/styles/custom.css',
  'public/favicon.svg',
  'src/content/i18n/en.json',
  'src/content/i18n/zh-cn.json',
  'src/content/docs/en/index.mdx',
  'src/content/docs/zh-cn/index.mdx',
];

for (const locale of locales) {
  for (const [version, docs] of Object.entries(docsByVersion)) {
    for (const doc of docs) {
      requiredFiles.push(`src/content/docs/${locale}/${version}/${doc}`);
    }
  }
}

const missing = requiredFiles.filter((relativePath) => !existsSync(join(siteRoot, relativePath)));

if (missing.length > 0) {
  console.error('Missing required site files:');
  for (const file of missing) {
    console.error(`- ${file}`);
  }
  process.exit(1);
}

console.log(`Verified ${requiredFiles.length} required site files.`);
