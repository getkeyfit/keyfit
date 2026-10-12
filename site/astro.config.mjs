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
			customCss: ['@fontsource-variable/inter', '@fontsource/ibm-plex-mono/400.css', '@fontsource/ibm-plex-mono/600.css', './src/styles/custom.css'],
			head: [{ tag: 'meta', attrs: { name: 'theme-color', content: '#111111' } }],
			sidebar: [{ label: 'Docs', items: [{ autogenerate: { directory: 'docs' } }] }],
			lastUpdated: false,
		}),
	],
});
