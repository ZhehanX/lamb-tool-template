// src/lib/services/workshopChatService.js
/**
 * Workshop chat SSE service.
 *
 * Sends one message and streams the turn. The server assembles the whole
 * request (instructions, document, history) itself, so only the message and
 * the knowledge-base mode travel. Frames (the old branch's dialect):
 *   - `data: {"type":"observability","data":{...}}`        → panel frames:
 *        phase "request" before the model is called, "result" at the end
 *   - `data: {"choices":[{"delta":{"content":"..."}}]}`   → answer text
 *   - `data: {"type":"tool_event","data":{...}}`           → tool timeline
 *   - `data: {"type":"tool_messages","data":{"messages":[…]}}` → tool rounds
 *   - `data: {"type":"error","data":{message, type, hint}}` → the turn failed
 *   - `data: [DONE]`                                       → stream end
 */

/**
 * @param {Object} params
 * @param {ReturnType<import('./workshopApi.js').createWorkshopApi>} params.api
 * @param {string} params.message
 * @param {string} [params.ragMode] - 'on' | 'off' (A/B experiment)
 * @param {Object} handlers
 * @param {(chunk: string) => void} [handlers.onChunk]
 * @param {(data: Object) => void} [handlers.onObservability]
 * @param {(evt: Object) => void} [handlers.onToolEvent]
 * @param {(messages: Array<Object>) => void} [handlers.onToolMessages]
 * @param {() => void} [handlers.onDone]
 * @param {(msg: string, info: {code?: string, hint?: string, status?: number}) => void} [handlers.onError]
 * @param {AbortSignal} [signal]
 */
export async function sendWorkshopChat({ api, message, ragMode = 'on' }, handlers = {}, signal) {
	const { onChunk, onObservability, onToolEvent, onToolMessages, onDone, onError } = handlers;

	let res;
	try {
		res = await api.chat({ message, rag_mode: ragMode }, signal);
	} catch (/** @type {any} */ e) {
		// AbortError on Stop / unmount is expected; do not surface as an error.
		if (e?.name === 'AbortError') return;
		if (onError) {
			onError(e?.message || 'Request failed', {
				code: e?.code,
				status: e?.status,
				hint: e?.data?.hint || ''
			});
			return;
		}
		throw e;
	}

	const reader = res.body?.getReader();
	if (!reader) return;

	const decoder = new TextDecoder();
	let buffer = '';

	try {
		while (true) {
			if (signal?.aborted) {
				try {
					await reader.cancel();
				} catch {
					/* noop */
				}
				return;
			}
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split('\n');
			buffer = lines.pop() || '';

			for (const line of lines) {
				if (!line.startsWith('data: ')) continue;
				const payload = line.slice(6);
				if (payload === '[DONE]') {
					if (onDone) onDone();
					return;
				}
				try {
					const data = JSON.parse(payload);
					if (data.type === 'observability') {
						if (onObservability) onObservability(data.data);
					} else if (data.type === 'tool_event') {
						if (onToolEvent) onToolEvent(data.data);
					} else if (data.type === 'tool_messages') {
						if (onToolMessages) onToolMessages(data.data?.messages || []);
					} else if (data.type === 'error') {
						// The turn failed after the stream started (LAMB or the model
						// service). Without this branch the UI would sit on
						// "streaming…" forever.
						if (onError) {
							onError(data.data?.message || 'Generation failed.', {
								code: data.data?.type,
								hint: data.data?.hint || ''
							});
						}
						return;
					} else if (data.choices?.[0]?.delta?.content) {
						if (onChunk) onChunk(data.choices[0].delta.content);
					}
				} catch {
					/* ignore non-JSON */
				}
			}
		}
	} catch (/** @type {any} */ e) {
		if (e?.name === 'AbortError') return;
		throw e;
	}
}
