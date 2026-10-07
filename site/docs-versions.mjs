export const docsRoots = ['docs', 'zh-cn/docs'];
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
export const latestOnlyDocs = [
  'first-analysis.mdx',
  'guides/business-question.mdx',
  'reference/project-configuration.mdx',
  'reference/telemetry.mdx',
  'reference/deployment.mdx',
];
export const docsByVersion = {
  latest: [
    ...commonDocs,
    'release-notes/0.5.2.mdx', 'release-notes/0.5.1.mdx', 'release-notes/0.5.0.mdx', 'release-notes/0.4.16.mdx', 'release-notes/0.4.15.mdx', 'release-notes/0.4.14.mdx', 'release-notes/0.4.13.mdx', 'release-notes/0.4.12.mdx', 'release-notes/0.4.11.mdx', 'release-notes/0.4.10.mdx', 'release-notes/0.4.9.mdx', 'release-notes/0.4.8.mdx', 'release-notes/0.4.7.mdx', 'release-notes/0.4.6.mdx', 'release-notes/0.4.5.mdx', 'release-notes/0.4.4.mdx', 'release-notes/0.4.3.mdx', 'release-notes/0.4.2.mdx', 'release-notes/0.4.1.mdx', 'release-notes/0.4.0.mdx', 'release-notes/0.3.3.mdx', 'release-notes/0.3.2.mdx', 'release-notes/0.3.1.mdx', 'release-notes/0.3.0.mdx', 'release-notes/0.2.8.mdx', 'release-notes/0.2.7.mdx', 'release-notes/0.2.6.mdx', 'release-notes/0.2.5.mdx', 'release-notes/0.2.4.mdx', 'release-notes/0.2.3.mdx', 'release-notes/0.2.2.mdx', 'release-notes/0.2.1.mdx', 'release-notes/0.2.0.mdx', 'release-notes/0.1.0.mdx',
  ],
  'v0.5': [
    ...commonDocs,
    'release-notes/0.5.2.mdx', 'release-notes/0.5.1.mdx', 'release-notes/0.5.0.mdx',
  ],
  'v0.4': [
    ...commonDocs,
    'release-notes/0.4.16.mdx', 'release-notes/0.4.15.mdx', 'release-notes/0.4.14.mdx', 'release-notes/0.4.13.mdx', 'release-notes/0.4.12.mdx', 'release-notes/0.4.11.mdx', 'release-notes/0.4.10.mdx', 'release-notes/0.4.9.mdx', 'release-notes/0.4.8.mdx', 'release-notes/0.4.7.mdx', 'release-notes/0.4.6.mdx', 'release-notes/0.4.5.mdx', 'release-notes/0.4.4.mdx', 'release-notes/0.4.3.mdx', 'release-notes/0.4.2.mdx', 'release-notes/0.4.1.mdx', 'release-notes/0.4.0.mdx', 'release-notes/0.3.3.mdx', 'release-notes/0.3.2.mdx', 'release-notes/0.3.1.mdx', 'release-notes/0.3.0.mdx', 'release-notes/0.2.8.mdx', 'release-notes/0.2.7.mdx', 'release-notes/0.2.6.mdx', 'release-notes/0.2.5.mdx', 'release-notes/0.2.4.mdx', 'release-notes/0.2.3.mdx', 'release-notes/0.2.2.mdx', 'release-notes/0.2.1.mdx', 'release-notes/0.2.0.mdx', 'release-notes/0.1.0.mdx',
  ],
  'v0.3': [
    ...commonDocs,
    'release-notes/0.3.3.mdx', 'release-notes/0.3.2.mdx', 'release-notes/0.3.1.mdx', 'release-notes/0.3.0.mdx', 'release-notes/0.2.8.mdx', 'release-notes/0.2.7.mdx', 'release-notes/0.2.6.mdx', 'release-notes/0.2.5.mdx', 'release-notes/0.2.4.mdx', 'release-notes/0.2.3.mdx', 'release-notes/0.2.2.mdx', 'release-notes/0.2.1.mdx', 'release-notes/0.2.0.mdx', 'release-notes/0.1.0.mdx',
  ],
  'v0.2': [
    ...commonDocs,
    'release-notes/0.2.8.mdx', 'release-notes/0.2.7.mdx', 'release-notes/0.2.6.mdx', 'release-notes/0.2.5.mdx', 'release-notes/0.2.4.mdx', 'release-notes/0.2.3.mdx', 'release-notes/0.2.2.mdx', 'release-notes/0.2.1.mdx', 'release-notes/0.2.0.mdx', 'release-notes/0.1.0.mdx',
  ],
  'v0.1': [...commonDocs, 'release-notes/0.1.0.mdx'],
};
