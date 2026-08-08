import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bell, ExternalLink, Check, X } from 'lucide-react';

import { Button, Input, Select } from './ui';
import { api } from '../services/api';
import { buildContactFormUrl, type PrefillValues } from './contactForm';

/**
 * The one voluntary outbound call this application makes.
 *
 * Pinpoint is self-hosted and phones home about nothing. That is a real
 * property and worth keeping, and it has a cost: when a security fix ships,
 * there is no way to tell the towns running the affected version. This form is
 * the disclosed exception -- a person types their details and presses a button,
 * and nothing leaves the browser until they do.
 *
 * Three rules follow from that, and they are the reason this file is shaped the
 * way it is:
 *
 *   * The payload carries only what somebody typed, plus the two consent
 *     booleans. No version string, no deployment fingerprint, no counts -- not
 *     because those would be unwelcome, but because "only what you typed" is a
 *     sentence a clerk can verify by reading the form, and "only what you typed
 *     plus some diagnostics" is not.
 *
 *   * It posts from the browser, not the server. A server-side submission would
 *     mean the application itself holds a connection to pinpoint311.org, which
 *     is exactly the property being preserved. From the browser, a town that
 *     firewalls outbound traffic sees this fail and nothing else changes.
 *
 *   * It never blocks. Closing the modal by any route is permanent for that
 *     browser -- it does not reappear on the next load -- and a failed
 *     submission degrades to a link rather than an error, because a town whose
 *     network refuses the request has not done anything wrong and should not be
 *     shown a red box about it.
 *
 * COMPLIANCE.md documents all of this, which is the point: the exception is
 * disclosed rather than discovered.
 *
 * One more mode, added later: a deployment whose operator hosts their own
 * registration form (CONTACT_FORM_URL, surfaced as `contact_form_url` on the
 * public config endpoint) gets a link out to that form -- opened in a new tab,
 * hosted by Microsoft Forms, nothing submitted from this application at all.
 * When no URL is configured, everything below behaves exactly as it always
 * has, so a self-hoster without a form loses nothing.
 */

/** Public, unauthenticated, CORS-open. No API key: distributing one with an
 *  open-source install would put it in every clone of the repository, which is
 *  not a secret, and requiring one would defeat the zero-config install. */
export const REGISTRATION_ENDPOINT = 'https://pinpoint311.org/api/deployments/register';

/** The same form on the website, for anyone who would rather not submit from
 *  inside their own console -- and the fallback when the request fails. */
export const REGISTRATION_FORM_URL = 'https://pinpoint311.org/register';

export const PRIVACY_POLICY_URL = 'https://pinpoint311.org/privacy';

/* Two flags, not one, and the distinction is the whole design.
 *
 * The modal is a one-time interruption: closed by any route, it never opens by
 * itself again. The banner is the standing way back in, and it is dismissed
 * separately. Collapsing them into one flag would mean "Not now" quietly
 * removes the only visible path back to the form -- which turns an optional
 * prompt into a one-shot offer, and buries the thing a town would want on the
 * day an advisory goes out.
 *
 * Per browser rather than per user. It is a nudge, not a task; the second admin
 * to sign in has no reason to be asked again about something the first dealt
 * with, and there is no server-side state here to key it to anyway. */
const MODAL_KEY = 'pinpoint311.stay-informed.dismissed';
const BANNER_KEY = 'pinpoint311.stay-informed.banner-dismissed';

const OPEN_EVENT = 'pinpoint311:stay-informed:open';
const CHANGE_EVENT = 'pinpoint311:stay-informed:change';

function readFlag(key: string): boolean {
    try {
        return localStorage.getItem(key) !== null;
    } catch {
        // Private browsing, or storage disabled by policy. Treating that as
        // "dismissed" is the safe direction: it means nothing appears rather
        // than appearing on every single page load with no way to stop it.
        return true;
    }
}

function writeFlag(key: string, value: string) {
    try {
        localStorage.setItem(key, value);
    } catch { /* nothing to do; see above */ }
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

export function isStayInformedDismissed(): boolean {
    return readFlag(MODAL_KEY);
}

type Outcome = 'submitted' | 'opened-form' | 'already-in-touch' | 'not-now';

function recordDismissal(how: Outcome) {
    writeFlag(MODAL_KEY, how);
    // Submitting answers the question, opening the operator's form is as
    // close to an answer as this side can see, and somebody already in touch
    // has answered it too. Only "Not now" leaves it open -- so only "Not now"
    // leaves the banner up.
    if (how !== 'not-now') writeFlag(BANNER_KEY, how);
}

/**
 * Reopen the form from the banner or the setup checklist.
 *
 * `immediate` distinguishes "you asked for this" from "we interrupted you",
 * and the operator's form is loaded from Microsoft the moment it appears -- so
 * only a click may cause that. A caller whose own button said "Register your
 * deployment" has already collected the click and passes true; the automatic
 * first-run prompt does not, and shows an invitation with a button instead.
 */
export function openStayInformed(options?: { immediate?: boolean }) {
    window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: options ?? {} }));
}

