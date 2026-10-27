// src/lib/services/workshopChatService.test.js
import { describe, test, expect, vi } from 'vitest';
import { sendWorkshopChat } from './workshopChatService.js';
import { WorkshopApiError } from './workshopApi.js';

/**
 * A Response-like object whose body emits the given chunks.
 * @param {string[]} chunks
 */
function streamOf(chunks) {
	return {
		body: new ReadableStream({
			start(controller) {
				for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
				controller.close();
			}
		})
	};
}

/** @param {any} chat */
function apiWith(chat) {
	return /** @type {any} */ ({ chat: vi.fn(chat) });
}

const sse = (/** @type {any} */ obj) => `data: ${JSON.stringify(obj)}\n\n`;

describe('sendWorkshopChat', () => {
	test('sends only the message and the mode: the server builds the request', async () => {
		const api = apiWith(async () => streamOf(['data: [DONE]\n\n']));
		await sendWorkshopChat({ api, message: 'Hola', ragMode: 'off' }, {});
		expect(api.chat).toHaveBeenCalledWith({ message: 'Hola', rag_mode: 'off' }, undefined);
	});

	test('dispatches a full turn in order', async () => {
		const api = apiWith(async () => streamOf([
			sse({ type: 'observability', data: { phase: 'request' } }),
			sse({ model: 'qwen3.5:9b', choices: [{ index: 0, delta: { content: 'Hola' } }] }),
			// a frame split across network chunks
			'data: {"choices":[{"delta":{"con',
			'tent":"!"}}]}\n\n',
			sse({ type: 'tool_event', data: { type: 'tool', name: 'calculator', args: '1+1' } }),
			sse({ type: 'observability', data: { phase: 'result', model: 'qwen3.5:9b' } }),
			'data: [DONE]\n\n'
		]));
		/** @type {any[]} */
		const events = [];
		await sendWorkshopChat({ api, message: 'Hola' }, {
			onObservability: (d) => events.push(['obs', /** @type {any} */ (d).phase]),
			onChunk: (c) => events.push(['chunk', c]),
			onToolEvent: (e) => events.push(['tool', /** @type {any} */ (e).name]),
			onDone: () => events.push(['done'])
		});
		expect(events).toEqual([
			['obs', 'request'], ['chunk', 'Hola'], ['chunk', '!'], ['tool', 'calculator'],
			['obs', 'result'], ['done']
		]);
	});

	test('an error frame ends the turn with its message and hint', async () => {
		const api = apiWith(async () => streamOf([
			sse({ type: 'observability', data: { phase: 'request' } }),
			sse({ type: 'error', data: { message: 'The model service returned an error.', type: 'model_error', hint: 'Shorten it.' } }),
			sse({ choices: [{ delta: { content: 'never shown' } }] })
		]));
		const onError = vi.fn();
		const onChunk = vi.fn();
		const onDone = vi.fn();
		await sendWorkshopChat({ api, message: 'x' }, { onError, onChunk, onDone });
		expect(onError).toHaveBeenCalledWith('The model service returned an error.', { code: 'model_error', hint: 'Shorten it.' });
		expect(onChunk).not.toHaveBeenCalled();
		expect(onDone).not.toHaveBeenCalled();
	});

	test('a refused request (HTTP error before streaming) is reported, not thrown', async () => {
		const api = apiWith(async () => {
			throw new WorkshopApiError('Too long.', { status: 413, code: 'context_overflow', data: { hint: '' } });
		});
		const onError = vi.fn();
		await sendWorkshopChat({ api, message: 'x' }, { onError });
		expect(onError).toHaveBeenCalledWith('Too long.', { code: 'context_overflow', status: 413, hint: '' });
	});

	test('Stop (an aborted request) is silent', async () => {
		const api = apiWith(async () => {
			throw Object.assign(new Error('aborted'), { name: 'AbortError' });
		});
		const onError = vi.fn();
		await sendWorkshopChat({ api, message: 'x' }, { onError });
		expect(onError).not.toHaveBeenCalled();
	});
});
