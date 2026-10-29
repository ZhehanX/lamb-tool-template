// src/lib/services/workshopApi.test.js
import { describe, test, expect, vi } from 'vitest';
import { createWorkshopApi, SESSION_EXPIRED, WorkshopApiError } from './workshopApi.js';

/** @param {number} status @param {any} body */
function response(status, body) {
	return { ok: status < 400, status, json: async () => body };
}

function setup(reply = response(200, { ok: true })) {
	const fetchImpl = vi.fn(async () => reply);
	const api = createWorkshopApi({ session: 'tok-123', fetchImpl: /** @type {any} */ (fetchImpl) });
	return { api, fetchImpl };
}

describe('createWorkshopApi', () => {
	test('sends the session in X-Session-Token and never sends cookies', async () => {
		const { api, fetchImpl } = setup();
		await api.state();
		const [url, init] = /** @type {any} */ (fetchImpl.mock.calls[0]);
		expect(url).toBe('/workshop/api/state');
		expect(init.headers['X-Session-Token']).toBe('tok-123');
		// The template reads its cookie before the header; a later launch in the
		// same browser overwrites it (plan §0.1).
		expect(init.credentials).toBe('omit');
	});

	test('JSON bodies are encoded and typed', async () => {
		const { api, fetchImpl } = setup();
		await api.saveAssistant({ instructions: 'Sigues breu.', current_step: 2 });
		const [url, init] = /** @type {any} */ (fetchImpl.mock.calls[0]);
		expect(url).toBe('/workshop/api/assistant');
		expect(init.method).toBe('PUT');
		expect(init.headers['Content-Type']).toBe('application/json');
		expect(JSON.parse(init.body)).toEqual({ instructions: 'Sigues breu.', current_step: 2 });
	});

	test('a document is sent as multipart form data', async () => {
		const { api, fetchImpl } = setup();
		await api.uploadDocument(new File(['# a'], 'a.md'));
		const [url, init] = /** @type {any} */ (fetchImpl.mock.calls[0]);
		expect(url).toBe('/workshop/api/document');
		expect(init.body).toBeInstanceOf(FormData);
		expect(init.headers['Content-Type']).toBeUndefined(); // the browser sets the boundary
	});

	test('the other calls hit their routes', async () => {
		const { api, fetchImpl } = setup();
		await api.removeDocument();
		await api.preview('Hola', 'off', 2000);
		await api.saveReflection({ answers: { q1: 'x' }, submit: true });
		await api.trace(7);
		await api.feedback();
		const calls = fetchImpl.mock.calls.map((/** @type {any} */ c) => [c[1].method, c[0]]);
		expect(calls).toEqual([
			['DELETE', '/workshop/api/document'],
			['POST', '/workshop/api/preview'],
			['PUT', '/workshop/api/reflection'],
			['GET', '/workshop/api/traces/7'],
			['POST', '/workshop/api/feedback']
		]);
		expect(JSON.parse(/** @type {any} */ (fetchImpl.mock.calls[1])[1].body)).toEqual({ message: 'Hola', rag_mode: 'off', input_cap: 2000 });
	});

	test('chat resolves to the raw streaming response', async () => {
		const reply = response(200, {});
		const { api } = setup(reply);
		expect(await api.chat({ message: 'Hola', rag_mode: 'on' })).toBe(reply);
	});

	test('a 401 means the session is over: re-enter from the course', async () => {
		const { api } = setup(response(401, { detail: 'Session expired — relaunch from your LMS.' }));
		const err = await api.state().catch((e) => e);
		expect(err).toBeInstanceOf(WorkshopApiError);
		expect(err.code).toBe(SESSION_EXPIRED);
		expect(err.message).toBe('Session expired — relaunch from your LMS.');
	});

	test('other errors keep the server code, message and extra fields', async () => {
		const { api } = setup(response(413, { detail: 'Too long.', code: 'context_overflow', needed: 30000, limit: 24780 }));
		const err = await api.chat({ message: 'x', rag_mode: 'on' }).catch((e) => e);
		expect([err.status, err.code, err.message]).toEqual([413, 'context_overflow', 'Too long.']);
		expect(err.data.limit).toBe(24780);
	});
});

describe('newConversation', () => {
	test('posts to its route', async () => {
		const { api, fetchImpl } = setup(response(200, { conversation: 1 }));
		expect(await api.newConversation()).toEqual({ conversation: 1 });
		const [url, init] = /** @type {any} */ (fetchImpl.mock.calls[0]);
		expect([init.method, url]).toEqual(['POST', '/workshop/api/conversation/new']);
	});
});