// ---------------------------------------------------------------------------

interface FormState {
    organization: string;
    contact_name: string;
    contact_email: string;
    contact_role: string;
    deployment_url: string;
    region: string;
    usage: string;
    consent_updates: boolean;
    consent_public_listing: boolean;
    /** Honeypot. Hidden from people, irresistible to form-filling bots, and
     *  submitted as-is so the receiving endpoint can drop anything non-empty
     *  without a CAPTCHA in front of a municipal clerk. */
    website: string;
}

const EMPTY: FormState = {
    organization: '',
    contact_name: '',
    contact_email: '',
    contact_role: '',
    deployment_url: '',
    region: '',
    usage: '',
    consent_updates: true,
    consent_public_listing: false,
    website: '',
};

const USAGE_OPTIONS = [
    { value: '', label: 'Select…' },
    { value: 'single', label: 'Self-hosted for one municipality' },
    { value: 'multiple', label: 'Hosting for multiple municipalities' },
    { value: 'evaluating', label: 'Evaluating' },
];

function Field({ label, hint, optional, children }: {
    label: string; hint?: string; optional?: boolean; children: React.ReactNode;
}) {
    return (
        <label className="block">
            <span className="flex items-baseline gap-2 mb-1.5">
                <span className="text-sm text-white/70">{label}</span>
                {optional && <span className="text-[11px] text-white/35">optional</span>}
            </span>
            {children}
            {hint && <span className="block text-[11px] text-white/40 mt-1">{hint}</span>}
        </label>
    );
}

function Consent({ checked, onChange, children }: {
    checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode;
}) {
    return (
        <label className="flex items-start gap-3 cursor-pointer group">
            <span className={`mt-0.5 w-5 h-5 rounded-lg shrink-0 flex items-center justify-center border transition-all duration-200 ${checked
                ? 'bg-gradient-to-br from-primary-400 to-indigo-500 border-transparent shadow-lg shadow-primary-500/25'
                : 'bg-white/[0.06] border-white/20 group-hover:border-white/35'
                }`}>
                {checked && <Check className="w-3.5 h-3.5 text-white" strokeWidth={3} />}
            </span>
            <input
                type="checkbox"
                checked={checked}
                onChange={(e) => onChange(e.target.checked)}
                className="sr-only"
            />
            <span className="text-sm text-white/65 leading-snug">{children}</span>
        </label>
    );
}

// ---------------------------------------------------------------------------

/**
 * Shown instead of the built-in form when the operator hosts a registration
 * form of their own.
 *
 * Two states, and the boundary between them is the whole point. The invitation
 * transmits nothing. Showing the form loads it from Microsoft, carrying
 * whatever the operator chose to prefill in its URL, so that has to be caused
 * by somebody clicking -- never by a modal appearing on its own. `startEmbedded`
 * is how a caller says it already has that click.
 *
 * The link out is present in both states rather than only as an error path. A
 * framed Forms page depends on third-party storage, which a hardened browser or
 * an organisation-restricted form will refuse, and an iframe that fails renders
 * as a blank rectangle with no event this side can observe -- so the way out
 * cannot be something only shown once failure is detected.
 */
