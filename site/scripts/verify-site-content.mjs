import { docsRoots, docsByVersion, latestOnlyDocs } from '../docs-versions.mjs';
import { materializeSharedDocs } from './materialize-shared-docs.mjs';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = fileURLToPath(new URL('..', import.meta.url));

materializeSharedDocs(siteRoot, JSON.parse(readFileSync(join(siteRoot, 'shared-docs.json'), 'utf8')));

const requiredFiles = [
  'package.json',
  'package-lock.json',
  'astro.config.mjs',
  'src/content.config.ts',
  'src/assets/marivo-mark.svg',
  'src/assets/blog/agent-analysis-flow.png',
  'src/assets/blog/analysis-dsl-agent-loop-en.png',
  'src/assets/blog/analysis-dsl-agent-loop.png',
  'src/assets/blog/analysis-dsl-business-abstraction-en.png',
  'src/assets/blog/analysis-dsl-business-abstraction.png',
  'src/assets/blog/evidence-engine-results-are-not-conclusions-en.png',
  'src/assets/blog/evidence-engine-result-to-evidence.png',
  'src/assets/blog/semantic-layer-contract-en.png',
  'src/assets/blog/semantic-layer-contract.png',
  'src/styles/custom.css',
  'src/styles/blog.css',
  'src/components/HomePage.astro',
  'src/components/BlogIndexPage.astro',
  'src/layouts/BlogArticleLayout.astro',
  'src/pages/index.astro',
  'src/pages/zh-cn/index.astro',
  'src/pages/blog/index.astro',
  'src/pages/blog/analysis-dsl-constrains-actions-not-agent-reasoning.mdx',
  'src/pages/blog/evidence-engine-results-are-not-conclusions.mdx',
  'src/pages/blog/semantic-layer-as-an-agent-contract.mdx',
  'src/pages/blog/why-data-analysis-agents-need-a-harness.mdx',
  'src/pages/zh-cn/blog/index.astro',
  'src/pages/zh-cn/blog/why-data-analysis-agents-need-a-harness.mdx',
  'src/pages/zh-cn/blog/semantic-layer-as-an-agent-contract.mdx',
  'src/pages/zh-cn/blog/analysis-dsl-constrains-actions-not-agent-reasoning.mdx',
  'src/pages/zh-cn/blog/evidence-engine-results-are-not-conclusions.mdx',
  'src/pages/install.sh.ts',
  'public/favicon.svg',
  'public/install-marivo-cn.sh',
  'public/robots.txt',
  'src/content/i18n/en.json',
  'src/content/i18n/zh-cn.json',
  'src/content/docs/docs/index.mdx',
  'src/content/docs/zh-cn/docs/index.mdx',
];

for (const docsRoot of docsRoots) {
  for (const [version, docs] of Object.entries(docsByVersion)) {
    const versionDocs = version === 'latest' ? [...docs, ...latestOnlyDocs] : docs;
    for (const doc of versionDocs) {
      requiredFiles.push(`src/content/docs/${docsRoot}/${version}/${doc}`);
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

const sidebarConfig = readFileSync(join(siteRoot, 'astro.config.mjs'), 'utf8');
const configuredVersions = [...sidebarConfig.matchAll(/items: docsItems\('([^']+)'/g)].map((entry) => entry[1]);
if (configuredVersions.join(',') !== Object.keys(docsByVersion).join(',')) {
  throw new Error('Documentation sidebar versions are out of sync');
}

const seoRequirements = new Map([
  ['astro.config.mjs', ["site: 'https://marivo.io'"]],
  ['src/components/HomePage.astro', ['rel="canonical"', 'hreflang="en"', 'hreflang="zh-CN"', 'hreflang="x-default"']],
  ['src/components/BlogIndexPage.astro', ['rel="canonical"', 'hreflang="en"', 'hreflang="zh-CN"', 'hreflang="x-default"']],
  ['src/layouts/BlogArticleLayout.astro', ['rel="canonical"', 'hreflang="en"', 'hreflang="zh-CN"', 'hreflang="x-default"']],
  ['public/robots.txt', ['User-agent: *', 'Allow: /', 'Sitemap: https://marivo.io/sitemap-index.xml']],
]);
const missingSeoSignals = [];

for (const [relativePath, signals] of seoRequirements) {
  const content = readFileSync(join(siteRoot, relativePath), 'utf8');
  for (const signal of signals) {
    if (!content.includes(signal)) {
      missingSeoSignals.push(`${relativePath}: ${signal}`);
    }
  }
}

if (missingSeoSignals.length > 0) {
  console.error('Required site indexing signals are missing:');
  for (const signal of missingSeoSignals) {
    console.error(`- ${signal}`);
  }
  process.exit(1);
}

console.log(`Verified ${requiredFiles.length} required site files.`);
