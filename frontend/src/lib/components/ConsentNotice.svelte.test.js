// src/lib/components/ConsentNotice.svelte.test.js
import { describe, test, expect, beforeAll, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import { waitLocale, locale } from 'svelte-i18n';
import { setupI18n } from '$lib/i18n';
import ConsentNotice from './ConsentNotice.svelte';

beforeAll(async () => {
	setupI18n();
	await waitLocale();
});

beforeEach(async () => {
	await locale.set('en');
	await new Promise((r) => setTimeout(r, 0));
});

describe('ConsentNotice', () => {
	test('says where the work goes, including LAMB logs', () => {
		render(ConsentNotice, { props: { consent: { version: 'v', model_note: '', retention_days: null } } });
		const text = screen.getByTestId('consent').textContent;
		expect(text).toMatch(/stores your work on its own server/);
		expect(text).toMatch(/LAMB may record the beginning of each message/);
		expect(text).toMatch(/your document is also kept in the workshop’s knowledge base server/);
		expect(text).toMatch(/deleted from there when you remove the document/);
		expect(screen.getByTestId('consent-model')).toHaveTextContent('not described the model yet');
		expect(screen.getByTestId('consent-retention')).toHaveTextContent('kept until your teacher deletes it');
	});

	test('shows the teacher’s note on the model and the retention period', () => {
		render(ConsentNotice, { props: { consent: { version: 'v', model_note: 'qwen3.5:9b, own server', retention_days: 180 } } });
		expect(screen.getByTestId('consent-model')).toHaveTextContent('qwen3.5:9b, own server');
		expect(screen.getByTestId('consent-retention')).toHaveTextContent('after 180 days without use');
	});

	test('accepting hands over to the page', async () => {
		let accepted = 0;
		render(ConsentNotice, { props: { consent: { version: 'v' }, onAccept: () => accepted++ } });
		await fireEvent.click(screen.getByTestId('consent-accept'));
		expect(accepted).toBe(1);
	});

	test('it is localized', async () => {
		await locale.set('ca');
		await new Promise((r) => setTimeout(r, 0));
		render(ConsentNotice, { props: { consent: { version: 'v', retention_days: 30 } } });
		expect(screen.getByTestId('consent-retention')).toHaveTextContent('després de 30 dies sense ús');
	});
});
