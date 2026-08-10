import { useState } from 'react';
import { Eye, EyeOff, Lock, CheckCircle, AlertCircle } from 'lucide-react';

type FieldKind = 'url' | 'email' | 'json' | 'auto';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Infer what a field should contain from its label, so we can give a gentle
 * format hint without every caller having to declare a type. */
function inferKind(label: string): FieldKind {
    const l = label.toLowerCase();
    if (l.includes('json')) return 'json';
    if (l.includes('email')) return 'email';
    if (l.includes('url') || l.includes('endpoint') || l.includes('domain')
        || l.includes('issuer') || l.includes('address')) return 'url';
    return 'auto';
}

/** Advisory validation — returns null (no hint), or {ok, msg}. Never blocks. */
function checkValue(kind: FieldKind, raw: string): { ok: boolean; msg: string } | null {
    const v = raw.trim();
    if (!v) return null;
    if (kind === 'url') {
        return /^https?:\/\/.+/i.test(v)
            ? { ok: true, msg: 'Looks like a valid web address' }
            : { ok: false, msg: 'Web addresses usually start with https://' };
    }
    if (kind === 'email') {
        return EMAIL_RE.test(v)
            ? { ok: true, msg: 'Looks like a valid email' }
            : { ok: false, msg: 'This doesn’t look like an email address yet' };
    }
    if (kind === 'json') {
        try { JSON.parse(v); return { ok: true, msg: 'Valid JSON' }; }
        catch { return { ok: false, msg: 'This isn’t valid JSON yet — paste the whole key file' }; }
    }
    return null;
}

/** Something wrong with a pasted value that we can name and fix for them. */
export interface PasteProblem {
    /** Shown to the clerk. Says what was found, not what to do about it. */
    label: string;
    /** The value with the problem removed. */
    fixed: string;
}

// Zero-width and non-breaking characters. These arrive from copying out of a
// PDF, a Word doc, or a vendor's docs page, and they are the worst class of
// paste error because the value looks exactly right on screen and the provider
// rejects it with a generic "invalid credentials".
const INVISIBLE_RE = /[\u200B-\u200D\uFEFF\u00A0\u2060]/g;

