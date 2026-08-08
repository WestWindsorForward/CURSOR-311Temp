// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The registration touchpoint on the setup tracker has two shapes, and this
 * pins the branch between them: an operator who set CONTACT_FORM_URL gets a
 * link out to their Microsoft Form, opened in a new tab and without a window
 * handle; a self-hoster who set nothing keeps the built-in contact form. The
 * second case is the one that must not regress quietly -- losing it means a
 * town without a form loses its only way to leave a contact.
 */

const state = vi.hoisted(() => ({ systemConfig: {} as Record<string, unknown> }));

vi.mock('../services/api', () => {
    /* Same proxy as the smoke test: every method resolves, list-shaped names
     * resolve to arrays, and only the calls this test branches on are named. */
    const shapes: Record<string, unknown> = {
        getConfig: { public_origin: 'https://town.gov' },
        getProviderStatus: {},
        getConnectorHealth: { connectors: [] },
        getCloudIdentity: null,
        getStorageStatus: { secrets: { store: 'google', count: 0, reachable: true }, pii: {} },
        getProviderCatalog: {
            capability: 'ai', current_provider: 'vertex', providers: [], configured: {},
        },
        getCloudProfile: {
            profile: 'google', managed: false, profiles: [],
            components: { identity: 'auth0' }, maps: { label: 'Google Maps' },
        },
    };
    const listish = /^(list|get)[A-Za-z]*(s|List|Configs|Layers|Errors|Catalog)$/;
    const api: any = new Proxy({}, {
        get: (_t, prop: string) => prop === 'getSystemConfig'
            ? vi.fn().mockImplementation(async () => state.systemConfig)
            : vi.fn().mockResolvedValue(
                prop in shapes ? shapes[prop] : listish.test(prop) ? [] : {},
            ),
    });
    return { default: api, api };
});

let host: HTMLDivElement;
let root: Root;

const props: any = {
    secrets: [],
    onSaveSecret: vi.fn().mockResolvedValue(undefined),
    onRefresh: vi.fn(),
};

async function mount() {
    const { default: Page } = await import('./SetupIntegrationsPage');
    const { DialogProvider } = await import('./DialogProvider');
    await act(async () => {
        root.render(React.createElement(DialogProvider, null, React.createElement(Page, props)));
    });
}

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

describe('the registration touchpoint on the setup tracker', () => {
    it('invites registration when a form is configured', async () => {
        state.systemConfig = { contact_form_url: 'https://forms.office.com/r/example' };
        await mount();

        expect(host.textContent).toContain('Register your deployment');
        // The honesty note: the form is not ours and the page says so.
        expect(host.textContent).toContain('hosted by Microsoft Forms');
    });

    it('opens the form in the console rather than navigating away', async () => {
        // A button, not a link. The form is shown in the modal so somebody can
        // answer it without losing the setup page they were working through --
        // and an <a href> to Microsoft would be the old behaviour.
        state.systemConfig = { contact_form_url: 'https://forms.office.com/r/example' };
        await mount();

        expect(host.querySelector('a[href*="forms.office.com"]')).toBeNull();
        const invite = [...host.querySelectorAll('button')]
            .find(b => b.textContent?.includes('Register your deployment'));
        expect(invite).toBeDefined();
    });

    it('asks for the form deliberately, so nothing is fetched from Microsoft first', async () => {
        // The click carries `immediate`, which is what permits the host to
        // render the third-party frame. Without it the modal would open on the
        // invitation and the clerk would have to ask twice.
        state.systemConfig = { contact_form_url: 'https://forms.office.com/r/example' };
        await mount();

        const events: CustomEvent[] = [];
        const listen = (e: Event) => events.push(e as CustomEvent);
        window.addEventListener('pinpoint311:stay-informed:open', listen);
        const invite = [...host.querySelectorAll('button')]
            .find(b => b.textContent?.includes('Register your deployment'))!;
        await act(async () => { invite.click(); });
        window.removeEventListener('pinpoint311:stay-informed:open', listen);

        expect(events).toHaveLength(1);
        expect(events[0].detail).toEqual({ immediate: true });
    });

    it('falls back to the built-in contact form when no URL is configured', async () => {
        state.systemConfig = { contact_form_url: '' };
        await mount();

        expect(host.textContent).toContain('Register a contact');
        expect(host.textContent).not.toContain('Microsoft Forms');
        expect(host.querySelector('a[href*="forms.office.com"]')).toBeNull();
    });

    it('falls back on an unusable URL too, so the footer cannot disagree with the modal', async () => {
        // Both sides run the setting through the same builder. Reading it raw
        // here would invite somebody to register and then open a modal showing
        // the built-in form.
        state.systemConfig = { contact_form_url: 'not-a-url' };
        await mount();

        expect(host.textContent).toContain('Register a contact');
        expect(host.textContent).not.toContain('Microsoft Forms');
    });
});
