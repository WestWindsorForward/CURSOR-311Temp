import type { ReactNode } from 'react';
import { stepsFor } from './setupSteps';
// Registers every provider's steps as a side effect of importing it.
import './setupStepsContent';
import type { StepContext } from './setupSteps';
import ProviderCredentialSteps from './ProviderCredentialSteps';
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Sparkles, Languages, KeyRound, CheckCircle, AlertCircle,
    Check, CircleDashed, HelpCircle, ShieldCheck, RefreshCw, Search,
    Lock, Map as MapIcon,
    Mail, MessageSquare, Image as ImageIcon,
} from 'lucide-react';

import { CollapsibleSection } from './ui';
import { StatusPill, CapabilityTile, Action, hasAlert, type CapabilityState } from './capabilityUI';
import { PlainSecrets } from './SetupWizard';
import { api, ProviderCatalog, ProviderInfo, ProviderModelSpec, CloudIdentity } from '../services/api';
import type { ConnectorHealth } from '../types';

// Relative "updated Xh ago" from an epoch-seconds timestamp.
function agoLabel(epochSeconds?: number | null): string {
    if (!epochSeconds) return '';
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
    if (secs < 90) return 'just now';
    const mins = Math.round(secs / 60);
    if (mins < 90) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 36) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
}

import type { Capability } from '../services/api';

const CAPS: { key: Capability; title: string; blurb: string; icon: typeof Sparkles }[] = [
    { key: 'ai', title: 'AI Provider', blurb: 'Where AI triage and the analytics assistant run. Each town brings its own key.', icon: Sparkles },
    { key: 'translation', title: 'Translation Provider', blurb: 'Powers end-to-end translation across 100+ languages.', icon: Languages },
    { key: 'identity', title: 'Staff Sign-In (Identity)', blurb: 'The identity provider that authenticates staff and admins.', icon: KeyRound },
    // Maps is a capability like the rest, so switching one works the same way
    // as switching an AI or translation provider -- same card, same save, same
    // test button. A separate picker elsewhere would be a second thing to learn.
    { key: 'maps', title: 'Maps Provider', blurb: 'Draws the map residents drop a pin on, and looks up addresses.', icon: MapIcon },
    // Four capabilities whose provider switch the backend already honoured and
    // nothing surfaced. Email and text had one hand-written SMTP/Twilio card
    // between them; PII encryption and photo redaction had no UI at all, so
    // face and plate blurring was a shipped feature a town could not turn on.
    { key: 'email', title: 'Email', blurb: 'Sends confirmations and status updates to residents. SMTP works with the mail server your town already has.', icon: Mail },
    { key: 'sms', title: 'Text Messages', blurb: 'Optional text alerts. Residents who give a mobile number get updates without checking email.', icon: MessageSquare },
    { key: 'kms', title: 'PII Encryption (KMS)', blurb: 'Which key service wraps the key that encrypts resident personal information. Cloud KMS is stronger than the application key.', icon: Lock },
    { key: 'redaction', title: 'Photo Redaction', blurb: 'Blurs faces and licence plates in resident photos before they are stored, so a municipal site never publishes them.', icon: ImageIcon },
    // The capability every card above depends on, and the last to get a check.
    // A credential saved while the store is unreachable stays in the encrypted
    // database and the card that saved it still shows a tick, so the failure
    // was invisible from here. No provider to pick: switching stores does not
    // move what is already in the old one, so that belongs to the cloud
    // profile. This says which store is in use and offers the round trip.
    { key: 'secrets', title: 'Secret Storage', blurb: 'Where every credential on this page is kept. The check writes a throwaway key, reads it back and removes it.', icon: ShieldCheck },
];

/** The same sliding pill the Modules screen uses, so on/off looks like on/off
 * everywhere in the console rather than being a labelled button here and a
 * toggle there. Held to the exact geometry of the modules one on purpose. */
function Switch({ on, busy, disabled, onChange, label }: {
    on: boolean; busy?: boolean; disabled?: boolean;
    onChange: () => void; label: string;
}) {
    return (
        <button
            type="button"
            onClick={onChange}
            disabled={disabled}
            role="switch"
            aria-checked={on}
            aria-label={label}
            className={`relative inline-flex items-center rounded-full transition-colors duration-300 shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/50 disabled:opacity-50 ${on ? 'bg-primary-500 shadow-lg shadow-primary-500/30' : 'bg-slate-600'}`}
            style={{ width: 44, height: 24, minHeight: 24, maxHeight: 24, padding: 0 }}
        >
            <span
                className={`inline-block rounded-full bg-white shadow-md transition-transform duration-300 ${on ? 'translate-x-6' : 'translate-x-1'} ${busy ? 'animate-pulse' : ''}`}
                style={{ width: 16, height: 16 }}
                aria-hidden="true"
            />
        </button>
    );
}

/** A numbered section heading inside a provider card.
 *
 * Configuring a provider is a short ordered task -- pick one, pick a model,
 * paste the credentials, save -- and the card used to present those as four
 * visually identical blocks. Numbering them makes the order legible at a glance
 * and gives someone following the setup guide something to match against. */
function Step({ n, children, aside }: { n: number; children: React.ReactNode; aside?: React.ReactNode }) {
    return (
        <div className="flex items-center justify-between gap-3 mb-2.5">
            <div className="flex items-center gap-2 min-w-0">
                <span
                    className="shrink-0 w-5 h-5 rounded-full bg-white/10 border border-white/15 text-[10px] font-bold text-white/70 flex items-center justify-center tabular-nums"
                    aria-hidden="true"
                >
                    {n}
                </span>
                <span className="text-[11px] uppercase tracking-wider text-white/60 font-semibold truncate">{children}</span>
            </div>
            {aside}
        </div>
    );
}

/** Whether it works, and when we last had evidence either way.
 *
 * The card used to show "Configured", which is a fact about our own database:
 * it goes green the moment a credential is saved and stays green through the
 * key being revoked, the card on file lapsing and the secret expiring. This
 * answers the question a clerk is actually asking, and says how old the answer
 * is -- because "working, checked three weeks ago" and "working, checked this
 * morning" are different claims and only one of them is worth much.
 */
function LiveState({ health }: { health?: ConnectorHealth }) {
    if (!health || health.status === 'unknown') {
        return <span className="text-white/40">not checked yet</span>;
    }
    const when = health.status === 'working' ? health.last_success_at : health.last_error_at;
    const ago = when ? relativeTime(when) : null;
    const [text, tone] = {
        working: ['working', 'text-emerald-300/90'],
        stale: ['not used recently', 'text-white/45'],
        failing: ['last check failed', 'text-amber-300/90'],
        down: [`failing (${health.consecutive_failures} in a row)`, 'text-red-300/90'],
    }[health.status] as [string, string];
    return (
        <span className={tone} title={health.last_error || health.summary}>
            {text}{ago ? <span className="text-white/35"> · checked {ago}</span> : null}
        </span>
    );
}

/** The provider's own name where the catalog knows it, its id otherwise.
 *
 * Used to say which provider a stored result was about. The id is a fallback
 * rather than the answer: "acs" is our word for it and "Azure Communication
 * Services" is the town's. */
export function providerLabel(
    catalog: { providers?: { provider: string; name: string }[] } | null | undefined,
    provider: string | null | undefined,
): string {
    if (!provider) return 'the previous provider';
    return catalog?.providers?.find(p => p.provider === provider)?.name || provider;
}

/**
 * Fold the browser's map verdict into the server's.
 *
 * Maps is the one capability whose real test cannot run on the server: providers
 * enforce a site restriction when the map initialises, so "will this draw for a
 * resident" is only answerable in a browser on the town's own origin — which is
 * this one. See maps/browserCheck.ts for why a server cannot substitute.
 *
 * The two halves check different things and both are reported. The server's says
 * the APIs are enabled, billing is attached and addresses geocode; the browser's
 * says the map draws. A browser failure wins, because it is the half a resident
 * sees — a green tick beside a grey map is the outcome this replaces.
 *
 * A check that could not conclude leaves the server's answer alone rather than
 * downgrading it: "we could not run the browser half" is not a failure.
 */
async function withBrowserMapVerdict<T extends { ok: boolean; detail?: string }>(
    server: T,
): Promise<T> {
    try {
        const [{ checkMapInBrowser, resolveMapProviderConfig }, raw] = await Promise.all([
            import('../maps'),
            api.getMapsConfig(),
        ]);
        const verdict = await checkMapInBrowser(resolveMapProviderConfig(raw));
        if (!verdict.conclusive) return server;
        return {
            ...server,
            ok: server.ok && verdict.ok,
            detail: [server.detail, verdict.detail].filter(Boolean).join(' '),
        };
    } catch {
        /* The browser half is additive. If it cannot run — a provider bundle that
         * will not import, settings that will not load — the server's verdict is
         * still the verdict, and reporting a failure here would blame the key for
         * a fault in this check. */
        return server;
    }
}

