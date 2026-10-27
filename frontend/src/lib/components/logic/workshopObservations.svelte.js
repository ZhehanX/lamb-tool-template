// src/lib/components/logic/workshopObservations.svelte.js
/**
 * Per-step observation snapshots for the workshop wizard (D2).
 *
 * Before this, the observation panel only had content once the student had
 * already chatted in step 5 — so steps 2, 3 and 4 showed nothing, and the panel
 * could not do its actual job ("look at what your assistant receives *now*").
 *
 * A snapshot is a small, JSON-serializable record:
 *
 *   { step: 2, kind: 'document', data: {...}, at: <epoch ms> }
 *
 * Kinds used by the wizard:
 *   - `document`      step 2: the document as it went into the context
 *   - `probe`         step 3: excerpts retrieved for the student's test question
 *   - `tools`         step 4: the tool definitions that will be sent
 *   - `chat_request`  step 5: the observability "request" frame
 *   - `chat_result`   step 5: the observability "result" frame
 *
 * Kept on `form.observations` for the page view; the server keeps the
 * traces themselves.
 */

/** @typedef {{step: number, kind: string, data: any, at: number}} Observation */

/**
 * Append a snapshot, returning the new list (never mutates in place, so Svelte's
 * reactivity sees the change).
 *
 * @param {Array<Observation>} observations
 * @param {number} step
 * @param {string} kind
 * @param {any} data
 * @param {number} [now]
 * @returns {Array<Observation>}
 */
export function recordObservation(observations, step, kind, data, now = Date.now()) {
	return [...(observations || []), { step, kind, data, at: now }];
}

/**
 * Remove every snapshot of the given kinds, returning the new list.
 *
 * Used when the document is removed: its chunking facts and the probe results
 * describe a knowledge base that no longer exists, so keeping them would show
 * the student evidence for something they just deleted.
 *
 * @param {Array<Observation>} observations
 * @param {Array<string>} kinds
 * @returns {Array<Observation>}
 */
export function dropObservations(observations, kinds) {
	return (observations || []).filter((o) => !(o && kinds.includes(o.kind)));
}

/**
 * The most recent snapshot of a kind for a step, or null.
 *
 * @param {Array<Observation>} observations
 * @param {number} step
 * @param {string} [kind]
 * @returns {Observation | null}
 */
export function latestObservation(observations, step, kind = '') {
	const matches = (observations || []).filter(
		(o) => o && o.step === step && (!kind || o.kind === kind)
	);
	return matches.length ? matches[matches.length - 1] : null;
}

/**
 * Every snapshot recorded for a step, in order.
 *
 * @param {Array<Observation>} observations
 * @param {number} step
 * @returns {Array<Observation>}
 */
export function observationsForStep(observations, step) {
	return (observations || []).filter((o) => o && o.step === step);
}

/**
 * Whether a step has anything to show in the panel.
 *
 * @param {Array<Observation>} observations
 * @param {number} step
 * @returns {boolean}
 */
export function hasObservations(observations, step) {
	return observationsForStep(observations, step).length > 0;
}

/**
 * Human-readable byte size (kept tiny on purpose — no dependency).
 * @param {number | null} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
	if (bytes === null || bytes === undefined) return '—';
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/**
 * Group A/B runs by question so the two modes can be compared side by side.
 *
 * The student is asked to run the *same* question twice (knowledge base on,
 * then off). Matching on the question text is what makes the comparison
 * honest: two different questions would show a difference no matter what the
 * knowledge base did.
 *
 * @param {Array<{turn?: number, ragMode?: string, question?: string,
 *                sources?: Array<any>, answer?: string, ragContext?: string}>} history
 * @returns {Array<{question: string, on: any | null, off: any | null}>}
 */
export function compareRagRuns(history) {
	/** @type {Array<{question: string, on: any | null, off: any | null}>} */
	const pairs = [];

	for (const run of history || []) {
		if (!run) continue;
		const question = (run.question || '').trim();
		if (!question) continue;

		// A linear scan keeps this a plain function over plain data — no
		// reactive Map, and the group counts here are tiny (a lesson's turns).
		let pair = pairs.find((candidate) => candidate.question === question);
		if (!pair) {
			pair = { question, on: null, off: null };
			pairs.push(pair);
		}
		// Keep the most recent run of each mode.
		if (run.ragMode === 'off') pair.off = run;
		else pair.on = run;
	}
	return pairs;
}