function RegisterPanel({ url, embedUrl, startEmbedded, onShowForm, onDone }: {
    url: string;
    embedUrl: string;
    startEmbedded: boolean;
    onShowForm: () => void;
    onDone: (how: Outcome) => void;
}) {
    if (startEmbedded && embedUrl) {
        return (
            <div className="space-y-3">
                <iframe
                    src={embedUrl}
                    title="Deployment registration form"
                    /* Enough for Forms to run: its own scripts, its own storage
                     * (allow-same-origin is its origin, not this one), the
                     * submission itself, and the new tab its "done" screen
                     * sometimes offers. Nothing beyond that. */
                    sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
                    /* Microsoft has no need for the address of a town's admin
                     * console, which is often an internal hostname. */
                    referrerPolicy="no-referrer"
                    className="w-full h-[65vh] min-h-[380px] rounded-xl border border-white/10 bg-white"
                />
                <p className="text-[11px] text-white/35 leading-relaxed">
                    This form is hosted by Microsoft Forms. Opening it loaded it from Microsoft, and
                    your answers go to them when you press Submit there.
                </p>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 text-xs text-primary-300 hover:text-primary-200 underline underline-offset-2"
                    >
                        Open it in a new tab instead
                        <ExternalLink className="w-3 h-3" />
                    </a>
                    <button
                        type="button"
                        onClick={() => onDone('opened-form')}
                        className="px-4 py-2 rounded-xl bg-white/[0.06] hover:bg-white/10 border border-white/12 text-white/70 text-sm transition-colors"
                    >
                        Done
                    </button>
                </div>
            </div>
        );
    }

    /* No embed URL, or embedding switched off: the button is a link, and the
     * only request to Microsoft is the one a new tab makes. */
    const linkOut = !embedUrl;

    return (
        <div className="space-y-5">
            <p className="text-white/65 text-sm leading-relaxed">
                Pinpoint 311 is open source and self-hosted, so we have no way to reach you about
                security fixes, releases, or anything affecting your instance. Registering takes
                two minutes: who to reach, and where this runs.
            </p>
            {linkOut ? (
                <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => onDone('opened-form')}
                    className="w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-gradient-to-r from-primary-500 to-indigo-500 hover:from-primary-400 hover:to-indigo-400 text-white text-sm font-medium shadow-lg shadow-primary-500/25 transition-colors"
                >
                    Register your deployment
                    <ExternalLink className="w-4 h-4" />
                </a>
            ) : (
                <button
                    type="button"
                    onClick={onShowForm}
                    className="w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-gradient-to-r from-primary-500 to-indigo-500 hover:from-primary-400 hover:to-indigo-400 text-white text-sm font-medium shadow-lg shadow-primary-500/25 transition-colors"
                >
                    Register your deployment
                </button>
            )}
            <p className="text-center text-[11px] text-white/35">
                {linkOut
                    ? 'Opens in a new tab. The form is hosted by Microsoft Forms.'
                    : 'The form is hosted by Microsoft Forms, and opens here. Nothing reaches them until you open it.'}
            </p>
            <div className="flex flex-col gap-3 pt-1">
                <button
                    type="button"
                    onClick={() => onDone('already-in-touch')}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/[0.06] hover:bg-white/10 border border-white/12 text-white/70 text-sm transition-colors"
                >
                    I'm already in touch with Pinpoint 311
                </button>
                <button
                    type="button"
                    onClick={() => onDone('not-now')}
                    className="text-white/40 hover:text-white/70 text-sm transition-colors"
                >
                    Not now
                </button>
            </div>
        </div>
    );
}

