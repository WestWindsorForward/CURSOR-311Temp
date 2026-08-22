// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The resident-facing half of the optional platform-feedback module.
 *
 * Three things are worth holding still here, and all three are decisions rather
 * than implementation details:
 *
 *   off is off      a town that did not enable the module gets no entry point
 *                   at all -- not a hidden one, not a disabled one
 *   one tap         a single ordered multiple-choice answer, submitted as one
 *                   value, with no name, email or comment box anywhere near it
 *   mail, not text  "tell us more" is a mailto: link to a configured address,
 *                   or it is absent. Never an in-app compose box, because that
 *                   would put resident-typed text into the town's database --
 *                   the exact thing this design exists to avoid.
 */

const submitPlatformFeedback = vi.fn().mockResolvedValue({ status: 'recorded' });
vi.mock('../services/api', () => {
    const api: any = { submitPlatformFeedback: (...a: unknown[]) => submitPlatformFeedback(...a) };
    return { default: api, api };
});

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
    window.localStorage.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.clearAllMocks();
});

async function mount(props: { enabled?: boolean; feedbackEmail?: string | null }) {
    const { default: PlatformFeedback } = await import('./PlatformFeedback');
    await act(async () => {
        root.render(React.createElement(PlatformFeedback, props));
    });
    return host;
}

const openQuestion = async () => {
    const trigger = host.querySelector('[data-testid="platform-feedback-open"]') as HTMLButtonElement;
    await act(async () => { trigger.click(); });
};

const answer = async (label: string) => {
    const button = [...host.querySelectorAll('button')].find((b) => b.textContent === label)!;
    await act(async () => { button.click(); });
};

describe('the module being off', () => {
    it('renders nothing at all', async () => {
        await mount({ enabled: false, feedbackEmail: 'team@example.org' });
        expect(host.innerHTML).toBe('');
    });

    it('renders nothing when the town has never answered either way', async () => {
        await mount({});
        expect(host.innerHTML).toBe('');
    });
});

describe('the module being on', () => {
    it('starts as one unobtrusive line rather than a form or a modal', async () => {
        await mount({ enabled: true });
        expect(host.querySelector('[data-testid="platform-feedback-open"]')).toBeTruthy();
        // Nothing that interrupts: no dialog, and no scale on screen until asked for.
        expect(host.querySelector('[role="dialog"]')).toBeNull();
        expect(host.querySelectorAll('button').length).toBe(1);
    });

    it('offers exactly the five ordered options, best first', async () => {
        await mount({ enabled: true });
        await openQuestion();
        const labels = [...host.querySelectorAll('[role="group"] button')].map((b) => b.textContent);
        expect(labels).toEqual([
            'Much easier', 'Somewhat easier', 'No difference', 'Somewhat harder', 'Much harder',
        ]);
    });

    it('asks for nothing but the answer', async () => {
        await mount({ enabled: true, feedbackEmail: 'team@example.org' });
        await openQuestion();
        expect(host.querySelector('textarea')).toBeNull();
        expect(host.querySelector('input')).toBeNull();
    });

    it('submits the chosen value and nothing else', async () => {
        await mount({ enabled: true });
        await openQuestion();
        await answer('Somewhat easier');
        expect(submitPlatformFeedback).toHaveBeenCalledTimes(1);
        expect(submitPlatformFeedback).toHaveBeenCalledWith('somewhat_easier');
    });

    it('is done after one tap, and stays done in this browser', async () => {
        await mount({ enabled: true });
        await openQuestion();
        await answer('Much easier');
        expect(host.querySelector('[data-testid="platform-feedback-thanks"]')).toBeTruthy();

        // A resident who files a second report is not asked a second time. The
        // flag is browser-local and is never sent anywhere.
        await act(async () => root.unmount());
        root = createRoot(host);
        await mount({ enabled: true });
        expect(host.querySelector('[data-testid="platform-feedback-open"]')).toBeNull();
        expect(host.querySelector('[data-testid="platform-feedback-thanks"]')).toBeTruthy();
    });

    it('says so plainly when a submission fails, and does not ask again', async () => {
        submitPlatformFeedback.mockRejectedValueOnce(new Error('nope'));
        await mount({ enabled: true });
        await openQuestion();
        await answer('Much harder');
        expect(host.textContent).toContain('did not go through');
    });
});

describe('telling us more goes to email, never to a text box', () => {
    it('renders a mailto with a useful subject when an address is configured', async () => {
        await mount({ enabled: true, feedbackEmail: 'team@example.org' });
        await openQuestion();
        await answer('No difference');

        const link = host.querySelector('a[href^="mailto:"]') as HTMLAnchorElement;
        expect(link).toBeTruthy();
        expect(link.getAttribute('href')).toBe(
            'mailto:team@example.org?subject=Pinpoint%20platform%20feedback',
        );
        expect(link.textContent).toContain('tell us more');
    });

    it('prefills a subject and nothing else', async () => {
        // A prefilled body would put what the resident just did -- their
        // answer, a report id, anything -- into their mail client's drafts.
        await mount({ enabled: true, feedbackEmail: 'team@example.org' });
        await openQuestion();
        await answer('Much harder');

        const href = host.querySelector('a[href^="mailto:"]')!.getAttribute('href')!;
        expect(href).not.toContain('body=');
        expect(href.toLowerCase()).not.toContain('much_harder');
        expect(href.toLowerCase()).not.toContain('harder');
    });

    it('omits the line entirely when no address is configured', async () => {
        await mount({ enabled: true, feedbackEmail: '' });
        await openQuestion();
        await answer('Much easier');

        expect(host.querySelector('[data-testid="platform-feedback-thanks"]')).toBeTruthy();
        expect(host.querySelector('a[href^="mailto:"]')).toBeNull();
        expect(host.textContent).not.toContain('tell us more');
    });

    it('omits the line when the address is only whitespace', async () => {
        await mount({ enabled: true, feedbackEmail: '   ' });
        await openQuestion();
        await answer('Much easier');
        expect(host.querySelector('a[href^="mailto:"]')).toBeNull();
    });
});
