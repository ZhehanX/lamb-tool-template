<script>
	// src/lib/components/WizardSteps.svelte
	// Six-step build wizard body. Presentational: the page owns the form store
	// (createWorkshopFormState) and the API calls; this component binds inputs
	// to form fields and emits next/back.
	//
	// Steps (the thesis numbering):
	//   1. Instructions → 2. Document → 3. Knowledge base → 4. Tools →
	//   5. Test chat → 6. Reflection
	//
	// Steps 2 and 3 use the same material, the student's own document (plan
	// T12, T15): step 2 places it in the context whole; step 3 searches it,
	// split into excerpts, and once connected only the excerpts most similar
	// to each message are sent. Without a document step 3 has nothing to search.
	//
	// Every student-visible string is localized (en/es/ca/eu) — students reach
	// this page straight from the LMS.

	import { _, locale } from '$lib/i18n';
	import { formatBytes } from './logic/workshopObservations.svelte.js';

	/**
	 * @type {{
	 *   form: any,
	 *   onNext?: () => void,
	 *   onBack?: () => void,
	 *   onSubmit?: () => void,
	 *   onUploadDocument?: (file: File) => void,
	 *   onRemoveDocument?: () => void,
	 *   onProbeKb?: () => void,
	 *   kb?: {status: string, chunks?: number, chunk_size?: number, chunk_overlap?: number},
	 *   acceptedTypes?: string[],
	 *   submitting?: boolean,
	 *   uploading?: boolean,
	 *   removing?: boolean,
	 *   probing?: boolean,
	 *   nextDisabled?: boolean
	 * }}
	 */
	let {
		form,
		onNext,
		onBack,
		onSubmit,
		onUploadDocument,
		onRemoveDocument,
		onProbeKb,
		kb = { status: 'none' },
		acceptedTypes = ['.txt', '.md', '.pdf', '.docx', '.pptx', '.html'],
		submitting = false,
		uploading = false,
		removing = false,
		probing = false,
		nextDisabled = false
	} = $props();

	/** True while a file is dragged over the step-2 drop zone. */
	let dragging = $state(false);

	/** Step 3 can search the document (and step 4's kb_search use it). */
	const kbReady = $derived(kb?.status === 'ready');

	/** Step 3 when the document cannot be searched (yet), by kb status. */
	const kbNotReady = $derived(
		/** @type {Record<string, string>} */ ({
			none: $_('workshop.kbNoDocument', { default: 'You have not attached a document in step 2, so there is nothing to search. Attach one there to try this step, or continue to the next step.' }),
			processing: $_('workshop.kbProcessing', { default: 'Preparing your document for searching: it is split into excerpts and each one is turned into numbers that capture its meaning (embeddings)…' }),
			failed: $_('workshop.kbFailed', { default: 'Your document could not be prepared for searching. Remove it in step 2 and attach it again.' }),
			unreachable: $_('workshop.kbUnreachable', { default: 'The knowledge base server cannot be reached right now. Try again in a moment.' }),
			unavailable: $_('workshop.kbNotAvailable', { default: 'This workshop has no knowledge base server, so this step is not available. You can continue to the next step.' })
		})[kb?.status] || ''
	);

	/** @param {File | null | undefined} file */
	function pickFile(file) {
		if (file && !uploading && onUploadDocument) onUploadDocument(file);
	}

	/** @param {Event} e */
	function handleFile(e) {
		const input = /** @type {HTMLInputElement} */ (e.target);
		const file = input.files && input.files[0];
		// Reset the input: browsers only fire `change` when the selection
		// differs, so after a failed upload the same file could not be retried.
		input.value = '';
		pickFile(file);
	}

	/** @param {DragEvent} e */
	function handleDragOver(e) {
		// preventDefault is what makes the zone a valid drop target; without it
		// the browser navigates to the dropped file and the wizard is lost.
		e.preventDefault();
		if (e.dataTransfer) e.dataTransfer.dropEffect = uploading ? 'none' : 'copy';
		dragging = !uploading;
	}

	/** @param {DragEvent} e */
	function handleDragLeave(e) {
		// dragleave also fires when moving onto a child element; ignore that.
		const next = /** @type {Node | null} */ (e.relatedTarget);
		if (next && /** @type {Node} */ (e.currentTarget).contains(next)) return;
		dragging = false;
	}

	/** @param {DragEvent} e */
	function handleDrop(e) {
		e.preventDefault();
		dragging = false;
		pickFile(e.dataTransfer?.files?.[0]);
	}

	/** @param {string} toolId */
	function toggleTool(toolId) {
		if (form.selectedTools.includes(toolId)) {
			form.selectedTools = form.selectedTools.filter((/** @type {string} */ t) => t !== toolId);
		} else {
			form.selectedTools = [...form.selectedTools, toolId];
		}
	}

	/** @param {boolean} on */
	function setRagMode(on) {
		form.ragMode = on ? 'on' : 'off';
	}

	const reflectionQuestions = $derived([
		{ key: 'q1', label: $_('workshop.reflectionQ1', { default: 'What did you ask your assistant to do, and why?' }) },
		{ key: 'q2', label: $_('workshop.reflectionQ2', { default: 'What did you see in the panels — what did the assistant actually receive?' }) },
		{ key: 'q3', label: $_('workshop.reflectionQ3', { default: 'What would you change in your design after testing it?' }) }
	]);