function StayInformedForm({ onDone }: { onDone: (how: Outcome) => void }) {
    const [form, setForm] = useState<FormState>(EMPTY);
    const [state, setState] = useState<'editing' | 'sending' | 'sent' | 'fallback'>('editing');

    const set = <K extends keyof FormState>(key: K) => (value: FormState[K]) =>
        setForm(prev => ({ ...prev, [key]: value }));

    const complete = form.organization.trim() && form.contact_name.trim() && form.contact_email.trim();

    async function submit(event: React.FormEvent) {
        event.preventDefault();
        if (!complete || state === 'sending') return;
        setState('sending');
        try {
            const response = await fetch(REGISTRATION_ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // No credentials and no custom headers: both would turn this
                // into a preflighted, cookie-bearing request from every town's
                // domain, and there is nothing here worth either.
                body: JSON.stringify(form),
            });
            if (!response.ok) throw new Error(String(response.status));
            setState('sent');
            recordDismissal('submitted');
        } catch {
            // Deliberately not an error. The most likely cause is a town that
            // firewalls outbound traffic, which is a reasonable thing for a
            // municipal network to do and not a mistake to report back at them.
            setState('fallback');
        }
    }

    if (state === 'sent') {
        return (
            <div className="text-center py-6">
                <div className="w-14 h-14 mx-auto rounded-2xl bg-gradient-to-br from-emerald-400 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/25">
                    <Check className="w-7 h-7 text-white" strokeWidth={2.5} />
                </div>
                <p className="text-white/80 mt-4">Thank you — we have your details.</p>
                <p className="text-white/45 text-sm mt-1.5">
                    You'll hear from us when there is a security advisory or a release worth knowing about,
                    and not otherwise.
                </p>
                <Button className="mt-6" onClick={() => onDone('submitted')}>Close</Button>
            </div>
        );
    }

    if (state === 'fallback') {
        return (
            <div className="text-center py-6">
                <p className="text-white/80">We couldn't reach pinpoint311.org from this browser.</p>
                <p className="text-white/45 text-sm mt-1.5 max-w-sm mx-auto">
                    That is often just a municipal network blocking outbound traffic, which is a perfectly
                    sensible thing for it to do. The same form is on our website if you'd like to use it
                    from somewhere else.
                </p>
                <a
                    href={REGISTRATION_FORM_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 mt-5 px-5 py-2.5 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 text-white/85 text-sm transition-colors"
                >
                    Open the form on pinpoint311.org
                    <ExternalLink className="w-3.5 h-3.5" />
                </a>
                <div>
                    <button
                        type="button"
                        onClick={() => onDone('not-now')}
                        className="text-white/40 hover:text-white/70 text-sm mt-5 transition-colors"
                    >
                        Close
                    </button>
                </div>
            </div>
        );
    }

    return (
        <form onSubmit={submit} className="space-y-5">
            <div className="space-y-3">
                <p className="text-white/65 text-sm leading-relaxed">
                    Pinpoint 311 is open source and self-hosted, so we have no way to reach you when a
                    security fix or important update is released. If you share your contact information,
                    we will send security advisories and release notes, and we are glad to help where we
                    can.
                </p>
                <p className="text-white/40 text-xs leading-relaxed">
                    This is entirely optional. Nothing is sent automatically — only what you enter below
                    is submitted.
                </p>
            </div>

            <div className="grid gap-4">
                <Field label="Organization or municipality">
                    <Input
                        value={form.organization}
                        onChange={(e) => set('organization')(e.target.value)}
                        placeholder="Township of Example"
                        required
                    />
                </Field>

                <div className="grid sm:grid-cols-2 gap-4">
                    <Field label="Your name">
                        <Input
                            value={form.contact_name}
                            onChange={(e) => set('contact_name')(e.target.value)}
                            required
                        />
                    </Field>
                    <Field label="Email">
                        <Input
                            type="email"
                            value={form.contact_email}
                            onChange={(e) => set('contact_email')(e.target.value)}
                            required
                        />
                    </Field>
                </div>

                <div className="grid sm:grid-cols-2 gap-4">
                    <Field label="Role or title" optional>
                        <Input
                            value={form.contact_role}
                            onChange={(e) => set('contact_role')(e.target.value)}
                            placeholder="Municipal Clerk"
                        />
                    </Field>
                    <Field label="State or country" optional>
                        <Input
                            value={form.region}
                            onChange={(e) => set('region')(e.target.value)}
                            placeholder="New Jersey"
                        />
                    </Field>
                </div>

                <Field
                    label="Deployment URL"
                    optional
                    hint="so we can notify you if we detect an issue with your instance"
                >
                    <Input
                        value={form.deployment_url}
                        onChange={(e) => set('deployment_url')(e.target.value)}
                        placeholder="https://311.example.gov"
                    />
                </Field>

                <Field label="How you're using it" optional>
                    <Select
                        options={USAGE_OPTIONS}
                        value={form.usage}
                        onChange={(e) => set('usage')(e.target.value)}
                    />
                </Field>
            </div>

            {/* Honeypot. Off-screen rather than display:none, which some bots
              * check for, and marked so assistive technology skips it. */}
            <div aria-hidden="true" className="absolute -left-[9999px] w-px h-px overflow-hidden">
                <label>
                    Website
                    <input
                        tabIndex={-1}
                        autoComplete="off"
                        value={form.website}
                        onChange={(e) => set('website')(e.target.value)}
                    />
                </label>
            </div>

            <div className="space-y-3 pt-1">
                <Consent checked={form.consent_updates} onChange={set('consent_updates')}>
                    Send me security advisories and release notes.
                </Consent>
                <Consent checked={form.consent_public_listing} onChange={set('consent_public_listing')}>
                    You may list us publicly as a Pinpoint 311 deployment.
                </Consent>
            </div>

            <div className="flex flex-col gap-3 pt-1">
                <Button type="submit" disabled={!complete || state === 'sending'} className="w-full">
                    {state === 'sending' ? 'Sending…' : 'Submit'}
                </Button>
                <button
                    type="button"
                    onClick={() => onDone('already-in-touch')}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/[0.06] hover:bg-white/10 border border-white/12 text-white/70 text-sm transition-colors"
                >
                    I'm already in touch with Pinpoint 311
                </button>
                <button
                    type="button"
                    onClick={() => onDone('not-now')}
                    className="text-white/40 hover:text-white/70 text-sm transition-colors"
                >
                    Not now
                </button>
            </div>

            <p className="text-center text-[11px] text-white/30 pt-1">
                <a href={PRIVACY_POLICY_URL} target="_blank" rel="noopener noreferrer"
                    className="hover:text-white/55 underline underline-offset-2">Privacy policy</a>
                <span className="mx-2">·</span>
                <a href={REGISTRATION_FORM_URL} target="_blank" rel="noopener noreferrer"
                    className="hover:text-white/55 underline underline-offset-2">Same form on pinpoint311.org</a>
            </p>
        </form>
    );
}

