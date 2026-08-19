// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// recharts' ResponsiveContainer measures its box on mount, and jsdom has no
// ResizeObserver. A stub that reports nothing is enough: the trend chart is not
// what these tests assert, it just has to mount without throwing.
(globalThis as any).ResizeObserver = class {
    observe() { /* no layout in jsdom */ }
    unobserve() { }
    disconnect() { }
};

/**
 * The staff-facing aggregate.
 *
 * The load-bearing claims: nothing renders when the module is off, the panel
 * shows a distribution rather than a score, and it never presents an average of
 * an ordinal scale as though the five options were evenly spaced.
 */

const STATS = {
    total_responses: 40,
    counts: {
        much_easier: 20,
        somewhat_easier: 10,
        no_difference: 4,
        somewhat_harder: 4,
        much_harder: 2,
    },
    percentages: {
        much_easier: 50,
        somewhat_easier: 25,
        no_difference: 10,
        somewhat_harder: 10,
        much_harder: 5,
    },
    net_easier_percent: 60,
    responses_by_month: { '2026-06': 12, '2026-07': 15, '2026-08': 13 },
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.clearAllMocks();
});

async function mount(props: { enabled?: boolean; stats: unknown }) {
    const { default: PlatformFeedbackStats } = await import('./PlatformFeedbackStats');
    await act(async () => {
        root.render(React.createElement(PlatformFeedbackStats as any, props));
    });
    return host;
}

describe('the module being off', () => {
    it('renders no panel, even if data somehow arrived', async () => {
        await mount({ enabled: false, stats: STATS });
        expect(host.innerHTML).toBe('');
    });

    it('renders no panel before the aggregate has loaded', async () => {
        await mount({ enabled: true, stats: null });
        expect(host.innerHTML).toBe('');
    });
});

describe('the aggregate panel', () => {
    it('shows the response total and every one of the five options', async () => {
        await mount({ enabled: true, stats: STATS });
        const text = host.textContent || '';
        expect(text).toContain('40');
        for (const label of ['Much easier', 'Somewhat easier', 'No difference', 'Somewhat harder', 'Much harder']) {
            expect(text).toContain(label);
        }
        expect(text).toContain('20 (50%)');
        expect(text).toContain('2 (5%)');
    });

    it('shows options that nobody chose rather than dropping them', async () => {
        // A distribution with a missing bar reads as a scale that never offered
        // that answer.
        await mount({
            enabled: true,
            stats: { ...STATS, counts: { ...STATS.counts, much_harder: 0 }, percentages: { ...STATS.percentages, much_harder: 0 } },
        });
        expect(host.textContent).toContain('Much harder');
        expect(host.textContent).toContain('0 (0%)');
    });

    it('labels its one summary number as a net, not as a score or an average', async () => {
        await mount({ enabled: true, stats: STATS });
        const text = (host.textContent || '').toLowerCase();
        expect(text).toContain('+60%');
        expect(text).toContain('not an average');
        expect(text).not.toMatch(/average rating|mean score|out of 5|\d\.\d\s*\/\s*5/);
    });

    it('says nothing has come in yet rather than drawing an empty chart', async () => {
        await mount({
            enabled: true,
            stats: { ...STATS, total_responses: 0, counts: {}, percentages: {}, responses_by_month: {} },
        });
        expect(host.textContent).toContain('No responses yet');
    });

    it('names the question and the fact that it collects no comments', async () => {
        await mount({ enabled: true, stats: STATS });
        const text = (host.textContent || '').toLowerCase();
        expect(text).toContain('made reporting issues to the town');
        expect(text).toContain('no comments collected');
    });
});
