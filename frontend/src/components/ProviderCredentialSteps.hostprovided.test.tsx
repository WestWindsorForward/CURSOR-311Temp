// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * A credential the deployment's host supplied says so, and behaves like any
 * other saved credential.
 *
 * A hosted town frequently has no account of its own with a map or translation
 * vendor, so its host can hand the instance working credentials. That is only
 * an improvement if the card reads honestly. The failure it replaces is a box
 * marked plainly "Saved" against a key the town never entered: a clerk goes
 * looking for a vendor account nobody at the town has, finds nothing, and
 * concludes the page is lying.
 *
 * The rest of the card must not change. Host-provided is not read-only, not a
 * lock, and not a different colour -- the badge stays green, the box still
 * accepts a value, and typing one is exactly how a town takes the key back. So
 * this asserts the wording changed and that nothing else did.
 */

let InlineProviderSetup: typeof import('./InlineProviderSetup').default;

const AUTH0 = {
    current_provider: 'auth0',
    configured: { auth0: true },
    stored_fields: { AUTH0_DOMAIN: true, AUTH0_CLIENT_ID: true, AUTH0_CLIENT_SECRET: true },
    providers: [{
        provider: 'auth0',
        name: 'Auth0',
        credential_fields: [
            { key: 'AUTH0_DOMAIN', label: 'Auth0 Domain', secret: false },
            { key: 'AUTH0_CLIENT_ID', label: 'Client ID', secret: false },
            { key: 'AUTH0_CLIENT_SECRET', label: 'Client Secret', secret: true },
        ],
    }],
};

let container: HTMLDivElement;
let root: Root;

const getCloudIdentity = vi.fn();
const getProviderCatalog = vi.fn();
const saveProvider = vi.fn();
const testProvider = vi.fn();

vi.mock('../services/api', () => ({
    api: {
        getProviderCatalog: (...a: unknown[]) => getProviderCatalog(...a),
        getCloudIdentity: (...a: unknown[]) => getCloudIdentity(...a),
        saveProvider: (...a: unknown[]) => saveProvider(...a),
        testProvider: (...a: unknown[]) => testProvider(...a),
    },
}));

async function mount(ui: React.ReactElement) {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(ui); });
    await act(async () => { await Promise.resolve(); });
}

beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    // setupStepsContent fills a module-scoped registry as an import side
    // effect, so after a reset it has to be re-imported before anything reads
    // the walk. Same ordering as InlineProviderSetup.test.tsx.
    await import('./setupStepsContent');
    InlineProviderSetup = (await import('./InlineProviderSetup')).default;
    getProviderCatalog.mockResolvedValue(AUTH0);
    getCloudIdentity.mockResolvedValue({ attached: false, provider: null, identity: null, skippable_keys: [] });
    saveProvider.mockResolvedValue({ ok: true, provider: 'auth0', warnings: [] });
    testProvider.mockResolvedValue({ ok: true, detail: 'Signed a test token.' });
});

afterEach(async () => {
    await act(async () => { root?.unmount(); });
    container?.remove();
});

const labelText = () => Array.from(container.querySelectorAll('label')).map(l => l.textContent || '');

describe('a credential the host provided', () => {
    it('says so instead of a bare "Saved"', async () => {
        getProviderCatalog.mockResolvedValue({
            ...AUTH0,
            host_provided: { AUTH0_CLIENT_SECRET: true },
        });

        await mount(<InlineProviderSetup cap="identity" provider="auth0" />);

        const hinted = labelText().filter(t => t.includes('Saved'));
        expect(hinted.some(t => t.includes('provided by your host'))).toBe(true);
        // Only the field the backend named. The other two are the town's, and
        // saying otherwise would send a clerk to the wrong place for those too.
        expect(hinted.filter(t => t.includes('provided by your host'))).toHaveLength(1);
    });

    it('leaves the box editable, so the town can take the key back', async () => {
        getProviderCatalog.mockResolvedValue({
            ...AUTH0,
            host_provided: { AUTH0_CLIENT_SECRET: true },
        });

        await mount(<InlineProviderSetup cap="identity" provider="auth0" />);

        const inputs = Array.from(container.querySelectorAll('input'));
        expect(inputs).toHaveLength(3);
        expect(inputs.some(i => i.disabled || i.readOnly)).toBe(false);
    });

    it('reads as an ordinary saved credential when the backend says nothing', async () => {
        // Standalone installs have no host, and older backends do not send the
        // field at all. Either way the hint is the one it has always been.
        await mount(<InlineProviderSetup cap="identity" provider="auth0" />);

        const text = labelText().join(' | ');
        expect(text).toContain('Saved');
        expect(text).not.toContain('provided by your host');
    });
});
