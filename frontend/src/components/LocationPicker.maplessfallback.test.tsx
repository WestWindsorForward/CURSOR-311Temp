// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * A map that will not draw must not take the report form with it.
 *
 * This is the resident-facing half of the rate-limit requirement: a key that is
 * rejected, blocked, or capped is the town's problem, and the resident standing
 * next to the pothole should not be the one who cannot file. The picker used to
 * replace itself with a red box on any map failure -- no address field at all,
 * so there was nothing left to submit.
 *
 * Note what this does NOT cover, because Google gives us no way to: an
 * exhausted Maps JavaScript API quota does not throw. The map object is
 * returned intact and Google paints its own overlay inside the div, so the
 * failure tested here is the harder one (the SDK refusing to come up), and the
 * quota case degrades even more gently -- the address box below is untouched
 * either way, since it resolves through the backend rather than the SDK.
 */

const geocodeCalls: string[] = [];
let createMapBehaviour: () => any = () => { throw new Error('SDK unavailable'); };

vi.mock('../maps', async () => {
    const actual = await vi.importActual<any>('../maps');
    return {
        ...actual,
        hasMapCredential: () => true,
        createMap: vi.fn(async () => createMapBehaviour()),
        createGeocoder: vi.fn(async () => null),
    };
});

vi.mock('../services/api', () => {
    const api: any = {
        geocodeAddress: vi.fn(async (query: string) => {
            geocodeCalls.push(query);
            return { formatted_address: `${query}, Testville, NJ`, lat: 40.22, lng: -74.01 };
        }),
        reverseGeocode: vi.fn(async () => null),
    };
    return { default: api, api };
});

import LocationPicker from './LocationPicker';

let host: HTMLDivElement;
let root: Root;

const config = { provider: 'google', apiKey: 'k' } as any;

const flush = async () => {
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
};

beforeEach(() => {
    geocodeCalls.length = 0;
    createMapBehaviour = () => { throw new Error('SDK unavailable'); };
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
});

afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.clearAllMocks();
});

const render = async (onChange: (v: any) => void) => {
    await act(async () => {
        root.render(<LocationPicker config={config} onChange={onChange} />);
    });
    await flush();
};

describe('LocationPicker when the map provider will not load', () => {
    it('keeps the address input on screen instead of a dead error box', async () => {
        await render(() => {});

        const input = host.querySelector('input[aria-label="Location or address"]');
        expect(input).not.toBeNull();
        expect((input as HTMLInputElement).disabled).toBe(false);
        // And the failure is stated rather than hidden -- staff reading a
        // resident's screenshot need to know the map was missing.
        expect(host.textContent).toContain('You can still type the address above');
    });

    it('sends what the resident types to the form, since no pin can', async () => {
        const changes: any[] = [];
        await render(v => changes.push(v));

        const input = host.querySelector('input') as HTMLInputElement;
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value',
            )!.set!;
            setter.call(input, '12 Main St');
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });

        expect(changes[changes.length - 1]).toEqual({ address: '12 Main St', lat: null, lng: null });
    });

    it('still resolves addresses through the backend, which has its own fallback', async () => {
        await render(() => {});

        const input = host.querySelector('input') as HTMLInputElement;
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value',
            )!.set!;
            setter.call(input, '12 Main Street');
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await flush();

        // The backend geocoder was wired before the SDK was ever touched, so
        // the suggestion list survives the map that did not.
        expect(geocodeCalls).toContain('12 Main Street');
        expect(host.textContent).toContain('12 Main Street, Testville, NJ');
    });
});
