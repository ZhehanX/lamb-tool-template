<script>
	// src/lib/components/ObservabilityPanel.svelte
	// The single rendering surface for "what is my assistant working with?"
	// across all six steps (D1 + D2).
	//
	// The tool assembles every request itself and LAMB passes it through
	// unchanged (plan T1/T3, checked by the contract test), so the frames shown
	// here ARE what the model reads: `request_body` is the payload as sent, and
	// `segments` split each message by where its text came from.
	//
	// Props (the page owns all of them; this component only renders):
	//   - obsRequest — the next/outgoing request: phase "preview" (built from
	//                  the current design, nothing sent yet) or "request" (sent,
	//                  the model is still answering)
	//   - obsData    — the "result" frame of the last turn, with the model LAMB
	//                  reported
	//   - document   — step 2: the server's view of the attached document
	//   - probe      — step 3: a test question and what it retrieved
	//   - toolEvents — tool timeline entries
	//   - step, ragMode, kbAvailable (the A/B badge only means something with
	//     the step-2 document connected in step 3)

	import { _, locale } from '$lib/i18n';
	import {
		budgetSummary,
		formatBytes,
		messageBreakdown,
		toolsText
	} from './logic/workshopObservations.svelte.js';
	import RetrievalCandidates from './RetrievalCandidates.svelte';

	/**
	 * @type {{
	 *   obsData?: any | null,
	 *   obsRequest?: any | null,
	 *   toolEvents?: Array<any>,
	 *   document?: any | null,
	 *   probe?: {query?: string, results?: Array<any>, count?: number} | null,
	 *   step?: number,
	 *   ragMode?: string,
	 *   kbAvailable?: boolean
	 * }}
	 */
	let {
		obsData = null,
		obsRequest = null,
		toolEvents = [],
		document = null,
		probe = null,
		step = 0,
		ragMode = 'on',
		kbAvailable = true
	} = $props();

	/** The frame describing the next/outgoing request. */
	let requestFrame = $derived(
		obsRequest || (obsData && obsData.phase !== 'result' ? obsData : null)
	);
	/** The frame with what the model finally got, and who answered. */
	let resultFrame = $derived(obsData?.phase === 'result' ? obsData : null);
	/** The frame the breakdown and the budget describe: the newest one. */
	let current = $derived(requestFrame || resultFrame);

	const breakdown = $derived(messageBreakdown(current));
	const budget = $derived(budgetSummary(current));
	const tools = $derived(toolsText(current));
	const docInContext = $derived(budget?.document || null);

	let hasAnything = $derived(
		!!current || toolEvents.length > 0 || !!document || (step === 3 && !!probe)
	);

	/** @type {Record<string, string>} */
	const SOURCE_CLASSES = {
		instructions: 'bg-blue-50 border-blue-300',
		document: 'bg-green-50 border-green-300',
		retrieval: 'bg-purple-50 border-purple-300',
		tools: 'bg-orange-50 border-orange-300',
		history: 'bg-gray-100 border-gray-300',
		user: 'bg-sky-50 border-sky-300',
		scaffold: 'bg-white border-dashed border-gray-300'
	};

	/** @param {string} source - a segment source, or a tool round's call/result */
	function sourceLabel(source) {
		/** @type {Record<string, string>} */
		const defaults = {
			instructions: 'Your instructions',
			document: 'Your document',
			retrieval: 'Excerpts of your document',
			tools: 'Tool instructions',
			history: 'Earlier messages',
			user: 'Your message',
			scaffold: 'Added by the workshop',
			call: 'Tool call written by the model',
			result: 'Tool result sent back by the workshop',
			history_call: 'Earlier tool call written by the model',
			history_result: 'Earlier tool result sent back by the workshop'
		};
		return $_(`workshop.source_${source}`, { default: defaults[source] || source });
	}

	/**
	 * Whitespace the workshop adds between blocks, made visible: an empty box
	 * would hide the line breaks the model does read.
	 * @param {string} text
	 */
	function visibleWhitespace(text) {
		return text.replace(/\n/g, '⏎').replace(/ /g, '·');
	}

	/** @param {string} phase */
	function phaseLabel(phase) {
		return phase === 'preview'
			? $_('workshop.previewPhase', { default: 'What your next message would send' })
			: $_('workshop.requestPhase', { default: 'About to be sent' });
	}

	/**
	 * @param {{type: string, name?: string, success?: boolean, args?: string}} t
	 */
	function toolIcon(t) {
		if (t.type === 'thinking') return '🧠';
		if (t.type === 'tool_done') return t.success ? '✓' : '✗';
		return '⚡';
	}

	/** @param {any} value */
	function rawJson(value) {
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	}

	/**
	 * Numbers in the language of the interface, not of the computer.
	 * @param {number} value
	 */
	function fmt(value) {
		return Number(value || 0).toLocaleString($locale || 'en');
	}
