import { useRef } from 'react';
import { X } from 'lucide-react';

interface PhotoLightboxProps {
    /** The photo to show full size. The lightbox renders only when set. */
    url: string;
    onClose: () => void;
}

/**
 * The full-size photo overlay used by Track Requests.
 *
 * It is a modal in the literal sense -- the page behind it is covered by an
 * opaque backdrop -- so it has to behave like one: aria-modal tells a screen
 * reader the rest of the page is unavailable, and Tab is kept inside the
 * overlay. Without the containment, Tab walked out of the dialog onto a page
 * nobody could see, and focus went missing behind the black.
 *
 * Escape and focus restoration are the caller's, because the caller knows
 * which thumbnail opened it.
 */
export default function PhotoLightbox({ url, onClose }: PhotoLightboxProps) {
    const containerRef = useRef<HTMLDivElement>(null);

    // Keep Tab inside the overlay. The dialog holds a single focusable
    // control today (the close button), but the cycle is written generally so
    // adding a "next photo" control later cannot quietly reopen the leak.
    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key !== 'Tab') return;
        const focusable = containerRef.current?.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (!focusable || focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (e.shiftKey) {
            if (active === first) {
                e.preventDefault();
                last.focus();
            }
        } else if (active === last) {
            e.preventDefault();
            first.focus();
        }
    };

    return (
        <div
            ref={containerRef}
            className="fixed inset-0 z-[100] flex items-center justify-center p-4 md:p-8"
            onClick={onClose}
            onKeyDown={handleKeyDown}
            role="dialog"
            aria-modal="true"
            aria-label="Photo preview"
        >
            {/* Backdrop with blur */}
            <div className="absolute inset-0 bg-black/95 backdrop-blur-xl" />

            {/* Close button */}
            <button
                autoFocus
                onClick={onClose}
                className="absolute top-4 right-4 md:top-6 md:right-6 z-20 p-2 rounded-full bg-white/10 hover:bg-white/20 border border-white/20 transition-all duration-300 group"
                aria-label="Close image preview"
            >
                <X className="w-6 h-6 text-white group-hover:rotate-90 transition-transform duration-300" aria-hidden="true" />
            </button>

            {/* Image container with premium styling */}
            <div
                className="relative z-10 max-w-[90vw] max-h-[85vh] rounded-2xl overflow-hidden shadow-2xl ring-1 ring-white/20"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Gradient glow effect behind image */}
                <div className="absolute -inset-1 bg-gradient-to-r from-primary-500/30 via-purple-500/30 to-primary-500/30 blur-xl opacity-50" />

                {/* Image */}
                <img
                    src={url}
                    alt="Full size preview"
                    className="relative max-w-full max-h-[85vh] object-contain bg-gray-900/50 rounded-2xl"
                />
            </div>

            {/* Instructions */}
            <p className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/50 text-sm">
                Press Escape or click anywhere to close
            </p>
        </div>
    );
}
