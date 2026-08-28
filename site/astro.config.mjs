import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

function docsItems(version, releaseNotes, isLatest) {
  const versionSlug = `docs/${version}`;
  const releaseNotesGroup = {
    label: 'Release Notes',
    translations: {
      'zh-CN': 'Release Notes',
    },
    items: releaseNotes.map((releaseNote) => ({
      slug: `${versionSlug}/release-notes/${releaseNote}`,
    })),
  };
  const conceptsGroup = {
    label: 'Concepts',
    translations: {
      'zh-CN': '核心概念',
    },
    items: [
      { slug: `${versionSlug}/concepts` },
      { slug: `${versionSlug}/concepts/semantic-layer` },
      { slug: `${versionSlug}/concepts/analysis-workflow` },
      { slug: `${versionSlug}/concepts/readiness` },
      { slug: `${versionSlug}/concepts/evidence` },
    ],
  };

  if (isLatest) {
    return [
      {
        label: 'Get started',
        translations: {
          'zh-CN': '开始使用',
        },
        items: [
          { slug: versionSlug },
          { slug: `${versionSlug}/installation` },
          { slug: `${versionSlug}/quick-start` },
          { slug: `${versionSlug}/first-analysis` },
        ],
      },
      {
        label: 'Work with an agent',
        translations: {
          'zh-CN': '与智能体协作',
        },
        items: [
          { slug: `${versionSlug}/guides/business-question` },
          { slug: `${versionSlug}/concepts/semantic-layer` },
          { slug: `${versionSlug}/concepts/analysis-workflow` },
          { slug: `${versionSlug}/concepts/readiness` },
          { slug: `${versionSlug}/concepts/evidence` },
        ],
      },
      {
        label: 'Integration and reference',
        translations: {
          'zh-CN': '集成与参考',
        },
        items: [
          { slug: `${versionSlug}/concepts` },
          { slug: `${versionSlug}/reference/project-configuration` },
          { slug: `${versionSlug}/reference/telemetry` },
          { slug: `${versionSlug}/reference/deployment` },
          { slug: `${versionSlug}/contributing` },
        ],
      },
      releaseNotesGroup,
    ];
  }

  return [
    { slug: versionSlug },
    { slug: `${versionSlug}/installation` },
    { slug: `${versionSlug}/quick-start` },
    releaseNotesGroup,
    conceptsGroup,
    { slug: `${versionSlug}/contributing` },
  ];
}

export default defineConfig({
  site: 'https://marivo.io',
  devToolbar: {
    enabled: false,
  },
  // The Python API reference is a single English Sphinx subtree emitted by
  // Sphinx into site/public/api/ and served at /api/. Starlight rewrites the
  // sidebar link to the locale-prefixed /zh-cn/api, so redirect it to the
  // real index file. We must NOT add a redirect for the bare
  // /api itself: in a static build that would emit dist/api/index.html and
  // clobber the Sphinx index. Hosts (and `astro preview`) resolve the bare
  // directory /api/ to /api/index.html on their own.
  redirects: {
    '/zh-cn/api': '/api/index.html',
  },
  // Dev-only: the Vite dev server serves files in public/ but does not resolve
  // the bare directory URL /api/ to /api/index.html (production hosts and
  // `astro preview` do). Rewrite the request in dev so /api/ works there too.
  // `apply: 'serve'` keeps this out of the production build.
  vite: {
    plugins: [
      {
        name: 'marivo-api-dir-index-dev',
        apply: 'serve',
        configureServer(server) {
          server.middlewares.use((req, _res, next) => {
            if (req.url === '/api' || req.url === '/api/') {
              req.url = '/api/index.html';
            }
            next();
          });
        },
      },
    ],
  },
  integrations: [
    starlight({
      title: 'Marivo',
      defaultLocale: 'root',
      locales: {
        root: {
          label: 'English',
          lang: 'en',
        },
        'zh-cn': {
          label: '简体中文',
          lang: 'zh-CN',
        },
      },
      customCss: ['./src/styles/custom.css'],
      logo: {
        src: './src/assets/marivo-mark.svg',
      },
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/chengxianglibra/marivo',
        },
      ],
      sidebar: [
        {
          label: 'Latest',
          translations: {
            'zh-CN': '最新版',
          },
          items: docsItems('latest', ['0.5.0', '0.4.16', '0.4.15', '0.4.14', '0.4.13', '0.4.12', '0.4.11', '0.4.10', '0.4.9', '0.4.8', '0.4.7', '0.4.6', '0.4.5', '0.4.4', '0.4.3', '0.4.2', '0.4.1', '0.4.0', '0.3.3', '0.3.2', '0.3.1', '0.3.0', '0.2.8', '0.2.7', '0.2.6', '0.2.5', '0.2.4', '0.2.3', '0.2.2', '0.2.1', '0.2.0', '0.1.0'], true),
        },
        {
          label: 'v0.5',
          items: docsItems('v0.5', ['0.5.0']),
          collapsed: true,
        },
        {
          label: 'v0.4',
          items: docsItems('v0.4', ['0.4.16', '0.4.15', '0.4.14', '0.4.13', '0.4.12', '0.4.11', '0.4.10', '0.4.9', '0.4.8', '0.4.7', '0.4.6', '0.4.5', '0.4.4', '0.4.3', '0.4.2', '0.4.1', '0.4.0', '0.3.3', '0.3.2', '0.3.1', '0.3.0', '0.2.8', '0.2.7', '0.2.6', '0.2.5', '0.2.4', '0.2.3', '0.2.2', '0.2.1', '0.2.0', '0.1.0']),
          collapsed: true,
        },
        {
          label: 'v0.3',
          items: docsItems('v0.3', ['0.3.3', '0.3.2', '0.3.1', '0.3.0', '0.2.8', '0.2.7', '0.2.6', '0.2.5', '0.2.4', '0.2.3', '0.2.2', '0.2.1', '0.2.0', '0.1.0']),
          collapsed: true,
        },
        {
          label: 'v0.2',
          items: docsItems('v0.2', ['0.2.8', '0.2.7', '0.2.6', '0.2.5', '0.2.4', '0.2.3', '0.2.2', '0.2.1', '0.2.0', '0.1.0']),
          collapsed: true,
        },
        {
          label: 'v0.1',
          items: docsItems('v0.1', ['0.1.0']),
          collapsed: true,
        },
        {
          label: 'Python API Reference',
          translations: {
            'zh-CN': 'Python API 参考',
          },
          link: '/api/',
        },
      ],
    }),
  ],
});
