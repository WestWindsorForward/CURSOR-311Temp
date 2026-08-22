import React, { useEffect, useId, useRef } from 'react';
import { Camera, X, Check, AlertTriangle, Loader2 } from 'lucide-react';

/** Where one attached photo is in the screening pipeline. */
export type PhotoState = 'uploading' | 'checking' | 'ready' | 'review' | 'blocked' | 'error';

export interface PhotoStatus {
    state: PhotoState;
    /** Why, for the states where "why" is the whole message. */
    message?: string;
}

interface PhotoUploadProps {
    previewUrls: string[];
    onAdd: (files: FileList) => void;
    onRemove: (index: number) => void;
    maxPhotos?: number;
    /** Per-photo screening state, positionally aligned with previewUrls.
     *  Absent means the host is not screening -- the control then behaves
     *  exactly as it did before screening existed. */
    statuses?: PhotoStatus[];
}

/**
 * What each state says, in the resident's terms rather than ours.
 *
 * "Checking your photo" rather than "moderating" or "redacting": the resident
 * did not ask for either and does not need the vocabulary. The two failure
 * states are worded to close the question -- a blocked photo will not become
 * unblocked by waiting, and a photo held for review is attached, not lost --
 * because an ambiguous status on a form people are trying to leave gets read as
 * "still working" and they wait for nothing.
 */
const LABELS: Record<PhotoState, string> = {
    uploading: 'Uploading…',
    checking: 'Checking your photo…',
    ready: 'Ready to send',
    review: 'A staff member will review this photo',
    blocked: "This photo can't be used",
    error: 'A staff member will review this photo',
};

const BUSY: PhotoState[] = ['uploading', 'checking'];

/**
 * The optional photo section of the resident request form.
 *
 * The trigger is a real <button> in front of a hidden file input rather than
 * a styled <label> around it: a label over a display:none input never enters
 * the tab order, which made this control mouse-only. The button sits in the
 * DOM exactly where it appears visually (between the map and the contact
 * card), activates the picker on Enter/Space natively, and tells a screen
 * reader what it is and that it is optional; the input itself is kept out of
 * the accessibility tree so the picker is announced once, not twice.
 *
 * Adding or removing a photo unmounts the control that was focused (the
 * trigger disappears at the cap, a remove button disappears with its photo),
 * which would silently drop focus to <body>; focus is moved to the nearest
 * surviving control instead, and a live region says what the count now is,
 * because a resident who cannot see the thumbnails otherwise gets no
 * feedback at all -- including when a too-large selection is truncated.
 *
 * Photos are also screened -- moderated and blurred -- while the resident is
 * still filling in the rest of the form, so `statuses` reports where each one
 * has got to. That progress is real information, not decoration: a photo can
 * come back refused, and the resident needs to hear that while they are still
 * looking at the photo they picked. There is exactly ONE live region in this
 * control and the per-photo states are folded into it alongside the count,
 * because two polite regions updating together is how a screen reader user
 * ends up hearing neither of them.
 */
