// src/lib/components/logic/workshopTrace.test.js
// The panel's reading of the server trace (plan G4): segments per message,
// the context budget and the tool instructions.
import { describe, test, expect } from 'vitest';
import { budgetSummary, messageBreakdown, toolsText } from './workshopObservations.svelte.js';

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

describe('messageBreakdown', () => {
	test('groups segments by message, in order, keeping every part', () => {
		const out = messageBreakdown(frame);
		expect(out.map((m) => [m.index, m.role])).toEqual([
			[0, 'system'], [1, 'user'], [2, 'assistant'], [3, 'user']
		]);
		expect(out[0].parts.map((p) => p.source)).toEqual([
			'instructions', 'scaffold', 'scaffold', 'document', 'scaffold', 'tools'
		]);
		expect(out[0].tokens).toBe(6 + 1 + 8 + 300 + 1 + 9);
	});

	test('the parts of a message join back into exactly its text', () => {
		const joined = messageBreakdown(frame)[0].parts.map((p) => p.text).join('');
		expect(joined).toBe(frame.segments.filter((s) => s.message === 0).map((s) => s.text).join(''));
	});

	test('long parts are folded, with the number of hidden characters', () => {
		const doc = messageBreakdown(frame, 280)[0].parts[3];
		expect(doc.excerpt).toHaveLength(280);
		expect(doc.hiddenChars).toBe(720);
		const short = messageBreakdown(frame, 280)[0].parts[0];
		expect([short.excerpt, short.hiddenChars]).toEqual(['Respon en català.', 0]);
	});

	test('a tool round of an earlier turn is told apart from one of this turn (T16)', () => {
		const f = {
			segments: [
				{ message: 0, role: 'assistant', source: 'history', text: '<tool name="calculator">1+1</tool>', tokens: 9, meta: { tool_round: 'call' } },
				{ message: 1, role: 'user', source: 'history', text: '<tool_result name="calculator">2</tool_result>', tokens: 9, meta: { tool_round: 'result' } },
				{ message: 2, role: 'assistant', source: 'history', text: 'Fa 2.', tokens: 3 },
				{ message: 3, role: 'assistant', source: 'tools', text: '<tool name="calculator">2*2</tool>', tokens: 9, meta: { tool_round: 'call' } }
			]
		};
		expect(messageBreakdown(f).map((m) => m.parts[0].role)).toEqual([
			'history_call', 'history_result', 'history', 'call'
		]);
	});

	test('no frame, no messages', () => {
		expect(messageBreakdown(null)).toEqual([]);
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
			document: frame.budget.document,
			historyDropped: 2,
			toolResultsElided: 0
		});
	});

	test('reports the earlier tool results replaced by a note (T16)', () => {
		const f = { ...frame, budget: { ...frame.budget, tool_results_elided: 3 } };
		expect(budgetSummary(f)?.toolResultsElided).toBe(3);
	});

	test('the bar never goes past 100%', () => {
		expect(budgetSummary({ ...frame, tokens_estimated: 99999 })?.percent).toBe(100);
	});

	test('a frame without a budget has none', () => {
		expect(budgetSummary({ segments: [] })).toBeNull();
	});
});

describe('toolsText', () => {
	test('is the tools part of the system message', () => {
		expect(toolsText(frame)).toBe('You can use the tools listed below.');
		expect(toolsText({ segments: frame.segments.filter((s) => s.source !== 'tools') })).toBe('');
	});
});
