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
	// Layout (T17): a header that stays put (which request this is, how much
	// of what the model can read it uses, and what it is made of), then one
	// row per kind of content in the order the model reads it. All earlier
	// messages are a single row, so the panel no longer grows with the
	// conversation. Rows are <details>: folded text stays in the page, and
	// only one row opens at a time.
	//
	// Props (the page owns all of them; this component only renders):
	//   - obsRequest — the next/outgoing request: phase "preview" (built from
	//                  the current design, nothing sent yet) or "request" (sent,
	//                  the model is still answering)
	//   - obsData    — the "result" frame of the last turn, with the model LAMB
	//                  reported (or of an earlier one, see `pinned`)
	//   - document   — step 2: the server's view of the attached document
	//   - probe      — step 3: a test question and what it retrieved
	//   - toolEvents — tool timeline entries
	//   - step, ragMode, kbAvailable (the A/B badge only means something with
	//     the step-2 document connected in step 3)
	//   - pinned     — {question} when obsData is an earlier message's request,
	//                  opened from its receipt in the chat; onUnpin goes back

	import { _, locale } from '$lib/i18n';
	import {
		budgetSummary,
		contextRows,
		formatBytes,
		SYSTEM_ROWS
	} from './logic/workshopObservations.svelte.js';
	import FoldedText from './FoldedText.svelte';
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
	 *   kbAvailable?: boolean,
	 *   pinned?: {question: string} | null,
	 *   onUnpin?: () => void
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
		kbAvailable = true,
		pinned = null,
		onUnpin
	} = $props();

	/** The frame describing the next/outgoing request. */
	let requestFrame = $derived(
		obsRequest || (obsData && obsData.phase !== 'result' ? obsData : null)
	);
	/** The frame with what the model finally got, and who answered. */
	let resultFrame = $derived(obsData?.phase === 'result' ? obsData : null);
	/** The frame the rows and the budget describe: the newest one. */
	let current = $derived(requestFrame || resultFrame);

	const budget = $derived(budgetSummary(current));
	const docInContext = $derived(budget?.document || null);

	/** On, the excerpts depend on the message, so a preview cannot have them (T15). */
	const excerptsLater = $derived(
		current?.phase === 'preview' && current?.rag_mode === 'on' && !!document
	);

	const rows = $derived.by(() => {
		const out = contextRows(current);
		// Step 4 is about tools: say so when there are none.
		if (step === 4 && current && !out.some((r) => r.key === 'tools')) {
			const at = out.findIndex((r) => !SYSTEM_ROWS.includes(r.key));
			out.splice(at < 0 ? out.length : at, 0, { key: 'tools', group: 'system', tokens: 0, parts: [] });
		}
		return out;
	});

	const rowsByKey = $derived(Object.fromEntries(rows.map((r) => [r.key, r])));

	// Groups list row keys, not rows: a new frame then leaves a row the
	// student opened open (its `open` only follows the step).
	const groups = $derived(
		['system', 'conversation']
			.map((key) => {
				const own = rows.filter((r) => r.group === key);
				return { key, keys: own.map((r) => r.key), tokens: own.reduce((n, r) => n + r.tokens, 0) };
			})
			.filter((g) => g.keys.length || (g.key === 'conversation' && excerptsLater))
	);

	/** What the request is made of, as shares of its parts (the coloured bar). */
	const makeup = $derived.by(() => {
		const total = rows.reduce((n, r) => n + r.tokens, 0);
		if (!total) return [];
		return rows
			.filter((r) => r.tokens > 0)
			.map((r) => ({ key: r.key, tokens: r.tokens, share: (r.tokens / total) * 100 }));
	});

	/** Earlier messages grow every turn; past this share the header says so. */
	const HISTORY_SHARE_NOTE = 40;
	const historyShare = $derived(Math.round(makeup.find((m) => m.key === 'history')?.share || 0));

	/** The row that opens by itself: the one the current step is about. */
	const openRow = $derived(
		/** @type {Record<number, string>} */ ({ 1: 'instructions', 2: 'document', 4: 'tools' })[step] ?? ''
	);

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

	/** The colour of each row, in its dot and in the bar. */
	/** @type {Record<string, string>} */
	const ROW_COLORS = {
		instructions: 'bg-blue-500',
		document: 'bg-green-600',
		tools: 'bg-amber-500',
		history: 'bg-gray-400',
		retrieval: 'bg-purple-500',
		user: 'bg-sky-500',
		toolround: 'bg-orange-600'
	};

	/** @param {string} source - a segment source, a row, or a tool round's call/result */
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
			history_result: 'Earlier tool result sent back by the workshop',
			toolround: 'Tool calls and results'
		};
		return $_(`workshop.source_${source}`, { default: defaults[source] || source });
	}

	/**
	 * The second line of a row: what is in it, in a few words.
	 * @param {any} row
	 */
	function rowDetail(row) {
		switch (row.key) {
			case 'document':
				return row.name || '';
			case 'tools':
				return row.parts.length ? '' : $_('workshop.rowNoTools', { default: 'None' });
			case 'history':
				return $_('workshop.rowHistory', {
					values: { messages: row.count, turns: row.turns.length },
					default: `${row.count} messages · ${row.turns.length} earlier turns`
				});
			case 'retrieval':
				return $_('workshop.rowRetrieval', {
					values: { count: row.count },
					default: `${row.count} excerpts, added before your message`
				});
			case 'user':
				return row.text;
			case 'toolround':
				return $_('workshop.rowToolRounds', {
					values: { count: row.count },
					default: `${row.count} rounds of this message`
				});
			default:
				return '';
		}
	}

	/** @param {string} key */
	function groupTitle(key) {
		return key === 'system'
			? $_('workshop.groupSystem', { default: 'System message' })
			: $_('workshop.groupConversation', { default: 'Conversation' });
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

	/** @param {any} frame */
	function roundLabel(frame) {
		return frame?.round > 0
			? ` · ${$_('workshop.toolRound', { values: { n: frame.round + 1 }, default: `round ${frame.round + 1}` })}`
			: '';
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

	const makeupLabel = $derived(
		makeup.map((m) => `${sourceLabel(m.key)} ${Math.round(m.share)}%`).join(', ')
	);
</script>

{#snippet partBox(/** @type {any} */ p)}
	<div
		class="border rounded px-2 py-1 {SOURCE_CLASSES[p.source] || ''}"
		data-testid="panel-part"
		data-source={p.source}
	>
		<div class="text-[10px] uppercase tracking-wide text-gray-500 flex justify-between gap-2">
			<span>{sourceLabel(p.role)}</span>
			<span>≈ {fmt(p.tokens)}</span>
		</div>
		{#if p.hiddenChars > 0}
			<FoldedText text={p.text} excerpt={p.excerpt} hiddenChars={p.hiddenChars} />
		{:else if !p.text.trim()}
			<p class="text-xs font-mono text-gray-500" data-testid="panel-whitespace">{visibleWhitespace(p.text)}</p>
		{:else}
			<p class="text-xs whitespace-pre-wrap text-gray-700">{p.text}</p>
		{/if}
	</div>
{/snippet}

<div class="flex h-full min-h-0 flex-col">
	<!-- Header: which request this is, and what it is made of -->
	<div class="border-b px-4 pt-4 pb-3 space-y-3">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0">
				<h2 class="text-sm font-semibold">
					{$_('workshop.observabilityTitle', { default: 'What your assistant received' })}
				</h2>
				{#if current && !pinned}
					<p class="text-xs text-gray-500" data-testid="panel-phase">
						{current === resultFrame
							? $_('workshop.resultPhase', { default: 'Sent to the model' })
							: phaseLabel(current.phase) + roundLabel(current)}
					</p>
				{/if}
				{#if resultFrame?.model && current === resultFrame}
					<p class="text-xs text-gray-500" data-testid="panel-answered-by">
						{$_('workshop.answeredBy', {
							values: { model: resultFrame.model },
							default: `Answered by: ${resultFrame.model}`
						})}
					</p>
				{/if}
			</div>
			<!-- A/B badge: which mode this turn ran in (D3) -->
			{#if step === 5 && current && kbAvailable}
				<span
					class="shrink-0 px-2 py-0.5 rounded-full text-xs {ragMode === 'off'
						? 'bg-amber-100 text-amber-800'
						: 'bg-green-100 text-green-800'}"
					data-testid="panel-rag-mode"
				>
					{ragMode === 'off'
						? $_('workshop.ragOffBadge', { default: 'Knowledge base off: the whole document' })
						: $_('workshop.ragOnBadge', { default: 'Knowledge base on: excerpts only' })}
				</span>
			{/if}
		</div>

		{#if pinned}
			<div class="flex items-center gap-2 rounded bg-blue-50 px-3 py-2 text-xs text-blue-900" data-testid="panel-pinned">
				<span class="min-w-0 flex-1 truncate">
					{$_('workshop.panelPinned', {
						values: { question: pinned.question },
						default: `An earlier message: “${pinned.question}”`
					})}
				</span>
				<button
					class="shrink-0 rounded border border-blue-300 bg-white px-2 py-1 font-medium hover:bg-blue-100"
					onclick={() => onUnpin && onUnpin()}
					data-testid="panel-unpin"
				>{$_('workshop.panelUnpin', { default: 'Back to the latest' })}</button>
			</div>
		{/if}

		<!-- How much of what the model can read this request uses (T14), and what it is made of (T17) -->
		{#if budget}
			<section data-testid="panel-budget">
				<div class="flex items-baseline justify-between gap-2 text-xs text-gray-600">
					<span data-testid="panel-budget-used">
						{$_('workshop.budgetUsed', {
							values: { used: fmt(budget.used), limit: fmt(budget.limit) },
							default: `About ${fmt(budget.used)} of ${fmt(budget.limit)} tokens`
						})}
					</span>
					<span class="tabular-nums">{budget.percent}%</span>
				</div>
				<div class="mt-1 h-1.5 rounded bg-gray-200 overflow-hidden" aria-hidden="true">
					<div
						class="h-1.5 {budget.percent >= 90 ? 'bg-amber-500' : 'bg-blue-500'}"
						style="width: {budget.percent}%"
					></div>
				</div>
				{#if makeup.length}
					<div class="mt-2 flex h-3 gap-0.5 overflow-hidden rounded" role="img" aria-label={makeupLabel} data-testid="panel-makeup">
						{#each makeup as m (m.key)}
							<div
								class="h-full min-w-[3px] {ROW_COLORS[m.key]}"
								style="flex: {m.share} 0 0"
								title="{sourceLabel(m.key)}: ≈ {fmt(m.tokens)}"
							></div>
						{/each}
					</div>
				{/if}
				{#if budget.cap}
					<!-- Step 5's experiment (T18): say so, or the student takes the cap for the model's size -->
					<p class="text-[11px] text-amber-800 mt-1" data-testid="panel-budget-capped">
						{$_('workshop.budgetCapped', {
							values: { tokenizer: budget.tokenizer, cap: fmt(budget.cap), context: fmt(budget.contextTokens) },
							default: `Estimated (${budget.tokenizer}). In this experiment the model may read only ${fmt(budget.cap)} tokens; it could read ${fmt(budget.contextTokens)}.`
						})}
					</p>
				{:else}
					<p class="text-[11px] text-gray-500 mt-1">
						{$_('workshop.budgetEstimate', {
							values: { tokenizer: budget.tokenizer, context: fmt(budget.contextTokens) },
							default: `Estimated (${budget.tokenizer}). The model reads ${fmt(budget.contextTokens)} tokens; part is kept free for its answer.`
						})}
					</p>
				{/if}
			</section>
		{/if}

		{#if docInContext?.truncated}
			<p class="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid="panel-document-truncated">
				{$_('workshop.documentTruncated', {
					values: { kept: fmt(docInContext.kept_chars), total: fmt(docInContext.total_chars) },
					default: `Too long: only the first ${fmt(docInContext.kept_chars)} of ${fmt(docInContext.total_chars)} characters fit. The model never sees the rest.`
				})}
			</p>
		{/if}
		{#if budget && budget.historyDropped > 0}
			<p class="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid="panel-history-dropped">
				{$_('workshop.historyDropped', {
					values: { count: budget.historyDropped },
					default: `${budget.historyDropped} of the oldest messages were left out to make room.`
				})}
			</p>
		{/if}
		{#if budget && budget.toolResultsElided > 0}
			<p class="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid="panel-results-elided">
				{$_('workshop.toolResultsElided', {
					values: { count: budget.toolResultsElided },
					default: `${budget.toolResultsElided} earlier tool results were replaced by a short note to make room.`
				})}
			</p>
		{/if}
		{#if historyShare >= HISTORY_SHARE_NOTE}
			<p class="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid="panel-history-share">
				{$_('workshop.historyShare', {
					values: { percent: historyShare },
					default: `Earlier messages are ${historyShare}% of what is sent, and they grow with every message. Start a new conversation to clear them.`
				})}
			</p>
		{/if}
	</div>

	<!-- Body: one row per kind of content, in the order the model reads it -->
	<div class="flex-1 min-h-0 overflow-y-auto px-2 py-3 space-y-4">
		{#if !hasAnything}
			<div class="mx-2 border rounded p-4 bg-gray-50 text-sm text-gray-500">
				{$_('workshop.panelEmpty', { default: 'Nothing to observe yet — the panel fills in as you build.' })}
			</div>
		{:else}
			<!-- Step 3: what the student's test question retrieved, every candidate -->
			{#if step === 3 && probe}
				<div class="mx-2">
					<RetrievalCandidates search={probe} testid="panel-probe" />
				</div>
			{/if}

			{#if groups.length}
				<div class="space-y-4" data-testid="panel-segments">
					{#each groups as g (g.key)}
						<section>
							<h3 class="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
								{groupTitle(g.key)}{#if g.tokens}{' · ≈ '}{fmt(g.tokens)}{/if}
							</h3>
							{#each g.keys as key (key)}
								{@const row = rowsByKey[key]}
								<details
									name="panel-rows"
									class="group/row rounded-md"
									open={openRow === key}
									data-testid="panel-row"
									data-row={key}
								>
									<summary
										class="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-md px-2 py-1.5 hover:bg-gray-50 group-open/row:bg-gray-50 [&::-webkit-details-marker]:hidden"
									>
										<span class="size-2.5 shrink-0 rounded-sm {ROW_COLORS[row.key]}" aria-hidden="true"></span>
										<span class="min-w-0 flex-1">
											<span class="block text-sm font-medium text-gray-900">{sourceLabel(row.key)}</span>
											{#if rowDetail(row)}
												<span class="block truncate text-xs text-gray-500">{rowDetail(row)}</span>
											{/if}
										</span>
										<span class="shrink-0 text-xs tabular-nums text-gray-500">≈ {fmt(row.tokens)}</span>
										<svg
											class="size-4 shrink-0 text-gray-400 transition-transform group-open/row:rotate-90"
											viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
											stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
										><path d="M9 6l6 6-6 6"></path></svg>
									</summary>

									<div class="ml-5 mr-1 mt-1 mb-3 space-y-2">
										{#if row.key === 'document' && document}
											<!-- The document goes into the context whole (T12) -->
											<div class="text-xs space-y-1" data-testid="panel-document">
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
											</div>
										{/if}

										{#if row.key === 'tools'}
											<!-- Tools are text the model reads, not a separate channel (T4) -->
											<div class="space-y-1" data-testid="panel-tools">
												{#each row.parts as p, i (i)}
													{@render partBox(p)}
												{:else}
													<p class="text-xs text-gray-500">
														{$_('workshop.toolsTextEmpty', { default: 'No tools: the assistant can only answer with text.' })}
													</p>
												{/each}
											</div>
										{:else if row.key === 'history'}
											<ol class="space-y-1">
												{#each row.turns as turn, t (t)}
													<li>
														<details class="group/turn rounded border border-gray-200">
															<summary class="flex cursor-pointer list-none items-center gap-2 px-2 py-1.5 text-xs [&::-webkit-details-marker]:hidden">
																<span class="shrink-0 text-gray-500">
																	{$_('workshop.historyTurn', { values: { n: t + 1 }, default: `Turn ${t + 1}` })}
																</span>
																<span class="min-w-0 flex-1 truncate text-gray-700">{turn.question}</span>
																<span class="shrink-0 tabular-nums text-gray-500">≈ {fmt(turn.tokens)}</span>
															</summary>
															<div class="space-y-2 px-2 pb-2">
																{#each turn.messages as message (message.index)}
																	<div>
																		<div class="text-[11px] font-mono text-gray-500">role: {message.role}</div>
																		<div class="mt-1 space-y-1">
																			{#each message.parts as p, i (i)}
																				{@render partBox(p)}
																			{/each}
																		</div>
																	</div>
																{/each}
															</div>
														</details>
													</li>
												{/each}
											</ol>
										{:else if row.key === 'retrieval'}
											<!-- A chat turn's search of the document (step 5) -->
											{#if current?.retrieval}
												<RetrievalCandidates search={current.retrieval} testid="panel-retrieval" />
											{/if}
											<details class="rounded border border-gray-200">
												<summary class="cursor-pointer px-2 py-1.5 text-xs text-gray-600">
													{$_('workshop.asSent', { default: 'The text exactly as it is sent' })}
												</summary>
												<div class="space-y-1 px-2 pb-2">
													{#each row.parts as p, i (i)}
														{@render partBox(p)}
													{/each}
												</div>
											</details>
										{:else}
											{#each row.parts as p, i (i)}
												{@render partBox(p)}
											{/each}
										{/if}
									</div>
								</details>
							{/each}

							{#if g.key === 'conversation' && excerptsLater}
								<!-- On, the excerpts depend on the message, so a preview cannot have them (T15) -->
								<div class="mx-2 mt-1 flex gap-3 rounded-md border border-dashed border-purple-300 px-2 py-2" data-testid="panel-excerpts-later">
									<span class="mt-1 size-2.5 shrink-0 rounded-sm {ROW_COLORS.retrieval}" aria-hidden="true"></span>
									<p class="text-xs text-purple-900">
										{$_('workshop.excerptsLater', { default: 'Your document is not sent whole: when you send a message, the excerpts most similar to it are added before it.' })}
									</p>
								</div>
							{/if}
						</section>
					{/each}
				</div>
			{/if}

			<!-- Tool timeline: this turn's calls as they happen -->
			{#if toolEvents.length > 0}
				<section class="mx-2 border rounded p-3 bg-gray-50" data-testid="panel-tool-timeline">
					<h4 class="text-xs font-semibold uppercase text-gray-500">
						{$_('workshop.toolTimeline', { default: 'Tool calls' })}
					</h4>
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
										<span class="text-red-600">{$_('workshop.toolFailed', { default: 'failed' })}</span>
									{/if}
								</div>
							{/if}
						{/each}
					</div>
				</section>
			{/if}

			<!-- The request exactly as sent (D1): for whoever wants the JSON itself -->
			{#if requestFrame?.request_body || resultFrame?.request_body}
				<details class="mx-2 rounded border border-gray-200" data-testid="panel-raw">
					<summary class="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600">
						{$_('workshop.rawRequest', { default: 'The request as JSON' })}
					</summary>
					<div class="space-y-3 px-3 pb-3">
						{#if requestFrame?.request_body}
							<section data-testid="panel-request">
								<h4 class="text-xs font-semibold uppercase text-gray-500 flex items-center justify-between gap-2">
									<span>{phaseLabel(requestFrame.phase)}{roundLabel(requestFrame)}</span>
									<span class="font-mono normal-case text-gray-500">{requestFrame.request_body.model}</span>
								</h4>
								<p class="mt-1 font-mono text-xs text-gray-600">messages ({requestFrame.request_body.messages.length})</p>
								<pre class="text-[10px] whitespace-pre-wrap mt-1 text-gray-600">{rawJson(requestFrame.request_body)}</pre>
							</section>
						{/if}
						{#if resultFrame?.request_body}
							<section data-testid="panel-result">
								<h4 class="text-xs font-semibold uppercase text-gray-500">
									{$_('workshop.resultPhase', { default: 'Sent to the model' })}
								</h4>
								<p class="mt-1 font-mono text-xs text-gray-600">messages ({resultFrame.request_body.messages.length})</p>
								<pre class="text-[10px] whitespace-pre-wrap mt-1 text-gray-600">{rawJson(resultFrame.request_body)}</pre>
							</section>
						{/if}
					</div>
				</details>
			{/if}
		{/if}
	</div>
</div>
