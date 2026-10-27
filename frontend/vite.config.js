import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	optimizeDeps: {
		include: ['svelte', 'svelte-i18n'],
		exclude: ['@sveltejs/kit']
	},
	ssr: {
		noExternal: ['svelte-i18n']
	},
	// `npm run dev` serves the SPA on :5173/workshop/ and forwards the API to
	// the tool (uvicorn on :8090). Open it with ?session=<token> from a launch.
	server: {
		proxy: {
			'/workshop/api': {
				target: process.env.TOOL_URL || 'http://localhost:8090',
				changeOrigin: true
			}
		}
	},
	test: {
		workspace: [
			{
				extends: './vite.config.js',
				plugins: [svelteTesting()],
				test: {
					name: 'client',
					environment: 'jsdom',
					clearMocks: true,
					include: ['src/**/*.svelte.{test,spec}.js'],
					setupFiles: ['./vitest-setup-client.js']
				}
			},
			{
				extends: './vite.config.js',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.js'],
					exclude: ['src/**/*.svelte.{test,spec}.js']
				}
			}
		]
	}
});
