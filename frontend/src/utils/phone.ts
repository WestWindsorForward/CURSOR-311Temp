/**
 * Client-side phone hygiene for the resident contact form.
 *
 * The backend stores the number KMS-envelope-encrypted and accepts any
 * string, so nothing here may be stricter than "a plausible phone number" --
 * a resident typing "+1 (555) 123-4567 ext. 12" or "(+44) 20 7946 0958" must
 * never be turned away. The job is only to keep obviously-not-a-phone
 * characters out of the field and to catch typo-level mistakes before the
 * value is encrypted and becomes unreadable to validation forever.
 *
 * The portal offers 100+ languages, so "digit" and "letter" here mean the
 * Unicode categories, not ASCII: a resident writing ٥٥٥ ١٢٣ ٤٥٦٧ or an
 * extension as "доб. 12" is typing a phone number, and an ASCII allowlist
 * would delete it keystroke by keystroke as they went.
 */

/** The one definition of a character a written phone number may contain. */
const DISALLOWED = /[^\p{L}\p{N}()+\-.,#*\s]/u;
const DISALLOWED_GLOBAL = new RegExp(DISALLOWED.source, 'gu');

/**
 * Drop characters no written phone number contains: symbols and punctuation
 * like <, %, _, @ or quotes, and emoji. Digits and letters in any script,
 * separators, +, and DTMF #/* all stay -- letters because "ext." (or "доб.")
 * is how people write extensions and because "1-800-GOT-JUNK" is a real
 * number, and because silently eating keystrokes mid-word reads as a broken
 * field; a word that isn't a number is rejected by isValidPhone at submit
 * instead, with an error they can read.
 */
export function filterPhoneInput(value: string): string {
    return value.replace(DISALLOWED_GLOBAL, '');
}

/**
 * A filled-in value is acceptable when it carries 7-15 digits before any
 * extension (NANP local number up to full E.164) and any + marks a country
 * code -- at the start, or inside leading punctuation as in "(+44) ...",
 * never after a digit.
 *
 * Vanity numbers count too: "1-800-GOT-JUNK" is dialled on the keypad, so
 * letters are dialable characters as long as the value carries at least one
 * digit -- otherwise "call me maybe" would sail through. Anything the filter
 * lets a resident type has to be submittable, or the field is a trap.
 */
export function isValidPhone(value: string): boolean {
    const trimmed = value.trim();
    if (!trimmed) return true;
    if (DISALLOWED.test(trimmed)) return false;
    const plusCount = (trimmed.match(/\+/g) || []).length;
    if (plusCount > 1) return false;
    if (plusCount === 1 && /\p{Nd}/u.test(trimmed.slice(0, trimmed.indexOf('+')))) return false;
    // Digits before any extension marker ("x", "ext", "#") are the number proper.
    const main = trimmed.split(/[xX#]/)[0];
    const digits = (main.match(/\p{Nd}/gu) || []).length;
    if (digits >= 7 && digits <= 15) return true;
    const letters = (main.match(/\p{L}/gu) || []).length;
    return digits >= 1 && digits + letters >= 7 && digits + letters <= 15;
}