/**
 * Where each part of a request came from, in the order the trace gives
 * (plan G4: the parts of a message, joined, are exactly that message).
 */
export const SOURCES = ['instructions', 'document', 'retrieval', 'tools', 'history', 'user', 'scaffold'];

/** Characters of a long part shown before it is folded. */
export const EXCERPT_CHARS = 280;

/**
 * Split a frame's segments by message, for the "where each part comes from"
 * view. Long parts carry a short excerpt and how much is folded away.
 *
 * @param {any} frame - observability frame data (preview/request/result)
 * @param {number} [excerptChars]
 * @returns {Array<{index: number, role: string, tokens: number,
 *   parts: Array<{source: string, role: string, text: string, tokens: number,
 *                 excerpt: string, hiddenChars: number}>}>}
 *   role: for tool parts, "call" (written by the model) or "result" (sent
 *   back by the workshop), "history_call" / "history_result" for those of an
 *   earlier turn (T16); otherwise the source itself
 */
export function messageBreakdown(frame, excerptChars = EXCERPT_CHARS) {
	/** @type {Map<number, any>} */
	const byMessage = new Map();
	for (const s of frame?.segments || []) {
		if (!byMessage.has(s.message)) {
			byMessage.set(s.message, { index: s.message, role: s.role, tokens: 0, parts: [] });
		}
		const m = byMessage.get(s.message);
		const long = s.text.length > excerptChars;
		const round = s.meta?.tool_round;
		m.tokens += s.tokens || 0;
		m.parts.push({
			source: s.source,
			role: round ? (s.source === 'history' ? `history_${round}` : round) : s.source,
			text: s.text,
			tokens: s.tokens || 0,
			excerpt: long ? s.text.slice(0, excerptChars) : s.text,
			hiddenChars: long ? s.text.length - excerptChars : 0
		});
	}
	return [...byMessage.values()].sort((a, b) => a.index - b.index);
}

/**
 * The context budget as the panel shows it (plan T14).
 *
 * @param {any} frame
 * @returns {null | {used: number, limit: number, percent: number, tokenizer: string,
 *   contextTokens: number, document: any, historyDropped: number,
 *   toolResultsElided: number}}
 */
export function budgetSummary(frame) {
	const budget = frame?.budget;
	if (!budget || !budget.input_limit) return null;
	const used = Number(frame.tokens_estimated) || 0;
	return {
		used,
		limit: budget.input_limit,
		percent: Math.min(100, Math.round((used / budget.input_limit) * 100)),
		tokenizer: frame.tokenizer || '',
		contextTokens: budget.context_tokens,
		document: budget.document || null,
		historyDropped: budget.history_dropped || 0,
		toolResultsElided: budget.tool_results_elided || 0
	};
}

/**
 * The text of the tool instructions in a frame (step 4), or ''.
 * @param {any} frame
 * @returns {string}
 */
export function toolsText(frame) {
	return (frame?.segments || [])
		.filter((/** @type {any} */ s) => s.source === 'tools' && s.message === 0)
		.map((/** @type {any} */ s) => s.text)
		.join('');
}

/**
 * Retrieval candidates in one shape, from a step-3 probe response
 * ({results: [{rank, similarity, data, source, selected, meta}]}) or from a
 * chat frame's trace ({retrieval: {candidates: [{rank, score, text, ...}]}}).
 *
 * @param {any} source - probe response, or the frame's `retrieval`
 * @returns {Array<{rank: number, score: number, text: string, source: string,
 *                  selected: boolean, meta: any}>}
 */
export function candidatesFrom(source) {
	const items = source?.results || (Array.isArray(source?.candidates) ? source.candidates : []);
	return items.map((/** @type {any} */ c) => ({
		rank: c.rank,
		score: c.similarity ?? c.score ?? 0,
		text: c.data ?? c.text ?? '',
		source: c.source || 'document',
		selected: !!c.selected,
		meta: c.meta || {}
	}));
}
