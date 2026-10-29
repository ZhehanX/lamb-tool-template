<script>
	// src/routes/+page.svelte
	// Workshop build wizard + live observability panel.
	//
	// Entry: the LTI launch redirects to /workshop/?session=<token>&lang=<LMS
	// locale>. The token is the template's session; every API call sends it in
	// X-Session-Token (never as a cookie, see workshopApi.js).
	//
	// The server owns the student's assistant and the conversation, and builds
	// every request itself. While the student edits steps 1-4 the page asks
	// for a preview of the next request, so the panel shows what the assistant
	// would receive before anything is sent (D1/D2).
	//
	// Layout (T17): wizard, chat and panel side by side, each scrolling on its
	// own, so neither the panel nor the chat pushes the other down. Under each
	// message of the student a receipt opens, in the panel, what that request
	// sent.

	import { onDestroy, onMount, tick } from 'svelte';
	import { _, locale } from '$lib/i18n';
	import { createWorkshopFormState, STEP_COUNT } from '$lib/components/logic/workshopFormState.svelte.js';
	import { frameFromTrace, toolCallsSent } from '$lib/components/logic/workshopObservations.svelte.js';
	import WizardSteps from '$lib/components/WizardSteps.svelte';
	import ObservabilityPanel from '$lib/components/ObservabilityPanel.svelte';
	import LocaleSwitcher from '$lib/components/LocaleSwitcher.svelte';
	import RagComparison from '$lib/components/RagComparison.svelte';
	import FormativeFeedback from '$lib/components/FormativeFeedback.svelte';
	import ConsentNotice from '$lib/components/ConsentNotice.svelte';
	import { sendWorkshopChat } from '$lib/services/workshopChatService.js';
	import { createWorkshopApi, SESSION_EXPIRED } from '$lib/services/workshopApi.js';

	/** Wait this long after the last edit before saving and refreshing the preview. */
	const PREVIEW_DEBOUNCE_MS = 600;

	/** @type {ReturnType<typeof createWorkshopApi> | null} */
	let api = null;

	/** @type {ReturnType<typeof createWorkshopFormState> | null} */
	let formStore = /** @type {any} */ ($state(null));
	/** @type {any} */ let form = $state(null);
	/** @type {any} */ let server = $state(null);

	let loading = $state(true);
	let error = $state('');
	let errorHint = $state('');
	let reentryRequired = $state(false);
	let uploading = $state(false);
	let removingDocument = $state(false);
	let submitting = $state(false);
	let probing = $state(false);
	let evaluating = $state(false);
	let accepting = $state(false);
	/** Step 6: the latest formative feedback, or null. */
	/** @type {any | null} */ let evaluation = $state(null);
	/** Step 3: the latest test question and what it retrieved. */
	/** @type {any | null} */ let probe = $state(null);

	// ── Chat + observability ──
	/**
	 * @type {Array<{role: string, content: string, ragMode?: string, status?: string,
	 *                errorCode?: string, errorMessage?: string, hint?: string,
	 *                retrieved?: number, conversation?: number,
	 *                traceId?: number | null, tokens?: number | null, toolCalls?: number,
	 *                frame?: any}>}
	 *   role 'divider' marks the start of a new conversation. A student's
	 *   message carries its request for the receipt (T17): `frame` once known
	 *   (live, or loaded from `traceId`), `tokens` its estimated size,
	 *   `toolCalls` the tool calls it carried.
	 */
	let chatMessages = $state([]);
	/** The chat, or the A/B comparison in its place (D3). */
	let chatView = $state('chat');
	/** @type {HTMLElement | null} */ let chatBox = $state(null);
	/** Index of the message whose request the panel shows, or null for the latest. */
	/** @type {number | null} */ let shownTurn = $state(null);
	/** @type {number | null} */ let loadingTurn = $state(null);
	let chatInput = $state('');
	let streaming = $state(false);
	/** True between the request frame and the first answer chunk. */
	let waitingForModel = $state(false);
	/** @type {AbortController | null} */
	let abortController = null;
	/** @type {any | null} */ let obsRequest = $state(null);
	/** @type {any | null} */ let obsData = $state(null);
	/** @type {Array<any>} */ let toolEvents = $state([]);

	/** Only the current conversation is sent as history. */
	let conversation = $state(0);

	/** How often to ask whether the kb-server has finished splitting and embedding the document. */
	const KB_POLL_MS = 2000;

	const engineReady = $derived(!!server?.engine?.configured);
	/**
	 * Step 3 searches the step-2 document (plan T15). Its status: unavailable,
	 * none, processing, ready, failed or unreachable (service.kb_status).
	 * @type {{status: string, chunks?: number, chunk_size?: number, chunk_overlap?: number}}
	 */
	let kb = $state({ status: 'none' });
	const kbReady = $derived(kb.status === 'ready');
	/** The A/B switch (D3) only means something with the document connected in step 3. */
	const kbConnected = $derived(kbReady && !!form?.retrievalEnabled);
	const ragMode = $derived(kbConnected && form?.ragMode !== 'off' ? 'on' : 'off');
	/** Steps 1-2 show the document whole: searching it is step 3's idea. */
	const previewRagMode = $derived((form?.currentStep ?? 1) >= 3 ? ragMode : 'off');

	/** @type {ReturnType<typeof setTimeout> | null} */
	let kbTimer = null;

	/** @param {any} status */
	function setKb(status) {
		kb = status || { status: 'none' };
		if (kbTimer) clearTimeout(kbTimer);
		kbTimer = null;
		if (kb.status === 'processing') kbTimer = setTimeout(pollKb, KB_POLL_MS);
	}

	async function pollKb() {
		if (!api) return;
		try {
			setKb(await api.kb());
		} catch {
			kbTimer = setTimeout(pollKb, KB_POLL_MS * 2);
		}
	}

	onDestroy(() => {
		if (kbTimer) clearTimeout(kbTimer);
	});

	/** The A/B comparison reads the turns, oldest first (D3). */
	const comparisonHistory = $derived(
		pairs(chatMessages).map((p, i) => ({
			turn: i + 1,
			question: p.user,
			ragMode: p.ragMode,
			answer: p.answer,
			retrieved: p.retrieved,
			conversation: p.conversation
		}))
	);

	/** @param {Array<any>} msgs */
	function pairs(msgs) {
		const out = [];
		for (let i = 0; i < msgs.length - 1; i++) {
			if (msgs[i].role === 'user' && msgs[i + 1].role === 'assistant' && msgs[i + 1].status !== 'error') {
				out.push({
					user: msgs[i].content,
					answer: msgs[i + 1].content,
					ragMode: msgs[i].ragMode || 'on',
					retrieved: msgs[i].retrieved || 0,
					conversation: msgs[i].conversation
				});
			}
		}
		return out;
	}

	/**
	 * Show an error; a 401 means the session is over (8 hours, no renewal).
	 * @param {any} e
	 * @param {string} [fallback]
	 */
	function fail(e, fallback = 'Request failed') {
		if (e?.code === SESSION_EXPIRED) {
			error = $_('workshop.sessionExpired', { default: 'Your workshop session has ended.' });
			reentryRequired = true;
		} else {
			error = e?.message || fallback;
		}
		errorHint = e?.data?.hint || '';
	}

	function clearError() {
		error = '';
		errorHint = '';
	}

	/** @param {Array<any>} turns */
	function messagesFromTurns(turns) {
		const out = [];
		let previous = null;
		for (const t of turns || []) {
			if (previous !== null && t.conversation !== previous) out.push({ role: 'divider', content: '' });
			previous = t.conversation;
			out.push({
				role: 'user',
				content: t.user,
				ragMode: t.rag_mode,
				retrieved: t.retrieved || 0,
				conversation: t.conversation,
				traceId: t.trace_id ?? null,
				tokens: t.tokens ?? null,
				toolCalls: t.tool_calls || 0
			});
			if (t.status === 'ok') {
				out.push({ role: 'assistant', content: t.assistant || '' });
			} else if (t.status === 'error' && t.error !== 'aborted') {
				out.push({ role: 'assistant', content: '', status: 'error', errorCode: t.error });
			}
		}
		return out;
	}

	onMount(async () => {
		const params = new URLSearchParams(window.location.search);
		const session = params.get('session') || '';
		if (!session) {
			error = $_('workshop.missingSession', {
				default: 'Missing session. Open this activity from your course page.'
			});
			reentryRequired = true;
			loading = false;
			return;
		}
		api = createWorkshopApi({ session });
		try {
			server = await api.state();
			formStore = createWorkshopFormState(server);
			form = formStore.form;
			setKb(server.kb);
			conversation = server.conversation || 0;
			evaluation = server.feedback || null;
			chatMessages = messagesFromTurns(server.turns);
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not load the workshop.');
		} finally {
			loading = false;
		}
	});

	/** Nothing is stored or sent before the student accepts the notice. */
	const consented = $derived(!!server?.consent?.accepted);

	async function handleAccept() {
		if (!api || accepting) return;
		accepting = true;
		try {
			await api.consent(server.consent.version);
			server = { ...server, consent: { ...server.consent, accepted: true } };
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not record your answer.');
		} finally {
			accepting = false;
		}
	}

	// ── Save the design and refresh the preview after edits (debounced) ──
	/** @type {ReturnType<typeof setTimeout> | null} */
	let previewTimer = null;

	$effect(() => {
		if (!form || !api || !consented) return;
		// Read what the server stores, so this re-runs when any of it changes.
		void form.instructions;
		void form.selectedTools.length;
		void form.retrievalEnabled;
		void form.currentStep;
		// On, the document is searched instead of sent whole (T15): a new preview.
		void form.ragMode;
		void form.inputCap;
		void kb.status;
		if (previewTimer) clearTimeout(previewTimer);
		previewTimer = setTimeout(saveAndPreview, PREVIEW_DEBOUNCE_MS);
		return () => {
			if (previewTimer) clearTimeout(previewTimer);
		};
	});

	async function saveDesign() {
		if (!api || !formStore) return;
		const saved = await api.saveAssistant(formStore.assistantFields());
		form.document = saved.document;
	}

	async function saveAndPreview() {
		if (!api || !formStore || streaming) return;
		try {
			await saveDesign();
			const frame = await api.preview(chatInput.trim(), previewRagMode, form.inputCap);
			if (!streaming) obsRequest = frame.data;
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not save your design.');
		}
	}

	// ── Reflection autosave (step 6) ──
	/** @type {ReturnType<typeof setTimeout> | null} */
	let reflectionTimer = null;
	let reflectionLoaded = false;

	$effect(() => {
		if (!form || !api || !consented) return;
		const answers = { ...form.reflectionAnswers };
		if (!reflectionLoaded) {
			reflectionLoaded = true; // not on the first run: nothing changed yet
			return;
		}
		if (reflectionTimer) clearTimeout(reflectionTimer);
		reflectionTimer = setTimeout(() => {
			api?.saveReflection({ answers }).catch((e) => fail(e, 'Could not save your reflection.'));
		}, 1000);
	});

	// ── Step 2 ──
	/** @param {File} file */
	async function handleUploadDocument(file) {
		if (!api || uploading) return;
		uploading = true;
		clearError();
		try {
			const saved = await api.uploadDocument(file);
			form.document = saved.document;
			// A new document starts out sent whole; step 3 connects it.
			form.retrievalEnabled = saved.retrieval_enabled;
			setKb(saved.kb);
			await saveAndPreview();
		} catch (/** @type {any} */ e) {
			fail(e, 'Upload failed.');
		} finally {
			uploading = false;
		}
	}

	async function handleRemoveDocument() {
		if (!api || removingDocument) return;
		removingDocument = true;
		clearError();
		try {
			const saved = await api.removeDocument();
			form.document = saved.document;
			form.retrievalEnabled = saved.retrieval_enabled;
			setKb(saved.kb);
			await saveAndPreview();
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not remove the document.');
		} finally {
			removingDocument = false;
		}
	}

	// ── Step 3 ──
	async function handleProbeKb() {
		if (!api || probing || !form.kbQuery.trim()) return;
		probing = true;
		clearError();
		try {
			probe = await api.probe(form.kbQuery.trim());
		} catch (/** @type {any} */ e) {
			fail(e, 'The knowledge base test failed.');
		} finally {
			probing = false;
		}
	}

	// ── Navigation ──
	function handleNext() {
		formStore?.nextStep();
	}

	function handleBack() {
		formStore?.prevStep();
	}

	// ── Step 5: chat ──
	async function handleSend() {
		const text = chatInput.trim();
		if (!text || streaming || !api) return;
		clearError();
		if (previewTimer) clearTimeout(previewTimer);
		try {
			await saveDesign(); // the turn must use what the student sees
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not save your design.');
			return;
		}

		chatMessages = [
			...chatMessages,
			{ role: 'user', content: text, ragMode, conversation },
			{ role: 'assistant', content: '' }
		];
		const slot = chatMessages.length - 1;
		chatInput = '';
		chatView = 'chat';
		shownTurn = null; // the panel follows the message being sent
		streaming = true;
		waitingForModel = false;
		toolEvents = [];
		abortController = new AbortController();

		/** @param {any} patch */
		const update = (patch) => {
			chatMessages = chatMessages.map((m, i) => (i === slot ? { ...m, ...patch } : m));
		};
		let answer = '';

		await sendWorkshopChat(
			{ api, message: text, ragMode, inputCap: form.inputCap },
			{
				onObservability: (/** @type {any} */ data) => {
					// Two frames per turn (D1): "request" before the model is
					// called, "result" at the end.
					// The student's message keeps the latest of them for its receipt.
					/** @type {Record<string, any>} */
					const receipt = {
						frame: data,
						tokens: data?.tokens_estimated ?? null,
						toolCalls: toolCallsSent(data)
					};
					if (data?.phase === 'request') {
						obsRequest = data;
						if (data.round === 0) receipt.retrieved = (data.retrieved_sources || []).length;
						waitingForModel = true;
					} else {
						// Sent and answered: the result frame now describes it.
						obsData = data;
						obsRequest = null;
					}
					chatMessages = chatMessages.map((m, i) => (i === slot - 1 ? { ...m, ...receipt } : m));
				},
				onChunk: (chunk) => {
					waitingForModel = false;
					answer += chunk;
					update({ content: answer });
				},
				onToolEvent: (evt) => {
					toolEvents = [...toolEvents, evt];
				},
				onDone: () => {
					streaming = false;
					waitingForModel = false;
					abortController = null;
					form.turnsOk += 1;
				},
				onError: (msg, info) => {
					streaming = false;
					waitingForModel = false;
					abortController = null;
					update({ content: answer, status: 'error', errorMessage: msg, hint: info?.hint || '' });
					if (info?.code === SESSION_EXPIRED) fail({ code: SESSION_EXPIRED });
				}
			},
			abortController.signal
		);
		streaming = false;
		waitingForModel = false;
	}

	async function handleNewConversation() {
		if (!api || streaming) return;
		try {
			conversation = (await api.newConversation()).conversation;
			if (chatMessages.length && chatMessages[chatMessages.length - 1].role !== 'divider') {
				chatMessages = [...chatMessages, { role: 'divider', content: '' }];
			}
			chatView = 'chat';
			shownTurn = null;
			obsData = null;
			await saveAndPreview();
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not start a new conversation.');
		}
	}

	function handleStop() {
		abortController?.abort();
		streaming = false;
		waitingForModel = false;
		abortController = null;
	}

	// ── Receipts (T17): what an earlier message sent, opened in the panel ──
	const shownMessage = $derived(shownTurn === null ? null : chatMessages[shownTurn] || null);

	/** @param {number} i - index of a student's message */
	async function showTurn(i) {
		if (shownTurn === i) {
			shownTurn = null;
			return;
		}
		const m = chatMessages[i];
		if (!m.frame && m.traceId && api) {
			loadingTurn = i;
			try {
				const trace = await api.trace(m.traceId);
				const frame = frameFromTrace(trace, m.ragMode);
				chatMessages = chatMessages.map((x, j) =>
					j === i ? { ...x, frame, tokens: x.tokens ?? trace.tokens_estimated ?? null } : x
				);
			} catch (/** @type {any} */ e) {
				fail(e, 'Could not load what this message sent.');
				return;
			} finally {
				loadingTurn = null;
			}
		}
		if (chatMessages[i].frame) shownTurn = i;
	}

	/** The receipt the panel shows: the opened one, or the last turn's when the panel shows its result. */
	const activeReceipt = $derived.by(() => {
		if (shownTurn !== null) return shownTurn;
		if (obsRequest || !obsData) return null;
		for (let i = chatMessages.length - 1; i >= 0; i--) {
			if (chatMessages[i].role === 'divider') return null;
			if (chatMessages[i].role === 'user') return i;
		}
		return null;
	});

	/** @param {any} m */
	function receiptText(m) {
		const parts = [
			m.tokens != null
				? $_('workshop.receipt', {
						values: { tokens: Number(m.tokens).toLocaleString($locale || 'en') },
						default: `What was sent · about ${m.tokens} tokens`
					})
				: $_('workshop.receiptUnknown', { default: 'What was sent' })
		];
		if (m.retrieved > 0) {
			parts.push($_('workshop.receiptExcerpts', { values: { count: m.retrieved }, default: `${m.retrieved} excerpts` }));
		}
		if (m.toolCalls > 0) {
			parts.push(
				$_('workshop.receiptToolCalls', { values: { count: m.toolCalls }, default: `${m.toolCalls} tool calls` })
			);
		}
		return parts.join(' · ');
	}

	// Follow the conversation as it grows, unless the student scrolled up to
	// read an earlier message.
	/** @type {HTMLElement | null} */
	let followedBox = null;
	$effect.pre(() => {
		void chatMessages;
		void waitingForModel;
		const box = chatBox;
		if (!box) return;
		const fresh = box !== followedBox; // first shown, or back from the comparison
		followedBox = box;
		if (fresh || box.scrollHeight - box.scrollTop - box.clientHeight < 120) {
			tick().then(() => {
				box.scrollTop = box.scrollHeight;
			});
		}
	});

	/** The A/B comparison (D3) only means something once there is a run to compare. */
	const canCompare = $derived(
		form?.currentStep === 5 && kbConnected && comparisonHistory.length > 0
	);
	const comparing = $derived(canCompare && chatView === 'compare');

	// ── Step 6 ──
	async function handleSubmit() {
		if (!api || submitting) return;
		submitting = true;
		clearError();
		try {
			const saved = await api.saveReflection({ answers: { ...form.reflectionAnswers }, submit: true });
			form.submittedAt = saved.submitted_at;
		} catch (/** @type {any} */ e) {
			fail(e, 'Submit failed.');
			return;
		} finally {
			submitting = false;
		}
		// The work is saved; feedback is advisory and never blocks the submission.
		await requestFeedback();
	}

	async function requestFeedback() {
		if (!api || evaluating) return;
		evaluating = true;
		try {
			evaluation = await api.feedback();
		} catch (/** @type {any} */ e) {
			fail(e, 'Could not generate feedback.');
		} finally {
			evaluating = false;
		}
	}

	/** @param {string} code */
	function chatErrorText(code) {
		return code === 'model_error'
			? $_('workshop.modelError', { default: 'The model service returned an error instead of an answer.' })
			: $_('workshop.turnFailed', { default: 'This message got no answer.' });
	}

	/** @type {Array<{n: number, label: string}>} */
	const stepLabels = $derived([
		{ n: 1, label: $_('workshop.step1', { default: 'Instructions' }) },
		{ n: 2, label: $_('workshop.step2', { default: 'Attach Document' }) },
		{ n: 3, label: $_('workshop.step3', { default: 'Connect Knowledge Base' }) },
		{ n: 4, label: $_('workshop.step4', { default: 'Add Tool' }) },
		{ n: 5, label: $_('workshop.step5Chat', { default: 'Test' }) },
		{ n: 6, label: $_('workshop.step6', { default: 'Reflect' }) }
	]);
