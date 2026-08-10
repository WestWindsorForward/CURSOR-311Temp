import { useCallback, useEffect, useState } from 'react';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

import ProviderCredentialSteps from './ProviderCredentialSteps';
import type { StepContext } from './setupSteps';
import { api } from '../services/api';
import type { Capability, CloudIdentity, ProviderCatalog, ProviderInfo } from '../services/api';

/**
 * A provider set up where it is described, rather than somewhere else.
 *
 * The setup guide used to end each section with "scroll to the Maps card
 * below". That is a handoff, and it is the wrong shape for a document whose
 * whole premise is that a clerk can follow it top to bottom: it sends them
 * three thousand pixels away mid-instruction, to a card that then repeats the
 * question they already answered in the questionnaire at the top.
 *
 * So the guide now does the work inline. The provider is not asked for again --
 * it comes from the questionnaire, which is the point of having asked -- and
 * the steps, the boxes and the Save & Test button are all here.
 *
 * The cards below are not redundant afterwards. They stay as the place to
 * change a provider later, to see health, and to switch to something the
 * questionnaire did not offer. The guide is the first run; the cards are the
 * standing configuration. Both mount the same walk from the same file.
 */
/* One probe for the whole page.
 *
 * The guide mounts eight of these at once. The attached-identity answer is a
 * property of the server, identical for all of them, so asking eight times is
 * eight round trips to learn the same thing -- and on a deployment with no
 * attached identity each one waits out the metadata timeout before failing.
 * Memoised on the promise rather than the result so concurrent mounts share the
 * single in-flight request instead of racing to start their own. */
let identityProbe: Promise<CloudIdentity | null> | null = null;
function probeIdentity(): Promise<CloudIdentity | null> {
    identityProbe ??= api.getCloudIdentity().catch(() => null);
    return identityProbe;
}

