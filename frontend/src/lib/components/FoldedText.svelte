<script>
	// src/lib/components/FoldedText.svelte
	// A long part of a request, folded after its first characters. Unfolding
	// continues the same text in place, so the excerpt and the rest read as
	// one: a <details> kept "… (n more characters)" in its summary and started
	// the rest on a new line, cutting words in two. The folded rest stays in
	// the page, hidden.

	import { _, locale } from '$lib/i18n';

	/** @type {{text: string, excerpt: string, hiddenChars: number}} */
	let { text, excerpt, hiddenChars } = $props();

	let open = $state(false);

	const more = $derived(Number(hiddenChars || 0).toLocaleString($locale || 'en'));
</script>

<p class="text-xs whitespace-pre-wrap text-gray-700">{excerpt}<span hidden={!open}>{text.slice(excerpt.length)}</span>{#if !open}…{/if}<button
		type="button"
		class="ml-1 text-gray-500 underline decoration-dotted hover:text-gray-700"
		aria-expanded={open}
		onclick={() => (open = !open)}
		data-testid="panel-fold"
	>{open
			? $_('workshop.showLess', { default: 'Show less' })
			: $_('workshop.moreChars', { values: { count: more }, default: `(${more} more characters)` })}</button></p>
