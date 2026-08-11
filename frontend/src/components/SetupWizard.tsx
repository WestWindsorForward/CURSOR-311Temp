import { useEffect, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, ChevronDown, ChevronRight, Circle, AlertCircle } from 'lucide-react';

import InlineProviderSetup from './InlineProviderSetup';
import SecretField from './SecretField';
import { buildPlan, type PlanInput, type PlanItem, type PlanTask } from './setupPlan';
import type { ProviderStatusMap } from '../services/api';
// Registers every provider's console walk as an import side effect.
import './setupStepsContent';

/**
 * Setup, one login at a time.
 *
 * What this replaces was ten stacked panels, all open, each with a paragraph of
 * prose above its boxes. Everything a town might ever configure was on screen
 * at once, in capability order, so a town on Azure read Azure, Google, Azure,
 * Azure, Azure. It was accurate and exhausting, and the person it is written
 * for -- a township clerk who was handed this job, not an engineer -- has no
 * way to tell from that page whether they are ten minutes from finished or two
 * days.
 *
 * Two changes. The list on the left is grouped by the account you sign in to
 * rather than by feature, so everything behind one login is one visit
 * (setupPlan.ts does that arithmetic). And only one is open at a time, so the
 * screen shows the thing you are doing and a list of what is left.
 *
 * Finishing advances you. Not on save -- on a save whose live test came back
 * green, because being moved along past a credential that does not work is the
 * exact failure this page exists to prevent.
 */

const STATUS_TONE = {
    done: 'text-emerald-300',
    todo: 'text-amber-300',
    optional: 'text-white/35',
} as const;

export interface SetupWizardProps extends PlanInput {
    /** Which provider each capability is on and which are set up, from the
     *  server. Null while it loads. */
    status: ProviderStatusMap | null;
    /** For items with no capability catalog -- backups, the Sentry key. */
    isDone: (itemId: string) => boolean;
    /** Plain settings, saved through the page's own secret endpoint. */
    secretValues: Record<string, string>;
    onSecretChange: (key: string, value: string) => void;
    onSaveSecrets: (keys: string[]) => Promise<void>;
    savingSecret: string | null;
    isSecretConfigured: (key: string) => boolean;
    /** A save landed somewhere; refresh the page's own status. */
    onRefresh: () => void;
    /** The address residents use, for callback URLs. */
    publicOrigin: string | null;
    /** The cloud foundation walk, which belongs to no single capability. */
    renderFoundation: (cloud: 'google' | 'azure' | 'aws') => React.ReactNode;
}

