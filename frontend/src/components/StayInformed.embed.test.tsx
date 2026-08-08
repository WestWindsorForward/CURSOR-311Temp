// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * When the operator hosts the registration form, the console frames it -- and
 * a framed form is a request to Microsoft the moment it renders.
 *
 * That makes one invariant worth more than the rest of this file: the frame
 * never appears without somebody having clicked for it. The automatic first-run
 * prompt must contact nobody, because a deployment that phones home on page
 * load is the exact property COMPLIANCE.md promises it does not have. The rest
 * checks that the prefill the operator asked for arrives, that the prefill they
 * did not ask for does not, and that the way out to a new tab is always there.
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

const FORM = 'https://forms.office.com/Pages/ResponsePage.aspx?id=AB12';

async function mount(props: Record<string, unknown> = {}) {
    const { StayInformedHost } = await import('./StayInformed');
    await act(async () => {
        root.render(React.createElement(StayInformedHost as any, { ready: true, ...props }));
    });
}

/** The standing way in, as a clerk would reach it. */
async function clickRegister() {
    const { openStayInformed } = await import('./StayInformed');
    await act(async () => { openStayInformed({ immediate: true }); });
}

const frame = () => host.querySelector<HTMLIFrameElement>('iframe');

beforeEach(() => {
    localStorage.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    state.systemConfig = { contact_form_url: FORM };
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    localStorage.clear();
    vi.clearAllMocks();
});

describe('the automatic first-run prompt', () => {
    it('contacts Microsoft with nothing at all', async () => {
        // It opens by itself, so it may not cause a request. This is the one
        // that must never regress.
        await mount();
        expect(host.textContent).toContain('Register your deployment');
        expect(frame()).toBeNull();
    });

    it('offers a button that shows the form when asked', async () => {
        await mount();
        const button = [...host.querySelectorAll('button')]
            .find(b => b.textContent?.trim() === 'Register your deployment')!;
        expect(button).toBeDefined();
        await act(async () => { button.click(); });
        expect(frame()).not.toBeNull();
    });

    it('sends nothing when it is dismissed', async () => {
        await mount();
        const notNow = [...host.querySelectorAll('button')]
            .find(b => b.textContent?.trim() === 'Not now')!;
        await act(async () => { notNow.click(); });
        // The dismissal is recorded and no frame was ever created. Asserting the
        // modal is gone from the DOM would be asserting on an exit animation,
        // which AnimatePresence is still running at this point.
        expect(localStorage.getItem('pinpoint311.stay-informed.dismissed')).toBe('not-now');
        expect(frame()).toBeNull();
    });
});

describe('the form, once somebody has asked for it', () => {
    it('is framed from the operator\'s form, asking Forms for its frameable view', async () => {
        await mount();
        await clickRegister();
        const src = frame()!.src;
        expect(src).toContain('forms.office.com');
        expect(src).toContain('embed=true');
    });

    it('is sandboxed and sends no referrer', async () => {
        // Microsoft has no need for the address of a town's admin console,
        // which is frequently an internal hostname.
        await mount();
        await clickRegister();
        const sandbox = frame()!.getAttribute('sandbox') ?? '';
        expect(sandbox).toContain('allow-scripts');
        expect(sandbox).toContain('allow-forms');
        expect(sandbox).not.toContain('allow-top-navigation');
        expect(frame()!.getAttribute('referrerpolicy')).toBe('no-referrer');
    });

    it('still offers a new tab, because a framed form can silently fail', async () => {
        // Third-party storage is what Forms needs and what a hardened browser
        // refuses, and a refused frame is a blank rectangle with no event this
        // side can see. So the way out cannot be conditional on noticing.
        await mount();
        await clickRegister();
        const link = host.querySelector<HTMLAnchorElement>('a[href*="forms.office.com"]');
        expect(link).not.toBeNull();
        expect(link!.target).toBe('_blank');
        expect(link!.rel).toContain('noopener');
    });

    it('says who hosts it', async () => {
        await mount();
        await clickRegister();
        expect(host.textContent).toContain('Microsoft Forms');
    });
});

describe('what gets pre-filled', () => {
    it('fills in the tokens the operator put in the URL', async () => {
        state.systemConfig = {
            contact_form_url: `${FORM}&r1f0c={organization}&r6b42={deployment_url}`,
            public_origin: 'https://311.example.gov',
        };
        await mount({ prefill: { organization: 'Township of Example' } });
        await clickRegister();

        const src = frame()!.src;
        expect(src).toContain('r1f0c=Township%20of%20Example');
        expect(src).toContain('r6b42=https%3A%2F%2F311.example.gov');
    });

    it('does not send the administrator when the operator did not ask', async () => {
        // The opt-in, end to end: a URL with no contact tokens must not carry
        // the signed-in admin's name or address to Microsoft.
        state.systemConfig = { contact_form_url: `${FORM}&r1f0c={organization}` };
        await mount({
            prefill: {
                organization: 'Example',
                contact_name: 'Dana Clerk',
                contact_email: 'dana@example.gov',
            },
        });
        await clickRegister();

        const src = frame()!.src;
        expect(src).not.toContain('Dana');
        expect(src.toLowerCase()).not.toContain('dana');
    });

    it('sends the administrator when the operator did ask', async () => {
        state.systemConfig = { contact_form_url: `${FORM}&r9aa1={contact_email}` };
        await mount({ prefill: { contact_email: 'dana@example.gov' } });
        await clickRegister();

        expect(frame()!.src).toContain('r9aa1=dana%40example.gov');
    });
});

describe('when the form cannot be framed', () => {
    it('links out in a new tab instead, with CONTACT_FORM_EMBED off', async () => {
        // What an organisation-restricted Form needs: framed, it serves a
        // sign-in page rather than the questions.
        state.systemConfig = { contact_form_url: FORM, contact_form_embed: false };
        await mount();
        await clickRegister();

        expect(frame()).toBeNull();
        const link = host.querySelector<HTMLAnchorElement>('a[href*="forms.office.com"]');
        expect(link).not.toBeNull();
        expect(link!.target).toBe('_blank');
        expect(host.textContent).toContain('Opens in a new tab');
    });
});

describe('when nothing is configured', () => {
    it('keeps the built-in form, so a self-hoster loses nothing', async () => {
        state.systemConfig = { contact_form_url: '' };
        await mount();
        expect(frame()).toBeNull();
        expect(host.textContent).toContain('Stay informed');
        expect(host.querySelector('form')).not.toBeNull();
    });

    it('treats an unusable URL as no form rather than framing it', async () => {
        // A mistyped setting must not take away the only way to leave a
        // contact, and must never reach an iframe src.
        state.systemConfig = { contact_form_url: 'javascript:alert(1)' };
        await mount();
        await clickRegister();
        expect(frame()).toBeNull();
        expect(host.querySelector('form')).not.toBeNull();
    });
});