</script>

<div class="space-y-4">
	{#if !hasAnything}
		<div class="border rounded p-4 bg-gray-50 text-sm text-gray-500">
			{$_('workshop.panelEmpty', { default: 'Nothing to observe yet — the panel fills in as you build.' })}
		</div>
	{:else}
		<!-- A/B badge: which mode this turn ran in (D3) -->
		{#if step === 5 && current && kbAvailable}
			<span
				class="inline-block px-2 py-0.5 rounded-full text-xs {ragMode === 'off'
					? 'bg-amber-100 text-amber-800'
					: 'bg-green-100 text-green-800'}"
				data-testid="panel-rag-mode"
			>
				{ragMode === 'off'
					? $_('workshop.ragOffBadge', { default: 'Knowledge base off: the whole document' })
					: $_('workshop.ragOnBadge', { default: 'Knowledge base on: excerpts only' })}
			</span>
		{/if}

		<!-- On, the excerpts depend on the message, so a preview cannot have them (T15) -->
		{#if current?.phase === 'preview' && current?.rag_mode === 'on' && document}
			<p class="text-xs text-purple-800 bg-purple-50 border border-purple-200 rounded px-2 py-1" data-testid="panel-excerpts-later">
				{$_('workshop.excerptsLater', { default: 'Your document is not sent whole: when you send a message, the excerpts most similar to it are added before it.' })}
			</p>
		{/if}

		<!-- Step 2: the document goes into the context whole (T12) -->
		{#if step === 2 && document}
			<section class="border rounded p-3 bg-gray-50" data-testid="panel-document">
				<h4 class="text-xs font-semibold uppercase text-gray-500">
					{$_('workshop.documentInContextTitle', { default: 'Your document in what the assistant reads' })}
				</h4>
				<div class="text-xs mt-2 space-y-1">
					<div class="font-medium text-gray-700">{document.name}</div>
					<div class="text-gray-600" data-testid="panel-document-size">
						{$_('workshop.documentSize', {
							values: { bytes: formatBytes(document.bytes), chars: fmt(document.chars), tokens: fmt(document.tokens) },
							default: `${formatBytes(document.bytes)} · ${fmt(document.chars)} characters · about ${fmt(document.tokens)} tokens`
						})}
					</div>
					<div class="text-gray-500">
						{$_('workshop.documentWhole', {
							values: { limit: fmt(document.input_limit) },
							default: `The whole text is placed in the system message. The model can read about ${fmt(document.input_limit)} tokens per request.`
						})}
					</div>
					{#if docInContext?.truncated}
						<p class="text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid="panel-document-truncated">
							{$_('workshop.documentTruncated', {
								values: { kept: fmt(docInContext.kept_chars), total: fmt(docInContext.total_chars) },
								default: `Too long: only the first ${fmt(docInContext.kept_chars)} of ${fmt(docInContext.total_chars)} characters fit. The model never sees the rest.`
							})}
						</p>
					{/if}
				</div>
			</section>
		{/if}

		<!-- Step 3: what the student's test question retrieved, every candidate -->
		{#if step === 3 && probe}
			<RetrievalCandidates search={probe} testid="panel-probe" />
		{/if}

		<!-- A chat turn's search of the document (step 5) -->
		{#if step !== 3 && current?.retrieval}
			<RetrievalCandidates search={current.retrieval} testid="panel-retrieval" />
		{/if}

		<!-- Step 4: tools are text the model reads, not a separate channel (T4) -->
		{#if step === 4}
			<section class="border rounded p-3 bg-gray-50" data-testid="panel-tools">
				<h4 class="text-xs font-semibold uppercase text-gray-500">
					{$_('workshop.toolsTextTitle', { default: 'Tool instructions added to the system message' })}
				</h4>
				{#if tools}
					<pre class="text-[11px] whitespace-pre-wrap mt-1 text-gray-700">{tools}</pre>
				{:else}
					<p class="text-xs text-gray-400 mt-1">
						{$_('workshop.toolsTextEmpty', { default: 'No tools: the assistant can only answer with text.' })}
					</p>
				{/if}
			</section>
		{/if}

		<!-- How much of what the model can read this request uses (T14) -->
		{#if budget}
			<section class="border rounded p-3 bg-gray-50" data-testid="panel-budget">
				<h4 class="text-xs font-semibold uppercase text-gray-500">
					{$_('workshop.budgetTitle', { default: 'How much the model can read' })}
				</h4>
				<div class="mt-2 h-2 rounded bg-gray-200 overflow-hidden" aria-hidden="true">
					<div
						class="h-2 {budget.percent >= 90 ? 'bg-amber-500' : 'bg-blue-500'}"
						style="width: {budget.percent}%"
					></div>
				</div>
				<p class="text-xs text-gray-600 mt-1" data-testid="panel-budget-used">
					{$_('workshop.budgetUsed', {
						values: { used: fmt(budget.used), limit: fmt(budget.limit) },
						default: `About ${fmt(budget.used)} of ${fmt(budget.limit)} tokens`
					})}
				</p>
				<p class="text-[11px] text-gray-400">
					{$_('workshop.budgetEstimate', {
						values: { tokenizer: budget.tokenizer, context: fmt(budget.contextTokens) },
						default: `Estimated (${budget.tokenizer}). The model reads ${fmt(budget.contextTokens)} tokens; part is kept free for its answer.`
					})}
				</p>
				{#if budget.historyDropped > 0}
					<p class="text-xs text-amber-700 mt-1" data-testid="panel-history-dropped">
						{$_('workshop.historyDropped', {
							values: { count: budget.historyDropped },
							default: `${budget.historyDropped} of the oldest messages were left out to make room.`
						})}
					</p>
				{/if}
				{#if budget.toolResultsElided > 0}
					<p class="text-xs text-amber-700 mt-1" data-testid="panel-results-elided">
						{$_('workshop.toolResultsElided', {
							values: { count: budget.toolResultsElided },
							default: `${budget.toolResultsElided} earlier tool results were replaced by a short note to make room.`
						})}
					</p>
				{/if}
			</section>
		{/if}

		<!-- Where each part of the request comes from (the trace) -->
		{#if breakdown.length > 0}
			<section class="border rounded p-3 bg-white" data-testid="panel-segments">
				<h4 class="text-xs font-semibold uppercase text-gray-500">
					{$_('workshop.segmentsTitle', { default: 'Where each part of the request comes from' })}
				</h4>
				<ol class="mt-2 space-y-3">
					{#each breakdown as message (message.index)}
						<li data-testid="panel-message">
							<div class="text-[11px] font-mono text-gray-500">
								{message.index + 1}. role: {message.role}
							</div>
							<div class="mt-1 space-y-1">
								{#each message.parts as part, i (i)}
									<div
										class="border rounded px-2 py-1 {SOURCE_CLASSES[part.source] || ''}"
										data-testid="panel-part"
										data-source={part.source}
									>
										<div class="text-[10px] uppercase tracking-wide text-gray-500 flex justify-between gap-2">
											<span>{sourceLabel(part.role)}</span>
											<span>≈ {fmt(part.tokens)}</span>
										</div>
										{#if part.hiddenChars > 0}
											<details>
												<summary class="text-xs whitespace-pre-wrap text-gray-700 cursor-pointer">{part.excerpt}… <span class="text-gray-400">
													{$_('workshop.moreChars', {
														values: { count: fmt(part.hiddenChars) },
														default: `(${fmt(part.hiddenChars)} more characters)`
													})}</span></summary>
												<p class="text-xs whitespace-pre-wrap text-gray-700">{part.text.slice(part.excerpt.length)}</p>
											</details>
										{:else if !part.text.trim()}
											<p class="text-xs font-mono text-gray-400" data-testid="panel-whitespace">{visibleWhitespace(part.text)}</p>
										{:else}
											<p class="text-xs whitespace-pre-wrap text-gray-700">{part.text}</p>
										{/if}
									</div>
								{/each}
							</div>
						</li>
					{/each}
				</ol>
			</section>
		{/if}

		<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
			<!-- The next/outgoing request, exactly as sent (D1) -->
			{#if requestFrame?.request_body}
				<section class="border rounded p-3 bg-blue-50" data-testid="panel-request">
					<h4 class="text-xs font-semibold uppercase text-gray-500 flex items-center justify-between gap-2">
						<span>{phaseLabel(requestFrame.phase)}{#if requestFrame.round > 0}{' · '}{$_('workshop.toolRound', { values: { n: requestFrame.round + 1 }, default: `round ${requestFrame.round + 1}` })}{/if}</span>
						<span class="font-mono text-gray-400">{requestFrame.request_body.model}</span>
					</h4>
					<details class="border border-gray-200 rounded px-2 py-1 mt-1">
						<summary class="opacity-80">
							<code>messages ({requestFrame.request_body.messages.length})</code>
						</summary>
						<pre class="text-[10px] whitespace-pre-wrap mt-1 text-gray-600">{rawJson(requestFrame.request_body)}</pre>
					</details>
				</section>
			{/if}

			<!-- The last turn: what the model got, and which model answered -->
			{#if resultFrame?.request_body}
				<section class="border rounded p-3 bg-gray-50" data-testid="panel-result">
					<h4 class="text-xs font-semibold uppercase text-gray-500">
						{$_('workshop.resultPhase', { default: 'Sent to the model' })}
					</h4>
					{#if resultFrame.model}
						<p class="text-xs text-gray-600 mt-1" data-testid="panel-answered-by">
							{$_('workshop.answeredBy', {
								values: { model: resultFrame.model },
								default: `Answered by: ${resultFrame.model}`
							})}
						</p>
					{/if}
					<details class="border border-gray-200 rounded px-2 py-1 mt-1">
						<summary class="opacity-80">
							<code>messages ({resultFrame.request_body.messages.length})</code>
						</summary>
						<pre class="text-[10px] whitespace-pre-wrap mt-1 text-gray-600">{rawJson(resultFrame.request_body)}</pre>
					</details>
				</section>
			{/if}
		</div>

		<!-- Tool timeline -->
		{#if step >= 4 || toolEvents.length > 0}
			<div class="border rounded p-3 bg-gray-50">
				<h4 class="text-xs font-semibold uppercase text-gray-500">
					{$_('workshop.toolTimeline', { default: 'Tool calls' })}
				</h4>
				{#if toolEvents.length === 0}
					<p class="text-xs text-gray-400 mt-1">
						{$_('workshop.noToolActivity', { default: 'No tool activity yet.' })}
					</p>
				{:else}
					<div class="text-xs mt-1 space-y-1">
						{#each toolEvents as t, i (t.type + (t.name ?? '') + i)}
							{#if t.type === 'tool_malformed'}
								<p class="text-amber-700" data-testid="tool-malformed">⚠ {$_('workshop.toolMalformed', { default: 'The model began a tool call but did not follow the format, so its text was taken as the answer.' })}</p>
							{:else}
								<div class="flex gap-2 items-baseline" data-testid="tool-event">
									<span>{toolIcon(t)}</span>
									<span class="font-mono">{t.name || t.type}</span>
									{#if t.args}<span class="font-mono text-gray-500 truncate">{t.args}</span>{/if}
									{#if t.type === 'tool_done' && t.result !== undefined}<span class="font-mono text-gray-700">→ {t.result}</span>{/if}
									{#if t.success === false}
										<span class="text-red-500">{$_('workshop.toolFailed', { default: 'failed' })}</span>
									{/if}
								</div>
							{/if}
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