// ---------------------------------------------------------------------------

/**
 * Mount once, near the root of the admin console.
 *
 * `ready` is what says setup is finished rather than in progress -- the caller
 * decides, because only it knows whether somebody is mid-configuration. This
 * component only guarantees that a dismissal is permanent.
 *
 * `prefill` is what the console already knows about the town and the person
 * signed in. None of it goes anywhere unless the operator's CONTACT_FORM_URL
 * asks for it by name; see contactForm.ts.
 */
export function StayInformedHost({ ready, prefill }: { ready: boolean; prefill?: PrefillValues }) {
    const [open, setOpen] = useState(false);
    const [dismissed, setDismissed] = useState(() => readFlag(MODAL_KEY));
    const [bannerDismissed, setBannerDismissed] = useState(() => readFlag(BANNER_KEY));
    const autoPrompted = useRef(false);
    /* The operator-hosted registration form, if this deployment has one.
     * Empty means it does not, and the modal shows the built-in form. */
    const [contactFormUrl, setContactFormUrl] = useState('');
    const [embedAllowed, setEmbedAllowed] = useState(true);
    /* The two facts the deployment knows about itself rather than about the
     * person: where it answers, and which build it is. */
    const [deploymentUrl, setDeploymentUrl] = useState<string | null>(null);
    const [version, setVersion] = useState<string | null>(null);
    /* Whether the operator's form is on screen, which is the same question as
     * whether this browser has contacted Microsoft. True from the start only
     * when the caller collected a click of its own; the automatic first-run
     * prompt always opens on the invitation. Lives here rather than in the
     * panel because the dialog has to be wider to hold a form. */
    const [showForm, setShowForm] = useState(false);

    useEffect(() => {
        api.getSystemConfig()
            .then(cfg => {
                setContactFormUrl((cfg?.contact_form_url ?? '').trim());
                setEmbedAllowed(cfg?.contact_form_embed !== false);
                setDeploymentUrl(cfg?.public_origin ?? null);
                setVersion(cfg?.app_version ?? null);
            })
            .catch(() => { /* unknowable reads as unconfigured: the in-app form */ });
    }, []);

    useEffect(() => {
        const reopen = (event: Event) => {
            setShowForm(Boolean((event as CustomEvent).detail?.immediate));
            setOpen(true);
        };
        const changed = () => {
            setDismissed(readFlag(MODAL_KEY));
            setBannerDismissed(readFlag(BANNER_KEY));
        };
        window.addEventListener(OPEN_EVENT, reopen);
        window.addEventListener(CHANGE_EVENT, changed);
        return () => {
            window.removeEventListener(OPEN_EVENT, reopen);
            window.removeEventListener(CHANGE_EVENT, changed);
        };
    }, []);

    useEffect(() => {
        // Once per page load at most, and never after any dismissal. Nobody
        // asked for this one, so it opens on the invitation rather than on a
        // form fetched from a third party.
        if (ready && !dismissed && !autoPrompted.current) {
            autoPrompted.current = true;
            setShowForm(false);
            setOpen(true);
        }
    }, [ready, dismissed]);

    /* Built here rather than in the panel so the panel stays a rendering of
     * values it was handed. Empty embedUrl is what makes the panel offer a link
     * out instead: either no form is configured, or the operator switched
     * framing off. */
    const values: PrefillValues = {
        ...prefill,
        deployment_url: prefill?.deployment_url ?? deploymentUrl,
        version: prefill?.version ?? version,
    };
    const formUrl = buildContactFormUrl(contactFormUrl, values);
    const formEmbedUrl = embedAllowed
        ? buildContactFormUrl(contactFormUrl, values, { embed: true })
        : '';
    /* An unusable CONTACT_FORM_URL builds to '' and lands here as "no form
     * configured", so a mistyped setting falls back to the built-in form
     * rather than taking away the only way to leave a contact. */
    const hasOperatorForm = Boolean(formUrl);
    const framing = hasOperatorForm && Boolean(formEmbedUrl) && showForm;

    const finish = (how: Outcome) => {
        recordDismissal(how);
        setDismissed(true);
        if (how !== 'not-now') setBannerDismissed(true);
        setOpen(false);
        setShowForm(false);
    };

    return (
        <>
            <AnimatePresence>
                {open && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-slate-950/50 backdrop-blur-sm overflow-y-auto"
                        // Clicking away is a dismissal like any other -- it is
                        // an optional form, not something to trap somebody in.
                        onClick={() => finish('not-now')}
                    >
                        <motion.div
                            initial={{ opacity: 0, y: 24, scale: 0.97 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: 12, scale: 0.98 }}
                            transition={{ type: 'spring', stiffness: 300, damping: 26 }}
                            onClick={(e) => e.stopPropagation()}
                            role="dialog"
                            aria-modal="true"
                            aria-label={hasOperatorForm
                                ? 'Register your deployment'
                                : 'Stay informed about security updates and new features'}
                            className={`setup-panel w-full my-auto p-7 sm:p-8 ${framing ? 'max-w-3xl' : 'max-w-xl'}`}
                        >
                            <button
                                type="button"
                                onClick={() => finish('not-now')}
                                aria-label="Close"
                                className="absolute top-4 right-4 w-8 h-8 rounded-full flex items-center justify-center text-white/35 hover:text-white/80 hover:bg-white/10 transition-colors"
                            >
                                <X className="w-4 h-4" />
                            </button>

                            {/* pr-8 so the heading never runs under the close
                              * button, which it does at this width without it. */}
                            <div className="flex items-center gap-4 mb-5 pr-8">
                                <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-400 to-indigo-500 flex items-center justify-center shadow-lg shadow-primary-500/25 shrink-0">
                                    <Bell className="w-6 h-6 text-white" />
                                </div>
                                <h2 className="font-bold text-lg text-white leading-tight">
                                    {hasOperatorForm
                                        ? 'Register your deployment'
                                        : 'Stay informed about security updates and new features'}
                                </h2>
                            </div>

                            {hasOperatorForm
                                ? <RegisterPanel
                                    url={formUrl}
                                    embedUrl={formEmbedUrl}
                                    startEmbedded={showForm}
                                    onShowForm={() => setShowForm(true)}
                                    onDone={finish}
                                />
                                : <StayInformedForm onDone={finish} />}
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Independent of the modal's flag: this is what "reachable
              * without blocking" means. It appears once the modal has been put
              * aside and stays until it is dismissed in its own right. */}
            {!bannerDismissed && dismissed && !open && ready && (
                <StayInformedBanner onDismiss={() => { writeFlag(BANNER_KEY, 'dismissed'); setBannerDismissed(true); }} />
            )}
        </>
    );
}

