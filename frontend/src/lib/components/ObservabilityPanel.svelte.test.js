// src/lib/components/ObservabilityPanel.svelte.test.js
import { describe, test, expect, beforeAll } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import { waitLocale } from 'svelte-i18n';
import { setupI18n } from '$lib/i18n';
import ObservabilityPanel from './ObservabilityPanel.svelte';

beforeAll(async () => {
	setupI18n();
	await waitLocale();
});

const messages = [
	{ role: 'system', content: 'Respon en català.\n\n## Document: apunts.md\n\nLa fotosíntesi passa als cloroplasts.' },
	{ role: 'user', content: 'Què és la fotosíntesi?' }
];

/** @param {string} phase @param {Record<string, any>} [extra] */
function frame(phase, extra = {}) {
	return {
		phase,
		rag_mode: 'on',
		request_body: { model: 'lamb_assistant.2', messages, stream: true },
		model: phase === 'result' ? 'qwen3.5:9b' : null,
		tokens_estimated: 60,
		tokenizer: 'o200k_base x 1.15, 1 token per digit',
		budget: { context_tokens: 32768, input_limit: 24780, history_dropped: 0, document: null },
		segments: [
			{ message: 0, role: 'system', source: 'instructions', text: 'Respon en català.', tokens: 6 },
			{ message: 0, role: 'system', source: 'scaffold', text: '\n\n', tokens: 1 },
			{ message: 0, role: 'system', source: 'scaffold', text: '## Document: apunts.md\n\n', tokens: 8 },
			{ message: 0, role: 'system', source: 'document', text: 'La fotosíntesi passa als cloroplasts.', tokens: 12 },
			{ message: 1, role: 'user', source: 'user', text: 'Què és la fotosíntesi?', tokens: 7 }
		],
		...extra
	};
}

