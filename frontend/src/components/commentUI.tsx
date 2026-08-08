import type { ReactNode } from 'react';
import { Shield, User, Plug, Lock, Globe, MessageSquare } from 'lucide-react';
import type { RequestComment, CommentVisibility } from '../types';

/**
 * The shared vocabulary for a comment, wherever it appears.
 *
 * A comment shows up twice: in the staff request drawer, where the question is
 * "who said this and can the resident see it", and on the public tracker,
 * where it is "is this the town speaking or a neighbour". Different audiences,
 * same object — and until this module each surface hand-rolled its own cards,
 * so the two threads drifted into looking like different products (and neither
 * looked like the console around them). Restyling a comment now means editing
 * this file, and there is nowhere else to edit.
 *
 * Behaviour stays with the callers: this module renders, it never fetches or
 * submits.
 */

/** Who wrote a comment, derived the same way everywhere.
 *
 * The backend encodes authorship rather than declaring it: staff comments
 * carry the author's user_id, residents post anonymously as "Resident", and
 * integration sync notes are written under the integration's display name
 * with no user_id (tasks/integrations.py). The order below matters — an
 * integration row also has `username !== 'Resident'`, which is the test the
 * public tracker used to use for "staff", so a work-order note would have
 * worn a staff badge.
 */
export type CommentActor = 'staff' | 'resident' | 'integration';

export function commentActor(c: Pick<RequestComment, 'username' | 'user_id'>): CommentActor {
    if (c.username === 'Resident') return 'resident';
    if (c.user_id == null) return 'integration';
    return 'staff';
}

/** "6 hours ago" — the console's relative-time voice (ServiceProviders uses
 *  the same buckets for connector checks). The exact instant still matters on
 *  a dispute, so callers put the absolute time in `title`. */
export function relativeTime(iso: string | null | undefined): string {
    if (!iso) return '';
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return '';
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 2) return 'just now';
    if (mins < 90) return `${mins} minutes ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 36) return `${hrs} hours ago`;
    const days = Math.round(hrs / 24);
    if (days < 45) return `${days} days ago`;
    return new Date(then).toLocaleDateString();
}

/* One skin per author, used by the avatar tile, the role pill and the card
 * wash together — a staff comment is indigo everywhere it is staff-coloured,
 * not indigo here and purple there. The tile geometry is CapabilityTile's
 * `sm` (w-9 h-9 rounded-xl, gradient, shadow-inner) so an author avatar and a
 * capability icon read as the same family of object. */
const ACTOR: Record<CommentActor, {
    label: string;
    Icon: typeof Shield;
    tile: string;
    pill: string;
    card: string;
}> = {
    staff: {
        label: 'Staff', Icon: Shield,
        tile: 'bg-gradient-to-br from-primary-500/25 via-indigo-500/20 to-purple-500/15 border-white/20 text-primary-200',
        pill: 'bg-primary-500/15 text-primary-200 border-primary-400/25',
        card: 'bg-gradient-to-br from-primary-500/[0.08] via-white/[0.02] to-transparent border-primary-400/20',
    },
    resident: {
        label: 'Resident', Icon: User,
        tile: 'bg-gradient-to-br from-teal-500/25 to-cyan-500/15 border-teal-400/30 text-teal-200',
        pill: 'bg-teal-500/15 text-teal-200 border-teal-400/25',
        card: 'bg-gradient-to-br from-teal-500/[0.07] via-white/[0.02] to-transparent border-teal-400/20',
    },
    integration: {
        label: 'Integration', Icon: Plug,
        tile: 'bg-gradient-to-br from-sky-500/25 to-blue-500/15 border-sky-400/30 text-sky-200',
        pill: 'bg-sky-500/15 text-sky-200 border-sky-400/25',
        card: 'bg-gradient-to-br from-sky-500/[0.07] via-white/[0.02] to-transparent border-sky-400/20',
    },
};

/* Internal wears the console's amber ("needs care around this"), public its
 * emerald ("safe, visible") — the same gradients StatusPill uses for todo and
 * working, so a glance at colour answers "can the resident read this" without
 * reading the label. */
const VISIBILITY: Record<CommentVisibility, { label: string; Icon: typeof Lock; cls: string }> = {
    internal: {
        label: 'Internal', Icon: Lock,
        cls: 'bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border-amber-500/30',
    },
    external: {
        label: 'Public', Icon: Globe,
        cls: 'bg-gradient-to-r from-emerald-500/20 to-teal-500/20 text-emerald-300 border-emerald-500/30',
    },
};

export function VisibilityPill({ visibility }: { visibility: CommentVisibility }) {
    const v = VISIBILITY[visibility] ?? VISIBILITY.external;
    return (
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-2xl text-[10px] font-semibold uppercase tracking-wider border shrink-0 ${v.cls}`}>
            <v.Icon className="w-3 h-3" aria-hidden="true" />
            {v.label}
        </span>
    );
}

