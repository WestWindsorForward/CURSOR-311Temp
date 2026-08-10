/**
 * Client-side phone hygiene for the resident contact form.
 *
 * The backend stores the number KMS-envelope-encrypted and accepts any
 * string, so nothing here may be stricter than "a plausible phone number" --
 * a resident typing "+1 (555) 123-4567 ext. 12" or "(+44) 20 7946 0958" must
 * never be turned away. The job is only to keep obviously-not-a-phone
 * characters out of the field and to catch typo-level mistakes before the
 * value is encrypted and becomes unreadable to validation forever.
 */

/**
 * Drop characters no written phone number contains: punctuation like <, %, _
 * or quotes. Digits, separators, +, DTMF #/*, and letters all stay -- letters
 * because "ext." is how people write extensions, and because silently eating
 * keystrokes mid-word reads as a broken field; a word that isn't a number is
 * rejected by isValidPhone at submit instead, with an error they can read.
 */
export function filterPhoneInput(value: string): string {
    return value.replace(/[^0-9A-Za-z()+\-.,#*\s]/g, '');
}

/**
 * A filled-in value is acceptable when it carries 7-15 digits before any
 * extension (NANP local number up to full E.164) and any + marks a country
 * code -- at the start, or inside leading punctuation as in "(+44) ...",
 * never after a digit.
 */
export function isValidPhone(value: string): boolean {
    const trimmed = value.trim();
    if (!trimmed) return true;
    if (/[^0-9A-Za-z()+\-.,#*\s]/.test(trimmed)) return false;
    const plusCount = (trimmed.match(/\+/g) || []).length;
    if (plusCount > 1) return false;
    if (plusCount === 1 && /\d/.test(trimmed.slice(0, trimmed.indexOf('+')))) return false;
    // Digits before any extension marker ("x", "ext", "#") are the number proper.
    const main = trimmed.split(/[xX#]/)[0];
    const digits = (main.match(/\d/g) || []).length;
    return digits >= 7 && digits <= 15;
}
