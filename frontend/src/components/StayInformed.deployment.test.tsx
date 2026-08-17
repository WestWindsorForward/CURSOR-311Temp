// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Two dismissals, answering two different questions.
 *
 * The localStorage flags are one person putting a nudge aside in their own
 * browser. `registration_prompt_dismissed` on the public config is the operator
 * settling the question for the deployment -- a demo instance, or a fleet
 * member that registered once and does not want every visitor asked again.
 *
 * So the flag is an override, and these tests pin both halves of that word.
 * When it is true nothing prompts anybody, in any browser, with storage empty.
 * When it is false every byte of the old behaviour is still in charge. And in
 * neither case does the way IN disappear: the host stays mounted and a click on
 * "Register your deployment" still opens the form, because an operator who
 * switched the prompt off has not asked to be locked out of the form.
 */

const state = vi.hoisted(() => ({ systemConfig: {} as Record<string, unknown> }));

vi.mock('../services/api', () => {
    const api: any = new Proxy({}, {
        get: (_t, prop: string) => prop === 'getSystemConfig'
            ? vi.fn().mockImplementation(async () => state.systemConfig)
            : vi.fn().mockResolvedValue({}),
    });
    return { default: api, api };
});

let host: HTMLDivElement;
let root: Root;

const MODAL_KEY = 'pinpoint311.stay-informed.dismissed';
const BANNER_KEY = 'pinpoint311.stay-informed.banner-dismissed';

async function mount(props: Record<string, unknown> = {}) {
    const { StayInformedHost } = await import('./StayInformed');
    await act(async () => {
        root.render(React.createElement(StayInformedHost as any, { ready: true, ...props }));
    });
}

const dialog = () => host.querySelector('[role="dialog"]');
/** The standing banner, identified by the only text unique to it. */
const banner = () => [...host.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === 'Register a contact');

beforeEach(() => {
    localStorage.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    state.systemConfig = {};
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    localStorage.clear();
    vi.clearAllMocks();
});

describe('when the deployment has answered', () => {
    beforeEach(() => { state.systemConfig = { registration_prompt_dismissed: true }; });

    it('does not open the first-run modal, in a browser that has never been asked', async () => {
        await mount();
        expect(dialog()).toBeNull();
        // And nothing was written: the operator answered, this visitor did not.
        expect(localStorage.getItem(MODAL_KEY)).toBeNull();
    });

    it('does not show the standing banner either', async () => {
        // The banner's own condition is met -- the modal is dismissed in this
        // browser and the banner is not -- and it still must not appear.
        localStorage.setItem(MODAL_KEY, 'not-now');
        await mount();
        expect(banner()).toBeUndefined();
    });

    it('still opens the form when somebody asks for it', async () => {
        // The host is mounted, not gated out of the tree. A sibling repo shipped
        // this component without mounting it and every button that opens it went
        // dead; suppressing the prompt must not reproduce that.
        await mount();
        const { openStayInformed } = await import('./StayInformed');
        await act(async () => { openStayInformed({ immediate: true }); });
        expect(dialog()).not.toBeNull();
    });
});

describe('when the deployment has not answered', () => {
    it('prompts on first run exactly as before', async () => {
        state.systemConfig = { registration_prompt_dismissed: false };
        await mount();
        expect(dialog()).not.toBeNull();
    });

    it('treats an absent flag as not answered', async () => {
        // An older backend, or a config read that failed. Silence must not be
        // read as "stop telling this town about security fixes".
        state.systemConfig = {};
        await mount();
        expect(dialog()).not.toBeNull();
    });

    it('still lets the browser dismissal decide, both ways', async () => {
        state.systemConfig = { registration_prompt_dismissed: false };
        localStorage.setItem(MODAL_KEY, 'not-now');
        await mount();
        // Dismissed here, so no modal -- and the banner in its place.
        expect(dialog()).toBeNull();
        expect(banner()).toBeDefined();
    });

    it('honours a dismissed banner as it always did', async () => {
        state.systemConfig = { registration_prompt_dismissed: false };
        localStorage.setItem(MODAL_KEY, 'not-now');
        localStorage.setItem(BANNER_KEY, 'dismissed');
        await mount();
        expect(banner()).toBeUndefined();
    });
});