export default function InlineProviderSetup({
    cap, provider, choices, onChoose, onSaved, note, publicOrigin,
}: {
    cap: Capability;
    /** The provider to set up, from the questionnaire. */
    provider: string;
    /** Providers this section may switch between inline, when the choice is not
     *  one the questionnaire asks (email and SMS are not a cloud decision).
     *  Omitted means the questionnaire already decided and this shows no picker. */
    choices?: { id: string; label: string }[];
    onChoose?: (id: string) => void;
    /** Told whether the live test passed, not merely that a save happened.
     *  The wizard advances on this, and moving somebody past a credential that
     *  does not work is the whole failure mode being designed out. */
    onSaved?: (verified: boolean) => void;
    /** A sentence above the steps, where the choice needs explaining. */
    note?: React.ReactNode;
    /** The address residents use, for callback URLs pasted into a vendor
     *  console. null means nothing has configured a domain, so the steps fall
     *  back to this browser's origin and say so. */
    publicOrigin?: string | null;
}) {
    const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
    const [identity, setIdentity] = useState<CloudIdentity | null>(null);
    const [values, setValues] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState<'save' | null>(null);
    const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
    const [warnings, setWarnings] = useState<{ key: string; severity: string; message: string }[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState<string | null>(null);

    const ctx: StepContext = {
        origin: publicOrigin || window.location.origin,
        copy: (text, id) => {
            navigator.clipboard?.writeText(text).then(
                () => { setCopied(id); setTimeout(() => setCopied(null), 1600); },
                () => { /* clipboard blocked; the value is visible and selectable */ },
            );
        },
        copied,
    };

    const load = useCallback(async () => {
        try {
            setCatalog(await api.getProviderCatalog(cap));
        } catch (e: any) {
            setError(e?.message || 'Could not load this provider.');
        }
    }, [cap]);

    useEffect(() => { load(); }, [load]);

    /* Best-effort. A failure here means the boxes render normally, which is the
     * safe direction -- the worst case is being asked for a credential that
     * would have turned out to be unnecessary, rather than being told to leave
     * one blank when it was needed. */
    useEffect(() => {
        let alive = true;
        probeIdentity().then(i => { if (alive) setIdentity(i); });
        return () => { alive = false; };
    }, []);

    /* Switching provider clears what was typed. Carrying values across would
     * mean a key entered for Azure sitting in a box the AWS save then posts. */
    useEffect(() => { setValues({}); setResult(null); setWarnings([]); }, [provider]);

    if (error) {
        return (
            <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200 flex items-start gap-2">
                <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />
                <span>{error} You can still set this up on the card further down the page.</span>
            </div>
        );
    }
    if (!catalog) {
        return <div className="h-20 rounded-lg bg-white/[0.03] animate-pulse" aria-busy="true" />;
    }

    const active: ProviderInfo | undefined = catalog.providers.find(p => p.provider === provider);
    if (!active) {
        return (
            <div className="rounded-lg border border-amber-400/25 bg-amber-500/[0.07] px-3 py-2 text-xs text-amber-100/80">
                This deployment does not offer that option. Use the card further down the page to pick one it does.
            </div>
        );
    }

    // Only identity hands out a redirect URI, so only identity needs the
    // warning; showing it above an API-key box would be noise.
    const needsCallbackUrl = cap === 'identity';
    const alreadySet = catalog.configured?.[provider] === true;
    const isCurrent = catalog.current_provider === provider;

    /* This session's test, or the last one recorded, whichever is fresher.
     *
     * The cards below rehydrate their result box from the health row; this
     * component showed only its in-session result, so a reload mid-setup lost
     * the verdict a clerk had just earned and the guide reopened as though
     * nothing had been tested. Only when the catalog's answer is about the
     * provider this section is setting up -- a verdict belongs to the provider
     * that produced it. `verifiable === false` is "we cannot check this from
     * here", which is worth showing and must not render as a pass. */
    const shownResult = result ?? ((isCurrent && catalog.last_result)
        ? {
            ok: catalog.last_result.ok && catalog.last_result.verifiable !== false,
            detail: catalog.last_result.detail,
        }
        : null);

    const save = async () => {
        setBusy('save'); setResult(null); setError(null);
        try {
            const settings: Record<string, string> = {};
            // Trim on save -- a stray space from a copy-paste is the commonest
            // reason a correct key is rejected. Trimming here rather than in the
            // input keeps mid-word typing intact.
            active.credential_fields.forEach(f => {
                const v = (values[f.key] || '').trim();
                if (v) settings[f.key] = v;
            });
            const saved = await api.saveProvider(cap, { provider, settings });
            setWarnings(saved.warnings || []);
            setValues({});
            await load();
            // Save and verify are one action here. A guide that says "saved"
            // and leaves a clerk to discover later that the key was wrong is
            // the failure this whole page exists to avoid.
            const verified = await api.testProvider(cap);
            setResult(verified);
            onSaved?.(verified.ok);
        } catch (e: any) {
            setResult({ ok: false, detail: e?.message || 'Save failed' });
            onSaved?.(false);
        } finally {
            setBusy(null);
        }
    };

    return (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            {note && <p className="text-xs text-white/50 leading-relaxed mb-3">{note}</p>}

            {/* A callback URL is the one value on this page that has to match
                something outside it exactly. If nobody has told Pinpoint its own
                address, the steps below are quoting this browser's -- fine on a
                laptop pointed at the real site, wrong from an internal
                hostname, and the resulting failure looks like a bad password. */}
            {!publicOrigin && needsCallbackUrl && (
                <div className="mb-3 rounded-lg border border-amber-400/25 bg-amber-500/[0.07] px-3 py-2 flex items-start gap-2">
                    <AlertCircle className="w-3.5 h-3.5 text-amber-300/80 mt-0.5 shrink-0" aria-hidden="true" />
                    <p className="text-[11px] text-amber-100/80 leading-relaxed">
                        The address below is the one you are using right now
                        (<code className="bg-black/30 px-1 rounded">{window.location.origin}</code>).
                        If residents reach this site at a different address, set the town's domain in
                        Settings first — the address you register here has to be the one they use.
                    </p>
                </div>
            )}

            {choices && choices.length > 1 && (
                <div className="flex flex-wrap gap-2 mb-3.5">
                    {choices.map(c => (
                        <button
                            key={c.id}
                            type="button"
                            onClick={() => onChoose?.(c.id)}
                            aria-pressed={provider === c.id}
                            className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${provider === c.id
                                ? 'bg-primary-500/20 border-primary-400/50 text-white'
                                : 'bg-white/5 border-white/10 text-white/60 hover:text-white'}`}
                        >
                            {c.label}
                        </button>
                    ))}
                </div>
            )}

            <ProviderCredentialSteps
                cap={cap}
                provider={provider}
                active={active}
                values={values}
                onChange={(key, value) => setValues(prev => ({ ...prev, [key]: value }))}
                ctx={ctx}
                identity={identity}
                storedFields={catalog.stored_fields}
                hostProvided={catalog.host_provided}
                alreadySet={alreadySet && isCurrent}
                compact
            />

            <div className="flex flex-wrap items-center gap-2.5 mt-3 pt-3 border-t border-white/[0.07]">
                <button
                    type="button"
                    onClick={save}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-semibold text-white bg-primary-500 hover:bg-primary-400 border border-primary-400/50 transition-colors disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300"
                >
                    {busy === 'save' ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving…</> : <>Save &amp; Test</>}
                </button>
                {alreadySet && isCurrent && !shownResult && (
                    <span className="text-[11px] text-emerald-300/80 inline-flex items-center gap-1.5">
                        <CheckCircle className="w-3.5 h-3.5" aria-hidden="true" />
                        Already saved — leave a box blank to keep what is stored.
                    </span>
                )}
            </div>

            {shownResult && (
                <div className={`mt-2.5 rounded-lg px-3 py-2 text-xs flex items-start gap-2 ${shownResult.ok
                    ? 'bg-emerald-500/10 border border-emerald-400/25 text-emerald-100/90'
                    : 'bg-red-500/10 border border-red-400/25 text-red-100/90'}`}>
                    {shownResult.ok
                        ? <CheckCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />
                        : <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />}
                    {/* pre-line: the tests report their work as numbered steps,
                        one per line — collapsing them to a paragraph turns a
                        verifiable log back into a claim. */}
                    <span className="whitespace-pre-line">{shownResult.detail}</span>
                </div>
            )}

            {/* Shown even though the save succeeded: these are "that value does
                not look like what this box wants", most often the right
                credential in the wrong field, which a connection test does not
                reliably tell apart from a wrong key. */}
            {warnings.length > 0 && (
                <ul className="mt-2 space-y-1">
                    {warnings.map(w => (
                        <li key={w.key} className="text-[11px] text-amber-200/80 flex items-start gap-1.5">
                            <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" aria-hidden="true" />
                            <span>{w.message}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
