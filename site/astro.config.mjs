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
      ],
    }),
  ],
});
