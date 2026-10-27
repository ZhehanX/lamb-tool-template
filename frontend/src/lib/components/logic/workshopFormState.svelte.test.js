// src/lib/components/logic/workshopFormState.svelte.test.js
import { describe, test, expect } from 'vitest';
import {
	composeReflection,
	createWorkshopFormState,
	STEP_COUNT
} from './workshopFormState.svelte.js';

const DOC = { name: 'apunts.md', bytes: 120, chars: 100, tokens: 30, input_limit: 24780, tokenizer: 't' };

/** A `GET /state` response. */
function state(overrides = {}) {
	return {
		assistant: { instructions: '', document: null, retrieval_enabled: false, tools: [], current_step: 1 },
		turns: [],
		reflection: null,
		...overrides
	};
}

describe('createWorkshopFormState — seeding from the server', () => {
	test('a new student starts on step 1 with nothing filled in', () => {
		const { form } = createWorkshopFormState(state());
		expect(form.currentStep).toBe(1);
		expect(form.instructions).toBe('');
		expect(form.document).toBeNull();
		expect(form.selectedTools).toEqual([]);
		expect(form.turnsOk).toBe(0);
		expect(form.reflectionAnswers).toEqual({ q1: '', q2: '', q3: '' });
	});

	test('a returning student gets their design, step, turns and reflection back', () => {
		const { form } = createWorkshopFormState(state({
			assistant: { instructions: 'Sigues breu.', document: DOC, tools: ['calculator'], current_step: 4 },
			turns: [{ status: 'ok' }, { status: 'error' }, { status: 'ok' }],
			reflection: { answers: { q1: 'Un tutor', q3: 'Més exemples' }, submitted_at: '2026-10-22T10:00:00+00:00' }
		}));
		expect(form.currentStep).toBe(4);
		expect(form.instructions).toBe('Sigues breu.');
		expect(form.document).toEqual(DOC);
		expect(form.selectedTools).toEqual(['calculator']);
		expect(form.turnsOk).toBe(2);
		expect(form.reflectionAnswers).toEqual({ q1: 'Un tutor', q2: '', q3: 'Més exemples' });
		expect(form.submittedAt).toBe('2026-10-22T10:00:00+00:00');
	});

	test('an out-of-range stored step is clamped', () => {
		expect(createWorkshopFormState(state({ assistant: { current_step: 9 } })).form.currentStep).toBe(STEP_COUNT);
		expect(createWorkshopFormState(state({ assistant: { current_step: 0 } })).form.currentStep).toBe(1);
		expect(createWorkshopFormState().form.currentStep).toBe(1);
	});
});

describe('navigation', () => {
	test('six steps, with bounds', () => {
		const store = createWorkshopFormState(state());
		expect(STEP_COUNT).toBe(6);
		store.prevStep();
		expect(store.form.currentStep).toBe(1);
		for (let i = 0; i < 10; i++) store.nextStep();
		expect(store.form.currentStep).toBe(6);
		store.goToStep(3);
		expect(store.form.currentStep).toBe(3);
		store.goToStep(7);
		expect(store.form.currentStep).toBe(3);
	});
});

describe('isStepComplete', () => {
	test('1 needs instructions, 2 a document, 5 one answered message; 3 and 4 are optional', () => {
		const store = createWorkshopFormState(state());
		expect(store.isStepComplete(1)).toBe(false);
		store.form.instructions = '   ';
		expect(store.isStepComplete(1)).toBe(false);
		store.form.instructions = 'Respon en català.';
		expect(store.isStepComplete(1)).toBe(true);

		expect(store.isStepComplete(2)).toBe(false);
		store.form.document = DOC;
		expect(store.isStepComplete(2)).toBe(true);

		expect(store.isStepComplete(3)).toBe(true);
		expect(store.isStepComplete(4)).toBe(true);

		expect(store.isStepComplete(5)).toBe(false);
		store.form.turnsOk = 1;
		expect(store.isStepComplete(5)).toBe(true);

		expect(store.isStepComplete(6)).toBe(false);
	});
});

describe('assistantFields — what PUT /assistant stores', () => {
	test('instructions, a copy of the tools, and the current step', () => {
		const store = createWorkshopFormState(state());
		store.form.instructions = 'Sigues breu.';
		store.form.selectedTools = ['calculator'];
		store.form.currentStep = 4;
		const fields = store.assistantFields();
		expect(fields).toEqual({
			instructions: 'Sigues breu.', tools: ['calculator'], retrieval_enabled: false, current_step: 4
		});
		fields.tools.push('x');
		expect(store.form.selectedTools).toEqual(['calculator']);
	});
});

describe('composeReflection', () => {
	test('joins the answered questions under their headings and skips blanks', () => {
		expect(composeReflection({ q1: 'Un tutor', q2: '  ', q3: 'Més exemples' })).toBe(
			'### What did you ask your assistant to do, and why?\nUn tutor\n\n' +
				'### What would you change in your design after testing it?\nMés exemples'
		);
		expect(composeReflection({})).toBe('');
	});
});
