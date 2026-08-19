import React, { useState } from 'react';
import { MessageSquareHeart, Check } from 'lucide-react';
import { api } from '../services/api';

/**
 * The resident-facing half of the optional platform-feedback module.
 *
 * WHY IT LOOKS LIKE THIS
 * ----------------------
 * It lives in the portal footer, collapsed to a single line, and nothing about
 * it interrupts filing a report. A modal that appears while somebody is trying
 * to tell the town about a broken streetlight competes with the actual job, and
 * the answers it collects are answers about being interrupted.
 *
 * One question, five options, one tap. No second screen, no "how likely are you
 * to recommend", no name, no email, no comment box.
 *
 * WHY NO COMMENT BOX
 * ------------------
 * Anything a resident types would be stored in the town's own database, which
 * makes it potentially responsive to a public-records request, and it can
 * contain the resident's own name, address or phone number without their
 * meaning it to. So the offer to say more is a plain `mailto:` link: the
 * sentence lands in a mailbox instead of the database, where there is nothing
 * to disclose, nothing to scrub from an export, and nothing to moderate. The
 * database only ever holds the one categorical answer.
 *
 * The address is per-deployment (`settings.platform_feedback_email`). Blank
 * means the line is omitted entirely rather than rendered dead. The link
 * carries a subject and NOTHING else — no answer, no report id, no context —
 * because a prefilled body is a way to leak what somebody just did into their
 * email client's drafts folder.
 *
 * SCALE
 * -----
 * Ordered, five-point, and asked about the thing the town can act on: whether
 * this made reporting easier or harder than it was. Stars would measure
 * satisfaction with an interface; "easier or harder than before" measures
 * whether the platform did the job it was bought to do, and it reads the same
 * to every resident without a legend.
 */

/** Stored token -> what the resident actually reads. Order is the scale order,
 *  best first. The tokens are stable; the wording can change without orphaning
 *  answers already collected. */
export const PLATFORM_FEEDBACK_OPTIONS: { value: string; label: string }[] = [
    { value: 'much_easier', label: 'Much easier' },
    { value: 'somewhat_easier', label: 'Somewhat easier' },
    { value: 'no_difference', label: 'No difference' },
    { value: 'somewhat_harder', label: 'Somewhat harder' },
    { value: 'much_harder', label: 'Much harder' },
];

export const PLATFORM_FEEDBACK_QUESTION =
    'This online reporting platform has made reporting issues to the town:';

/** Remembers, in this browser only, that the question has been answered, so a
 *  resident who files three reports is not asked three times.
 *
 *  Browser-local and nothing else: it is never sent to the server, never stored
 *  next to the answer, and cannot link a row to a person. Clearing it just
 *  means being asked again, which is the harmless direction. */
const ANSWERED_KEY = 'pinpoint.platformFeedbackAnswered';

function alreadyAnswered(): boolean {
    try {
        return window.localStorage.getItem(ANSWERED_KEY) === '1';
    } catch {
        return false;
    }
}

interface Props {
    /** Whether the town enabled the module. The server enforces this too — it
     *  404s the endpoint — so this only decides whether anything is drawn. */
    enabled?: boolean;
    /** Address for "tell us more". Blank/undefined omits that line. */
    feedbackEmail?: string | null;
}

const PlatformFeedback: React.FC<Props> = ({ enabled, feedbackEmail }) => {
    const [done, setDone] = useState(() => alreadyAnswered());
    const [open, setOpen] = useState(false);
    const [sending, setSending] = useState(false);
    const [failed, setFailed] = useState(false);

    if (!enabled) return null;

    const submit = async (value: string) => {
        setSending(true);
        setFailed(false);
        try {
            await api.submitPlatformFeedback(value);
            try { window.localStorage.setItem(ANSWERED_KEY, '1'); } catch { /* private mode */ }
            setDone(true);
        } catch {
            // Nothing is retried and nothing is queued. This is an opinion
            // about a website, and a resident should not be told twice that
            // recording their opinion failed.
            setFailed(true);
        } finally {
            setSending(false);
        }
    };

    const email = (feedbackEmail || '').trim();
    const tellUsMore = email ? (
        <a
            href={`mailto:${email}?subject=${encodeURIComponent('Pinpoint platform feedback')}`}
            className="text-white/60 underline underline-offset-2 hover:text-white/90 transition-colors"
        >
            Want to tell us more? Email us
        </a>
    ) : null;

    if (done) {
        return (
            <div className="flex flex-col items-center gap-1.5 text-sm" data-testid="platform-feedback-thanks">
                <p className="flex items-center gap-1.5 text-white/50">
                    <Check className="w-4 h-4 text-emerald-400" aria-hidden="true" />
                    Thanks — that helps.
                </p>
                {tellUsMore}
            </div>
        );
    }

    if (!open) {
        return (
            <button
                type="button"
                onClick={() => setOpen(true)}
                className="text-sm text-white/40 hover:text-white/80 transition-colors inline-flex items-center gap-1.5"
                data-testid="platform-feedback-open"
            >
                <MessageSquareHeart className="w-4 h-4" aria-hidden="true" />
                How is this site working for you?
            </button>
        );
    }

    return (
        <div className="flex flex-col items-center gap-3 w-full max-w-xl">
            <p className="text-sm text-white/60 text-center" id="platform-feedback-question">
                {PLATFORM_FEEDBACK_QUESTION}
            </p>
            <div
                className="flex flex-wrap items-center justify-center gap-2"
                role="group"
                aria-labelledby="platform-feedback-question"
            >
                {PLATFORM_FEEDBACK_OPTIONS.map((opt) => (
                    <button
                        key={opt.value}
                        type="button"
                        disabled={sending}
                        onClick={() => submit(opt.value)}
                        className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 disabled:opacity-50 text-white/80 text-xs sm:text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/50"
                    >
                        {opt.label}
                    </button>
                ))}
            </div>
            <p className="text-[11px] text-white/30 text-center">
                Anonymous. We store your answer and nothing else — no name, no email, no comment.
            </p>
            {failed && (
                <p className="text-xs text-amber-300/80" role="status">
                    That did not go through. No harm done.
                </p>
            )}
        </div>
    );
};

export default PlatformFeedback;
