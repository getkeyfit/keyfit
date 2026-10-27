// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
	site: 'https://keyfit.dev',
	integrations: [
		starlight({
			title: 'Keyfit',
			description:
				'Multiple accounts on one Mac, one per client: Keyfit checks that Claude Code, Codex, aws, gh and the other CLIs use the project’s own accounts before they start.',
			logo: { light: './src/assets/wordmark-on-light.svg', dark: './src/assets/wordmark-on-dark.svg', replacesTitle: true },
			favicon: '/favicon.svg',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/getkeyfit/keyfit' }],
			customCss: ['@fontsource-variable/inter', '@fontsource/ibm-plex-mono/400.css', '@fontsource/ibm-plex-mono/600.css', './src/styles/custom.css'],
			head: [
				{ tag: 'meta', attrs: { name: 'theme-color', content: '#111111' } },
				{ tag: 'meta', attrs: { property: 'og:image', content: 'https://keyfit.dev/og/keyfit-og.png' } },
				{ tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
				{ tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
				{ tag: 'meta', attrs: { name: 'twitter:image', content: 'https://keyfit.dev/og/keyfit-og.png' } },
			],
			sidebar: [{ label: 'Docs', items: [{ autogenerate: { directory: 'docs' } }] }],
			lastUpdated: false,
		}),
	],
});
