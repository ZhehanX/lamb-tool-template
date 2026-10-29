// src/lib/components/logic/workshopFormState.svelte.js
/**
 * Reactive state for the six-step workshop wizard.
 *
 * Steps (the thesis numbering):
 *   1. Instructions  2. Document  3. Knowledge base  4. Tools
 *   5. Test chat     6. Reflection
 *
 * The server owns the student's assistant (instructions, document, tools,
 * step) and the conversation; this store mirrors what the wizard edits and is
 * seeded from `GET /workshop/api/state`. Unlike the old branch nothing here
 * is a LAMB assistant: the assistant is the tool's own record (plan T2).
 *
 * Usage (Svelte 5 runes, `.svelte.js` module):
 *   const store = createWorkshopFormState(state);
 *   store.form.instructions;      // reactive read / write
 *   store.isStepComplete(1);      // enables the Next button
 */

export const STEP_COUNT = 6;

/**
 * Reflection questions, in order. Kept here (not only in the component) so the
 * stored answers and the UI agree on the keys.
 */
export const REFLECTION_QUESTIONS = [
	{ key: 'q1', heading: 'What did you ask your assistant to do, and why?' },
	{ key: 'q2', heading: 'What did the assistant actually receive?' },
	{ key: 'q3', heading: 'What would you change in your design after testing it?' }
];

/**
 * Compose the three answers into one text (for the teacher view and the
 * formative feedback). Blank answers are skipped.
 *
 * @param {Record<string, string>} [answers]
 * @returns {string}
 */
export function composeReflection(answers = {}) {
	const parts = [];
	for (const question of REFLECTION_QUESTIONS) {
		const answer = (answers?.[question.key] || '').trim();
		if (!answer) continue;
		parts.push(`### ${question.heading}\n${answer}`);
	}
	return parts.join('\n\n');
}

/**
 * @typedef {{name: string, bytes: number, chars: number, tokens: number,
 *            input_limit: number, tokenizer: string}} DocumentInfo
 */

/**
 * A form store seeded from the server state.
 *
 * @param {any} [state] - the `GET /state` response
 */
export function createWorkshopFormState(state = {}) {
	const assistant = state.assistant || {};
	const answers = state.reflection?.answers || {};
	let form = $state({
		currentStep: clampStep(assistant.current_step ?? 1),

		// Step 1
		instructions: assistant.instructions ?? '',

		// Step 2: the server's view of the attached document, or null
		/** @type {DocumentInfo | null} */
		document: assistant.document ?? null,

		// Step 3: the step-2 document searched instead of sent whole (T15), or
		// not, and a test question
		retrievalEnabled: !!assistant.retrieval_enabled,
		kbQuery: '',

		// Step 4
		/** @type {string[]} */
		selectedTools: assistant.tools ?? [],

		// Step 5: A/B experiment, excerpts only (on) vs the whole document (off) (D3)
		/** @type {'on' | 'off'} */
		ragMode: 'on',
		// Step 5: how many tokens the model may read, an experiment (T18); null for all
		/** @type {number | null} */
		inputCap: null,
		/** Successful chat turns so far (the step is done after one). */
		turnsOk: (state.turns || []).filter((/** @type {any} */ t) => t.status === 'ok').length,

		// Step 6
		/** @type {{q1: string, q2: string, q3: string}} */
		reflectionAnswers: {
			q1: answers.q1 ?? '',
			q2: answers.q2 ?? '',
			q3: answers.q3 ?? ''
		},
		/** @type {string | null} */
		submittedAt: state.reflection?.submitted_at ?? null,

		/** Per-step panel snapshots (workshopObservations.svelte.js). */
		/** @type {Array<{step: number, kind: string, data: any, at?: number}>} */
		observations: []
	});

	return {
		form,
		/** @param {number} step */
		goToStep(step) {
			if (step >= 1 && step <= STEP_COUNT) form.currentStep = step;
		},
		nextStep() {
			if (form.currentStep < STEP_COUNT) form.currentStep++;
		},
		prevStep() {
			if (form.currentStep > 1) form.currentStep--;
		},
		/**
		 * Whether a step is done (enables the Next button).
		 * @param {number} step
		 * @returns {boolean}
		 */
		isStepComplete(step) {
			switch (step) {
				case 1: return !!form.instructions.trim();
				case 2: return !!form.document;
				case 3: return true; // searching the document is a choice
				case 4: return true; // "no tools" is a valid design
				case 5: return form.turnsOk > 0;
				default: return false;
			}
		},
		/** The fields PUT /assistant stores. */
		assistantFields() {
			return {
				instructions: form.instructions,
				tools: [...form.selectedTools],
				retrieval_enabled: form.retrievalEnabled,
				current_step: form.currentStep
			};
		}
	};
}

/** @param {number} step */
function clampStep(step) {
	const n = Number(step) || 1;
	return Math.min(Math.max(n, 1), STEP_COUNT);
}