</script>

<div class="space-y-6">
	{#if form.currentStep === 1}
		<!-- Step 1: Instructions -->
		<div>
			<label class="block text-sm font-medium text-gray-700 mb-1" for="ws-instructions">
				{$_('workshop.instructionsPlaceholder', { default: 'Write what your assistant should do and how to answer...' })}
			</label>
			<textarea
				id="ws-instructions"
				bind:value={form.instructions}
				rows="8"
				class="w-full border rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
				placeholder={$_('workshop.instructionsExample', { default: 'You are a helpful tutor that explains fractions step by step...' })}
			></textarea>
			<p class="text-xs text-gray-500 mt-1">
				{$_('workshop.instructionHint', { default: 'This text becomes the system message: the first thing your assistant reads.' })}
			</p>
		</div>
	{:else if form.currentStep === 2}
		<!-- Step 2: Attach a document (placed in the context whole, T12) -->
		<div class="space-y-3">
			{#if form.document}
				<div class="border rounded p-3 bg-green-50" data-testid="document-card">
					<div class="flex items-center justify-between">
						<span class="text-sm truncate">📎 {form.document.name}</span>
						<button
							class="text-xs text-red-600 hover:underline disabled:opacity-50"
							disabled={removing}
							onclick={() => onRemoveDocument && onRemoveDocument()}
						>{$_('workshop.remove', { default: 'Remove' })}</button>
					</div>
					<p class="text-xs text-gray-600 mt-1">
						{formatBytes(form.document.bytes)} ·
						{$_('workshop.documentChars', {
							values: { chars: Number(form.document.chars).toLocaleString($locale || 'en') },
							default: `${Number(form.document.chars).toLocaleString($locale || 'en')} characters of text`
						})}
					</p>
					<p class="text-xs text-green-700 mt-1">
						{$_('workshop.documentAttached', { default: 'Its text is now part of what your assistant reads with every message.' })}
					</p>
				</div>
			{:else}
				<div
					role="group"
					class="border-2 border-dashed rounded p-6 text-center text-sm text-gray-500 transition-colors
					{dragging ? 'border-blue-500 bg-blue-50' : ''}"
					ondragover={handleDragOver}
					ondragleave={handleDragLeave}
					ondrop={handleDrop}
				>
					<p>
						{$_('workshop.attachDocWholeHint', { default: 'Choose a file (text, Markdown, PDF, Word, PowerPoint or HTML). Its whole text will be given to your assistant.' })}
					</p>
					<input
						type="file"
						class="mt-3 text-xs"
						accept={acceptedTypes.join(',')}
						disabled={uploading}
						onchange={handleFile}
					/>
					<p class="mt-2 text-xs text-gray-400">
						{$_('workshop.dropHint', { default: 'You can also drag and drop the file here.' })}
					</p>
				</div>
				{#if uploading}
					<p class="text-xs text-blue-600">
						{$_('workshop.readingDocument', { default: 'Reading your document…' })}
					</p>
				{/if}
			{/if}
			<p class="text-xs text-gray-500">
				{$_('workshop.documentPanelHint', { default: 'The panel shows how much of what the model can read the document takes.' })}
			</p>
		</div>
	{:else if form.currentStep === 3}
		<!-- Step 3: the step-2 document as a knowledge base (Phase 8, T15) -->
		<div class="space-y-3">
			{#if kbReady}
				<p class="text-sm text-gray-700" data-testid="kb-ready">
					{$_('workshop.kbReady', {
						values: { name: form.document?.name ?? '', chunks: kb.chunks ?? 0, size: kb.chunk_size ?? 0 },
						default: `Your document “${form.document?.name ?? ''}” was split into ${kb.chunks ?? 0} excerpts of up to ${kb.chunk_size ?? 0} characters.`
					})}
				</p>
				<label class="flex items-start gap-2 border rounded p-3 cursor-pointer">
					<input type="checkbox" class="mt-0.5" bind:checked={form.retrievalEnabled} data-testid="kb-connect" />
					<span class="text-sm">
						{$_('workshop.kbConnect', { default: 'Search my document instead of sending it whole' })}
						<span class="block text-xs text-gray-500">
							{$_('workshop.kbConnectHint', { default: 'Your assistant then no longer receives the whole document: with every message, only the excerpts most similar to it are added before your question. In step 5 you can switch between the two.' })}
						</span>
					</span>
				</label>
				<label class="block text-sm font-medium text-gray-700" for="ws-kb-query">
					{$_('workshop.kbTestQuestion', { default: 'Ask a test question' })}
				</label>
				<input
					id="ws-kb-query"
					bind:value={form.kbQuery}
					class="w-full border rounded-md p-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
					placeholder={$_('workshop.kbTestPlaceholder', { default: 'What does my document say about…?' })}
				/>
				<button
					class="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
					disabled={probing || !form.kbQuery.trim()}
					onclick={() => onProbeKb && onProbeKb()}
				>{probing
						? $_('workshop.probing', { default: 'Retrieving…' })
						: $_('workshop.verifyKb', { default: 'Test retrieval' })}</button>
				<p class="text-xs text-gray-500">
					{$_('workshop.probePanelHint', { default: 'See the panel for the excerpts and how similar they are.' })}
				</p>
			{:else}
				<p class="text-sm text-gray-600" data-testid="kb-{kb?.status}">{kbNotReady}</p>
			{/if}
		</div>
	{:else if form.currentStep === 4}
		<!-- Step 4: tools (described to the model as text, T4) -->
		<div class="space-y-3">
			<p class="text-sm font-medium text-gray-700">
				{$_('workshop.selectTools', { default: 'Select tools your assistant can call' })}
			</p>
			<label class="flex items-center gap-2 border rounded p-3 cursor-pointer">
				<input type="checkbox" checked={form.selectedTools.includes('calculator')} onchange={() => toggleTool('calculator')} />
				<span class="text-sm">🧮 {$_('workshop.calculator', { default: 'Calculator' })}</span>
			</label>
			<label class="flex items-center gap-2 border rounded p-3 {kbReady ? 'cursor-pointer' : 'opacity-50'}">
				<input
					type="checkbox"
					disabled={!kbReady}
					checked={kbReady && form.selectedTools.includes('kb_search')}
					onchange={() => toggleTool('kb_search')}
				/>
				<span class="text-sm">📚 {$_('workshop.kbQuery', { default: 'Knowledge Base Query' })}</span>
			</label>
			<label class="flex items-center gap-2 border rounded p-3 opacity-50">
				<input type="checkbox" disabled checked={false} />
				<span class="text-sm">🖥️ {$_('workshop.sandbox', { default: 'Sandbox Execution (unavailable)' })}</span>
			</label>
			<p class="text-xs text-gray-500">
				{$_('workshop.toolsAsTextHint', { default: 'A tool is described to the model in words. When the model wants to use it, it writes a call as text; the workshop runs it and sends back the result.' })}
			</p>
		</div>
	{:else if form.currentStep === 5}
		<!-- Step 5: test chat -->
		<div class="space-y-4">
			<p class="text-sm text-gray-700">
				{$_('workshop.chatHint', { default: 'Send a message to test your assistant, and watch what it receives in the panel.' })}
			</p>

			{#if kbReady && form.retrievalEnabled}
				<!-- A/B experiment (D3): excerpts only (on) vs the whole document (off) -->
				<div class="border rounded p-3">
					<div class="flex items-center justify-between gap-2">
						<span class="text-sm font-medium">
							{$_('workshop.abToggle', { default: 'Knowledge base' })}
						</span>
						<div class="inline-flex rounded border overflow-hidden text-xs" role="group">
							<button
								class="px-3 py-1 {form.ragMode !== 'off' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600'}"
								aria-pressed={form.ragMode !== 'off'}
								data-testid="rag-mode-on"
								onclick={() => setRagMode(true)}
							>{$_('workshop.abOn', { default: 'On' })}</button>
							<button
								class="px-3 py-1 {form.ragMode === 'off' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600'}"
								aria-pressed={form.ragMode === 'off'}
								data-testid="rag-mode-off"
								onclick={() => setRagMode(false)}
							>{$_('workshop.abOff', { default: 'Off' })}</button>
						</div>
					</div>
					<p class="text-xs text-gray-500 mt-2">
						{$_('workshop.abHint', { default: 'On: only the excerpts of your document most similar to your question are sent. Off: the whole document is sent. Ask the same question both ways, with a new conversation in between, and compare the answers.' })}
					</p>
				</div>
			{/if}
		</div>
	{:else}
		<!-- Step 6: reflection (D5) -->
		<div class="space-y-2">
			<h4 class="text-sm font-semibold">
				{$_('workshop.reflectionTitle', { default: 'Reflection' })}
			</h4>
			{#each reflectionQuestions as question (question.key)}
				<label class="block text-xs font-medium text-gray-700" for="ws-reflection-{question.key}">
					{question.label}
				</label>
				<textarea
					id="ws-reflection-{question.key}"
					data-testid="reflection-{question.key}"
					bind:value={form.reflectionAnswers[question.key]}
					rows="3"
					class="w-full border rounded-md p-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
				></textarea>
			{/each}

			{#if form.submittedAt}
				<p class="text-xs text-green-700" data-testid="submitted">
					{$_('workshop.submitted', { default: 'Submitted. You can still edit and submit again.' })}
				</p>
			{/if}
			{#if onSubmit}
				<button
					class="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
					disabled={submitting}
					onclick={onSubmit}
				>{submitting
						? $_('workshop.submitting', { default: 'Submitting…' })
						: $_('workshop.submit', { default: 'Submit workshop' })}</button>
			{/if}
		</div>
	{/if}

	<!-- Navigation -->
	<div class="flex justify-between pt-4 border-t mt-6">
		{#if form.currentStep > 1}
			<button class="px-4 py-2 border rounded text-sm hover:bg-gray-50" onclick={onBack}>
				← {$_('workshop.back', { default: 'Back' })}
			</button>
		{:else}
			<span></span>
		{/if}
		{#if form.currentStep < 6}
			<button
				class="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
				disabled={nextDisabled}
				onclick={onNext}
			>{$_('workshop.next', { default: 'Next' })} →</button>
		{/if}
	</div>
</div>