/**
 * The standing way back to the form. Small, in the corner, and out of the way
 * of everything -- but present, because the alternative is that "Not now" is
 * indistinguishable from "never", and the moment a town wants this is the
 * morning an advisory goes out rather than the afternoon they installed.
 */
function StayInformedBanner({ onDismiss }: { onDismiss: () => void }) {
    return (
        <div className="fixed bottom-4 right-4 z-40 max-w-sm">
            <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                className="setup-panel px-4 py-3 flex items-start gap-3 shadow-2xl"
            >
                <Bell className="w-4 h-4 text-primary-300 mt-0.5 shrink-0" />
                <p className="text-xs text-white/60 leading-snug flex-1">
                    We have no way to reach you about security fixes or updates.{' '}
                    <button
                        type="button"
                        onClick={() => openStayInformed({ immediate: true })}
                        className="text-primary-300 hover:text-primary-200 underline underline-offset-2"
                    >
                        Register a contact
                    </button>
                </p>
                <button
                    type="button"
                    onClick={onDismiss}
                    aria-label="Dismiss"
                    className="text-white/30 hover:text-white/70 transition-colors shrink-0"
                >
                    <X className="w-3.5 h-3.5" />
                </button>
            </motion.div>
        </div>
    );
}

export default StayInformedHost;