/** "6 hours ago" from an ISO timestamp. */
function relativeTime(iso: string): string {
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return '';
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 2) return 'just now';
    if (mins < 90) return `${mins} minutes ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 36) return `${hrs} hours ago`;
    return `${Math.round(hrs / 24)} days ago`;
}

export interface CapStatus {
    providerName?: string;
    /** The provider id in use, so a stored health row can be checked against
     *  it. A verdict is only true of the provider that produced it. */
    provider?: string;
    onDefault?: boolean;
    verified?: boolean | null;
    /** False when the provider cannot be checked from here at all. Distinct
     *  from a failed check: there is no test to run and there never will be,
     *  so reporting it as broken is a badge that can never go green. */
    verifiable?: boolean;
    /** Whether the in-use provider has its credentials stored. undefined means
     *  the endpoint did not say, which is not the same as no. */
    configured?: boolean;
    /** Whether the town wants this capability at all.
     *
     *  Independent of `configured`, and that independence is the point. A town
     *  can save an email or an AI key and decide not to use it; the credential
     *  stays stored and readable and nothing dispatches through it. Before this
     *  existed the only way to stop a configured capability was to delete the
     *  credential, so "we are not using this" and "this was never set up" were
     *  the same state on screen and in the database.
     *
     *  undefined means the endpoint did not say, which reads as on -- an absent
     *  answer must not switch a town's email off. */
    enabled?: boolean;
}

/** What the last live check found, as one word the rest of the page can sort on.
 *
 * Deliberately not derived from `configured` alone. "A credential exists in our
 * database" is the fact the old badge reported, and it stays true through the
 * key being revoked; it is the reason this page could show eight green ticks on
 * a town whose AI had been failing for a fortnight.
 */
/** Whether a stored health row is a verdict about the provider in use.
 *
 * A result belongs to the provider that produced it. The row carries the
 * provider it was recorded against, and until now nothing compared them: the
 * SMS card was showing "There is no way to check http without sending a real
 * text" while SMS_PROVIDER read `acs` -- a true statement about a gateway the
 * town had switched away from, presented as the state of the one it is on. In
 * the other direction the same row would have shown green for a provider that
 * had never been tested.
 *
 * A row with no provider recorded is accepted rather than discarded: every row
 * written before this was stored that way, and throwing away a real verdict is
 * the more expensive mistake of the two.
 */
export function healthIsAboutCurrentProvider(
    s: CapStatus | undefined, health?: ConnectorHealth,
): boolean {
    if (!health) return false;
    if (!health.provider || !s?.provider) return true;
    return health.provider === s.provider;
}

export function capabilityState(s: CapStatus | undefined, health?: ConnectorHealth): CapabilityState | null {
    if (!s) return null;                      // catalog still loading
    /* First, and ahead of `configured`, because it overrides it in both
     * directions.
     *
     * Switched off with credentials saved is not "working" -- nothing runs
     * through it -- and switched off with nothing saved is still not "not set
     * up", because the town has answered the question. Either way there is
     * nothing here for anyone to do, which is what separates this from every
     * other state on this list. Health is not consulted at all: a stored
     * verdict about a capability nobody is using describes a call that no
     * longer happens. */
    if (s.enabled === false) return 'off';
    if (!s.configured) return 'unset';
    // Anything the current provider did not produce says nothing about it.
    if (!healthIsAboutCurrentProvider(s, health)) health = undefined;
    /* The session's answer when there is one, the stored one otherwise.
     *
     * Not an `||` of the two: a town that swapped an HTTP gateway for Twilio
     * has `verifiable: true` from the test it just ran and `false` still in
     * the health row, and reading both would keep telling it that text
     * messages cannot be checked. The stored value is a fallback for a fresh
     * page, not a second opinion. */
    const knownUnverifiable = s.verifiable !== undefined
        ? s.verifiable === false
        : health?.verifiable === false;
    if (knownUnverifiable) return 'unverifiable';
    // A test run in this session is fresher than the stored health row.
    if (s.verified === true) return 'working';
    if (s.verified === false) return 'failing';
    if (!health || health.status === 'unknown' || health.status === 'stale') return 'unchecked';
    return health.status === 'working' ? 'working' : 'failing';
}

function CapabilityCard({ cap, title, blurb, icon: Icon, delay, recheckToken, reloadToken, onStatus, health, step, guided, identity,
    variant = 'full', state, expanded, onExpandToggle, onChanged, publicOrigin, onSwitchOff }: {
    cap: Capability; title: string; blurb: string; icon: typeof Sparkles; delay: number;
    recheckToken: number; reloadToken: number; onStatus: (cap: Capability, s: CapStatus) => void;
    health?: ConnectorHealth;
    /** Switch this capability off, credentials intact. Absent on the ones a
     *  town cannot run without (sign-in, maps, the secret store) and when the
     *  page has nowhere to persist the answer. */
    onSwitchOff?: () => Promise<void>;
    /* How this card is drawn. `full` is the guided walk, unchanged. The other
     * two are the Spotlight layout the standing cards use once setup is done:
     * anything wrong gets the whole width, everything healthy shrinks to a
     * bubble. Same component either way -- a second component would be a second
     * place for the save-then-test behaviour to drift. */
    variant?: 'full' | 'spotlight' | 'bubble';
    state?: CapabilityState | null;
    /** Controlled by the parent in the Spotlight layout, so opening one card
     *  can widen its grid cell. */
    expanded?: boolean;
    onExpandToggle?: () => void;
    /** Something was saved or tested here; the setup guide above shares this
     *  data and has to be told. */
    onChanged?: () => void;
    /** The address residents use. See the note on stepCtx below. */
    publicOrigin?: string | null;
    /* Fetched once by the parent and passed down: the probe is a metadata call
     * and the answer is the same for every card on the page. */
    identity?: CloudIdentity | null;
    /* Guided setup: the parent walks one capability at a time, so it decides
     * which card is open rather than each card deciding for itself. `step` is
     * the position in that walk, shown so a clerk can see where they are; it is
     * undefined once setup is done and the page is just cards again. */
    step?: { index: number; total: number; active: boolean };
    guided?: boolean;
}) {
    const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
    const [selected, setSelected] = useState<string>('');
    const [model, setModel] = useState<string>('');
    const [values, setValues] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState<'save' | 'test' | null>(null);
    const [switching, setSwitching] = useState(false);
    /* `recorded === false` rides along, because "we cannot check this from
     * here" is a third answer and the box only had two. It was drawn in the
     * same amber as a failure, so a generic HTTP gateway -- which by definition
     * has no read-only call to make -- carried a permanent warning about a
     * gateway that might be working perfectly. */
    const [result, setResult] = useState<{ ok: boolean; detail: string; recorded?: boolean } | null>(null);

    /* What to show in the result box: this session's test, or the last one
     * recorded, whichever is fresher.
     *
     * `record_success` has stored the message since #436, and nothing read it
     * back -- so pressing Test showed "Twilio credentials accepted. Nothing was
     * sent." and a reload showed an empty card, which reads as the test having
     * been forgotten rather than as the box not being rehydrated.
     *
     * Taken from the health row this card already receives rather than added
     * to the catalog endpoint as well. The same fact served by two endpoints is
     * two things that can disagree, and the badge beside this box already reads
     * the health row -- a second copy could put a green message under a red
     * badge.
     */
    /* The stored row only counts if it is about the provider now selected.
     *
     * `connector_health` has always carried the provider a result was recorded
     * against and nothing compared it. Live, the SMS card read "There is no way
     * to check http without sending a real text" while SMS_PROVIDER was `acs`:
     * a true sentence about the previous gateway, shown as the state of the
     * current one. Had `http` passed instead, the card would have been green
     * for a provider the town no longer uses -- the same bug, in the direction
     * nobody notices. */
    const staleHealth = !!health?.provider && !!catalog?.current_provider
        && health.provider !== catalog.current_provider;
    const currentHealth = staleHealth ? undefined : health;

    const shownResult = result ?? (currentHealth?.last_result
        ? {
            // `verifiable === false` is "we tried and cannot check this from
            // here", which is not a pass and must not render as one.
            ok: currentHealth.verifiable !== false && currentHealth.status === 'working',
            detail: currentHealth.last_result,
            recorded: currentHealth.verifiable !== false,
        }
        : null);
    /* Neither a pass nor a failure. Drawn plainly rather than in the amber a
     * real failure gets: nothing here is wrong, and a warning that no action
     * can ever clear is how a page teaches people to stop reading it. */
    const resultUncheckable = !!shownResult && shownResult.ok === false
        && shownResult.recorded === false;
    const [error, setError] = useState<string | null>(null);
    // Live model discovery (AI only)
    // Copy targets inside steps: a callback URL retyped by hand is the single
    // most common reason sign-in fails after the password is accepted.
    const [copied, setCopied] = useState<string | null>(null);
    const stepCtx: StepContext = {
        /* The configured public address, not wherever this browser happens to
         * be. An admin on an internal hostname, a port-forward or an IP would
         * otherwise be shown a redirect URI that can never be redirected to --
         * and the login then fails *after* the password is accepted, which
         * reads as a wrong secret rather than a wrong URL. The guide already
         * did this; the cards render the same walk and did not, so the two
         * surfaces printed different URLs for the same field. */
        origin: publicOrigin || window.location.origin,
        copy: (text, id) => {
            navigator.clipboard?.writeText(text).then(
                () => { setCopied(id); setTimeout(() => setCopied(null), 1600); },
                () => { /* clipboard blocked; the value is visible and selectable */ },
            );
        },
        copied,
    };

    const [refreshingModels, setRefreshingModels] = useState(false);
    const [liveModels, setLiveModels] = useState<ProviderModelSpec[] | null>(null);
    const [modelsMeta, setModelsMeta] = useState<{ source?: string; fetched_at?: number | null } | null>(null);
    const [staleOverride, setStaleOverride] = useState<boolean | null>(null);
    /* Filters the model tiles.
     *
     * Declared, which the version in #433 was not -- it used the setter and the
     * value five times with no useState, so opening any AI card threw a
     * ReferenceError. `vite build` passed anyway, because esbuild strips types
     * without resolving identifiers, and nothing in CI runs tsc. */
    const [modelSearch, setModelSearch] = useState('');
    const [warnings, setWarnings] = useState<{ key: string; severity: string; message: string }[]>([]);
    /* Collapsed by default, expanded when something needs attention.
     *
     * I removed this disclosure earlier after reading "I don't like the drop down
     * configuration options" as being about the collapse. It was about the
     * <select> model picker, which is now tiles. Four always-open full-width
     * cards made the page enormous, so the collapse comes back -- with the
     * distinction that a card nobody has configured opens itself, so the fields
     * you still have to fill in are never hidden. */
    const [open, setOpen] = useState<boolean | null>(null);

    const load = useCallback(async () => {
        try {
            const cat = await api.getProviderCatalog(cap);
            setCatalog(cat);
            setSelected(cat.current_provider);
            setModel(cat.current_model || '');
            if (cat.last_result) {
                // `configured` was being passed here too. It is not part of
                // this state -- the badge reads it from the catalog -- so it
                // was dropped on the floor while breaking the type.
                setResult({ ok: cat.last_result.ok, detail: cat.last_result.detail });
            }
            onStatus(cap, {
                // `providerLabel` answers "the previous provider" for an absent
                // selection, which reads correctly in the "switched from X"
                // sentence it was written for and not at all here. A capability
                // can now genuinely have nothing selected -- the secret store,
                // until the town picks one -- so say that instead.
                providerName: cat.current_provider
                    ? providerLabel(cat, cat.current_provider)
                    : 'Not chosen yet',
                // So the badge can tell a verdict about this provider from one
                // left behind by the last.
                provider: cat.current_provider,
                onDefault: !cat.default_provider || cat.current_provider === cat.default_provider,
                // The summary above the cards needs this: "which provider is
                // picked" is not what an admin is trying to find out, "which
                // ones still need a key" is.
                configured: cat.configured?.[cat.current_provider] === true,
            });
        } catch (e: any) {
            setError(e?.message || 'Failed to load providers');
        }
    }, [cap, onStatus]);

    useEffect(() => { load(); }, [load]);

    // A cloud-profile switch changes the selected provider server-side; reload so
    // the card reflects the new selection (and its credential fields).
    useEffect(() => {
        if (reloadToken > 0) load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reloadToken]);

    /* Declared here, above the early returns, and not further down with the
       other handlers.


       The effect below is registered on every render -- hooks run before the
       `if (error)` / `if (!catalog)` returns -- but a render that took one of
       those returns never reached a `const handleTest` sitting after them. The
       binding stayed in the temporal dead zone for that render's scope, so
       pressing "Recheck all" while any card was still loading, or after one had
       failed to load its catalog, threw "Cannot access 'handleTest' before
       initialization" and took the whole page down with it. */
    const handleTest = useCallback(async () => {
        setBusy('test'); setResult(null);
        try {
            let t = await api.testProvider(cap);
            /* Maps is the one capability whose real test cannot run on the
             * server. Google enforces its referrer restriction when a map
             * initialises, so the only place the question "will this draw for a
             * resident" has an answer is a browser on the town's own origin --
             * which is this one. The server's verdict covers the APIs being
             * enabled and billing being attached; this covers the map. A browser
             * failure wins, because it is the half residents see. */
            if (cap === 'maps') {
                t = await withBrowserMapVerdict(t);
            }
            setResult(t);
            // `recorded === false` is the provider saying "there is nothing to
            // test here", not "the test failed". Passing t.ok straight through
            // is what put a red "Not working" on an HTTP SMS gateway whose own
            // message said it could not be checked.
            // `configured: false` beats everything: the test looked for the
            // credentials and they are not there, whatever the catalog said.
            onStatus(cap, t.configured === false
                ? { configured: false, verifiable: undefined, verified: null }
                : t.recorded === false
                    ? { verifiable: false, verified: null }
                    : { verifiable: true, verified: t.ok });
        } catch (e: any) {
            setResult({ ok: false, detail: e?.message || 'Test failed' });
            onStatus(cap, { verified: false });
        } finally {
            setBusy(null);
            // The test endpoint records its outcome, so the guide's view of
            // this capability is now stale.
            onChanged?.();
        }
    }, [cap, onStatus, onChanged]);

    // Parent "Recheck all" bumps this token — each card verifies its own live
    // connection and reports the result up for the summary.
    useEffect(() => {
        if (recheckToken > 0) handleTest();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [recheckToken]);

    /* "I know about this one."
     *
     * Held locally so the button reacts at once, then reconciled by the health
     * reload the parent runs. It silences the email and nothing else: the card
     * below keeps whatever colour the connector has earned. */
    const [mutedUntil, setMutedUntil] = useState<string | null | undefined>(undefined);
    const [muting, setMuting] = useState(false);
    const effectiveMute = mutedUntil !== undefined ? mutedUntil : (health?.alerts_muted_until ?? null);
    const toggleMute = useCallback(async () => {
        setMuting(true);
        try {
            const r = await api.muteConnectorAlerts(cap, effectiveMute ? 0 : undefined);
            setMutedUntil(r.muted_until);
            onChanged?.();
        } catch (e: any) {
            /* Said out loud. The button not changing is the honest state --
             * claiming a mute that did not take would produce silence nobody
             * asked for -- but a click that does nothing and says nothing
             * reads as a broken button, and the clerk walks away believing
             * the emails have stopped. */
            setResult({
                ok: false,
                detail: `Alert emails were not ${effectiveMute ? 'resumed' : 'paused'}: `
                    + `${e?.message || 'the request failed'}. They carry on as before.`,
            });
        } finally {
            setMuting(false);
        }
    }, [cap, effectiveMute, onChanged]);

    const discover = useCallback(async (provider: string) => {
        setRefreshingModels(true);
        try {
            const r = await api.refreshAIModels(provider);
            setLiveModels(r.models);
            setModelsMeta({ source: r.source, fetched_at: r.fetched_at });
            return r;
        } finally {
            setRefreshingModels(false);
        }
    }, []);

    /* Pull the model list from the provider on first sight, not only when
       someone presses the button.
       
       The catalog endpoint serves models out of a database cache, and that cache
       is filled by a daily Celery task or by an explicit refresh. Neither has
       happened on a new deployment, and the beat schedule is an interval rather
       than a wall-clock time, so the first automatic run lands 24 hours after the
       worker boots. Until then the picker showed the built-in list and reported
       "curated" -- which is exactly the "it isn't fetching dynamically" this was
       supposed to avoid.
       
       Guarded three ways: AI only, only when the provider's credentials are
       actually present (there is nothing to ask otherwise), and once per mount
       via the ref, so a failing provider cannot turn into a request loop. */
    const autoDiscovered = useRef(false);
    useEffect(() => {
        if (cap !== 'ai' || autoDiscovered.current || !catalog) return;
        const provider = catalog.current_provider;
        const entry = catalog.providers.find(p => p.provider === provider);
        const alreadyLive = entry?.models_source === 'live';
        if (alreadyLive || !catalog.configured?.[provider]) return;
        autoDiscovered.current = true;
        discover(provider).catch(() => {
            // Best-effort. The curated list stays and the button still works.
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [catalog, cap]);

    if (error) {
        return (
            <div className="rounded-2xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" /> {title}: {error}
            </div>
        );
    }
    if (!catalog) {
        return (
            <div
                className={variant === 'full'
                    ? 'premium-card p-6 h-32 animate-pulse'
                    : 'rounded-3xl border border-white/10 bg-white/[0.04] h-24 animate-pulse'}
                aria-busy="true"
            />
        );
    }

    const active: ProviderInfo | undefined = catalog.providers.find(p => p.provider === selected);
    const currentName = catalog.providers.find(p => p.provider === catalog.current_provider)?.name || catalog.current_provider;

    /* Whether the provider actually in use has its credentials stored.
     *
     * `undefined` means the endpoint did not tell us, which is not the same as
     * "no". Three capabilities used to omit this map entirely and every one of
     * their cards claimed "Not configured" on a working connector. The status
     * line now leads with what the last live check found, so an absent answer
     * reads as "not checked yet" rather than as a confident negative. */
    const configuredState = catalog.configured?.[catalog.current_provider];
    const configured = configuredState === true;
    // null means "not touched yet", so an unconfigured card starts open and a
    // configured one starts closed, without overriding a deliberate click.
    /* In guided setup the parent's cursor wins until the clerk clicks a
     * header, at which point their click wins -- being walked through setup
     * should not mean losing the ability to jump back to something. */
    /* In the Spotlight layout the parent owns which card is open, because
     * opening one has to widen its cell in the grid -- a card cannot do that to
     * itself. Everywhere else the card keeps deciding for itself. */
    const controlled = variant !== 'full';
    const isOpen = controlled
        ? !!expanded
        : (open !== null ? open : (guided ? !!step?.active : !configured));
    const toggle = controlled
        ? () => onExpandToggle?.()
        : () => setOpen(v => (v === null ? !isOpen : !v));

    const shown: CapabilityState = state ?? (configured ? 'unchecked' : 'unset');
    const bad = shown === 'failing';
    /* A bubble is the collapsed form of a healthy capability. Opening one
     * promotes it to the wide treatment, so the fields never appear inside a
     * third of a column. */
    const compact = variant === 'bubble' && !isOpen;
    const lastChecked = currentHealth?.status === 'working'
        ? currentHealth.last_success_at : currentHealth?.last_error_at;
    /* Named, when the only check on file was of something else. "Not checked
     * yet" would be true and unhelpful -- it hides that there IS an answer and
     * that it is about a provider this town has moved off. */
    const checkedLine = lastChecked
        ? `Checked ${relativeTime(lastChecked)}`
        : staleHealth
            ? `Not checked since switching from ${providerLabel(catalog, health?.provider)}`
            : 'Not checked yet';
    /* The provider's own words when there are any. A clerk searching the web
     * for their error needs the actual string, not our paraphrase of it. */
    const spotlightDetail = bad
        ? (currentHealth?.last_error || currentHealth?.last_result || currentHealth?.summary || 'The last check failed.')
        : shown === 'unchecked'
            ? 'Nothing has used this yet, so we cannot say whether it works.'
            : blurb;

    const handleSave = async () => {
        if (!active) return;
        setBusy('save'); setResult(null); setError(null);
        try {
            const settings: Record<string, string> = {};
            // Trim on save — a stray space from copy-paste is the #1 cause of a
            // "correct" key failing. Trimming here keeps mid-word typing intact.
            active.credential_fields.forEach(f => {
                const v = (values[f.key] || '').trim();
                if (v) settings[f.key] = v;
            });
            const saved = await api.saveProvider(cap, { provider: selected, model: model || undefined, settings });
            // Shown even though the save succeeded. These are "that value does
            // not look like what this field wants" -- most often the right
            // credential in the wrong box, which the connection test below may
            // not distinguish from a wrong key.
            setWarnings(saved.warnings || []);
            setValues({});
            await load();

            // Selecting a provider saves; it does not configure one. With no
            // credentials entered the settings payload is empty, which the
            // backend reads as "keep what is stored" -- so the save succeeds,
            // answers ok:true, and the card used to say "Set up" about a
            // service with no account SID in it.
            //
            // Said here rather than left to the test below, because the test's
            // own message ("Account SID or auth token is missing") reads as a
            // connection failure rather than as "you have not finished".
            if (saved.configured === false) {
                setResult({
                    ok: false,
                    detail: saved.missing?.length
                        ? `Saved your choice of provider. Still needed before this can work: ${saved.missing.join(', ')}.`
                        : 'Saved your choice of provider. Its credentials still need to be entered.',
                });
                onStatus(cap, { configured: false, verifiable: undefined, verified: null });
                return;
            }

            // Immediately verify
            const t = await api.testProvider(cap);
            setResult(t);
            onStatus(cap, t.configured === false
                ? { configured: false, verifiable: undefined, verified: null }
                : t.recorded === false
                    ? { verifiable: false, verified: null }
                    : { verifiable: true, verified: t.ok });
        } catch (e: any) {
            setError(e?.message || 'Save failed');
        } finally {
            setBusy(null);
            onChanged?.();
        }
    };

    const handleRefreshModels = async () => {
        try {
            const r = await discover(selected);
            // Staleness only meaningful for the currently-active provider.
            if (catalog && selected === catalog.current_provider) {
                setStaleOverride(r.current_model_available === false);
            }
        } catch {
            // keep the existing list on failure — discovery is best-effort
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            /* The handcrafted card template, the same one the rest of the
               console uses, rather than a green wash unique to this page. The
               wash was doing a job the status line does better and said the
               same thing three times -- tile, tint and badge all meaning
               "configured", which is the least interesting fact here. */
            className={variant === 'full'
                ? 'premium-card overflow-hidden p-5'
                : compact
                    ? 'group relative h-full px-4 py-4 rounded-3xl bg-gradient-to-br from-white/[0.06] via-white/[0.02] to-indigo-950/40 border border-white/10 backdrop-blur-2xl hover:border-primary-400/40 hover:-translate-y-0.5 transition-all duration-300'
                    : `relative overflow-hidden p-5 sm:p-6 rounded-3xl border backdrop-blur-2xl shadow-[0_14px_40px_rgba(0,0,0,0.4)] ${bad
                        ? 'bg-gradient-to-br from-red-500/[0.14] via-white/[0.02] to-indigo-950/40 border-red-400/30'
                        : 'bg-gradient-to-br from-white/[0.08] via-white/[0.02] to-indigo-950/40 border-white/15'}`}
        >

            <div className="relative">
                {/* ── The bubble: a healthy capability, collapsed ── */}
                {compact ? (
                    <button
                        type="button"
                        onClick={toggle}
                        aria-expanded={false}
                        aria-controls={`prov-${cap}`}
                        className="w-full text-left rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
                    >
                        <div className="flex items-center gap-3">
                            <CapabilityTile icon={Icon} />
                            <span className="min-w-0 flex-1">
                                <span className="block font-semibold text-white text-sm truncate">{title}</span>
                                <span className="block text-[11px] text-white/50 truncate">
                                    {configured ? currentName : 'Not set up'}
                                </span>
                            </span>
                            <span
                                className={`shrink-0 ${shown === 'working' ? 'text-emerald-300' : 'text-white/30'}`}
                                aria-hidden="true"
                            >
                                {shown === 'working' ? <Check className="w-4 h-4" />
                                    : shown === 'unverifiable' ? <HelpCircle className="w-4 h-4" />
                                    : <CircleDashed className="w-4 h-4" />}
                            </span>
                        </div>
                        {/* Nothing about check times on something with no
                            credentials: the tile already says "Not set up", and
                            "Not checked yet" underneath reads as a second,
                            different problem. */}
                        {configured ? (
                            <span className="block text-[11px] text-white/45 mt-2.5">
                                {shown === 'unverifiable'
                                    ? (currentHealth?.last_result || 'Set up. There is no way to test this one from here.')
                                    : checkedLine}
                            </span>
                        ) : (
                            /* Somewhere to go.
                             *
                             * A card that says "Not set up" and stops is a dead
                             * end: the questionnaire and the steps are back up
                             * the page, and nothing here said so. This is a
                             * town that asked for the feature, so the useful
                             * thing is the next action, not the diagnosis. */
                            <span className="block text-[11px] text-amber-200/85 mt-2.5">
                                Add its credentials in Setup Instructions, above.
                            </span>
                        )}
                    </button>
                ) : variant !== 'full' ? (
                    /* ── The spotlight: something is wrong, or the clerk opened it ── */
                    <>
                        <div
                            className="aurora-glow w-64 h-64 -top-24 -left-10 opacity-45 pointer-events-none"
                            style={bad ? { background: 'radial-gradient(closest-side, rgba(244,63,94,0.5), transparent)' } : undefined}
                            aria-hidden="true"
                        />
                        <div className="relative flex items-start gap-5 flex-wrap">
                            <CapabilityTile icon={Icon} size="lg" tone={bad ? 'alert' : 'normal'} />
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-3 flex-wrap">
                                    <h3 className="font-bold text-lg text-white tracking-tight">{title}</h3>
                                    <StatusPill state={shown} />
                                </div>
                                <p className={`text-sm mt-1.5 whitespace-pre-line ${bad ? 'text-red-100/90' : 'text-white/70'}`}>
                                    {spotlightDetail}
                                </p>
                                {effectiveMute && (
                                    <p className="text-xs text-amber-200/85 mt-1.5">
                                        Nobody is being emailed about this until{' '}
                                        {new Date(effectiveMute).toLocaleDateString()}. It is still not working.
                                    </p>
                                )}
                                <p className="text-xs text-white/50 mt-1.5">
                                    {configured ? currentName : 'No provider credentials yet'}
                                    {cap === 'ai' && catalog.current_model ? ` · ${catalog.current_model}` : ''}
                                    {/* A check time under "not set up" reads as a
                                        contradiction: there are no credentials, so
                                        whatever was checked was not this. */}
                                    {configured && lastChecked ? ` · checked ${relativeTime(lastChecked)}` : ''}
                                </p>
                            </div>
                            <div className="flex items-center gap-2 shrink-0">
                                {/* Only where there is an alert to silence. A
                                    capability nobody is being emailed about
                                    does not need an off switch. */}
                                {/* Only where something is actually alerting.
                                    Derived from the health status rather than
                                    from the pill, because "not checked yet"
                                    covers both `unknown`, which never alerts,
                                    and `stale`, which does. */}
                                {(hasAlert(health?.status) || !!effectiveMute) && (
                                    <Action onClick={toggleMute} busy={muting} disabled={muting}
                                        title={effectiveMute
                                            ? 'Start emailing administrators about this again'
                                            : 'Stop emailing administrators about this for a week. The card stays as it is.'}>
                                        {effectiveMute ? 'Unmute' : 'Mute alerts'}
                                    </Action>
                                )}
                                {configured && (
                                    <Action onClick={handleTest} busy={busy === 'test'} disabled={busy !== null}>
                                        {busy === 'test' ? 'Testing…' : 'Test now'}
                                    </Action>
                                )}
                                <Action variant="primary" onClick={toggle} chevron>
                                    {isOpen ? 'Close' : configured ? 'Edit' : 'Set up'}
                                </Action>
                            </div>
                        </div>
                    </>
                ) : (
                <>
                {/* Header — same shape as every other connector card: a large
                    gradient icon tile, the name, and a status pill on the right. */}
                {/* Not one big button any more. "Test now" has to be its own
                    control, and a button cannot live inside a button -- nesting
                    them produces markup browsers silently restructure and
                    screen readers announce wrongly. */}
                <div className="flex items-start justify-between gap-3 flex-wrap">
                    <button
                        type="button"
                        onClick={toggle}
                        aria-expanded={isOpen}
                        aria-controls={`prov-${cap}`}
                        className="flex items-center gap-3.5 min-w-0 flex-1 text-left rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
                    >
                        <CapabilityTile
                            icon={Icon}
                            badge={guided && step && !configured ? step.index + 1 : undefined}
                        />
                        <div className="min-w-0">
                            <h3 className="font-semibold text-white leading-tight truncate">{title}</h3>
                            {/* The one line worth reading at a glance: which
                                provider, whether it works, and when we last had
                                evidence. "Configured" answered none of those --
                                it only ever meant a row exists in our database. */}
                            <p className="text-white/45 text-xs truncate mt-0.5">
                                {configured
                                    ? <>
                                        {currentName}
                                        {cap === 'ai' && catalog.current_model ? ` · ${catalog.current_model}` : ''}
                                        {' · '}<LiveState health={currentHealth} />
                                      </>
                                    : 'Not set up yet'}
                            </p>
                        </div>
                    </button>
                    <div className="flex items-center gap-2 shrink-0">
                        {/* Only offered once there is something to test.
                            Pressing it on an empty card would report a missing
                            credential as a failure, which is true and useless. */}
                        {configured && (
                            <Action size="sm" onClick={handleTest} busy={busy === 'test'} disabled={busy !== null}>
                                {busy === 'test' ? 'Testing…' : 'Test now'}
                            </Action>
                        )}
                        <Action size="sm" variant="primary" onClick={toggle} chevron>
                            {isOpen ? 'Close' : configured ? 'Edit' : 'Set up'}
                        </Action>
                    </div>
                </div>

                <p className="text-white/60 text-sm mb-4">{blurb}</p>
                </>
                )}

            {!compact && warnings.length > 0 && (
                <div className="mt-3 space-y-1.5">
                    {warnings.map(w => (
                        <div key={w.key} className={`rounded-xl px-3 py-2.5 text-xs border flex items-start gap-2 ${w.severity === 'error'
                            ? 'bg-amber-500/10 border-amber-400/30 text-amber-100/90'
                            : 'bg-white/[0.04] border-white/12 text-white/65'}`}>
                            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />
                            <span><span className="font-semibold">{w.key}</span> — {w.message}</span>
                        </div>
                    ))}
                </div>
            )}
            {!compact && shownResult && (
                <motion.div
                    initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                    className={`mt-3 rounded-xl px-3 py-2.5 text-xs border flex items-start gap-2 ${shownResult.ok
                        ? 'bg-emerald-500/10 border-emerald-400/30 text-emerald-200'
                        : resultUncheckable
                            ? 'bg-white/[0.05] border-white/15 text-white/70'
                            : 'bg-amber-500/10 border-amber-400/30 text-amber-200'}`}
                >
                    {shownResult.ok
                        ? <CheckCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                        : resultUncheckable
                            ? <HelpCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                            : <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                    {/* pre-line: the tests report their work as numbered steps,
                        one per line — collapsing them to a paragraph turns a
                        verifiable log back into a claim. */}
                    <span className="whitespace-pre-line">{shownResult.detail}</span>
                </motion.div>
            )}

            {/* Configuration is always visible. It used to sit behind a
                "Configure" disclosure, which meant the fields a deployment
                actually has to fill in were one click away from being missed,
                and left the card looking finished when nothing was set. */}
            <AnimatePresence initial={false}>
            {isOpen && (
            <motion.div
                id={`prov-${cap}`}
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden"
            >
            <div className="mt-4 pt-4 border-t border-white/10 space-y-5">
                {/* No provider picker here.
                 *
                 * Which provider runs each capability is answered once, in the
                 * questionnaire at the top of this page, and that answer drives
                 * the setup guide. A second picker on the card was a second
                 * place to decide -- the two could disagree, and there was
                 * nowhere to see which one the town had actually meant. These
                 * cards edit the credentials of the provider already chosen. */}
                {(active?.description || active?.boundary) && (
                    <div className="rounded-lg bg-white/[0.03] border border-white/10 px-3 py-2 space-y-1">
                        {active?.description && <p className="text-white/55 text-xs leading-relaxed">{active.description}</p>}
                        {active?.boundary && (
                            <p className="text-white/60 text-[11px] flex items-center gap-1.5">
                                <ShieldCheck className="w-3 h-3 text-primary-300/70 shrink-0" aria-hidden="true" />
                                Compliance boundary: {active.boundary}
                            </p>
                        )}
                    </div>
                )}

                {/* AI model dropdown — with live discovery */}
                {cap === 'ai' && active && (() => {
                    const models = liveModels ?? active.models ?? [];
                    const source = modelsMeta?.source ?? active.models_source;
                    const fetchedAt = modelsMeta?.fetched_at ?? active.models_fetched_at;
                    const isStale = staleOverride !== null
                        ? staleOverride
                        : (selected === catalog.current_provider && catalog.current_model_available === false);
                    if (!models || models.length === 0) return null;
                    return (
                        <div>
                            <Step n={1} aside={
                                <button
                                    type="button"
                                    onClick={handleRefreshModels}
                                    disabled={refreshingModels}
                                    className="shrink-0 inline-flex items-center gap-1 text-[11px] text-white/60 hover:text-white transition-colors disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60 rounded"
                                >
                                    <RefreshCw className={`w-3 h-3 ${refreshingModels ? 'animate-spin' : ''}`} aria-hidden="true" />
                                    {refreshingModels ? 'Checking…' : 'Refresh from provider'}
                                </button>
                            }>Model</Step>
                            {/* Tiles, not a <select>. The choice is consequential and
                                a dropdown hides every option but one -- including the
                                "new" markers live discovery just added. Same control as
                                the provider picker above, so the page has one idiom.

                                Searchable once the list is long enough to scroll:
                                Vertex Model Garden legitimately returns a couple of
                                hundred entries, and scanning those as tiles is worse
                                than the dropdown this replaced. */}
                            {models.length > 8 && (
                                <div className="relative mb-2">
                                    <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-white/50 pointer-events-none" aria-hidden="true" />
                                    <input
                                        type="search"
                                        id={`model-search-${cap}`}
                                        value={modelSearch}
                                        onChange={e => setModelSearch(e.target.value)}
                                        /* A real label, not just a placeholder: the
                                           placeholder disappears the moment somebody
                                           types, and this console is audited for AA. */
                                        aria-label={`Search the ${models.length} models ${active.name} offers`}
                                        placeholder={`Search ${models.length} models — try "flash", "claude", "mini"`}
                                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl pl-9 pr-3 py-1.5 text-xs text-white placeholder-white/50 focus:outline-none focus:border-primary-400/60 transition-colors"
                                    />
                                </div>
                            )}
                            {(() => {
                                const chosen = model || active.default_model || models[0].id;
                                const q = modelSearch.trim().toLowerCase();
                                const filtered = q
                                    ? models.filter(m => m.id.toLowerCase().includes(q)
                                        || m.label.toLowerCase().includes(q))
                                    : models;
                                if (filtered.length === 0) {
                                    return (
                                        <p className="py-4 text-center text-xs text-white/60 bg-white/[0.02] border border-white/10 rounded-xl">
                                            No model matches “{modelSearch}”.
                                        </p>
                                    );
                                }
                                return (
                                    <div className="max-h-80 overflow-y-auto pr-1 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2" role="radiogroup" aria-label="AI model">
                                        {filtered.map(m => {
                                            const isSel = m.id === chosen;
                                            return (
                                                <button
                                                    key={m.id}
                                                    type="button"
                                                    role="radio"
                                                    aria-checked={isSel}
                                                    onClick={() => setModel(m.id)}
                                                    className={`relative text-left rounded-xl px-3 py-2.5 border transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60 ${isSel
                                                        ? 'bg-gradient-to-br from-primary-500/25 to-primary-700/15 border-primary-400/50 shadow-lg shadow-primary-900/30'
                                                        : 'bg-white/[0.03] border-white/10 hover:bg-white/[0.06] hover:border-white/20'}`}
                                                >
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className={`text-sm font-medium truncate ${isSel ? 'text-white' : 'text-white/70'}`}>{m.label}</span>
                                                        {isSel && (
                                                            <span className="shrink-0 w-4 h-4 rounded-full bg-primary-400 flex items-center justify-center">
                                                                <Check className="w-3 h-3 text-primary-950" strokeWidth={3} />
                                                            </span>
                                                        )}
                                                    </div>
                                                    {m.discovered && (
                                                        <span className="text-[10px] font-semibold uppercase tracking-wide text-primary-300/90 mt-1 inline-block">New</span>
                                                    )}
                                                </button>
                                            );
                                        })}
                                    </div>
                                );
                            })()}
                            <p className="text-[10px] text-white/40 mt-1.5">
                                {source === 'live'
                                    ? `Live from ${active.name}${fetchedAt ? ` · updated ${agoLabel(fetchedAt)}` : ''}`
                                    : refreshingModels
                                        ? `Checking ${active.name} for its current models…`
                                        : 'Built-in list. It refreshes from the provider automatically once credentials are saved — or press “Refresh from provider”.'}
                            </p>
                            {isStale && (
                                <div className="mt-2 rounded-lg bg-amber-500/10 border border-amber-400/30 px-3 py-2 text-[11px] text-amber-200 flex items-start gap-2">
                                    <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />
                                    The model you’re using ({catalog.current_model}) is no longer offered by {active.name}. Pick a current one above and save.
                                </div>
                            )}
                        </div>
                    );
                })()}

                {/* Credential/config fields */}
                {/* Also when the provider collects nothing but needs something.
                    Gating on credential_fields alone meant a provider whose
                    credentials all live on another card -- the secret store,
                    every one of whose providers collects nothing -- skipped this
                    block entirely, so "Not set up" appeared with no box and no
                    reason. */}
                {active && ((active?.credential_fields || []).length > 0
                    || (active?.requires || []).length > 0) && (() => {
                    const alreadySet = !!(catalog.configured?.[selected] && selected === catalog.current_provider);

                    /* Steps own the boxes they produce, so each instruction is
                     * followed immediately by the inputs it just told you how to
                     * obtain. A provider with no steps written yet falls back to
                     * the plain list, which is what every provider had before.
                     *
                     * The layout lives in ProviderCredentialSteps because the
                     * setup guide renders the same walk inline. One component,
                     * one content file, two mount points -- the alternative was
                     * two hand-written copies, which is what this page had
                     * before and which drifted. */
                    const hasSteps = stepsFor(cap, selected, stepCtx).length > 0;

                    return (
                        <div>
                            <Step n={cap === 'ai' ? 2 : 1}>{hasSteps ? 'Set it up' : 'Credentials'}</Step>
                            <ProviderCredentialSteps
                                cap={cap}
                                provider={selected}
                                active={active}
                                values={values}
                                onChange={(key, value) => setValues(p => ({ ...p, [key]: value }))}
                                ctx={stepCtx}
                                identity={identity}
                                storedFields={catalog.stored_fields}
                                hostProvided={catalog.host_provided}
                                alreadySet={alreadySet}
                            />
                        </div>
                    );
                })()}

                <div className="flex flex-wrap items-center gap-2.5 pt-1 border-t border-white/5 mt-1">
                    {/* No Save where there is nothing this card may change. The
                        secret store is repointed by the cloud-profile flow,
                        which moves the existing credentials across; a Save
                        button here would post a selection the API refuses, and
                        a button that always errors is worse than no button. */}
                    {catalog.selectable !== false && (
                        <Action variant="primary" onClick={handleSave} busy={busy === 'save'} disabled={busy !== null}>
                            {busy === 'save' ? 'Saving…' : 'Save & Test'}
                        </Action>
                    )}
                    <Action onClick={handleTest} busy={busy === 'test'} disabled={busy !== null}>
                        {busy === 'test' ? 'Testing…' : 'Test connection'}
                    </Action>
                    {cap === 'identity' && (
                        <span className="text-white/60 text-[11px] ml-auto hidden sm:block">Auth0 by default · Entra, Okta and any OIDC provider also supported</span>
                    )}
                    {/* On the far side of the row from Save, so a hand reaching
                        for one cannot land on the other. Off means off-and-kept:
                        the tile this card collapses into says the credentials
                        are still saved, and switching back on picks up as it
                        was. */}
                    {onSwitchOff && (
                        <span className="ml-auto inline-flex items-center gap-2.5"
                            title="Stops this service running and stops testing it. Everything entered here stays saved.">
                            <span className="text-white/45 text-[11px] hidden sm:block">
                                {switching ? 'Switching off…' : 'On — switching off keeps the credentials saved'}
                            </span>
                            <Switch
                                on={!switching}
                                busy={switching}
                                disabled={switching || busy !== null}
                                onChange={async () => {
                                    setSwitching(true);
                                    try { await onSwitchOff(); } finally { setSwitching(false); }
                                }}
                                label={`Switch ${title} off`}
                            />
                        </span>
                    )}
                </div>
            </div>
            </motion.div>
            )}
            </AnimatePresence>
            </div>
        </motion.div>
    );
}


/**
 * A setting with no provider behind it, drawn as one of the cards.
 *
 * Backups and crash reporting were rendered below the grid in an "Other
 * settings" block, in a completely different treatment and at a different
 * size, and they read as a separate class of thing. They are not: they are
 * something the town either has set up or has not, exactly like the eight
 * above them. The only real difference is that there is nothing to pick
 * between and nothing to test, so the card carries no provider name and no
 * "Test now".
 *
 * Same shell, same tile, same expand-to-full-width behaviour, so restyling the
 * capability cards restyles these too rather than leaving them behind again.
 */
export interface PlainSetting {
    id: string;
    title: string;
    subtitle: string;
    icon: typeof Sparkles;
    fields: { key: string; label: string; secret?: boolean; help?: string }[];
    configured: boolean;
    /** Anything the plain fields cannot express -- the backup passphrase, which
     *  is generated rather than typed and shown exactly once. */
    body?: ReactNode;
    /** Why saving is refused, if it is. */
    blockedReason?: string | null;
}

function PlainSettingCard({ setting, expanded, onToggle, secrets }: {
    setting: PlainSetting;
    expanded: boolean;
    onToggle: () => void;
    secrets: PlainSecretsBridge;
}) {
    const { title, subtitle, icon: Icon, fields, configured, body } = setting;
    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className={!expanded
                ? 'group relative h-full px-4 py-4 rounded-3xl bg-gradient-to-br from-white/[0.06] via-white/[0.02] to-indigo-950/40 border border-white/10 backdrop-blur-2xl hover:border-primary-400/40 hover:-translate-y-0.5 transition-all duration-300'
                : 'relative overflow-hidden p-5 sm:p-6 rounded-3xl border backdrop-blur-2xl shadow-[0_14px_40px_rgba(0,0,0,0.4)] bg-gradient-to-br from-white/[0.08] via-white/[0.02] to-indigo-950/40 border-white/15'}
        >
            <div className="relative">
                {!expanded ? (
                    <button
                        type="button"
                        onClick={onToggle}
                        aria-expanded={false}
                        className="w-full text-left rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
                    >
                        <div className="flex items-center gap-3">
                            <CapabilityTile icon={Icon} />
                            <span className="min-w-0 flex-1">
                                <span className="block font-semibold text-white text-sm truncate">{title}</span>
                                <span className="block text-[11px] text-white/50 truncate">
                                    {configured ? subtitle : 'Not set up'}
                                </span>
                            </span>
                            <span className={`shrink-0 ${configured ? 'text-emerald-300' : 'text-white/30'}`} aria-hidden="true">
                                {configured ? <Check className="w-4 h-4" /> : <CircleDashed className="w-4 h-4" />}
                            </span>
                        </div>
                        <span className={`block text-[11px] mt-2.5 ${configured ? 'text-white/45' : 'text-amber-200/85'}`}>
                            {configured
                                ? 'Set up. Nothing here reports back, so there is nothing to test.'
                                : 'Add its details in Setup Instructions, above.'}
                        </span>
                    </button>
                ) : (
                    <>
                        <div className="flex items-start justify-between gap-4 flex-wrap">
                            <div className="flex items-start gap-4 min-w-0 flex-1">
                                <CapabilityTile icon={Icon} size="lg" />
                                <div className="min-w-0">
                                    <div className="flex items-center gap-3 flex-wrap">
                                        <h3 className="font-bold text-lg text-white tracking-tight">{title}</h3>
                                        <StatusPill state={configured ? 'done' : 'unset'} />
                                    </div>
                                    <p className="text-sm text-white/60 mt-1">{subtitle}</p>
                                </div>
                            </div>
                            <Action variant="primary" onClick={onToggle} chevron>Close</Action>
                        </div>
                        <div className="mt-4 pt-4 border-t border-white/10">
                            <PlainSecrets
                                fields={fields}
                                values={secrets.values}
                                onChange={secrets.onChange}
                                onSave={secrets.onSave}
                                saving={secrets.saving}
                                isConfigured={secrets.isConfigured}
                                onSaved={secrets.onSaved}
                                blockedReason={setting.blockedReason}
                            />
                            {body}
                        </div>
                    </>
                )}
            </div>
        </motion.div>
    );
}

export interface PlainSecretsBridge {
    values: Record<string, string>;
    onChange: (key: string, value: string) => void;
    onSave: (keys: string[]) => Promise<void>;
    saving: string | null;
    isConfigured: (key: string) => boolean;
    onSaved: () => void;
}

export default function ServiceProviders({ show, statusMap, extras, footer, extraOff = [], plainSettings = [], plainSecrets, refreshToken = 0, onChanged, publicOrigin = null, onSwitch }: {
    /* Which capabilities the town said it wants, from the setup questions.
     * Undefined means "no answer yet", which shows everything -- an absent
     * answer must not read as "wanted nothing", the same distinction the
     * configured badges make between unknown and no.
     *
     * Sign-in and maps are never filtered: a town cannot take a report without
     * them, so hiding them behind a question would let someone opt out of
     * having a working system. */
    show?: Set<Capability>;
    /* What the server says about each capability, for the ones no card is
     * drawn for.
     *
     * A switched-off capability has no card, so nothing fetches its catalog and
     * this section cannot otherwise tell "switched off, key still saved" from
     * "switched off, never configured". Those need different sentences: the
     * first has to say the credentials are still there, or the obvious reading
     * of a greyed-out tile is that switching it back on means starting over. */
    statusMap?: import('../services/api').ProviderStatusMap | null;
    /* The settings that are not a provider choice -- the Google Cloud
     * credentials, error reporting, database backups. They lived in a second
     * collapsible section beside this one, which meant two places to look for
     * "the thing I have not configured yet" and a Setup Progress bar counting
     * across both. Rendered here instead, after the capability cards, so the
     * page has one list. */
    extras?: ReactNode;
    /** One quiet line under everything. Not a section: the block this replaced
     *  had a heading announcing a group that ended up containing one sentence. */
    footer?: ReactNode;
    /* Features the town switched off that are not capabilities.
     *
     * Backups and crash reporting have no provider catalog, so they are absent
     * from CAPS and could never appear in the switched-off list -- which is
     * exactly the pair somebody is most likely to untick and then wonder where
     * they went. Passed in rather than hardcoded here, because the
     * questionnaire owns the feature list. */
    extraOff?: { id: string; title: string; blurb: string; icon: typeof Sparkles }[];
    /** Settings with no provider behind them, drawn as cards in the same grid.
     *  They were in a separate block below, in a different treatment at a
     *  different size, reading as a different class of thing. */
    plainSettings?: PlainSetting[];
    plainSecrets?: PlainSecretsBridge;
    /* Bumped by the page when the setup guide above saves something.
     *
     * The guide and these cards are two views of one set of credentials, on the
     * same screen, and until now neither told the other anything: a key entered
     * in the guide left the card below still reading "Not set up", and the
     * obvious conclusion from that is that the save did not take. */
    refreshToken?: number;
    /** The reverse direction: something changed down here, so the guide's ticks
     *  are stale. */
    onChanged?: () => void;
    /* Switch a capability (or backups/errors, by feature id) on or off,
     * credentials intact. Owned by the page, which holds the persisted
     * questionnaire ticks -- the same switch, reachable from the card, so
     * turning something off does not mean finding the question that owns it
     * three sections up. */
    onSwitch?: (id: string, on: boolean) => Promise<void>;
    /** The address residents use, for the callback URLs in the console walks. */
    publicOrigin?: string | null;
} = {}) {
    const [recheckToken, setRecheckToken] = useState(0);
    /* Whether this server has an identity the cloud attached to it. Fetched
     * once for the whole section -- the answer is the same for every card, and
     * the probe is a metadata call that either answers instantly or times out.
     * Failure is silent and reads as "no identity", which just means the cards
     * ask for credentials the way they always did. */
    const [identity, setIdentity] = useState<CloudIdentity | null>(null);
    useEffect(() => {
        let cancelled = false;
        api.getCloudIdentity()
            .then(i => { if (!cancelled) setIdentity(i); })
            .catch(() => undefined);
        return () => { cancelled = true; };
    }, []);
    /* One request for the whole section rather than one per card: the endpoint
     * returns every connector, and four parallel calls on page load for data
     * that arrives together is wasteful.
     *
     * Refetched whenever a recheck runs, because a recheck is precisely the
     * moment the answer changes. Failure is silent -- the pill degrades to
     * "not used yet", which is honest: if we cannot read the health table, we
     * do not know. */
    const [health, setHealth] = useState<Record<string, ConnectorHealth>>({});
    /* Whether the health table could be read at all.
     *
     * The catch used to set an empty map, which renders as "not checked yet"
     * on every card -- indistinguishable from a town that genuinely has not
     * run a check, and silent about the fact that the request failed. */
    const [healthUnavailable, setHealthUnavailable] = useState(false);
    const loadHealth = useCallback(async () => {
        try {
            const report = await api.getConnectorHealth();
            setHealth(Object.fromEntries(report.connectors.map(c => [c.connector, c])));
            setHealthUnavailable(false);
        } catch {
            setHealth({});
            setHealthUnavailable(true);
        }
    }, []);
    useEffect(() => { loadHealth(); }, [loadHealth, recheckToken, refreshToken]);

    /* Testing one card records a result the other cards' badges read from, so
     * the stored view is stale the moment any test finishes. Previously only
     * "Recheck all" refetched, which is why a single test looked like it had
     * not persisted: the badge was still rendering the health row from page
     * load. */
    const refreshAfterTest = useCallback(() => { loadHealth(); onChanged?.(); }, [loadHealth, onChanged]);

    const [reloadToken, setReloadToken] = useState(0);
    /* A save in the guide above changes exactly what these cards read, so pull
     * both the catalogs and the health rows again. */
    useEffect(() => {
        if (refreshToken > 0) setReloadToken(t => t + 1);
    }, [refreshToken]);
    const [statuses, setStatuses] = useState<Record<string, CapStatus>>({});

    const onStatus = useCallback((cap: Capability, s: CapStatus) => {
        setStatuses(prev => ({ ...prev, [cap]: { ...prev[cap], ...s } }));
    }, []);

    /* Not optional, so never filed under "switched off". Staff have to sign in
     * and residents have to drop a pin -- and every credential either of those
     * needs is kept by the secret store, which is not a feature a town ticks. */
    const ALWAYS = new Set<Capability>(['identity', 'maps', 'secrets']);
    const visible = CAPS.filter(c => !show || ALWAYS.has(c.key) || show.has(c.key));
    /* The other half of the answer.
     *
     * Hiding what a town did not tick made the page honest about what it has
     * and silent about what it could have -- so "we cannot do that" became the
     * standing assumption for anything switched off during a five-minute
     * questionnaire months earlier. There is no way to discover otherwise
     * except by going back to a page that does not say it holds the answer.
     *
     * Listed, not offered. Turning one on means going through its setup, and a
     * toggle here that quietly enabled a capability with no credentials behind
     * it would be a switch that appears to work and does nothing. */
    /* Whether the credentials for a switched-off capability are still stored.
     *
     * The requirement in the reporter's own words: "I can for example save an
     * email or AI key but not use it and then this is reflected in the service
     * provider card but things are still saved." A tile that only says "not
     * switched on" leaves the second half unanswered, and the safe assumption
     * from a greyed-out card is that whatever was entered has gone. */
    const stillSaved = (id: string): boolean => {
        const entry = statusMap?.[id];
        const provider = entry?.current_provider;
        return !!provider && entry?.configured?.[provider] === true;
    };

    const notChosen = [
        ...(show ? CAPS.filter(c => !ALWAYS.has(c.key) && !show.has(c.key)) : []).map(c => ({
            id: c.key as string, title: c.title, blurb: c.blurb, icon: c.icon,
        })),
        ...extraOff,
    ];
    /* Which switched-off tile is mid-flight. One at a time: the write lands on
     * a shared settings row, and two optimistic toggles racing each other is
     * how a tick springs back with no error to explain it. */
    const [switchingOnId, setSwitchingOnId] = useState<string | null>(null);

    /* Order matters, and not only for readability.
     *
     * A credential saved before the secret store is reachable lands in the
     * encrypted database instead, and until now nothing said so. The store is
     * made reachable by the cloud credentials entered under Other settings, so
     * anything asking for a key has to come after the town has had the chance
     * to enter those -- which is what the ordering note below tells them, since
     * the bootstrap card itself lives outside this list. */
    const loaded = visible.filter(c => statuses[c.key]);
    const configuredCount = (loaded || []).filter(c => statuses[c.key]?.configured).length;

    /* No guided walk here any more.
     *
     * This section used to run its own step-by-step: a progress bar, a "Step 3
     * of 8" cursor, and a "Skip the guide" link. The setup guide at the top of
     * the page does that job, grouped by the account you sign in to rather than
     * by capability, and having both meant two progress indicators that counted
     * differently and two places a town could be told what to do next.
     *
     * What is left is what these cards are for: is it working, and where do I
     * change it. */
    const verifiedCount = (loaded || []).filter(c => statuses[c.key]?.verified === true).length;
    const failedCount = (loaded || []).filter(c => statuses[c.key]?.verified === false).length;
    // Counted separately, and never as "needs attention": nobody can act on it.
    const unverifiableCount = (loaded || []).filter(c => statuses[c.key]?.verifiable === false).length;

    /* Which card the clerk has opened in the Spotlight layout. Held here rather
     * than in the card, because opening one has to widen its cell. */
    /* Which card is expanded. A string rather than a Capability, because the
     * plain settings -- backups, crash reporting -- share this grid and this
     * behaviour, and giving them a second piece of open-state would let two
     * cards be open at once. */
    const [openCap, setOpenCap] = useState<string | null>(null);
    /* `show` is the town's answer to "do you want this", so it is also the
     * `enabled` the state machine needs -- derived here rather than passed in
     * again, because two props carrying the same fact is how the page ended up
     * with a questionnaire and a set of cards that could disagree. */
    const capState = (cap: Capability) => capabilityState(
        { ...statuses[cap], enabled: !show || ALWAYS.has(cap) || show.has(cap) },
        health[cap],
    );

    /* Rendered above the cards. A town whose health table cannot be read
     * should be told that, rather than shown ten badges that all say "not
     * checked yet" and mean "we could not ask". */
    const healthNotice = healthUnavailable ? (
        <div role="alert" className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
            The status of these services could not be read, so the badges below
            show what was last known rather than what is true now.
        </div>
    ) : null;
    /* Only what is wrong gets the whole width.
     *
     * "Not set up" is deliberately not in this list, though the first version
     * of it was. Rendered against a town mid-setup that put six of eight
     * capabilities into full-width cards, which is the wall this layout exists
     * to avoid -- and it was shouting about work the setup guide above is
     * already walking somebody through. Something switched off is not a fault.
     *
     * A capability still loading its catalog has no state yet, and guessing one
     * would mean flashing "not working" at a town whose page is merely slow. It
     * waits in the bubble grid, where it renders as a skeleton. */
    const spotlit = visible.filter(c => {
        const s = capState(c.key);
        // 'unverifiable' is excluded on purpose. It never resolves -- a generic
        // HTTP gateway will never become checkable -- so spotlighting it would
        // leave a permanent card demanding attention nobody can give it.
        return s === 'failing' || s === 'unchecked';
    });
    const bubbles = visible.filter(c => !spotlit.includes(c));

    return (
        <CollapsibleSection
            title="Service Providers"
            icon={Sparkles}
            accent="primary"
            defaultOpen={true}
            subtitle="Whether each one is working, and where to change its credentials"
            trailing={
                <button
                    onClick={() => setRecheckToken(t => t + 1)}
                    className="inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium text-white/80 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
                >
                    <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" /> Recheck
                </button>
            }
        >
            {healthNotice}
            {loaded.length > 0 && (
                <div className="text-[11px] text-white/55 flex flex-wrap items-center gap-x-3 gap-y-0.5 mb-4">
                    <span>{configuredCount === loaded.length
                        ? `All ${loaded.length} have credentials`
                        : `${loaded.length - configuredCount} of ${loaded.length} still need credentials`}</span>
                    {/* Named, and pointed somewhere.
                        A count on its own makes a clerk hunt for which ones,
                        and the answer to "so what do I do" is up the page in a
                        panel this section never mentioned. */}
                    {configuredCount < loaded.length && (
                        <span className="text-amber-200/85">
                            {loaded.filter(c => !statuses[c.key]?.configured).map(c => c.title).join(', ')}
                            {' — set these up in Setup Instructions, above'}
                        </span>
                    )}
                    {verifiedCount > 0 && <span className="text-emerald-300/80 inline-flex items-center gap-1"><CheckCircle className="w-3 h-3" />{verifiedCount} verified</span>}
                    {failedCount > 0 && <span className="text-amber-300/90 inline-flex items-center gap-1"><AlertCircle className="w-3 h-3" />{failedCount} not working</span>}
                    {unverifiableCount > 0 && (
                        <span className="text-white/55">{unverifiableCount} cannot be tested from here</span>
                    )}
                </div>
            )}

            {/* ── Spotlight ──────────────────────────────────────────────
               Once setup is done the question stops being "how do I
               configure this" and becomes "is anything wrong". So anything
               failing, unchecked or missing its credentials takes the full
               width and says what the provider said; everything healthy
               shrinks to a bubble that answers "yes, and here is when we
               last looked".

               One grid, not two lists, deliberately. A card that moves
               between the groups -- which is exactly what pressing "Test
               now" can do -- keeps its React key and its place in the same
               parent, so it re-sorts without remounting. Split across two
               containers it would remount, and a remount mid-edit throws
               away whatever has been typed into it. */}
            <div className="relative grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 items-start">
                {spotlit.concat(bubbles).map((c) => {
                    const wide = openCap === c.key || spotlit.includes(c);
                    return (
                        <div key={c.key} className={wide ? 'sm:col-span-2 lg:col-span-3' : ''}>
                            <CapabilityCard cap={c.key} title={c.title} blurb={c.blurb} icon={c.icon} delay={0}
                                recheckToken={recheckToken} reloadToken={reloadToken} onStatus={onStatus}
                                health={health[c.key]} identity={identity} onChanged={refreshAfterTest}
                                publicOrigin={publicOrigin}
                                variant={spotlit.includes(c) ? 'spotlight' : 'bubble'}
                                state={capState(c.key)}
                                expanded={openCap === c.key}
                                onExpandToggle={() => setOpenCap(k => (k === c.key ? null : c.key))}
                                onSwitchOff={onSwitch && !ALWAYS.has(c.key)
                                    ? () => onSwitch(c.key, false)
                                    : undefined} />
                        </div>
                    );
                })}

                {/* In the grid, not below it. */}
                {plainSecrets && plainSettings.map(setting => (
                    <div
                        key={setting.id}
                        className={openCap === setting.id ? 'sm:col-span-2 lg:col-span-3' : ''}
                    >
                        <PlainSettingCard
                            setting={setting}
                            secrets={plainSecrets}
                            expanded={openCap === setting.id}
                            onToggle={() => setOpenCap(k => (k === setting.id ? null : setting.id))}
                        />
                    </div>
                ))}
            </div>

            {notChosen.length > 0 && (
                <div className="mt-8">
                    <div className="flex items-center gap-3 mb-4">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-white/45">Switched off</h3>
                        <div className="h-px flex-1 bg-white/10" />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                        {notChosen.map(c => (
                            <div
                                key={c.id}
                                className="relative px-4 py-4 rounded-3xl bg-gradient-to-br from-white/[0.035] via-white/[0.01] to-indigo-950/30 border border-white/[0.08] backdrop-blur-2xl"
                            >
                                <div className="flex items-center gap-3">
                                    {/* Deliberately the same tile as a live
                                        capability, at lower contrast. These are
                                        the same objects, not a different class
                                        of thing -- one is simply switched off. */}
                                    <span className="opacity-50">
                                        <CapabilityTile icon={c.icon} size="sm" />
                                    </span>
                                    <div className="min-w-0 flex-1">
                                        <p className="font-semibold text-white/75 text-sm truncate">{c.title}</p>
                                        <p className="text-[11px] text-white/50">
                                            {stillSaved(c.id)
                                                ? 'Switched off — credentials still saved'
                                                : 'Not switched on'}
                                        </p>
                                    </div>
                                </div>
                                <p className="text-[11px] text-white/55 mt-2.5 leading-relaxed line-clamp-2">{c.blurb}</p>
                                {stillSaved(c.id) && (
                                    /* Said on the tile, not in the paragraph at
                                       the foot of the section. Whether *this*
                                       one still has its key is a fact about
                                       this capability, and a clerk deciding
                                       whether to switch it back on is looking
                                       at the tile. */
                                    <p className="text-[11px] text-emerald-200/60 mt-1.5">
                                        Nothing was deleted. Switch it back on and it works as it did.
                                    </p>
                                )}
                                {/* The way back, on the tile itself. "Go find
                                    the question that owns this, three sections
                                    up" is a scavenger hunt; the off switch is
                                    on the card, so the on switch is too. */}
                                {onSwitch && (
                                    <div className="mt-2.5 flex items-center gap-2.5">
                                        <Switch
                                            on={switchingOnId === c.id}
                                            busy={switchingOnId === c.id}
                                            disabled={switchingOnId !== null}
                                            onChange={async () => {
                                                setSwitchingOnId(c.id);
                                                try { await onSwitch(c.id, true); } finally { setSwitchingOnId(null); }
                                            }}
                                            label={`Switch ${c.title} on`}
                                        />
                                        <span className="text-[11px] text-white/50">
                                            {switchingOnId === c.id ? 'Switching on…' : 'Switch back on'}
                                        </span>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                    <p className="text-[11px] text-white/55 mt-3.5">
                        These are built and ready — nothing here needs writing. Switch one back on
                        and its card returns above; anything never set up also appears in the{' '}
                        <strong className="text-white/75">Setup Instructions</strong> guide with its steps.
                    </p>
                </div>
            )}

            {footer && <div className="mt-6">{footer}</div>}

            {extras && (
                <div className="mt-8">
                    <div className="flex items-center gap-3 mb-4">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-white/45">Other settings</h3>
                        <div className="h-px flex-1 bg-white/10" />
                    </div>
                    {/* Not provider choices -- there is nothing to pick between --
                        so these render without a provider row rather than being
                        given invented alternatives the backend cannot route to. */}
                    {extras}
                </div>
            )}
        </CollapsibleSection>
    );
}