describe('ObservabilityPanel', () => {
	test('empty until there is something to show', () => {
		render(ObservabilityPanel, { props: {} });
		expect(screen.getByText(/Nothing to observe yet/)).toBeInTheDocument();
	});

	test('a preview shows every part of the request labelled by where it came from (G4)', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview'), step: 1 } });
		const parts = screen.getAllByTestId('panel-part');
		expect(parts.map((p) => p.dataset.source)).toEqual([
			'instructions', 'scaffold', 'scaffold', 'document', 'user'
		]);
		expect(within(parts[0]).getByText('Your instructions')).toBeInTheDocument();
		expect(within(parts[3]).getByText('Your document')).toBeInTheDocument();
		expect(within(parts[1]).getByText('Added by the workshop')).toBeInTheDocument();
		expect(screen.getAllByTestId('panel-row').map((r) => r.dataset.row)).toEqual(['instructions', 'document', 'user']);
		expect(screen.getByTestId('panel-phase')).toHaveTextContent('What your next message would send');
		expect(screen.getByTestId('panel-request')).toHaveTextContent('What your next message would send');
		expect(screen.getByTestId('panel-request')).toHaveTextContent('lamb_assistant.2');
	});

	test('the row the step is about opens by itself (T17)', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview'), step: 2 } });
		const open = screen.getAllByTestId('panel-row').filter((r) => /** @type {HTMLDetailsElement} */ (r).open);
		expect(open.map((r) => r.dataset.row)).toEqual(['document']);
	});

	test('the makeup bar has one part per row, named for screen readers', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview') } });
		const bar = screen.getByTestId('panel-makeup');
		expect(bar.children).toHaveLength(3);
		expect(bar.getAttribute('aria-label')).toMatch(/^Your instructions \d+%, Your document \d+%, Your message \d+%$/);
	});

	test('the raw request is the payload as sent, not a reconstruction', () => {
		const f = frame('request');
		render(ObservabilityPanel, { props: { obsRequest: f } });
		const raw = screen.getByTestId('panel-request').querySelector('pre');
		expect(JSON.parse(raw?.textContent || '')).toEqual(f.request_body);
		expect(screen.getByTestId('panel-request')).toHaveTextContent('About to be sent');
	});

	test('the result names the model LAMB reported', () => {
		render(ObservabilityPanel, { props: { obsData: frame('result') } });
		expect(screen.getByTestId('panel-answered-by')).toHaveTextContent('Answered by: qwen3.5:9b');
	});

	test('the budget shows use against the limit and says it is an estimate', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview') } });
		expect(screen.getByTestId('panel-budget-used')).toHaveTextContent('About 60 of 24,780 tokens');
		expect(screen.getByTestId('panel-budget')).toHaveTextContent(/Estimated \(o200k_base/);
		expect(screen.queryByTestId('panel-history-dropped')).toBeNull();
	});

	test('a step-5 cap is named as an experiment, not as the model\'s size (T18)', () => {
		const f = frame('preview', {
			budget: { context_tokens: 32768, input_limit: 2000, input_cap: 2000, history_dropped: 0, document: null }
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		expect(screen.getByTestId('panel-budget-used')).toHaveTextContent('About 60 of 2,000 tokens');
		expect(screen.getByTestId('panel-budget-capped')).toHaveTextContent(
			'In this experiment the model may read only 2,000 tokens; it could read 32,768.'
		);
		expect(screen.getByTestId('panel-budget')).not.toHaveTextContent(/The model reads/);
	});

	test('without a cap the panel says nothing about one', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview') } });
		expect(screen.queryByTestId('panel-budget-capped')).toBeNull();
	});

	test('dropped history is reported', () => {
		const f = frame('request', { budget: { context_tokens: 8192, input_limit: 5939, history_dropped: 4, document: null } });
		render(ObservabilityPanel, { props: { obsRequest: f } });
		expect(screen.getByTestId('panel-history-dropped')).toHaveTextContent('4 of the oldest messages');
	});

	test('step 2 shows the document and warns when it was cut (T14)', () => {
		const doc = { name: 'apunts.md', bytes: 4096, chars: 9000, tokens: 2500, input_limit: 24780, tokenizer: 't' };
		const f = frame('preview', {
			budget: {
				context_tokens: 32768, input_limit: 24780, history_dropped: 0,
				document: { name: 'apunts.md', total_chars: 9000, kept_chars: 4000, truncated: true }
			}
		});
		render(ObservabilityPanel, { props: { obsRequest: f, document: doc, step: 2 } });
		expect(screen.getByTestId('panel-document-size')).toHaveTextContent('4.0 KB · 9,000 characters · about 2,500 tokens');
		expect(screen.getByTestId('panel-document-truncated')).toHaveTextContent('only the first 4,000 of 9,000 characters fit');
	});

	test('a long part is folded with the number of hidden characters', () => {
		const long = 'paraula '.repeat(100);
		const f = frame('preview', {
			segments: [{ message: 0, role: 'user', source: 'user', text: long, tokens: 100 }]
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		expect(screen.getByTestId('panel-part')).toHaveTextContent(`(${(long.length - 280).toLocaleString()} more characters)`);
	});

	test('unfolding a long part continues the same text in place (T17)', async () => {
		const long = 'Tools:\n- calculator: ' + 'paraula '.repeat(60);
		const f = frame('preview', {
			segments: [{ message: 0, role: 'system', source: 'tools', text: long, tokens: 100 }]
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		const fold = screen.getByTestId('panel-fold');
		const text = /** @type {HTMLElement} */ (fold.parentElement);
		expect(text.querySelector('span')?.hidden).toBe(true);
		await fireEvent.click(fold);
		expect(text.querySelector('span')?.hidden).toBe(false);
		// One run of text: the excerpt and the rest, then the button to fold it again.
		expect(text.textContent).toBe(long + 'Show less');
		await fireEvent.click(fold);
		expect(fold).toHaveTextContent(`(${(long.length - 280).toLocaleString()} more characters)`);
	});

	test('step 4 shows the tool instructions as the text the model reads', () => {
		const f = frame('preview', {
			segments: [{ message: 0, role: 'system', source: 'tools', text: 'You can use the tools listed below.', tokens: 9 }]
		});
		const { unmount } = render(ObservabilityPanel, { props: { obsRequest: f, step: 4 } });
		expect(screen.getByTestId('panel-tools')).toHaveTextContent('You can use the tools listed below.');
		unmount();
		render(ObservabilityPanel, { props: { obsRequest: frame('preview'), step: 4 } });
		expect(screen.getByTestId('panel-tools')).toHaveTextContent('No tools');
	});

	test('step 5 shows the knowledge-base mode of the turn (D3)', () => {
		render(ObservabilityPanel, { props: { obsData: frame('result'), step: 5, ragMode: 'off' } });
		expect(screen.getByTestId('panel-rag-mode')).toHaveTextContent('Knowledge base off');
	});

	test('the tool timeline lists calls and failures', () => {
		render(ObservabilityPanel, {
			props: {
				step: 5,
				toolEvents: [
					{ type: 'tool', name: 'calculator', args: '17*23' },
					{ type: 'tool_done', name: 'calculator', success: false }
				]
			}
		});
		expect(screen.getAllByText('calculator')).toHaveLength(2);
		expect(screen.getByText('17*23')).toBeInTheDocument();
		expect(screen.getByText('failed')).toBeInTheDocument();
	});
});

describe('ObservabilityPanel — whitespace the workshop adds', () => {
	test('a blank separator is shown as visible line breaks, not an empty box', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview') } });
		const marks = screen.getAllByTestId('panel-whitespace');
		expect(marks).toHaveLength(1);
		expect(marks[0]).toHaveTextContent('⏎⏎');
	});
});

describe('ObservabilityPanel — the text tool protocol (Phase 6)', () => {
	test('a tool round shows the call, its argument and its result', () => {
		render(ObservabilityPanel, {
			props: {
				step: 5,
				toolEvents: [
					{ type: 'tool', name: 'calculator', args: '17*23', round: 1 },
					{ type: 'tool_done', name: 'calculator', success: true, result: '391', round: 1 }
				]
			}
		});
		const rows = screen.getAllByTestId('tool-event');
		expect(rows[0]).toHaveTextContent(/calculator\s*17\*23/);
		expect(rows[1]).toHaveTextContent(/✓\s*calculator\s*→ 391/);
	});

	test('a call that broke the format is explained', () => {
		render(ObservabilityPanel, { props: { step: 5, toolEvents: [{ type: 'tool_malformed' }] } });
		expect(screen.getByTestId('tool-malformed')).toHaveTextContent('did not follow the format');
	});

	test('a later request of the same turn says which round it is', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('request', { round: 1 }) } });
		expect(screen.getByTestId('panel-request')).toHaveTextContent('About to be sent · round 2');
	});
});

