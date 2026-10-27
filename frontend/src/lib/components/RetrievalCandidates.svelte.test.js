// src/lib/components/RetrievalCandidates.svelte.test.js
import { describe, test, expect, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import { waitLocale } from 'svelte-i18n';
import { setupI18n } from '$lib/i18n';
import RetrievalCandidates from './RetrievalCandidates.svelte';
import { candidatesFrom } from './logic/workshopObservations.svelte.js';

beforeAll(async () => {
	setupI18n();
	await waitLocale();
});

/** A probe response as POST /workshop/api/probe returns it. */
const probe = {
	query: 'Què passa quan el vapor es refreda?',
	k: 2,
	candidate_count: 3,
	elapsed_ms: 41,
	embedding: { vendor: 'ollama', model: 'nomic-embed-text' },
	chunk_size: 1000,
	chunk_overlap: 200,
	results: [
		{ rank: 1, similarity: 0.8123, data: 'Es condensa en núvols.', source: 'aigua.md', selected: true, meta: { chunk_index: 1, chunk_count: 6 } },
		{ rank: 2, similarity: 0.71, data: 'Cau com a pluja.', source: 'aigua.md', selected: true, meta: {} },
		{ rank: 3, similarity: -0.05, data: 'Els volcans.', source: 'terra.md', selected: false, meta: {} }
	]
};

describe('candidatesFrom', () => {
	test('reads a probe response and a chat frame the same way', () => {
		const fromProbe = candidatesFrom(probe);
		const fromFrame = candidatesFrom({
			candidates: probe.results.map((r) => ({ rank: r.rank, score: r.similarity, text: r.data, source: r.source, selected: r.selected, meta: r.meta }))
		});
		expect(fromFrame).toEqual(fromProbe);
		expect(fromProbe[2]).toEqual({ rank: 3, score: -0.05, text: 'Els volcans.', source: 'terra.md', selected: false, meta: {} });
		expect(candidatesFrom(null)).toEqual([]);
	});
});

describe('RetrievalCandidates', () => {
	test('shows every candidate, marks the selected ones and where the cut fell', () => {
		render(RetrievalCandidates, { props: { search: probe, testid: 'probe' } });
		const rows = screen.getAllByTestId('probe-candidate');
		expect(rows.map((r) => r.dataset.selected)).toEqual(['true', 'true', 'false']);
		expect(rows[0]).toHaveTextContent('#1 · aigua.md · chunk 2 of 6');
		expect(rows[0]).toHaveTextContent('0.812');
		expect(rows[2]).toHaveTextContent('-0.050'); // similarity can be negative
		expect(screen.getByTestId('probe-cut')).toHaveTextContent('Only the 2 most similar go into the prompt');
		expect(screen.getByTestId('probe-info')).toHaveTextContent(
			'chunks of 1000 characters overlapping by 200 · embeddings: nomic-embed-text · 41 ms');
	});

	test('no results says so', () => {
		render(RetrievalCandidates, { props: { search: { query: 'x', results: [] }, testid: 'probe' } });
		expect(screen.getByTestId('probe-empty')).toBeInTheDocument();
	});
});
