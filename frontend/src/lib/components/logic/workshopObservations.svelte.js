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
 * The panel's rows, in the order the model reads them (T17): the three
 * blocks of the system message, every earlier message as one row, then the
 * new message (the excerpts added before it, the student's own words) and
 * this turn's tool rounds.
 */
export const ROW_KEYS = ['instructions', 'document', 'tools', 'history', 'retrieval', 'user', 'toolround'];

/** Rows that are blocks of the system message; the rest come after it. */
export const SYSTEM_ROWS = ['instructions', 'document', 'tools'];

/**
 * @typedef {{source: string, role: string, message: number, messageRole: string,
 *   text: string, tokens: number, excerpt: string, hiddenChars: number}} Part
 *   role: for tool parts, "call" (written by the model) or "result" (sent
 *   back by the workshop), "history_call" / "history_result" for those of an
 *   earlier turn (T16); otherwise the source itself. messageRole: the role
 *   of the message the part is in.
 */

/**
 * @typedef {{key: string, group: 'system' | 'conversation', tokens: number,
 *   parts: Array<Part>, name?: string, text?: string, count?: number,
 *   turns?: Array<HistoryTurn>}} Row
 */

/**
 * @typedef {{question: string, tokens: number,
 *   messages: Array<{index: number, role: string, parts: Array<Part>}>}} HistoryTurn
 */

/**
 * @param {any} s - a trace segment
 * @param {number} excerptChars
 * @returns {Part}
 */
function partOf(s, excerptChars) {
	const long = s.text.length > excerptChars;
	const round = s.meta?.tool_round;
	return {
		source: s.source,
		role: round ? (s.source === 'history' ? `history_${round}` : round) : s.source,
		message: s.message,
		messageRole: s.role,
		text: s.text,
		tokens: s.tokens || 0,
		excerpt: long ? s.text.slice(0, excerptChars) : s.text,
		hiddenChars: long ? s.text.length - excerptChars : 0
	};
}

/**
 * Group a frame's segments into the panel's rows (T17).
 *
 * The panel used to list every message, so it grew with each turn of the
 * conversation. Now all earlier messages are one row, and a request has at
 * most seven rows however long the conversation is. Every segment is still
 * in exactly one row, in order, so nothing sent is hidden (T3).
 *
 * Text the workshop adds (`scaffold`) goes with the part it introduces: a
 * heading or a separator with what follows it in the same message, a
 * truncation note with the document it ends.
 *
 * @param {any} frame - observability frame data (preview/request/result)
 * @param {number} [excerptChars]
 * @returns {Array<Row>}
 */
export function contextRows(frame, excerptChars = EXCERPT_CHARS) {
	/** @type {Array<any>} */
	const segments = frame?.segments || [];
	if (!segments.length) return [];
	const system = segments.some((s) => s.message === 0 && s.role === 'system') ? 0 : -1;
	// The student's message; a preview of an empty box has none, and then
	// everything after the system message is history.
	const asked = segments.find((s) => s.source === 'user');
	const current = asked ? asked.message : Infinity;

	/** @param {any} s @returns {string | null} */
	function keyOf(s) {
		if (s.message === system) {
			return SYSTEM_ROWS.includes(s.source) ? s.source : null;
		}
		// An earlier result shortened to a note is a scaffold, but still history.
		if (s.message < current) return 'history';
		if (s.message > current) return 'toolround';
		return s.source === 'retrieval' || s.source === 'user' ? s.source : null;
	}

	const keys = segments.map(keyOf);
	/** @param {number} i @param {number} step */
	function nearest(i, step) {
		for (let j = i + step; j >= 0 && j < segments.length; j += step) {
			if (segments[j].message !== segments[i].message) break;
			if (keys[j]) return keys[j];
		}
		return null;
	}
	segments.forEach((s, i) => {
		if (keys[i]) return;
		const forward = s.meta?.truncation ? null : nearest(i, 1);
		keys[i] = forward || nearest(i, -1) || (s.message === system ? 'instructions' : 'user');
	});

	/** @type {Map<string, Row>} */
	const rows = new Map();
	segments.forEach((s, i) => {
		const key = /** @type {string} */ (keys[i]);
		if (!rows.has(key)) {
			rows.set(key, {
				key,
				group: SYSTEM_ROWS.includes(key) ? 'system' : 'conversation',
				tokens: 0,
				parts: []
			});
		}
		const row = /** @type {Row} */ (rows.get(key));
		row.tokens += s.tokens || 0;
		row.parts.push(partOf(s, excerptChars));
	});

	for (const row of rows.values()) {
		const own = row.parts.filter((p) => p.source !== 'scaffold');
		if (row.key === 'document') {
			row.name = segments.find((s) => s.source === 'document')?.meta?.name || '';
		} else if (row.key === 'retrieval') {
			row.count = own.length;
		} else if (row.key === 'user') {
			row.text = own.map((p) => p.text).join('');
		} else if (row.key === 'toolround') {
			row.count = row.parts.filter((p) => p.role === 'call').length;
		} else if (row.key === 'history') {
			row.turns = historyTurns(row.parts);
			row.count = new Set(row.parts.map((p) => p.message)).size;
		}
	}
	return ROW_KEYS.filter((k) => rows.has(k)).map((k) => /** @type {Row} */ (rows.get(k)));
}

/**
 * The history row's parts as earlier turns: a turn starts with each message
 * the student wrote (a tool result goes back as a user message too, but it
 * belongs to the turn that called the tool, T16).
 *
 * @param {Array<Part>} parts
 * @returns {Array<HistoryTurn>}
 */
export function historyTurns(parts) {
	/** @type {Array<HistoryTurn>} */
	const turns = [];
	/** @type {{index: number, role: string, parts: Array<Part>} | null} */
	let message = null;
	for (const p of parts) {
		if (!message || message.index !== p.message) {
			message = { index: p.message, role: p.messageRole, parts: [] };
			// History messages are one part each; a shortened result is a scaffold.
			const asked = p.messageRole === 'user' && p.source === 'history' && p.role === 'history';
			if (asked || !turns.length) turns.push({ question: asked ? p.text : '', tokens: 0, messages: [] });
			turns[turns.length - 1].messages.push(message);
		}
		message.parts.push(p);
		turns[turns.length - 1].tokens += p.tokens;
	}
	return turns;
}

/**
 * The context budget as the panel shows it (plan T14). `cap` is the limit
 * the student chose in step 5 (T18), or null: `limit` already is that cap.
 *
 * @param {any} frame
 * @returns {null | {used: number, limit: number, percent: number, tokenizer: string,
 *   contextTokens: number, cap: number | null, document: any, historyDropped: number,
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
		cap: budget.input_cap || null,
		document: budget.document || null,
		historyDropped: budget.history_dropped || 0,
		toolResultsElided: budget.tool_results_elided || 0
	};
}

/**
 * A stored chat trace (`GET /traces/{id}`) as the "result" frame the panel
 * reads, so an earlier message's receipt opens what that request sent (T17).
 * The trace is what `frames.observability` was built from: the same
 * segments, budget and payload as sent.
 *
 * @param {any} trace
 * @param {string} [ragMode] - the turn's knowledge-base mode
 * @returns {any | null}
 */
export function frameFromTrace(trace, ragMode = 'off') {
	if (!trace) return null;
	return {
		phase: 'result',
		rag_mode: ragMode,
		request_body: trace.payload || null,
		model: trace.model || null,
		segments: trace.segments || [],
		tokens_estimated: trace.tokens_estimated,
		tokenizer: trace.tokenizer,
		budget: trace.budget,
		retrieval: trace.retrieval || null
	};
}

/**
 * How many tool calls of its own a request carried: the calls of this turn's
 * tool rounds, for the message's receipt (T17). Calls resent with the
 * earlier exchanges are history, not this message's.
 *
 * @param {any} frame
 * @returns {number}
 */
export function toolCallsSent(frame) {
	return (frame?.segments || []).filter(
		(/** @type {any} */ s) => s.source === 'tools' && s.meta?.tool_round === 'call'
	).length;
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