export default function SetupWizard(props: SetupWizardProps) {
    const { onRefresh, publicOrigin, renderFoundation, status } = props;

    const tasks = useMemo(() => buildPlan(props), [
        props.cloud, props.idp, props.maps, props.aiProvider,
        props.emailProvider, props.smsProvider, props.redactionProvider, props.wanted,
        // Nothing in this application passes `exclude` (see PlanInput), but a
        // console that does would otherwise keep the plan it built before the
        // exclusion changed -- the memo would hold, and the walk would go on
        // offering a task for a credential that had just stopped being that
        // console's to enter.
        props.exclude,
    ]);

    /* Finished means finished *for the provider currently chosen*.
     *
     * This was the bug behind two complaints at once. Done-ness was read from
     * the stored secrets per capability, so "maps is set up" was true if any
     * map provider's key existed. A town that configured Google Maps and then
     * switched to Esri saw a green tick against a provider with no credentials,
     * and the guide skipped the one thing it most needed to ask for.
     *
     * Asking the server per provider fixes both: the tick is honest, and
     * switching to something unconfigured makes the task unfinished again,
     * which is what reopens it.
     */
    const itemDone = (item: PlanItem): boolean => {
        if (item.cap && item.provider) {
            // Unknown until the status arrives. Treated as unfinished, which is
            // the safe direction: the cost is asking about something already
            // done rather than skipping something that is not.
            return status?.[item.cap]?.configured?.[item.provider] === true;
        }
        return props.isDone(item.id);
    };
    const taskDone = (t: PlanTask) => t.items.every(itemDone);

    const [openId, setOpenId] = useState<string | null>(null);
    /* Once a clerk clicks a row, their choice wins over the automatic one --
     * being walked through setup should not mean losing the ability to go back
     * and look at something already finished. */
    const chosen = useRef(false);

    const remaining = tasks.filter(t => !taskDone(t)).length;
    const allDone = tasks.length > 0 && remaining === 0;

    // Land on the first unfinished task once the server has said which those
    // are. Guarded on `status` as well as on the ref: before it arrives every
    // task looks unfinished, so landing then would always pick the first one.
    useEffect(() => {
        if (chosen.current || openId !== null || tasks.length === 0 || !status) return;
        const next = tasks.find(t => !taskDone(t));
        if (next) setOpenId(next.id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tasks.length, status]);

    /* Switching a provider reopens the task it belongs to.
     *
     * Changing the map provider from Google to Esri makes that task unfinished
     * again -- there are no Esri credentials -- and leaving it collapsed with a
     * tick beside it is how a town ends up live on a provider it never
     * configured. Only reopens what the clerk has not deliberately navigated
     * away from since. */
    const openTask = tasks.find(t => t.id === openId) ?? null;
    useEffect(() => {
        // Guarded on `status` for the same reason the landing effect above is.
        // This effect runs on mount too, and before the status arrives every
        // task reads as unfinished and nothing is open yet -- so it fell
        // straight through to tasks[0] and opened it, which is how a clerk who
        // had already finished the Google task landed on it anyway. Not in the
        // dependency list on purpose: re-running when the status refreshes
        // would reopen a task the clerk had deliberately navigated away from.
        if (!status || (openTask && !taskDone(openTask))) return;
        const next = tasks.find(t => !taskDone(t));
        if (next && next.id !== openId) setOpenId(next.id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [props.cloud, props.idp, props.maps, props.aiProvider,
        props.emailProvider, props.smsProvider, props.redactionProvider]);

    /** Move on, but only from a task that is genuinely finished. */
    const advanceFrom = (taskId: string) => {
        const index = tasks.findIndex(t => t.id === taskId);
        if (index < 0) return;
        const next = tasks.slice(index + 1).find(t => !taskDone(t))
            ?? tasks.find(t => !taskDone(t));
        setOpenId(next ? next.id : null);
    };

    const open = openTask;

    /* One item open inside the task, for the same reason one task is open in
     * the rail. Grouping by login turned four visits into one, and then put
     * everything from all four on the screen together: the Azure task rendered
     * about six thousand pixels tall while the rail said "1 left". That is the
     * same wall in a new place.
     *
     * Reset when the task changes, then filled in by the effect below. */
    const [openItemId, setOpenItemId] = useState<string | null>(null);
    const itemChosen = useRef(false);
    useEffect(() => { setOpenItemId(null); itemChosen.current = false; }, [openId]);
    useEffect(() => {
        if (!open || itemChosen.current || openItemId !== null) return;
        const next = open.items.find(i => !itemDone(i));
        setOpenItemId((next ?? open.items[0])?.id ?? null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [openId, status, open?.items.length]);

    /** Finishing an item opens the next one, or moves on to the next task. */
    const advanceItem = (fromId: string) => {
        if (!open) return;
        const index = open.items.findIndex(i => i.id === fromId);
        const next = open.items.slice(index + 1).find(i => !itemDone(i))
            ?? open.items.find(i => i.id !== fromId && !itemDone(i));
        if (next) {
            setOpenItemId(next.id);
        } else {
            advanceFrom(open.id);
        }
    };

    return (
        <div className="grid lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] gap-5">
            {/* ── The list ── */}
            <nav aria-label="Setup tasks" className="lg:sticky lg:top-4 self-start">
                <p className="text-[11px] uppercase tracking-wider text-white/40 font-semibold mb-2.5 px-1">
                    {remaining === 0 ? 'All done' : `${remaining} left`}
                </p>
                <ul className="space-y-1">
                    {tasks.map(task => {
                        const done = taskDone(task);
                        const active = task.id === openId;
                        const tone = done ? 'done' : task.required ? 'todo' : 'optional';
                        return (
                            <li key={task.id}>
                                <button
                                    type="button"
                                    onClick={() => { chosen.current = true; setOpenId(active ? null : task.id); }}
                                    aria-current={active ? 'step' : undefined}
                                    className={`w-full text-left rounded-xl px-3 py-2.5 flex items-center gap-2.5 border transition-colors ${active
                                        ? 'bg-white/[0.09] border-white/20'
                                        : 'bg-white/[0.03] border-transparent hover:bg-white/[0.06]'}`}
                                >
                                    <span className={`shrink-0 ${STATUS_TONE[tone]}`} aria-hidden="true">
                                        {done
                                            ? <Check className="w-4 h-4" />
                                            : <Circle className="w-4 h-4" strokeWidth={task.required ? 2.5 : 1.5} />}
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block text-sm text-white/85 truncate">{task.title}</span>
                                        <span className="block text-[11px] text-white/40 truncate">
                                            {done ? 'Set up' : task.items.map(i => i.title).join(' · ')}
                                        </span>
                                    </span>
                                    {active && <ChevronRight className="w-3.5 h-3.5 text-white/30 shrink-0" aria-hidden="true" />}
                                </button>
                            </li>
                        );
                    })}
                </ul>
            </nav>

            {/* ── The one you are on ── */}
            <div className="min-w-0">
                <AnimatePresence mode="wait">
                    {open && (

                        <motion.section
                            key={open.id}
                            initial={{ opacity: 0, y: 8 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -6 }}
                            transition={{ duration: 0.18 }}
                            className="setup-panel p-5 sm:p-6"
                        >
                            <h3 className="font-semibold text-white text-base">{open.title}</h3>
                            <p className="text-sm text-white/55 leading-relaxed mt-1">{open.blurb}</p>

                            {open.foundation && (
                                <div className="mt-4 pt-4 border-t border-white/[0.07]">
                                    {renderFoundation(open.foundation)}
                                </div>
                            )}

                            <div className="mt-4 space-y-5">
                                {open.items.map((item, i) => (
                                    <TaskItem
                                        key={item.id}
                                        item={item}
                                        index={i + 1}
                                        total={open.items.length}
                                        done={itemDone(item)}
                                        expanded={item.id === openItemId}
                                        onToggle={() => {
                                            itemChosen.current = true;
                                            setOpenItemId(item.id === openItemId ? null : item.id);
                                        }}
                                        {...props}
                                        onDone={(verified) => {
                                            onRefresh();
                                            // Only on a green test: moving somebody past a
                                            // credential that does not work reads as confirmation.
                                            if (verified) advanceItem(item.id);
                                        }}
                                        publicOrigin={publicOrigin}
                                    />
                                ))}
                            </div>
                        </motion.section>
                    )}
                </AnimatePresence>

                {/* Outside the AnimatePresence above, deliberately.
                    Inside it, `mode="wait"` holds the outgoing task panel until
                    its exit animation finishes, so collapsing a row left the
                    old panel on screen and this one unmounted. It is also what
                    made the bug invisible to a test: jsdom produces no frames,
                    so the exit never completes and the wrong panel never
                    appears. */}
                {!open && (allDone ? (
                    <div className="setup-panel p-6 text-center">
                        <div className="w-12 h-12 mx-auto rounded-2xl bg-gradient-to-br from-emerald-400 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/25">
                            <Check className="w-6 h-6 text-white" strokeWidth={2.5} />
                        </div>
                        <p className="text-white/80 mt-3.5">Everything you picked is set up.</p>
                        <p className="text-white/45 text-sm mt-1.5">
                            Pick anything from the list to look at it again, or change it later on the cards below.
                        </p>
                    </div>
                ) : (
                    /* Nothing open, but not finished either.
                     *
                     * This state used to render the "everything is set up"
                     * panel, because that panel was shown whenever nothing was
                     * open -- and nothing is open after collapsing a row, not
                     * only after finishing the last task. So clicking the open
                     * task shut congratulated a town that had configured
                     * nothing at all. */
                    <div className="setup-panel p-6 text-center">
                        <p className="text-white/70">
                            {remaining} {remaining === 1 ? 'thing' : 'things'} left to set up.
                        </p>
                        <p className="text-white/45 text-sm mt-1.5">Pick one from the list to carry on.</p>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------

/** One thing inside a task: a provider with a catalog, plain settings, or both. */
function TaskItem({
    item, index, total, done, expanded, onToggle, onDone, publicOrigin,
    secretValues, onSecretChange, onSaveSecrets, savingSecret, isSecretConfigured,
}: {
    item: PlanItem;
    index: number;
    total: number;
    done: boolean;
    expanded: boolean;
    onToggle: () => void;
    onDone: (verified: boolean) => void;
    publicOrigin: string | null;
} & Pick<SetupWizardProps,
    'secretValues' | 'onSecretChange' | 'onSaveSecrets' | 'savingSecret' | 'isSecretConfigured'>) {
    return (
        <div className={`rounded-2xl border transition-colors ${expanded
            ? 'border-white/15 bg-white/[0.04]'
            : 'border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.04]'}`}>
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={expanded}
                className="w-full text-left px-4 py-3 flex items-center gap-3 rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60"
            >
                <span
                    className={`shrink-0 w-6 h-6 rounded-full border text-[11px] font-semibold flex items-center justify-center ${done
                        ? 'bg-emerald-500/20 border-emerald-400/40 text-emerald-300'
                        : 'bg-white/10 border-white/15 text-white/60'}`}
                    aria-hidden="true"
                >
                    {done ? <Check className="w-3.5 h-3.5" /> : index}
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-white/90 truncate">{item.title}</span>
                    {/* The one-line summary stays visible when collapsed, so the
                        list reads as a plan rather than as a row of headings. */}
                    <span className="block text-[11px] text-white/45 truncate">
                        {done ? 'Set up' : item.blurb}
                    </span>
                </span>
                <span className="text-[10px] uppercase tracking-wider text-white/30 shrink-0 hidden sm:block">
                    {index} of {total}
                </span>
                <ChevronDown
                    className={`w-4 h-4 text-white/35 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
                    aria-hidden="true"
                />
            </button>

            {/* No blurb repeated inside: it is already in the header above,
                which stays visible while expanded. */}
            {expanded && (
                <div className="px-4 pb-4 pt-1">
                    {item.cap && item.provider && (
                        <InlineProviderSetup
                            cap={item.cap}
                            provider={item.provider}
                            onSaved={onDone}
                            publicOrigin={publicOrigin}
                        />
                    )}

                    {item.secrets && (
                        <PlainSecrets
                            fields={item.secrets}
                            values={secretValues}
                            onChange={onSecretChange}
                            onSave={onSaveSecrets}
                            saving={savingSecret}
                            isConfigured={isSecretConfigured}
                            onSaved={() => onDone(false)}
                            className={item.cap ? 'mt-3' : ''}
                        />
                    )}
                </div>
            )}
        </div>
    );
}

/**
 * Boxes for settings that belong to no provider card.
 *
 * A few things here -- the backup bucket, Azure's Content Safety pair, the
 * Sentry key -- have no capability catalog behind them, and were previously
 * printed as bare environment-variable names in the middle of a sentence. That
 * is not an instruction a clerk can act on: it reads as something to hand to
 * IT, and it was the only place on this page asking anyone to edit a file.
 */
export function PlainSecrets({
    fields, values, onChange, onSave, saving, isConfigured, onSaved, className = '',
    blockedReason,
}: {
    fields: { key: string; label: string; secret?: boolean; help?: string }[];
    values: Record<string, string>;
    onChange: (key: string, value: string) => void;
    onSave: (keys: string[]) => Promise<void>;
    saving: string | null;
    isConfigured: (key: string) => boolean;
    onSaved: () => void;
    className?: string;
    /** Why saving is not allowed yet, if it is not.
     *
     * Added for one caller and it earns its place there: backup settings must
     * not be savable until somebody has confirmed they kept a copy of the
     * encryption passphrase. Without that, a town ends up with nightly backups
     * encrypted by a passphrase nobody wrote down -- which is not a backup, and
     * is discovered at restore time. */
    blockedReason?: string | null;
}) {
    const pending = fields.filter(f => values[f.key]).map(f => f.key);
    const allStored = fields.every(f => isConfigured(f.key));

    return (
        <div className={`rounded-xl border border-white/10 bg-white/[0.03] p-3.5 ${className}`}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-3">
                {fields.map(f => (
                    <SecretField
                        key={f.key}
                        label={f.label}
                        secret={f.secret}
                        help={f.help}
                        savedHint={isConfigured(f.key)}
                        value={values[f.key] || ''}
                        onChange={(v) => onChange(f.key, v)}
                    />
                ))}
            </div>
            <div className="flex flex-wrap items-center gap-2.5 mt-3 pt-3 border-t border-white/[0.07]">
                <button
                    type="button"
                    onClick={async () => { await onSave(pending); onSaved(); }}
                    disabled={pending.length === 0 || saving !== null || !!blockedReason}
                    title={blockedReason || undefined}
                    className="inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-semibold text-white bg-primary-500 hover:bg-primary-400 border border-primary-400/50 transition-colors disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300"
                >
                    {saving ? 'Saving…' : 'Save'}
                </button>
                {blockedReason && (
                    <span className="text-[11px] text-amber-200/90 inline-flex items-center gap-1.5">
                        <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />
                        {blockedReason}
                    </span>
                )}
                {!blockedReason && allStored && (
                    <span className="text-[11px] text-emerald-300/80 inline-flex items-center gap-1.5">
                        <Check className="w-3.5 h-3.5" aria-hidden="true" />
                        Saved. Leave a box blank to keep what is stored.
                    </span>
                )}
                {/* These have no live test, and saying so is better than a green
                    tick that only means the value reached the database. */}
                {!allStored && pending.length === 0 && (
                    <span className="text-[11px] text-white/35 inline-flex items-center gap-1.5">
                        <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />
                        Not filled in yet.
                    </span>
                )}
            </div>
        </div>
    );
}
