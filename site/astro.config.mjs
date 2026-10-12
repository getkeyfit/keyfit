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
				'claude, codex, aws and gh use the accounts of the project you are in. Keyfit checks them with the services before the tool starts.',
			logo: { src: './src/assets/logo.svg' },
			favicon: '/favicon.svg',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/FlamurMaliqi/keyfit' }],
			customCss: ['@fontsource-variable/geist', '@fontsource-variable/geist-mono', './src/styles/custom.css'],
			head: [{ tag: 'meta', attrs: { name: 'theme-color', content: '#0b0b0d' } }],
			sidebar: [{ label: 'Docs', items: [{ autogenerate: { directory: 'docs' } }] }],
			lastUpdated: false,
		}),
	],
});
