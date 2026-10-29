// src/lib/components/logic/workshopTrace.test.js
// The panel's reading of the server trace (plan G4): its rows (T17), the
// context budget, and a stored trace read back as a frame.
import { describe, test, expect } from 'vitest';
import { budgetSummary, contextRows, frameFromTrace, toolCallsSent } from './workshopObservations.svelte.js';

/** A frame shaped like app/workshop/frames.py produces it. */
const frame = {
	phase: 'preview',
	tokens_estimated: 2100,
	tokenizer: 'o200k_base x 1.15, 1 token per digit',
	budget: {
		context_tokens: 32768,
		input_limit: 24780,
		history_dropped: 2,
		document: { name: 'apunts.md', total_chars: 9000, kept_chars: 4000, truncated: true }
	},
	segments: [
		{ message: 0, role: 'system', source: 'instructions', text: 'Respon en català.', tokens: 6 },
		{ message: 0, role: 'system', source: 'scaffold', text: '\n\n', tokens: 1 },
		{ message: 0, role: 'system', source: 'scaffold', text: '## Document: apunts.md\n\n', tokens: 8 },
		{ message: 0, role: 'system', source: 'document', text: 'x'.repeat(1000), tokens: 300 },
		{ message: 0, role: 'system', source: 'scaffold', text: '\n\n', tokens: 1 },
		{ message: 0, role: 'system', source: 'tools', text: 'You can use the tools listed below.', tokens: 9 },
		{ message: 1, role: 'user', source: 'history', text: 'Hola', tokens: 2 },
		{ message: 2, role: 'assistant', source: 'history', text: 'Bon dia!', tokens: 3 },
		{ message: 3, role: 'user', source: 'user', text: 'Què és?', tokens: 4 }
	]
};

