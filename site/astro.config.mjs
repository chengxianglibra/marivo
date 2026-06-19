import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

function docsItems(version) {
  return [
    { slug: version },
    { slug: `${version}/installation` },
    { slug: `${version}/quick-start` },
    {
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
    },
    { slug: `${version}/contributing` },
  ];
}

export default defineConfig({
  // The Python API reference is a single English Sphinx subtree at /api/.
  // Starlight localizes the sidebar link to /en/api/ and /zh-cn/api/, so
  // redirect those locale-prefixed paths back to the canonical /api/ tree.
  redirects: {
    '/en/api': '/api/',
    '/zh-cn/api': '/api/',
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
          href: 'https://github.com/lumendata/marivo',
        },
      ],
      sidebar: [
        {
          label: 'Latest',
          translations: {
            'zh-CN': '最新版',
          },
          items: docsItems('latest'),
        },
        {
          label: 'v0.1',
          items: docsItems('v0.1'),
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
