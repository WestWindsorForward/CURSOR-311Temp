/**
 * Building the operator's registration-form URL, with what this deployment
 * already knows filled in.
 *
 * Microsoft Forms question ids are unique per form and look like
 * `r7a3c1f9e04b2...`. Nothing here can know them, and hard-coding a field
 * mapping would mean every operator edits this file. So the operator supplies
 * the whole thing: Forms has a "Get pre-filled URL" button that emits a URL
 * with the answers in the query string, and CONTACT_FORM_URL is that URL with
 * named tokens standing where the sample answers were.
 *
 *   CONTACT_FORM_URL=https://forms.office.com/Pages/ResponsePage.aspx?id=AB…\
 *     &r1f0c…={organization}&r6b42…={deployment_url}&r9de1…={version}
 *
 * The operator therefore decides which facts travel, by choosing which tokens
 * to paste -- which is the right place for that decision, because they are the
 * one who knows what their form asks and who reads the answers. A token they
 * do not use is a fact this application never sends.
 *
 * `{contact_name}`, `{contact_email}` and `{contact_role}` are the signed-in
 * administrator's own details, and they are personal data. They are supported
 * because retyping your own name and address is the most tedious part of the
 * form, and they are opt-in for the same reason everything else here is: they
 * travel only if the operator wrote the token. COMPLIANCE.md says so too.
 */

/** What a token may be replaced with. Absent and empty are the same thing:
 *  the answer arrives blank and the person fills it in. */
export interface PrefillValues {
    /** The township name from branding. */
    organization?: string | null;
    /** The address residents use, not wherever the admin happens to be. */
    deployment_url?: string | null;
    /** Build stamp, so an advisory can name the versions it applies to. */
    version?: string | null;
    /** The signed-in administrator. Personal data -- see the note above. */
    contact_name?: string | null;
    contact_email?: string | null;
    contact_role?: string | null;
}

/** Every token `buildContactFormUrl` understands, for documentation and tests. */
export const PREFILL_TOKENS: readonly (keyof PrefillValues)[] = [
    'organization', 'deployment_url', 'version',
    'contact_name', 'contact_email', 'contact_role',
];

/**
 * Substitute `{token}` placeholders and, for the embedded view, ask Forms for
 * its frameable rendering.
 *
 * Returns '' for anything unusable -- no template, or a scheme that is not
 * http(s). The callers treat '' as "no form configured", so a mistyped setting
 * degrades to the built-in form rather than putting a strange value into an
 * iframe `src`.
 */
export function buildContactFormUrl(
    template: string | null | undefined,
    values: PrefillValues = {},
    options: { embed?: boolean } = {},
): string {
    const raw = (template ?? '').trim();
    if (!raw) return '';

    let url: URL;
    try {
        url = new URL(raw);
    } catch {
        return '';
    }
    // Only the two schemes a form can actually be served over. Notably not
    // `javascript:`, which is what an iframe src must never be handed.
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';

    /* Substituted on the string rather than through URLSearchParams, because
     * the operator pasted a URL whose parameter *order and names* came from
     * Forms, and rebuilding it from parsed parts would re-encode and reorder
     * what Forms emitted. An unknown token becomes empty rather than staying
     * literal: `{contact_emial}` should read as an unanswered question, not
     * send the word "contact_emial" to Microsoft as somebody's address. */
    const substituted = raw.replace(/\{(\w+)\}/g, (_match, token: string) => {
        const value = (values as Record<string, unknown>)[token];
        return typeof value === 'string' || typeof value === 'number'
            ? encodeURIComponent(String(value))
            : '';
    });

    if (!options.embed) return substituted;

    // `embed=true` is what makes Forms serve the bare form rather than its full
    // page furniture. Appended only if the operator has not already added it,
    // and by concatenation rather than through searchParams so the parameters
    // Forms emitted keep the order and encoding it gave them.
    if (new URL(substituted).searchParams.has('embed')) return substituted;
    return `${substituted}${substituted.includes('?') ? '&' : '?'}embed=true`;
}
