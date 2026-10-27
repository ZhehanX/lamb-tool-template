// The SPA runs only in the browser (adapter-static fallback, no SSR).
import { waitLocale } from 'svelte-i18n';
import { setupI18n } from '$lib/i18n';
import { initWorkshopLocale } from '$lib/workshop/workshopLocale.js';

export const ssr = false;
export const prerender = false;

/** @type {import('./$types').LayoutLoad} */
export async function load({ url }) {
	setupI18n();
	// Locale chain (C6): the LMS locale (?lang=), then the saved choice, then
	// the browser, then English. Resolved before the first render, which
	// cannot format a message until its dictionary has loaded.
	initWorkshopLocale(url.searchParams.get('lang'));
	try {
		await waitLocale();
	} catch (err) {
		// A dictionary that fails to load must not blank the page: the
		// components carry English defaults.
		console.error('waitLocale failed, continuing with defaults:', err);
	}
	return {};
}