/**
 * One comment. `showVisibility` is the staff drawer's flag — the public
 * tracker only ever receives external comments, so a "Public" pill there
 * would label every card with the same word.
 *
 * `children` replaces the plain body when given, so the resident tracker can
 * run the text through TranslatedContent without this module knowing that
 * translation exists.
 */
export function CommentCard({ comment, showVisibility = false, children }: {
    comment: RequestComment;
    showVisibility?: boolean;
    children?: ReactNode;
}) {
    const actor = commentActor(comment);
    const a = ACTOR[actor];
    const absolute = comment.created_at ? new Date(comment.created_at).toLocaleString() : '';
    return (
        <div className={`rounded-2xl border p-3.5 ${a.card}`}>
            <div className="flex items-center gap-2.5 flex-wrap">
                <div className={`w-9 h-9 rounded-xl shrink-0 flex items-center justify-center border shadow-inner ${a.tile}`}>
                    <a.Icon className="w-4 h-4" aria-hidden="true" />
                </div>
                <div className="flex items-center gap-2 flex-wrap min-w-0">
                    <span className="text-sm font-semibold text-white/90 truncate">{comment.username}</span>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-2xl text-[10px] font-semibold uppercase tracking-wider border shrink-0 ${a.pill}`}>
                        {a.label}
                    </span>
                    {showVisibility && <VisibilityPill visibility={comment.visibility} />}
                </div>
                {absolute && (
                    <time
                        dateTime={comment.created_at ?? undefined}
                        title={absolute}
                        className="ml-auto text-[11px] text-white/40 shrink-0"
                    >
                        {relativeTime(comment.created_at)}
                    </time>
                )}
            </div>
            <div className="mt-2.5 pl-[46px] text-sm text-white/80 leading-relaxed whitespace-pre-wrap break-words">
                {children ?? comment.content}
            </div>
        </div>
    );
}

/** The thread with nothing in it yet — an invitation, not a bare "No comments"
 *  string floating in the dark. */
export function CommentEmptyState({ title, hint }: { title: string; hint?: string }) {
    return (
        <div className="flex flex-col items-center text-center py-8 px-4 rounded-2xl border border-dashed border-white/10 bg-white/[0.02]">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center border shadow-inner bg-gradient-to-br from-primary-500/25 via-indigo-500/20 to-purple-500/15 border-white/20 text-primary-200 mb-3">
                <MessageSquare className="w-5 h-5" aria-hidden="true" />
            </div>
            <p className="text-sm font-medium text-white/70">{title}</p>
            {hint && <p className="text-xs text-white/45 mt-1">{hint}</p>}
        </div>
    );
}

/** Ghost cards while the thread loads — the shape of what is coming, in
 *  place of a lone spinner. */
export function CommentSkeleton({ rows = 2 }: { rows?: number }) {
    return (
        <div className="space-y-3" role="status" aria-label="Loading comments">
            {Array.from({ length: rows }, (_, i) => (
                <div key={i} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3.5 animate-pulse">
                    <div className="flex items-center gap-2.5">
                        <div className="w-9 h-9 rounded-xl bg-white/[0.07]" />
                        <div className="h-3 w-28 rounded bg-white/[0.07]" />
                        <div className="ml-auto h-3 w-16 rounded bg-white/[0.05]" />
                    </div>
                    <div className="mt-3 ml-[46px] space-y-2">
                        <div className="h-3 w-3/4 rounded bg-white/[0.06]" />
                        <div className="h-3 w-1/2 rounded bg-white/[0.05]" />
                    </div>
                </div>
            ))}
            <span className="sr-only">Loading comments…</span>
        </div>
    );
}