describe('contextRows (T17)', () => {
	test('one row per kind of content, in the order the model reads it', () => {
		const rows = contextRows(frame);
		expect(rows.map((r) => [r.key, r.group])).toEqual([
			['instructions', 'system'], ['document', 'system'], ['tools', 'system'],
			['history', 'conversation'], ['user', 'conversation']
		]);
		// Headings and separators go with the block they introduce.
		expect(rows[1].parts.map((p) => p.source)).toEqual(['scaffold', 'scaffold', 'document']);
		expect(rows[1].tokens).toBe(1 + 8 + 300);
		expect(rows[2].parts.map((p) => p.source)).toEqual(['scaffold', 'tools']);
		expect(rows[1].name).toBe('');
		expect(rows[4].text).toBe('Què és?');
	});

	test('every segment is in exactly one row, so the rows join back into what was sent', () => {
		const joined = contextRows(frame).flatMap((r) => r.parts).map((p) => p.text).join('');
		expect(joined).toBe(frame.segments.map((s) => s.text).join(''));
	});

	test('long parts are folded, with the number of hidden characters', () => {
		const [instructions, doc] = contextRows(frame, 280);
		expect(doc.parts[2].excerpt).toHaveLength(280);
		expect(doc.parts[2].hiddenChars).toBe(720);
		expect([instructions.parts[0].excerpt, instructions.parts[0].hiddenChars]).toEqual(['Respon en català.', 0]);
	});

	test('the history stays one row however long the conversation, split into turns', () => {
		const turns = 12;
		const segments = [{ message: 0, role: 'system', source: 'instructions', text: 'Sigues breu.', tokens: 4 }];
		for (let t = 0; t < turns; t++) {
			segments.push({ message: 2 * t + 1, role: 'user', source: 'history', text: `Pregunta ${t}`, tokens: 3 });
			segments.push({ message: 2 * t + 2, role: 'assistant', source: 'history', text: `Resposta ${t}`, tokens: 3 });
		}
		segments.push({ message: 2 * turns + 1, role: 'user', source: 'user', text: 'I ara?', tokens: 3 });
		const rows = contextRows({ segments });
		expect(rows.map((r) => r.key)).toEqual(['instructions', 'history', 'user']);
		const history = rows[1];
		expect([history.count, history.tokens]).toEqual([2 * turns, 6 * turns]);
		expect(history.turns?.map((t) => t.question)).toEqual([...Array(turns).keys()].map((t) => `Pregunta ${t}`));
		expect(history.turns?.[0].messages.map((m) => m.role)).toEqual(['user', 'assistant']);
	});

	test('a tool round of an earlier turn stays in its turn; this turn’s rounds have their own row (T16)', () => {
		const f = {
			segments: [
				{ message: 0, role: 'user', source: 'history', text: 'Quant fa 1+1?', tokens: 6 },
				{ message: 1, role: 'assistant', source: 'history', text: '<tool name="calculator">1+1</tool>', tokens: 9, meta: { tool_round: 'call' } },
				{ message: 2, role: 'user', source: 'history', text: '<tool_result name="calculator">2</tool_result>', tokens: 9, meta: { tool_round: 'result' } },
				{ message: 3, role: 'assistant', source: 'history', text: 'Fa 2.', tokens: 3 },
				{ message: 4, role: 'user', source: 'user', text: 'I 2*2?', tokens: 4 },
				{ message: 5, role: 'assistant', source: 'tools', text: '<tool name="calculator">2*2</tool>', tokens: 9, meta: { tool_round: 'call' } },
				{ message: 6, role: 'user', source: 'tools', text: '<tool_result name="calculator">4</tool_result>', tokens: 9, meta: { tool_round: 'result' } }
			]
		};
		const [history, user, rounds] = contextRows(f);
		expect(history.turns).toHaveLength(1);
		expect(history.parts.map((p) => p.role)).toEqual(['history', 'history_call', 'history_result', 'history']);
		expect(user.key).toBe('user');
		expect([rounds.key, rounds.count]).toEqual(['toolround', 1]);
		expect(rounds.parts.map((p) => p.role)).toEqual(['call', 'result']);
	});

	test('excerpts are part of the new message, before the question (T15)', () => {
		const f = {
			segments: [
				{ message: 0, role: 'system', source: 'instructions', text: 'Sigues breu.', tokens: 4 },
				{ message: 1, role: 'user', source: 'scaffold', text: 'Excerpts from the document:\n\n', tokens: 6 },
				{ message: 1, role: 'user', source: 'scaffold', text: '[1] apunts.md\n', tokens: 5 },
				{ message: 1, role: 'user', source: 'retrieval', text: 'La fotosíntesi…', tokens: 7 },
				{ message: 1, role: 'user', source: 'scaffold', text: '\n\n', tokens: 1 },
				{ message: 1, role: 'user', source: 'scaffold', text: '[2] apunts.md\n', tokens: 5 },
				{ message: 1, role: 'user', source: 'retrieval', text: 'Els cloroplasts…', tokens: 7 },
				{ message: 1, role: 'user', source: 'scaffold', text: '\n\nQuestion: ', tokens: 3 },
				{ message: 1, role: 'user', source: 'user', text: 'Què és?', tokens: 4 }
			]
		};
		const rows = contextRows(f);
		expect(rows.map((r) => r.key)).toEqual(['instructions', 'retrieval', 'user']);
		expect([rows[1].group, rows[1].count, rows[1].parts.length]).toEqual(['conversation', 2, 6]);
		expect(rows[2].parts.map((p) => p.text)).toEqual(['\n\nQuestion: ', 'Què és?']);
	});

	test('a truncation note stays with the document it ends (T14)', () => {
		const f = {
			segments: [
				{ message: 0, role: 'system', source: 'document', text: 'Molt de text', tokens: 4, meta: { name: 'apunts.md' } },
				{ message: 0, role: 'system', source: 'scaffold', text: '\n[Document truncated]', tokens: 4, meta: { truncation: true } },
				{ message: 0, role: 'system', source: 'scaffold', text: '\n\n', tokens: 1 },
				{ message: 0, role: 'system', source: 'tools', text: 'You can use…', tokens: 4 }
			]
		};
		const [doc, tools] = contextRows(f);
		expect(doc.parts.map((p) => p.source)).toEqual(['document', 'scaffold']);
		expect(doc.name).toBe('apunts.md');
		expect(tools.parts.map((p) => p.source)).toEqual(['scaffold', 'tools']);
	});

	test('no frame, no rows', () => {
		expect(contextRows(null)).toEqual([]);
	});
});

