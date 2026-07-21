import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

function docsItems(version, releaseNotes, isLatest) {
  const releaseNotesGroup = {
    label: 'Release Notes',
    translations: {
      'zh-CN': 'Release Notes',
    },
    items: releaseNotes.map((releaseNote) => ({
      slug: `${version}/release-notes/${releaseNote}`,
    })),
  };
  const conceptsGroup = {
    label: 'Concepts',
    translations: {
      'zh-CN': '核心概念',
    },
    items: [
      { slug: `${version}/concepts` },
      { slug: `${version}/concepts/semantic-layer` },
      { slug: `${version}/concepts/analysis-workflow` },
      { slug: `${version}/concepts/readiness` },
      { slug: `${version}/concepts/evidence` },
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
          { slug: version },
          { slug: `${version}/installation` },
          { slug: `${version}/quick-start` },
          { slug: `${version}/first-analysis` },
        ],
      },
      {
        label: 'Work with an agent',
        translations: {
          'zh-CN': '与智能体协作',
        },
        items: [
          { slug: `${version}/guides/business-question` },
          { slug: `${version}/concepts/semantic-layer` },
          { slug: `${version}/concepts/analysis-workflow` },
          { slug: `${version}/concepts/readiness` },
          { slug: `${version}/concepts/evidence` },
        ],
      },
      {
        label: 'Integration and reference',
        translations: {
          'zh-CN': '集成与参考',
        },
        items: [
          { slug: `${version}/concepts` },
          { slug: `${version}/reference/project-configuration` },
          { slug: `${version}/reference/telemetry` },
          { slug: `${version}/reference/deployment` },
          { slug: `${version}/contributing` },
        ],
      },
      releaseNotesGroup,
    ];
  }

  return [
    { slug: version },
    { slug: `${version}/installation` },
    { slug: `${version}/quick-start` },
    releaseNotesGroup,
    conceptsGroup,
    { slug: `${version}/contributing` },
  ];
}

export default defineConfig({
  // The Python API reference is a single English Sphinx subtree emitted by
  // Sphinx into site/public/api/ and served at /api/. Starlight rewrites the
  // sidebar link to the locale-prefixed /en/api and /zh-cn/api, so redirect
  // those to the real index file. We must NOT add a redirect for the bare
  // /api itself: in a static build that would emit dist/api/index.html and
  // clobber the Sphinx index. Hosts (and `astro preview`) resolve the bare
  // directory /api/ to /api/index.html on their own.
  redirects: {
    // The default locale is served under /en/, so Astro emits no page at the
    // bare site root and hosts return 404 for /. Redirect / to the latest
    // English splash page so the site has a working entry point.
    '/': '/en/latest/',
    '/en/api': '/api/index.html',
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
      defaultLocale: 'en',
      locales: {
        en: {
          label: 'English',
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
          items: docsItems('latest', ['0.3.3', '0.3.2', '0.3.1', '0.3.0', '0.2.8', '0.2.7', '0.2.6', '0.2.5', '0.2.4', '0.2.3', '0.2.2', '0.2.1', '0.2.0', '0.1.0'], true),
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
