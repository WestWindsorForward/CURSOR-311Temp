// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * The keyboard-only review's repro: Tab through the status filters to
 * "Resolved", press Tab, and focus lands in the footer -- every request card
 * skipped, because the click handler sat on a plain animation div. A resident
 * without a mouse could see their own submissions listed and never open one.
 *
 * The contract now: each card is a role="button" in the tab sequence in list
 * order, activates with Enter and with Space, and carries an accessible name
 * a screen reader can use ("Pothole, at 12 Main St, status Resolved, ...").
 */

vi.mock('../services/api', () => {
    const requests = [
        {
            service_request_id: 'REQ-001',
            service_code: 'pothole',
            service_name: 'Pothole',
            status: 'closed',
            description: 'Deep pothole near the crosswalk',
            address: '12 Main St',
            requested_datetime: '2026-06-03T10:00:00Z',
            photo_count: 1,
            media_urls: ['https://town.example/photos/1.jpg'],
        },
        {
            service_request_id: 'REQ-002',
            service_code: 'streetlight',
            service_name: 'Streetlight Out',
            status: 'open',
            description: 'Light flickers all night',
            address: '4 Oak Ave',
            requested_datetime: '2026-06-04T10:00:00Z',
            photo_count: 0,
        },
    ];
    const api = {
        getPublicRequests: vi.fn().mockResolvedValue(requests),
        getPublicRequestDetail: vi.fn().mockImplementation(async (id: string) =>
            requests.find(r => r.service_request_id === id)),
        getPublicComments: vi.fn().mockResolvedValue([]),
        getPublicAuditLog: vi.fn().mockResolvedValue([]),
        getMapsConfig: vi.fn().mockResolvedValue({}),
    };
    return { api, default: api };
});

// The card list is what is under test; live translation and map tiles are not.
vi.mock('../hooks/useContentTranslation', () => ({
    useContentTranslation: (text: string) => ({ translatedText: text, isTranslating: false }),
}));
vi.mock('./RequestDetailMap', () => ({ default: () => null }));

import TrackRequests from './TrackRequests';

// jsdom has no PointerEvent; framer-motion's keyboard press support fires one
// when Enter lands on a whileTap element.
if (typeof window.PointerEvent === 'undefined') {
    (window as any).PointerEvent = class PointerEvent extends MouseEvent { };
}

beforeEach(() => {
    localStorage.clear();
    window.scrollTo = vi.fn();
});
afterEach(cleanup);

const cardFor = (name: RegExp) => screen.findByRole('button', { name });

describe('TrackRequests keyboard access', () => {
    it('renders each request card as a focusable button with a descriptive name', async () => {
        render(<TrackRequests />);

        const card = await cardFor(/Pothole, at 12 Main St, status Resolved, opened Jun 3, 2026/);
        expect(card.tabIndex).toBe(0);

        const second = await cardFor(/Streetlight Out, at 4 Oak Ave, status Open/);
        expect(second.tabIndex).toBe(0);
    });

    it('reaches the cards by Tab from the Resolved filter, in list order', async () => {
        const user = userEvent.setup();
        render(<TrackRequests />);
        const first = await cardFor(/Pothole.*status Resolved/);
        const second = await cardFor(/Streetlight Out.*status Open/);

        // jsdom applies no responsive CSS, so the filter's accessible name
        // includes both its desktop and mobile labels; the first /Resolved/
        // button in DOM order is the filter tab.
        screen.getAllByRole('button', { name: /resolved/i })[0].focus();

        // Between the filters and the list sit the three stat tiles; the
        // cards must come next rather than being skipped for the footer.
        const reached: Element[] = [];
        for (let i = 0; i < 8 && !reached.includes(second); i++) {
            await user.tab();
            reached.push(document.activeElement!);
        }
        const firstAt = reached.indexOf(first);
        const secondAt = reached.indexOf(second);
        expect(firstAt).toBeGreaterThanOrEqual(0);
        expect(secondAt).toBeGreaterThan(firstAt);
    });

    it('opens the request detail with Enter, exactly once', async () => {
        const user = userEvent.setup();
        const { api } = await import('../services/api');
        (api.getPublicRequestDetail as ReturnType<typeof vi.fn>).mockClear();
        render(<TrackRequests />);

        (await cardFor(/Pothole.*status Resolved/)).focus();
        await user.keyboard('{Enter}');

        expect(await screen.findByRole('heading', { name: 'Pothole', level: 1 })).toBeTruthy();
        expect(screen.getByText('Deep pothole near the crosswalk')).toBeTruthy();
        // One activation: Card's own key handler and framer-motion's keyboard
        // press support must not both fire the click.
        expect(api.getPublicRequestDetail).toHaveBeenCalledTimes(1);
    });

    it('opens the request detail with Space', async () => {
        const user = userEvent.setup();
        render(<TrackRequests />);

        (await cardFor(/Streetlight Out.*status Open/)).focus();
        await user.keyboard(' ');

        expect(await screen.findByRole('heading', { name: 'Streetlight Out', level: 1 })).toBeTruthy();
    });

    it('labels a card from "Your Submissions" as submitted by you, with its ID', async () => {
        localStorage.setItem('my_requests', JSON.stringify(['REQ-001']));
        render(<TrackRequests />);
        expect(await cardFor(/Pothole.*status Resolved.*submitted by you.*request REQ-001/)).toBeTruthy();
    });

    it('carries the content role="button" hides into the accessible name', async () => {
        // A button's descendants are presentational, so a screen reader can
        // no longer arrow into the card to read these -- if they are not in
        // the name they are gone.
        render(<TrackRequests />);
        const card = await cardFor(/Pothole/);
        const name = card.getAttribute('aria-label')!;
        expect(name).toContain('Deep pothole near the crosswalk');  // description
        expect(name).toContain('REQ-001');                          // the searchable ID
        expect(name).toContain('1 photo');                          // the photo badge
    });

    it('opens the photo lightbox from the keyboard, closes on Escape, restores focus', async () => {
        const user = userEvent.setup();
        render(<TrackRequests />);

        (await cardFor(/Pothole.*status Resolved/)).focus();
        await user.keyboard('{Enter}');
        await screen.findByRole('heading', { name: 'Pothole', level: 1 });

        const thumb = await screen.findByRole('button', { name: /view submitted photo 1/i });
        thumb.focus();
        await user.keyboard('{Enter}');

        await screen.findByRole('dialog', { name: /photo preview/i });
        // Focus starts inside the dialog, on the close button.
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /close image preview/i }));

        await user.keyboard('{Escape}');
        expect(screen.queryByRole('dialog', { name: /photo preview/i })).toBeNull();
        // Focus returns to the thumbnail that opened it.
        expect(document.activeElement).toBe(thumb);
    });
});