</script>

<svelte:head>
	<title>{$_('workshop.title', { default: 'AI Workshop' })}</title>
</svelte:head>

<div class="max-w-[90rem] mx-auto px-4 py-6">
	<!-- Minimal student shell (C7): activity identity + language. The LMS
	     provides the "back to course" affordance. -->
	<header class="flex items-center justify-between gap-4 mb-6">
		<div class="min-w-0">
			<h1 class="text-2xl font-bold truncate">
				{$_('workshop.title', { default: 'AI Workshop' })}
			</h1>
			{#if server?.activity?.title}
				<p class="text-sm text-gray-500 truncate">{server.activity.title}</p>
			{/if}
		</div>
		<LocaleSwitcher />
	</header>

	{#if loading}
		<div class="py-16 text-center text-gray-500">
			{$_('workshop.loadingWorkshop', { default: 'Loading your workshop…' })}
		</div>
	{:else if error && !form}
		<div class="border rounded p-4 bg-red-50 text-red-700 text-sm" data-testid="fatal-error">
			<p class="font-medium">{$_('workshop.workshopUnavailable', { default: 'Workshop unavailable' })}</p>
			<p class="mt-1">{error}</p>
			{#if reentryRequired}
				<p class="mt-2">
					{$_('workshop.reenterFromCourse', { default: 'Open the activity again from your course page to continue.' })}
				</p>
			{/if}
		</div>
	{:else if !consented}
		<ConsentNotice consent={server.consent} onAccept={handleAccept} {accepting} />
		{#if error}
			<p class="mt-4 text-center text-sm text-red-700">{error}</p>
		{/if}
	{:else}
		{#if server?.role === 'instructor'}
			<div class="mb-4 border rounded p-3 bg-blue-50 text-blue-800 text-sm" data-testid="teacher-banner">
				{$_('workshop.teacherTrial', { default: 'You are trying the workshop as a teacher. Your work is kept apart from the students’.' })}
			</div>
		{/if}
		{#if !engineReady}
			<div class="mb-4 border rounded p-3 bg-amber-50 text-amber-800 text-sm" data-testid="engine-missing">
				{$_('workshop.engineMissing', { default: 'The workshop is not connected to a model yet, so the chat cannot answer. Tell your teacher.' })}
			</div>
		{/if}
		{#if error}
			<div class="mb-4 border rounded p-3 bg-amber-50 text-amber-800 text-sm" data-testid="error">
				<p>{error}</p>
				{#if errorHint}<p class="mt-1">{errorHint}</p>{/if}
				{#if reentryRequired}
					<p class="mt-1">
						{$_('workshop.reenterFromCourse', { default: 'Open the activity again from your course page to continue.' })}
					</p>
				{/if}
			</div>
		{/if}

		<!-- Step progress bar -->
		<nav class="flex gap-2 mb-6 overflow-x-auto pb-1">
			{#each stepLabels as s (s.n)}
				<button
					class="px-3 py-1 rounded-full text-xs font-medium border whitespace-nowrap
					{form.currentStep === s.n ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300'}"
					onclick={() => formStore?.goToStep(s.n)}
				>{s.n}. {s.label}</button>
			{/each}
		</nav>

		<!-- Wizard | chat | panel (T17). On a wide screen the three share one
		     height and each scrolls on its own; on a narrow one they stack. -->
		<div
			class="grid grid-cols-1 gap-4 lg:h-[max(36rem,calc(100vh-12rem))] lg:grid-rows-[minmax(0,1fr)] lg:grid-cols-[19rem_minmax(0,1fr)_24rem] xl:grid-cols-[21rem_minmax(0,1fr)_28rem]"
		>
			<!-- Wizard card, and in step 6 the formative feedback -->
			<div class="space-y-4 lg:min-h-0 lg:overflow-y-auto">
				<section class="border rounded-lg p-5 bg-white shadow-sm">
					<p class="text-xs font-semibold uppercase text-gray-500 mb-4">
						{$_('workshop.stepOfN', {
							values: { n: form.currentStep, total: STEP_COUNT, label: stepLabels[form.currentStep - 1].label },
							default: `Step ${form.currentStep} of ${STEP_COUNT} — ${stepLabels[form.currentStep - 1].label}`
						})}
					</p>
					<WizardSteps
						{form}
						onNext={handleNext}
						onBack={handleBack}
						onSubmit={handleSubmit}
						onUploadDocument={handleUploadDocument}
						onRemoveDocument={handleRemoveDocument}
						onProbeKb={handleProbeKb}
						{probing}
						{kb}
						acceptedTypes={server?.limits?.document_types}
						inputCaps={server?.budget?.input_caps || []}
						inputLimit={server?.budget?.input_limit || 0}
						{submitting}
						{uploading}
						removing={removingDocument}
						nextDisabled={!formStore?.isStepComplete(form.currentStep)}
					/>
				</section>

				<!-- Step 6: formative feedback (a suggestion, never a grade) -->
				{#if form.currentStep === 6 && (evaluating || evaluation)}
					<div data-testid="feedback">
						<FormativeFeedback {evaluation} {evaluating} />
						{#if evaluation?.status === 'failed' && !evaluating}
							<button
								class="mt-2 text-xs px-3 py-1 border rounded hover:bg-gray-50"
								onclick={requestFeedback}
								data-testid="feedback-retry"
							>{$_('workshop.feedbackRetry', { default: 'Try generating the feedback again' })}</button>
						{/if}
					</div>
				{/if}
			</div>

			<!-- Chat, and the A/B comparison in its place (D3) -->
			<section class="flex h-[32rem] flex-col border rounded-lg bg-white shadow-sm lg:h-auto lg:min-h-0">
				<div class="border-b px-3 py-2 flex flex-wrap items-center justify-between gap-2">
					{#if canCompare}
						<div class="flex gap-1" role="tablist">
							<button
								role="tab"
								aria-selected={!comparing}
								class="whitespace-nowrap rounded px-3 py-1.5 text-sm {comparing ? 'text-gray-600 hover:bg-gray-50' : 'bg-gray-100 font-semibold'}"
								onclick={() => (chatView = 'chat')}
								data-testid="chat-tab"
							>{$_('workshop.testChat', { default: 'Test your assistant' })}</button>
							<button
								role="tab"
								aria-selected={comparing}
								class="whitespace-nowrap rounded px-3 py-1.5 text-sm {comparing ? 'bg-gray-100 font-semibold' : 'text-gray-600 hover:bg-gray-50'}"
								onclick={() => (chatView = 'compare')}
								data-testid="compare-tab"
							>{$_('workshop.compareTab', { default: 'Compare on / off' })}</button>
						</div>
					{:else}
						<h2 class="px-1 text-sm font-semibold">{$_('workshop.testChat', { default: 'Test your assistant' })}</h2>
					{/if}
					<button
						class="text-xs px-2 py-1 border rounded hover:bg-gray-50 disabled:opacity-50"
						disabled={streaming || chatMessages.length === 0}
						onclick={handleNewConversation}
						data-testid="new-conversation"
					>{$_('workshop.newConversation', { default: 'New conversation' })}</button>
				</div>

				{#if comparing}
					<div class="flex-1 min-h-0 overflow-y-auto p-4">
						<RagComparison history={comparisonHistory} />
					</div>
				{:else}
					<div bind:this={chatBox} class="flex-1 min-h-0 overflow-y-auto p-4 space-y-3" data-testid="chat-messages">
						{#if chatMessages.length === 0}
							<p class="text-sm text-gray-400 text-center pt-16">
								{$_('workshop.chatEmpty', { default: 'Send a message to test your assistant.' })}
							</p>
						{:else}
							{#each chatMessages as m, i (i)}
								{#if m.role === 'divider'}
									<p class="text-center text-xs text-gray-400 border-t pt-2" data-testid="chat-divider">
										{$_('workshop.newConversationStarted', { default: 'New conversation: the messages above are no longer sent to the model.' })}
									</p>
								{:else}
								<div class="flex flex-col gap-1 {m.role === 'user' ? 'items-end' : 'items-start'}">
									{#if m.status === 'error'}
										<div class="max-w-[80%] rounded-lg px-3 py-2 text-sm bg-red-50 text-red-700 border border-red-200" data-testid="chat-error">
											<p>{m.errorMessage || chatErrorText(m.errorCode || '')}</p>
											{#if m.hint}<p class="text-xs mt-1">{m.hint}</p>{/if}
											{#if m.content}<p class="text-xs mt-1 text-gray-500 whitespace-pre-wrap">{m.content}</p>{/if}
										</div>
									{:else}
										<div
											class="max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap {m.role === 'user'
												? 'bg-blue-600 text-white'
												: 'bg-gray-100 text-gray-800'}"
											data-testid="chat-{m.role}"
										>
											{#if m.role === 'assistant' && m.content === '' && streaming && i === chatMessages.length - 1}
												<span class="text-gray-500">…</span>
											{:else}
												{m.content}
											{/if}
										</div>
									{/if}
									<!-- The receipt: what this message sent, opened in the panel (T17) -->
									{#if m.role === 'user' && (m.frame || m.traceId) && !(streaming && i === chatMessages.length - 2)}
										<button
											class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs disabled:opacity-50
											{activeReceipt === i
												? 'border-blue-600 bg-blue-50 text-blue-800'
												: 'border-gray-300 bg-white text-gray-600 hover:bg-gray-50'}"
											aria-pressed={activeReceipt === i}
											title={$_('workshop.receiptHint', { default: 'Show in the panel what your assistant received for this message' })}
											disabled={loadingTurn === i}
											onclick={() => showTurn(i)}
											data-testid="chat-receipt"
										>
											<svg class="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"></path><path d="M9 8h6M9 12h6"></path></svg>
											<span>{receiptText(m)}</span>
										</button>
									{/if}
								</div>
								{/if}
							{/each}
							{#if waitingForModel}
								<div class="text-xs text-gray-500 pl-1" data-testid="model-thinking">
									{$_('workshop.modelThinking', { default: 'Sent. The model is thinking before it answers (its thinking is not passed on, only the answer)…' })}
								</div>
							{:else if streaming}
								<div class="text-xs text-gray-500 pl-1">
									{$_('workshop.streaming', { default: 'streaming…' })}
								</div>
							{/if}
						{/if}
					</div>
				{/if}

				<div class="border-t p-3 flex gap-2">
					<input
						class="flex-1 border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
						placeholder={$_('workshop.chatPlaceholder', { default: 'Ask your assistant something…' })}
						bind:value={chatInput}
						onkeydown={(e) => { if (e.key === 'Enter' && !streaming) handleSend(); }}
						disabled={streaming || !engineReady}
						data-testid="chat-input"
					/>
					{#if streaming}
						<button class="px-4 py-2 border rounded text-sm hover:bg-gray-50" onclick={handleStop}>
							{$_('workshop.stop', { default: 'Stop' })}
						</button>
					{:else}
						<button
							class="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
							onclick={handleSend}
							disabled={!chatInput.trim() || !engineReady}
							data-testid="chat-send"
						>{$_('workshop.send', { default: 'Send' })}</button>
					{/if}
				</div>
			</section>

			<!-- What the assistant received (D1, D2): the latest request, or the one
			     a receipt opened -->
			<aside class="h-[36rem] border rounded-lg bg-white shadow-sm overflow-hidden lg:h-auto lg:min-h-0">
				<ObservabilityPanel
					obsData={shownMessage ? shownMessage.frame : obsData}
					obsRequest={shownMessage ? null : obsRequest}
					toolEvents={shownMessage ? [] : toolEvents}
					document={form.document}
					{probe}
					step={form.currentStep}
					ragMode={shownMessage ? shownMessage.ragMode : ragMode}
					kbAvailable={kbConnected}
					pinned={shownMessage ? { question: shownMessage.content } : null}
					onUnpin={() => (shownTurn = null)}
				/>
			</aside>
		</div>
	{/if}
</div>