export default function PhotoUpload({ previewUrls, onAdd, onRemove, maxPhotos = 3, statuses }: PhotoUploadProps) {
    const inputRef = useRef<HTMLInputElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const removeRefs = useRef<(HTMLButtonElement | null)[]>([]);
    const uid = useId();
    const hintId = `photo-upload-hint-${uid}`;

    const count = previewUrls.length;
    const atCap = count >= maxPhotos;

    // When the previously focused control unmounted with the change, put
    // focus on what replaced it visually.
    const pendingFocus = useRef<'after-add' | number | null>(null);
    useEffect(() => {
        if (pendingFocus.current === null) return;
        if (pendingFocus.current === 'after-add') {
            // A multi-file selection arrives one preview at a time (each
            // FileReader resolves separately), so the count climbs 0 -> 1 ->
            // 2 -> 3 and this effect runs on every step. The add is only
            // "settled" for focus purposes once the trigger is gone: consume
            // the pending move at the cap, and until then leave it armed --
            // clearing it at the first step would let the trigger unmount a
            // tick later with nothing to catch the focus it was holding.
            if (atCap) {
                removeRefs.current[count - 1]?.focus();
            } else {
                // The trigger survived, so it keeps focus on its own -- unless
                // something in the batch dropped it, in which case put it back.
                if (document.activeElement === document.body) triggerRef.current?.focus();
                return;
            }
        } else {
            const target = triggerRef.current ?? removeRefs.current[Math.min(pendingFocus.current, count - 1)];
            target?.focus();
        }
        pendingFocus.current = null;
    }, [count, atCap]);

    const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            pendingFocus.current = 'after-add';
            onAdd(e.target.files);
        }
        // The same file must be selectable again after being removed.
        e.target.value = '';
    };

    const handleRemove = (idx: number) => {
        pendingFocus.current = idx;
        onRemove(idx);
    };

    const statusAt = (idx: number): PhotoStatus | undefined => statuses?.[idx];
    const labelAt = (idx: number): string => {
        const status = statusAt(idx);
        if (!status) return '';
        return status.message || LABELS[status.state];
    };

    // One sentence per photo whose state is worth saying out loud. "Ready" is
    // deliberately included: a resident who cannot see the tick otherwise never
    // learns that the wait ended.
    const spoken = (statuses ?? [])
        .slice(0, count)
        .map((status, idx) => `Photo ${idx + 1}: ${status.message || LABELS[status.state]}.`)
        .join(' ');

    return (
        <div className="space-y-3">
            <span className="block text-sm font-medium text-white/70">
                {"Photos (optional, max 3)"}
            </span>
            <p id={hintId} className="sr-only">
                Optional. You can attach up to {maxPhotos} photos of the issue.
            </p>
            <p className="sr-only" role="status" aria-live="polite">
                {count === 0
                    ? ''
                    : `${count} of ${maxPhotos} photos attached.${atCap ? ' Maximum reached; extra selections are not attached.' : ''}${spoken ? ' ' + spoken : ''}`}
            </p>

            <div className="flex gap-3 flex-wrap">
                {previewUrls.map((url, idx) => {
                    const status = statusAt(idx);
                    const label = labelAt(idx);
                    const busy = !!status && BUSY.includes(status.state);
                    const bad = status?.state === 'blocked';
                    return (
                        <div key={idx} className="relative group">
                            <img
                                src={url}
                                // The state rides on the image's own name too, so a
                                // resident arrowing through the thumbnails hears it
                                // without waiting for the live region to repeat.
                                alt={label ? `Photo ${idx + 1}, ${label}` : `Photo ${idx + 1}`}
                                className={`w-24 h-24 object-cover rounded-xl border ${bad ? 'border-red-400/70 opacity-50' : 'border-white/20'}`}
                            />
                            {status && (
                                <span
                                    // Announced through the single live region above,
                                    // so this copy is visual only -- otherwise every
                                    // change is read twice.
                                    aria-hidden="true"
                                    className={`absolute inset-x-0 bottom-0 rounded-b-xl px-1 py-0.5 flex items-center gap-1 text-[10px] leading-tight ${bad ? 'bg-red-600/90 text-white' : 'bg-black/70 text-white/90'}`}
                                >
                                    {busy && <Loader2 className="w-3 h-3 shrink-0 animate-spin" />}
                                    {status.state === 'ready' && <Check className="w-3 h-3 shrink-0" />}
                                    {(bad || status.state === 'review' || status.state === 'error') && (
                                        <AlertTriangle className="w-3 h-3 shrink-0" />
                                    )}
                                    <span className="truncate">{label}</span>
                                </span>
                            )}
                            <button
                                type="button"
                                ref={(el) => { removeRefs.current[idx] = el; }}
                                onClick={() => handleRemove(idx)}
                                className="absolute -top-2 -right-2 p-1 bg-red-500 rounded-full opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                                aria-label={`Remove photo ${idx + 1}`}
                            >
                                <X className="w-4 h-4 text-white" aria-hidden="true" />
                            </button>
                        </div>
                    );
                })}

                {!atCap && (
                    <>
                        <button
                            type="button"
                            ref={triggerRef}
                            onClick={() => inputRef.current?.click()}
                            aria-label="Add photos"
                            aria-describedby={hintId}
                            className="w-24 h-24 flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-white/20 hover:border-white/40 cursor-pointer transition-colors"
                        >
                            <Camera className="w-6 h-6 text-white/40" aria-hidden="true" />
                            <span className="text-xs text-white/40 mt-1">{"Add Photos"}</span>
                        </button>
                        <input
                            ref={inputRef}
                            type="file"
                            accept="image/*"
                            multiple
                            onChange={handleFiles}
                            className="hidden"
                            tabIndex={-1}
                            aria-hidden="true"
                        />
                    </>
                )}
            </div>
        </div>
    );
}