// Curly quotes, from the same sources.
const SMART_QUOTES = /^[\u2018\u2019\u201C\u201D'"`]+|[\u2018\u2019\u201C\u201D'"`]+$/g;

/** Find the one thing most likely wrong with a pasted credential.
 *
 * Deliberately one at a time and deliberately opt-in: the field never rewrites
 * what someone typed on its own. A credential is the one input where silently
 * "helping" is dangerous -- if we strip something that was genuinely part of the
 * key, the failure is a confusing auth error days later. So this names what it
 * found and offers a button.
 *
 * Ordered by how badly each one misleads. Invisible characters first, because
 * they are undetectable by eye; the rest at least look wrong on inspection.
 */
export function diagnosePaste(raw: string): PasteProblem | null {
    if (!raw) return null;

    if (INVISIBLE_RE.test(raw)) {
        return {
            label: 'Contains invisible characters (from copying out of a document). These will make the key fail.',
            fixed: raw.replace(INVISIBLE_RE, ''),
        };
    }

    // A whole "KEY=value" line, or "KEY: value" -- the single most common paste
    // when someone copies from a .env file or a vendor's setup snippet.
    const assignment = raw.trim().match(/^([A-Z][A-Z0-9_]{3,})\s*[=:]\s*(.+)$/s);
    if (assignment) {
        return {
            label: `Looks like a whole line copied from a config file. Only the part after "${assignment[1]}=" is the value.`,
            fixed: assignment[2].trim().replace(SMART_QUOTES, ''),
        };
    }

    const unquoted = raw.trim().replace(SMART_QUOTES, '');
    if (unquoted !== raw.trim() && unquoted.length > 0) {
        return { label: 'Wrapped in quotes. Providers do not expect them.', fixed: unquoted };
    }

    if (raw !== raw.trim()) {
        return {
            label: 'Has a space or line break at one end.',
            fixed: raw.trim(),
        };
    }

    return null;
}

/** A placeholder someone pasted without replacing, or an obviously partial key.
 *
 * Advisory only -- these are guesses, and a real credential could in principle
 * contain any of them, so this never blocks a save. */
export function looksLikePlaceholder(raw: string): boolean {
    const v = raw.trim().toLowerCase();
    if (!v) return false;
    if (/^[x.\-_•*]+$/.test(v)) return true;
    return ['your', 'yourorg', 'example', 'changeme', 'todo', 'xxx', 'abc123', '<', 'paste']
        .some(token => v.startsWith(token) || v === token);
}

/**
 * A credential/config input tuned for non-technical staff:
 *   - a show/hide reveal toggle on secrets, so a clerk can eyeball a pasted
 *     key and catch a wrong or truncated paste before saving;
 *   - a "Saved" badge + "leave blank to keep" affordance for already-stored
 *     secrets, so re-editing never forces re-entry;
 *   - inline plain-language help.
 *
 * Whitespace is NOT trimmed here (that would fight mid-word typing); callers
 * trim credential values at save time instead — see ServiceProviders /
 * GovtechIntegrations save handlers.
 */
export default function SecretField({
    label, value, onChange, secret = false, placeholder, help,
    savedHint = false, savedLabel, required = false, autoFocus = false, kind = 'auto',
}: {
    label: string;
    value: string;
    onChange: (v: string) => void;
    secret?: boolean;
    placeholder?: string;
    help?: string;
    savedHint?: boolean;
    /** What the "Saved" badge says, when plain "Saved" is not the whole story.
     *  Used for a credential the deployment's host supplied, which is saved and
     *  working -- the badge stays green and the box still accepts a value of
     *  the town's own -- but which a clerk would otherwise go looking for in an
     *  account their town does not have. Absent means "Saved". */
    savedLabel?: string;
    required?: boolean;
    autoFocus?: boolean;
    kind?: FieldKind;
}) {
    const [reveal, setReveal] = useState(false);
    const isPassword = secret && !reveal;
    const check = checkValue(kind === 'auto' ? inferKind(label) : kind, value);
    const paste = diagnosePaste(value);
    const placeholderish = !paste && looksLikePlaceholder(value);

    return (
        <div>
            <label className="text-[11px] uppercase tracking-wider text-white/60 mb-1.5 font-semibold flex items-center gap-1.5">
                {secret && <Lock className="w-3 h-3 text-white/35" aria-hidden="true" />}
                {label}
                {required && !savedHint && <span className="normal-case tracking-normal text-amber-300 font-medium">(required)</span>}
                {savedHint && (
                    <span className="ml-auto normal-case tracking-normal text-[10px] font-medium text-emerald-300/80 inline-flex items-center gap-1">
                        <CheckCircle className="w-3 h-3" aria-hidden="true" /> {savedLabel || 'Saved'}
                    </span>
                )}
            </label>
            <div className="relative">
                <input
                    type={isPassword ? 'password' : 'text'}
                    autoFocus={autoFocus}
                    placeholder={savedHint ? '•••••••••  leave blank to keep' : (placeholder || '')}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    className={`w-full rounded-xl bg-white/[0.04] border border-white/10 text-white text-sm px-3.5 py-2.5 ${secret ? 'pr-10' : ''} placeholder:text-white/40 transition-all focus:outline-none focus:border-primary-400/50 focus:bg-white/[0.06] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]`}
                    spellCheck={false}
                    autoComplete="off"
                />
                {secret && (
                    <button
                        type="button"
                        onClick={() => setReveal(v => !v)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-lg text-white/40 hover:text-white/80 hover:bg-white/10 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
                        aria-label={reveal ? 'Hide value' : 'Show value'}
                        title={reveal ? 'Hide' : 'Show'}
                        tabIndex={-1}
                    >
                        {reveal ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                )}
            </div>
            {/* Offered, never applied automatically. Silently rewriting a
                credential is how you turn a visible paste mistake into an auth
                error three days later that nobody can explain. */}
            {paste && (
                <div className="mt-1.5 rounded-lg bg-amber-500/10 border border-amber-400/25 px-2.5 py-2 flex items-start gap-2">
                    <AlertCircle className="w-3.5 h-3.5 text-amber-300/90 mt-0.5 shrink-0" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                        <p className="text-xs text-amber-100/85 leading-relaxed">{paste.label}</p>
                        <button
                            type="button"
                            onClick={() => onChange(paste.fixed)}
                            className="mt-1 text-xs font-semibold text-amber-200 hover:text-white underline underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/60 rounded"
                        >
                            Fix this for me
                        </button>
                    </div>
                </div>
            )}
            {placeholderish && (
                <p className="text-xs mt-1.5 flex items-start gap-1 text-amber-300/90">
                    <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" aria-hidden="true" />
                    This looks like example text rather than a real value.
                </p>
            )}
            {check && !paste && (
                <p className={`text-xs mt-1.5 flex items-center gap-1 ${check.ok ? 'text-emerald-300/80' : 'text-amber-300/90'}`}>
                    {check.ok ? <CheckCircle className="w-3 h-3 shrink-0" aria-hidden="true" /> : <AlertCircle className="w-3 h-3 shrink-0" aria-hidden="true" />}
                    {check.msg}
                </p>
            )}
            {help && <p className="text-white/50 text-xs mt-1.5 leading-relaxed">{help}</p>}
        </div>
    );
}