describe('budgetSummary', () => {
	test('reports use against the input limit, and what was cut', () => {
		expect(budgetSummary(frame)).toEqual({
			used: 2100,
			limit: 24780,
			percent: 8,
			tokenizer: 'o200k_base x 1.15, 1 token per digit',
			contextTokens: 32768,
			cap: null,
			document: frame.budget.document,
			historyDropped: 2,
			toolResultsElided: 0
		});
	});

	test('reports the earlier tool results replaced by a note (T16)', () => {
		const f = { ...frame, budget: { ...frame.budget, tool_results_elided: 3 } };
		expect(budgetSummary(f)?.toolResultsElided).toBe(3);
	});

	test('the limit is the step-5 cap when one was chosen (T18)', () => {
		const f = { ...frame, budget: { ...frame.budget, input_cap: 2000, input_limit: 2000 } };
		expect(budgetSummary(f)).toMatchObject({ cap: 2000, limit: 2000, contextTokens: 32768 });
	});

	test('the bar never goes past 100%', () => {
		expect(budgetSummary({ ...frame, tokens_estimated: 99999 })?.percent).toBe(100);
	});

	test('a frame without a budget has none', () => {
		expect(budgetSummary({ segments: [] })).toBeNull();
	});
});

describe('frameFromTrace (T17)', () => {
	test('a stored chat trace reads as the result frame it was sent as', () => {
		const payload = { model: 'lamb_assistant.2', messages: [{ role: 'user', content: 'Què és?' }], stream: true };
		const trace = {
			messages: payload.messages, segments: frame.segments, tokenizer: frame.tokenizer,
			tokens_estimated: 2100, budget: frame.budget, retrieval: null, payload, model: 'qwen3.5:9b',
			tool_rounds: []
		};
		expect(frameFromTrace(trace, 'on')).toEqual({
			phase: 'result', rag_mode: 'on', request_body: payload, model: 'qwen3.5:9b',
			segments: frame.segments, tokens_estimated: 2100, tokenizer: frame.tokenizer,
			budget: frame.budget, retrieval: null
		});
		expect(frameFromTrace(null)).toBeNull();
	});
});

describe('toolCallsSent (T17)', () => {
	test('counts this message’s calls, not the tool instructions nor the calls resent as history', () => {
		/** @param {number} message @param {string} source */
		const call = (message, source) => ({
			message, role: 'assistant', source, text: '<tool name="calculator">2*2</tool>', tokens: 9, meta: { tool_round: 'call' }
		});
		/** @param {number} message @param {string} source */
		const result = (message, source) => ({
			message, role: 'user', source, text: '<tool_result name="calculator">4</tool_result>', tokens: 9, meta: { tool_round: 'result' }
		});
		const f = {
			segments: [
				{ message: 0, role: 'system', source: 'tools', text: 'You can use the tools listed below.', tokens: 9, meta: {} },
				{ message: 1, role: 'user', source: 'history', text: 'Quant fa 1+1?', tokens: 6 },
				call(2, 'history'), result(3, 'history'),
				{ message: 4, role: 'assistant', source: 'history', text: 'Fa 2.', tokens: 3 },
				{ message: 5, role: 'user', source: 'user', text: 'I 2*2 i 3*3?', tokens: 4 },
				call(6, 'tools'), result(7, 'tools'), call(8, 'tools'), result(9, 'tools')
			]
		};
		expect(toolCallsSent(f)).toBe(2);
		expect(toolCallsSent(frame)).toBe(0);
		expect(toolCallsSent(null)).toBe(0);
	});
});
