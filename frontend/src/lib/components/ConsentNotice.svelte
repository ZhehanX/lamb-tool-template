<script>
	// src/lib/components/ConsentNotice.svelte
	// Shown before the wizard until the student accepts (the old branch's
	// consent page, decisions C8/C17/C20, moved into the SPA). It says what the
	// workshop does with the work, where it goes — the tool's server, LAMB
	// (which may log the start of each message) and the model — how long it is
	// kept, and what the student can do instead (plan Q6).

	import { _ } from '$lib/i18n';

	/**
	 * @type {{
	 *   consent: {version: string, model_note?: string, retention_days?: number | null},
	 *   onAccept?: () => void,
	 *   accepting?: boolean
	 * }}
	 */
	let { consent, onAccept, accepting = false } = $props();
</script>

<div class="max-w-2xl mx-auto border rounded-lg p-6 bg-white shadow-sm space-y-4" data-testid="consent">
	<h2 class="text-xl font-bold">{$_('workshop.consent_title', { default: 'Before you start' })}</h2>
	<p class="text-gray-700">
		{$_('workshop.consent_intro', { default: 'This activity asks you to build a small AI assistant. Please read how your work is used before you continue.' })}
	</p>

	<section>
		<h3 class="text-sm font-semibold uppercase text-gray-500">{$_('workshop.consent_purposeHeading', { default: 'What this activity does with your work' })}</h3>
		<ul class="list-disc pl-5 text-sm text-gray-700 space-y-1 mt-1">
			<li>{$_('workshop.consent_purposeBuild', { default: 'You build an assistant: the instructions you write and the text of the document you attach are stored so your assistant can use them.' })}</li>
			<li>{$_('workshop.consent_purposeTest', { default: 'You test it in a chat: your messages and the answers are stored with your activity, so you can review them and your teacher can see your progress.' })}</li>
			<li>{$_('workshop.consent_purposeEvaluate', { default: 'When you submit, your design, conversation and reflection are sent to the model to produce formative feedback. It is a suggestion: your teacher decides the grade.' })}</li>
		</ul>
	</section>

	<section>
		<h3 class="text-sm font-semibold uppercase text-gray-500">{$_('workshop.consent_whereHeading', { default: 'Where your work goes' })}</h3>
		<ul class="list-disc pl-5 text-sm text-gray-700 space-y-1 mt-1">
			<li>{$_('workshop.consent_whereTool', { default: 'This workshop tool stores your work on its own server.' })}</li>
			<li>{$_('workshop.consent_whereLamb', { default: 'Every message is sent to LAMB, the AI platform that runs the model. LAMB may record the beginning of each message in its technical logs.' })}</li>
			<li>{$_('workshop.consent_whereKb', { default: 'So that it can be searched in step 3, the text of your document is also kept in the workshop’s knowledge base server, split into excerpts. It is deleted from there when you remove the document.' })}</li>
		</ul>
	</section>

	<section>
		<h3 class="text-sm font-semibold uppercase text-gray-500">{$_('workshop.consent_modelHeading', { default: 'Which model sees your work' })}</h3>
		<p class="text-sm text-gray-700 mt-1" data-testid="consent-model">
			{consent.model_note || $_('workshop.consent_modelUnknown', { default: 'Your teacher has not described the model yet. Ask before you continue if it matters to you.' })}
		</p>
	</section>

	<section>
		<h3 class="text-sm font-semibold uppercase text-gray-500">{$_('workshop.consent_retentionHeading', { default: 'How long it is kept' })}</h3>
		<p class="text-sm text-gray-700 mt-1" data-testid="consent-retention">
			{#if consent.retention_days}
				{$_('workshop.consent_retentionDays', {
					values: { days: consent.retention_days },
					default: `Your work is deleted automatically after ${consent.retention_days} days without use. Your teacher can also delete it at any time.`
				})}
			{:else}
				{$_('workshop.consent_retentionManual', { default: 'Your work is kept until your teacher deletes it.' })}
			{/if}
		</p>
	</section>

	<section>
		<h3 class="text-sm font-semibold uppercase text-gray-500">{$_('workshop.consent_privacyHeading', { default: 'Your choices' })}</h3>
		<p class="text-sm text-gray-700 mt-1">
			{$_('workshop.consent_privacyBody', { default: 'You can take part without uploading a personal document: any course material works. If you would rather not take part, tell your teacher instead of continuing.' })}
		</p>
	</section>

	<button
		class="w-full bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
		disabled={accepting}
		onclick={() => onAccept && onAccept()}
		data-testid="consent-accept"
	>{$_('workshop.consent_button', { default: 'I understand — start the activity' })}</button>
	<p class="text-xs text-gray-500 text-center">
		{$_('workshop.consent_declineHint', { default: 'If you do not agree, close this page and tell your teacher.' })}
	</p>
</div>