describe('ObservabilityPanel — the two halves of a tool round', () => {
	test('the call and the result are labelled for what they are', () => {
		const f = frame('result', {
			segments: [
				{ message: 0, role: 'user', source: 'user', text: 'Quant fa 17 × 23?', tokens: 9 },
				{ message: 1, role: 'assistant', source: 'tools', text: '<tool name="calculator">17*23</tool>', tokens: 12, meta: { tool_round: 'call' } },
				{ message: 2, role: 'user', source: 'tools', text: '<tool_result name="calculator">391</tool_result>', tokens: 14, meta: { tool_round: 'result' } }
			]
		});
		render(ObservabilityPanel, { props: { obsData: f } });
		const parts = screen.getAllByTestId('panel-part');
		expect(parts[1]).toHaveTextContent('Tool call written by the model');
		expect(parts[2]).toHaveTextContent('Tool result sent back by the workshop');
	});

	test('the rounds of an earlier turn are labelled as earlier (T16)', () => {
		const f = frame('preview', {
			segments: [
				{ message: 0, role: 'user', source: 'history', text: 'Quant fa 17 × 23?', tokens: 9 },
				{ message: 1, role: 'assistant', source: 'history', text: '<tool name="calculator">17*23</tool>', tokens: 12, meta: { tool_round: 'call' } },
				{ message: 2, role: 'user', source: 'history', text: '<tool_result name="calculator">391</tool_result>', tokens: 14, meta: { tool_round: 'result' } },
				{ message: 3, role: 'user', source: 'user', text: 'I el doble?', tokens: 5 }
			]
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		const parts = screen.getAllByTestId('panel-part');
		expect(parts[0]).toHaveTextContent('Earlier messages');
		expect(parts[1]).toHaveTextContent('Earlier tool call written by the model');
		expect(parts[2]).toHaveTextContent('Earlier tool result sent back by the workshop');
	});

	test('the history row also counts the rounds of earlier turns', () => {
		const f = frame('preview', {
			segments: [
				{ message: 0, role: 'user', source: 'history', text: 'Quant fa 17 × 23?', tokens: 9 },
				{ message: 1, role: 'assistant', source: 'history', text: '<tool name="calculator">17*23</tool>', tokens: 12, meta: { tool_round: 'call' } },
				{ message: 2, role: 'user', source: 'history', text: '<tool_result name="calculator">391</tool_result>', tokens: 14, meta: { tool_round: 'result' } },
				{ message: 3, role: 'assistant', source: 'history', text: 'Fa 391.', tokens: 4 },
				{ message: 4, role: 'user', source: 'user', text: 'I el doble?', tokens: 5 }
			]
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		const history = screen.getAllByTestId('panel-row').find((r) => r.dataset.row === 'history');
		expect(history).toHaveTextContent('4 messages · 1 earlier turn');
		expect(history).toHaveTextContent('Turn 1');
	});

	test('earlier results replaced by a note are reported', () => {
		const f = frame('request', {
			budget: { context_tokens: 8192, input_limit: 5939, history_dropped: 0, tool_results_elided: 2, document: null }
		});
		render(ObservabilityPanel, { props: { obsRequest: f } });
		expect(screen.getByTestId('panel-results-elided')).toHaveTextContent('2 earlier tool results were replaced');
		expect(screen.queryByTestId('panel-history-dropped')).toBeNull();
	});
});

describe('ObservabilityPanel — an earlier message, opened from its receipt (T17)', () => {
	test('says which message it shows and goes back to the latest', async () => {
		let unpinned = 0;
		render(ObservabilityPanel, {
			props: { obsData: frame('result'), pinned: { question: 'Què és la fotosíntesi?' }, onUnpin: () => unpinned++ }
		});
		expect(screen.getByTestId('panel-pinned')).toHaveTextContent('An earlier message: “Què és la fotosíntesi?”');
		expect(screen.queryByTestId('panel-phase')).toBeNull();
		screen.getByTestId('panel-unpin').click();
		expect(unpinned).toBe(1);
	});
});

describe('ObservabilityPanel — a long conversation (T17)', () => {
	/** @param {number} turns */
	function conversation(turns) {
		const segments = [{ message: 0, role: 'system', source: 'instructions', text: 'Sigues breu.', tokens: 4 }];
		for (let t = 0; t < turns; t++) {
			segments.push({ message: 2 * t + 1, role: 'user', source: 'history', text: `Pregunta ${t}`, tokens: 30 });
			segments.push({ message: 2 * t + 2, role: 'assistant', source: 'history', text: `Resposta ${t}`, tokens: 60 });
		}
		segments.push({ message: 2 * turns + 1, role: 'user', source: 'user', text: 'I ara?', tokens: 3 });
		return frame('result', { segments });
	}

	test('keeps the same rows however many turns, and says what the history costs', () => {
		const { unmount } = render(ObservabilityPanel, { props: { obsData: conversation(1) } });
		const few = screen.getAllByTestId('panel-row').length;
		unmount();
		render(ObservabilityPanel, { props: { obsData: conversation(20) } });
		expect(screen.getAllByTestId('panel-row')).toHaveLength(few);
		expect(screen.getByTestId('panel-history-share')).toHaveTextContent('Earlier messages are 100% of what is sent');
	});

	test('no note while the history is a small part', () => {
		render(ObservabilityPanel, { props: { obsRequest: frame('preview') } });
		expect(screen.queryByTestId('panel-history-share')).toBeNull();
	});
});
