// src/lib/services/workshopApi.js
/**
 * Client for the tool's workshop API (`/workshop/api/*`).
 *
 * Auth: the template's session token, which the LTI launch puts on the URL
 * (`?session=`), travels in the `X-Session-Token` header. Every request is
 * sent with `credentials: 'omit'`: the template reads its `tool_session`
 * cookie BEFORE the header, and a later launch in the same browser (a student
 * after the teacher) overwrites that cookie, so sending it could make this
 * tab act as someone else (plan §0.1).
 *
 * Sessions last 8 hours and cannot be renewed (the old branch renewed a
 * 30-minute JWT; the template has no such thing). A 401 therefore means
 * "open the activity again from the course page": `SESSION_EXPIRED`.
 */

/** Error code for a missing, unknown or expired session (HTTP 401). */
export const SESSION_EXPIRED = 'session_expired';

/** Default API root: the SPA is served under /workshop (svelte.config.js). */
export const DEFAULT_API_BASE = '/workshop/api';

export class WorkshopApiError extends Error {
	/**
	 * @param {string} message - safe to show the student
	 * @param {{status?: number, code?: string, data?: any}} [info]
	 */
	constructor(message, { status = 0, code = '', data = {} } = {}) {
		super(message);
		this.name = 'WorkshopApiError';
		this.status = status;
		this.code = code;
		this.data = data;
	}
}

/**
 * Turn a failed response into a WorkshopApiError.
 * @param {Response} res
 * @returns {Promise<WorkshopApiError>}
 */
export async function errorFromResponse(res) {
	/** @type {any} */
	let data = {};
	try {
		data = await res.json();
	} catch {
		/* not JSON */
	}
	const code = res.status === 401 ? SESSION_EXPIRED : data.code || `http_${res.status}`;
	const message = typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`;
	return new WorkshopApiError(message, { status: res.status, code, data });
}

/**
 * @param {{session: string, apiBase?: string, fetchImpl?: typeof fetch}} options
 */
export function createWorkshopApi({ session, apiBase = DEFAULT_API_BASE, fetchImpl }) {
	const doFetch = fetchImpl || ((/** @type {string} */ url, /** @type {RequestInit} */ init) => fetch(url, init));

	/**
	 * @param {string} path
	 * @param {{method?: string, body?: any, signal?: AbortSignal, raw?: boolean}} [options]
	 *   raw: return the Response itself (for the SSE stream) instead of JSON
	 */
	async function request(path, { method = 'GET', body, signal, raw = false } = {}) {
		/** @type {Record<string, string>} */
		const headers = { 'X-Session-Token': session };
		/** @type {RequestInit} */
		const init = { method, headers, credentials: 'omit', signal };
		if (body instanceof FormData) {
			init.body = body;
		} else if (body !== undefined) {
			headers['Content-Type'] = 'application/json';
			init.body = JSON.stringify(body);
		}
		const res = await doFetch(`${apiBase}${path}`, init);
		if (!res.ok) throw await errorFromResponse(res);
		return raw ? res : res.json();
	}

	return {
		request,
		/** Everything the wizard needs on load. */
		state: () => request('/state'),
		/** @param {{instructions?: string, tools?: string[], current_step?: number, retrieval_enabled?: boolean}} fields */
		saveAssistant: (fields) => request('/assistant', { method: 'PUT', body: fields }),
		/** @param {File} file */
		uploadDocument: (file) => {
			const fd = new FormData();
			fd.append('file', file);
			return request('/document', { method: 'POST', body: fd });
		},
		removeDocument: () => request('/document', { method: 'DELETE' }),
		/**
		 * The request the next message would make, without calling the model.
		 * @param {string} [message]
		 * @param {string} [ragMode]
		 */
		preview: (message = '', ragMode = 'on') =>
			request('/preview', { method: 'POST', body: { message, rag_mode: ragMode } }),
		/** @param {string} version - the notice version the student read */
		consent: (version) => request('/consent', { method: 'PUT', body: { version } }),
		/** Earlier turns stay stored but are no longer sent as history. */
		newConversation: () => request('/conversation/new', { method: 'POST' }),
		/** @param {{answers: Record<string, string>, submit?: boolean}} reflection */
		saveReflection: (reflection) => request('/reflection', { method: 'PUT', body: reflection }),
		/** Step 3: where the search of the step-2 document stands (status, chunks). */
		kb: () => request('/kb'),
		/**
		 * Step 3: what a test question retrieves from the step-2 document.
		 * @param {string} query
		 */
		probe: (query) => request('/probe', { method: 'POST', body: { query } }),
		/** Step 6: formative feedback on the submitted work (slow: one model call). */
		feedback: () => request('/feedback', { method: 'POST' }),
		/** @param {number} id */
		trace: (id) => request(`/traces/${id}`),
		/**
		 * Start a chat turn; resolves to the streaming Response.
		 * @param {{message: string, rag_mode: string}} body
		 * @param {AbortSignal} [signal]
		 */
		chat: (body, signal) => request('/chat', { method: 'POST', body, signal, raw: true })
	};
}
