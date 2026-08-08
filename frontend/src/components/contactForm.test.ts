import { describe, it, expect } from 'vitest';

import { buildContactFormUrl, PREFILL_TOKENS } from './contactForm';

const TEMPLATE = 'https://forms.office.com/Pages/ResponsePage.aspx?id=AB12'
    + '&r1f0c={organization}&r6b42={deployment_url}';

describe('building the operator registration-form URL', () => {
    it('substitutes the tokens the operator pasted', () => {
        const url = buildContactFormUrl(TEMPLATE, {
            organization: 'Township of Example',
            deployment_url: 'https://311.example.gov',
        });
        expect(url).toContain('r1f0c=Township%20of%20Example');
        expect(url).toContain('r6b42=https%3A%2F%2F311.example.gov');
    });

    it('encodes values so an ampersand cannot invent a second answer', () => {
        // The failure this prevents: "Smith & Sons" splitting into a new query
        // parameter, which lands as an answer to a question nobody asked.
        const url = buildContactFormUrl('https://forms.office.com/r/x?a={organization}', {
            organization: 'Smith & Sons',
        });
        expect(url).toBe('https://forms.office.com/r/x?a=Smith%20%26%20Sons');
    });

    it('leaves a token blank when the value is missing', () => {
        // An unanswered question, which the person then fills in. Not the string
        // "null", and not the token still sitting there in braces.
        const url = buildContactFormUrl(TEMPLATE, { organization: 'Example' });
        expect(url).toContain('r6b42=');
        expect(url).not.toContain('{deployment_url}');
        expect(url).not.toContain('null');
    });

    it('blanks a token it does not recognise rather than sending the word', () => {
        // A typo in .env must read as an unanswered question, not transmit
        // "contact_emial" to Microsoft as somebody's address.
        const url = buildContactFormUrl('https://forms.office.com/r/x?a={contact_emial}', {
            contact_email: 'clerk@example.gov',
        });
        expect(url).toBe('https://forms.office.com/r/x?a=');
    });

    it('sends the administrator only where the operator asked for them', () => {
        // The opt-in, as a test. A template with no contact tokens must not
        // carry the signed-in admin's name or address anywhere in it.
        const admin = { contact_name: 'Dana Clerk', contact_email: 'dana@example.gov' };
        const url = buildContactFormUrl(TEMPLATE, { organization: 'Example', ...admin });
        expect(url).not.toContain('Dana');
        expect(url).not.toContain('dana');
        expect(url).not.toContain('example.gov');
    });

    it('carries them when the operator did ask', () => {
        const url = buildContactFormUrl('https://forms.office.com/r/x?n={contact_name}', {
            contact_name: 'Dana Clerk',
        });
        expect(url).toBe('https://forms.office.com/r/x?n=Dana%20Clerk');
    });

    it('asks Forms for the frameable rendering when embedding', () => {
        expect(buildContactFormUrl(TEMPLATE, {}, { embed: true })).toContain('embed=true');
    });

    it('does not add a second embed parameter', () => {
        const already = 'https://forms.office.com/r/x?embed=true';
        expect(buildContactFormUrl(already, {}, { embed: true })).toBe(already);
    });

    it('adds the first query parameter with ? rather than &', () => {
        expect(buildContactFormUrl('https://forms.office.com/r/x', {}, { embed: true }))
            .toBe('https://forms.office.com/r/x?embed=true');
    });

    it('leaves the URL alone when not embedding', () => {
        expect(buildContactFormUrl('https://forms.office.com/r/x')).toBe('https://forms.office.com/r/x');
    });

    describe('what counts as no form at all', () => {
        // All of these reach an iframe src and an href, so each has to come back
        // empty -- the callers read '' as "unconfigured" and fall back to the
        // built-in form.
        it.each([
            ['unset', undefined],
            ['empty', ''],
            ['whitespace', '   '],
            ['not a URL', 'forms.office.com/r/x'],
            ['a javascript: scheme', 'javascript:alert(1)'],
            ['a data: scheme', 'data:text/html,<h1>hi'],
        ])('%s', (_label, value) => {
            expect(buildContactFormUrl(value as string | undefined)).toBe('');
        });
    });

    it('documents every token it substitutes', () => {
        // The list is what .env.example and COMPLIANCE.md describe; a token
        // added to one and not the other is the drift this catches.
        const values = Object.fromEntries(PREFILL_TOKENS.map(t => [t, `v-${t}`]));
        const template = 'https://forms.office.com/r/x?'
            + PREFILL_TOKENS.map((t, i) => `q${i}={${t}}`).join('&');
        const url = buildContactFormUrl(template, values);
        for (const token of PREFILL_TOKENS) expect(url).toContain(`v-${token}`);
    });
});
