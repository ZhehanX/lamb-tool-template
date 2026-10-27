// src/lib/components/WizardSteps.svelte.test.js
/**
 * The wizard is reached straight from the LMS, so every student-visible string
 * must come from the locale bundles; these tests switch the locale and assert
 * the rendered wizard follows. They also pin the standalone tool's step
 * semantics (plan T12, T15): step 2 places the whole document in the context,
 * step 3 searches that same document, and is empty without one.
 *
 * (Named `*.svelte.test.js` so the vitest workspace runs it in jsdom.)
 */
import { describe, test, expect, beforeAll, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import { waitLocale, locale } from 'svelte-i18n';
import { setupI18n } from '$lib/i18n';
import { createWorkshopFormState } from './logic/workshopFormState.svelte.js';
import WizardSteps from './WizardSteps.svelte';

beforeAll(async () => {
	setupI18n();
	await waitLocale();
});

beforeEach(async () => {
	await useLocale('en');
});

/**
 * svelte-i18n's `locale.set` is asynchronous while the target dictionary still
 * has a queued loader; wait for the store to flip.
 * @param {string} lang
 */
async function useLocale(lang) {
	await locale.set(lang);
	await new Promise((resolve) => setTimeout(resolve, 0));
}

/** @param {number} step @param {Record<string, any>} [assistant] */
function formAt(step, assistant = {}) {
	const store = createWorkshopFormState({ assistant: { current_step: step, ...assistant } });
	return store.form;
}

const DOC = { name: 'apunts.md', bytes: 2048, chars: 1500, tokens: 400, input_limit: 24780, tokenizer: 't' };

describe('WizardSteps — localization', () => {
	test('I1: step 1 text is English by default', () => {
		render(WizardSteps, { props: { form: formAt(1) } });
		expect(screen.getByText(/the first thing your assistant reads/)).toBeInTheDocument();
	});

	test('I2: the same step renders Spanish, Catalan and Basque', async () => {
		for (const [lang, text] of [
			['es', /lo primero que lee tu asistente/],
			['ca', /el primer que llegeix el teu assistent/],
			['eu', /irakurtzen duen lehenengo gauza/]
		]) {
			await useLocale(/** @type {string} */ (lang));
			const { unmount } = render(WizardSteps, { props: { form: formAt(1) } });
			expect(screen.getByText(text)).toBeInTheDocument();
			unmount();
		}
	});

	test('I3: navigation and tools are localized', async () => {
		await useLocale('ca');
		render(WizardSteps, { props: { form: formAt(4) } });
		expect(screen.getByText(/Enrere/)).toBeInTheDocument();
		expect(screen.getByText(/Calculadora/)).toBeInTheDocument();
	});

	test('I4: step 6 shows the three reflection questions in the active language', async () => {
		await useLocale('es');
		render(WizardSteps, { props: { form: formAt(6) } });
		expect(screen.getByTestId('reflection-q1')).toBeInTheDocument();
		expect(screen.getByTestId('reflection-q2')).toBeInTheDocument();
		expect(screen.getByTestId('reflection-q3')).toBeInTheDocument();
		expect(screen.getByText(/Reflexión|Reflexionar/)).toBeInTheDocument();
	});
});

describe('WizardSteps — step 2: the whole document goes into the context', () => {
	const notes = () => new File(['# notes'], 'notes.md', { type: 'text/markdown' });

	/** @param {Record<string, any>} [extra] */
	function renderStep2(extra = {}) {
		/** @type {File[]} */
		const uploaded = [];
		const { container } = render(WizardSteps, {
			props: { form: formAt(2), onUploadDocument: (/** @type {File} */ f) => uploaded.push(f), ...extra }
		});
		const zone = /** @type {HTMLElement} */ (container.querySelector('.border-dashed'));
		const input = /** @type {HTMLInputElement} */ (container.querySelector('input[type=file]'));
		return { uploaded, zone, input };
	}

	test('choosing a file starts the upload', async () => {
		const { uploaded, input } = renderStep2();
		await fireEvent.change(input, { target: { files: [notes()] } });
		expect(uploaded.map((f) => f.name)).toEqual(['notes.md']);
	});

	test('the input only offers the accepted types', () => {
		const { input } = renderStep2({ acceptedTypes: ['.md', '.pdf'] });
		expect(input.accept).toBe('.md,.pdf');
	});

	test('the input is reset so the same file can be chosen again after a failure', async () => {
		const { input } = renderStep2();
		let value = 'C:\\fakepath\\notes.md';
		Object.defineProperty(input, 'value', {
			get: () => value,
			set: (v) => { value = v; },
			configurable: true
		});
		Object.defineProperty(input, 'files', { value: [notes()], configurable: true });
		await fireEvent.change(input);
		expect(value).toBe('');
	});

	test('dropping a file on the zone starts the upload', async () => {
		const { uploaded, zone } = renderStep2();
		await fireEvent.drop(zone, { dataTransfer: { files: [notes()] } });
		expect(uploaded.map((f) => f.name)).toEqual(['notes.md']);
	});

	test('dragover is cancelled (otherwise the browser opens the file) and highlights the zone', async () => {
		const { zone } = renderStep2();
		const notCancelled = await fireEvent.dragOver(zone, { dataTransfer: { files: [] } });
		expect(notCancelled).toBe(false);
		expect(zone.className).toContain('border-blue-500');
		await fireEvent.dragLeave(zone);
		expect(zone.className).not.toContain('border-blue-500');
	});

	test('a drop while an upload is running is ignored', async () => {
		const { uploaded, zone } = renderStep2({ uploading: true });
		await fireEvent.drop(zone, { dataTransfer: { files: [notes()] } });
		expect(uploaded).toEqual([]);
	});

	test('an attached document shows its size and can be removed through the page', async () => {
		let removed = 0;
		render(WizardSteps, { props: { form: formAt(2, { document: DOC }), onRemoveDocument: () => removed++ } });
		expect(screen.getByTestId('document-card')).toHaveTextContent('apunts.md');
		expect(screen.getByTestId('document-card')).toHaveTextContent('2.0 KB');
		expect(screen.getByTestId('document-card')).toHaveTextContent(/1,500 characters/);
		await fireEvent.click(screen.getByText('Remove'));
		expect(removed).toBe(1);
	});

	test('Remove is disabled while a removal is in flight', () => {
		render(WizardSteps, { props: { form: formAt(2, { document: DOC }), removing: true } });
		expect(screen.getByText('Remove')).toBeDisabled();
	});

	test('it says nothing about chunking or ingesting any more', () => {
		const { container } = render(WizardSteps, { props: { form: formAt(2) } });
		expect(container.textContent).not.toMatch(/ingest|chunk|split/i);
		expect(container.textContent).toMatch(/whole text/);
	});
});

const READY = { status: 'ready', chunks: 4, chunk_size: 1000, chunk_overlap: 200 };

describe('WizardSteps — steps 3 to 6', () => {
	test('step 3 without a document is empty and does not block', () => {
		render(WizardSteps, { props: { form: formAt(3), kb: { status: 'none' } } });
		expect(screen.getByTestId('kb-none')).toHaveTextContent(/not attached a document in step 2/);
		expect(screen.queryByText('Test retrieval')).toBeNull();
		expect(screen.getByText(/Next/)).not.toBeDisabled();
	});

	test.each([
		['processing', /split into excerpts/],
		['failed', /Remove it in step 2/],
		['unreachable', /cannot be reached/],
		['unavailable', /no knowledge base server/]
	])('step 3 says when the document cannot be searched yet (%s)', (status, text) => {
		render(WizardSteps, { props: { form: formAt(3, { document: DOC }), kb: { status } } });
		expect(screen.getByTestId(`kb-${status}`)).toHaveTextContent(text);
		expect(screen.queryByTestId('kb-connect')).toBeNull();
	});

	test('step 3 with the document ready says how it was split and offers a test question', async () => {
		let probes = 0;
		const form = formAt(3, { document: DOC });
		render(WizardSteps, { props: { form, kb: READY, onProbeKb: () => probes++ } });
		expect(screen.getByTestId('kb-ready')).toHaveTextContent('“apunts.md” was split into 4 excerpts of up to 1000 characters');
		const button = screen.getByText('Test retrieval');
		expect(button).toBeDisabled();
		await fireEvent.input(screen.getByPlaceholderText(/my document/), { target: { value: 'Què és?' } });
		expect(button).not.toBeDisabled();
		await fireEvent.click(button);
		expect(probes).toBe(1);
	});

	test('step 4: the calculator toggles; the knowledge-base tool needs a searchable document', async () => {
		const form = formAt(4);
		const { container } = render(WizardSteps, { props: { form, kb: { status: 'none' } } });
		const [calc, kb, sandbox] = /** @type {NodeListOf<HTMLInputElement>} */ (
			container.querySelectorAll('input[type=checkbox]'));
		await fireEvent.click(calc);
		expect(form.selectedTools).toEqual(['calculator']);
		await fireEvent.click(calc);
		expect(form.selectedTools).toEqual([]);
		expect(kb).toBeDisabled();
		expect(sandbox).toBeDisabled();
		expect(container.textContent).toMatch(/writes a call as text/);
	});

	test('step 5 shows the knowledge-base switch only once the document is connected (D3)', async () => {
		for (const props of [{ form: formAt(5), kb: { status: 'none' } },
			{ form: formAt(5, { document: DOC }), kb: READY }]) {
			const { unmount } = render(WizardSteps, { props });
			expect(screen.queryByTestId('rag-mode-off')).toBeNull();
			unmount();
		}
		const form = formAt(5, { document: DOC, retrieval_enabled: true });
		render(WizardSteps, { props: { form, kb: READY } });
		expect(screen.getByText(/Off: the whole document is sent/)).toBeInTheDocument();
		await fireEvent.click(screen.getByTestId('rag-mode-off'));
		expect(form.ragMode).toBe('off');
	});

	test('step 6 submits, says when it was submitted, and has no Next', async () => {
		let submits = 0;
		const form = formAt(6);
		form.submittedAt = '2026-10-22T10:00:00+00:00';
		render(WizardSteps, { props: { form, onSubmit: () => submits++ } });
		expect(screen.getByTestId('submitted')).toBeInTheDocument();
		await fireEvent.click(screen.getByText('Submit workshop'));
		expect(submits).toBe(1);
		expect(screen.queryByText(/Next/)).toBeNull();
	});

	test('Next follows nextDisabled', () => {
		render(WizardSteps, { props: { form: formAt(1), nextDisabled: true } });
		expect(screen.getByText(/Next/)).toBeDisabled();
	});
});

describe('WizardSteps — step 3: searching the document instead of sending it whole', () => {
	test('the student chooses whether their assistant uses it', async () => {
		const form = formAt(3, { document: DOC });
		render(WizardSteps, { props: { form, kb: READY } });
		expect(form.retrievalEnabled).toBe(false);
		expect(screen.getByText(/no longer receives the whole document/)).toBeInTheDocument();
		await fireEvent.click(screen.getByTestId('kb-connect'));
		expect(form.retrievalEnabled).toBe(true);
	});

	test('the strings are localized', async () => {
		await useLocale('ca');
		render(WizardSteps, { props: { form: formAt(3), kb: { status: 'none' } } });
		expect(screen.getByTestId('kb-none')).toHaveTextContent(/No has adjuntat cap document al pas 2/);
	});
});
