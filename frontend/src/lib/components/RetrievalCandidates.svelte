<script>
	// src/lib/components/RetrievalCandidates.svelte
	// What a search of the student's document found (plan Phase 8, G7, T15):
	// every candidate with its similarity and source, the ones that went into
	// the prompt marked, and a line where the top-k cut fell. Below, how the
	// document was split and embedded, and how long the search took. Used for
	// the step-3 test question and for the excerpts of a chat turn.

	import { _, locale } from '$lib/i18n';
	import { candidatesFrom } from './logic/workshopObservations.svelte.js';

	/**
	 * @type {{
	 *   search: any,
	 *   testid?: string
	 * }}
	 *   search: a probe response, or a chat frame's `retrieval`
	 */
	let { search, testid = 'retrieval' } = $props();

	const items = $derived(candidatesFrom(search));
	const k = $derived(search?.k ?? items.filter((c) => c.selected).length);
	const embedding = $derived(search?.embedding || {});

	/** @param {number} value */
	function score(value) {
		return Number(value).toLocaleString($locale || 'en', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
	}
</script>

<section class="border rounded p-3 bg-gray-50" data-testid={testid}>
	<h4 class="text-xs font-semibold uppercase text-gray-500">
		{$_('workshop.candidatesTitle', { default: 'What the search found in your document' })}
	</h4>
	{#if search?.query}
		<p class="text-xs mt-1 text-gray-600">
			{$_('workshop.probeQuestion', { values: { query: search.query }, default: `Question: “${search.query}”` })}
		</p>
	{/if}
	{#if items.length === 0}
		<p class="text-xs text-amber-700 mt-2" data-testid="{testid}-empty">
			{$_('workshop.probeNoHits', { default: 'No excerpt matched this question. Try different words.' })}
		</p>
	{:else}
		<ol class="text-xs mt-2 space-y-2">
			{#each items as c, i (c.rank)}
				{#if i === k && i > 0}
					<li class="text-center text-[11px] text-gray-500 border-t-2 border-dashed border-gray-400 pt-1" data-testid="{testid}-cut">
						{$_('workshop.cutLine', { values: { k }, default: `Only the ${k} most similar go into the prompt` })}
					</li>
				{/if}
				<li
					class="border rounded px-2 py-1 {c.selected ? 'bg-purple-50 border-purple-300' : 'bg-white opacity-60'}"
					data-testid="{testid}-candidate"
					data-selected={c.selected}
				>
					<div class="flex justify-between gap-2 text-[11px] text-gray-500">
						<span>#{c.rank} · {c.source}{#if c.meta.chunk_index !== undefined}{' · '}{$_('workshop.chunkOf', {
							values: { n: c.meta.chunk_index + 1, total: c.meta.chunk_count },
							default: `chunk ${c.meta.chunk_index + 1} of ${c.meta.chunk_count}`
						})}{/if}</span>
						<span class="font-mono" title={$_('workshop.similarityHint', { default: 'Similarity: 1 means same direction, 0 unrelated' })}>{score(c.score)}</span>
					</div>
					<p class="mt-1 whitespace-pre-wrap text-gray-700 max-h-32 overflow-y-auto">{c.text}</p>
				</li>
			{/each}
		</ol>
	{/if}
	{#if search?.chunk_size}
		<p class="text-[11px] text-gray-400 mt-2" data-testid="{testid}-info">
			{$_('workshop.retrievalInfo', {
				values: {
					size: search.chunk_size,
					overlap: search.chunk_overlap,
					model: embedding.model || '?',
					ms: search.elapsed_ms ?? '?'
				},
				default: `Split into chunks of ${search.chunk_size} characters overlapping by ${search.chunk_overlap} · embeddings: ${embedding.model || '?'} · ${search.elapsed_ms ?? '?'} ms`
			})}
		</p>
	{/if}
</section>
