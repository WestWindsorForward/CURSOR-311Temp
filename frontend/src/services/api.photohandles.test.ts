import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { api } from './api';

/**
 * What happens when a photo handle dies between picking and submitting.
 *
 * The portal screens a photo the moment it is chosen and holds a short handle
 * until Submit, which is what got the Google Vision round trip off the Submit
 * button. Handles expire after an hour, and people leave forms half-finished
 * for longer than that.
 *
 * The server cannot fix this on its own: it deliberately never kept the
 * unredacted original, so there is nothing on that side to fall back to. Only
 * the browser still has the bytes. So the server answers 409 naming the dead
 * handles and the client resends those photos inline, taking the
 * screen-at-submit path that existed before any of this.
 *
 * The failure mode this exists to prevent is the quiet one: a resident presses
 * Submit, the report is filed, and their photo is simply not on it. They have
 * no way to notice, and the town gets a report about a hazard with no picture
 * of the hazard.
 */

const originalFetch = globalThis.fetch;

const HANDLE = 'screened:tok-1';
const ORIGINAL = 'data:image/jpeg;base64,ORIGINALBYTES';

const created = { service_request_id: 'REQ-1' };

function staleThenOk() {
    let call = 0;
    return vi.fn().mockImplementation(async () => {
        call += 1;
        if (call === 1) {
            return {
                ok: false,
                status: 409,
                json: async () => ({
                    detail: {
                        error: 'stale_photo_handles',
                        handles: [HANDLE],
                        message: 'Your photos took too long to attach.',
                    },
                }),
            } as unknown as Response;
        }
        return { ok: true, status: 201, json: async () => created } as unknown as Response;
    });
}

function bodyOf(fetchMock: any, call: number) {
    return JSON.parse(fetchMock.mock.calls[call][1].body);
}

describe('a photo handle that expired while the form was open', () => {
    beforeEach(() => { api.setToken(null); });
    afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks(); });

    it('resends the photo inline instead of losing it', async () => {
        const fetchMock = staleThenOk();
        globalThis.fetch = fetchMock as any;

        const result = await api.createRequest(
            { service_code: 'POTHOLE', media_urls: [HANDLE] } as any,
            { [HANDLE]: ORIGINAL },
        );

        expect(result).toEqual(created);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(bodyOf(fetchMock, 0).media_urls).toEqual([HANDLE]);
        expect(bodyOf(fetchMock, 1).media_urls).toEqual([ORIGINAL]);
    });

    it('leaves the photos that are still good alone', async () => {
        // Only the dead handle is swapped. A live handle must not be turned
        // back into base64 -- that would re-run the Vision call the whole
        // design exists to avoid, on the Submit button, for no reason.
        const live = 'screened:tok-2';
        const fetchMock = staleThenOk();
        globalThis.fetch = fetchMock as any;

        await api.createRequest(
            { service_code: 'POTHOLE', media_urls: [HANDLE, live] } as any,
            { [HANDLE]: ORIGINAL, [live]: 'data:image/jpeg;base64,OTHER' },
        );

        expect(bodyOf(fetchMock, 1).media_urls).toEqual([ORIGINAL, live]);
    });

    it('does not retry forever', async () => {
        // A second 409 is a real fault, not a stale handle, and a client that
        // keeps resending several megabytes at a struggling server makes it
        // worse.
        const fetchMock = vi.fn().mockResolvedValue({
            ok: false,
            status: 409,
            json: async () => ({ detail: { error: 'stale_photo_handles', handles: [HANDLE] } }),
        } as unknown as Response);
        globalThis.fetch = fetchMock as any;

        await expect(api.createRequest(
            { service_code: 'POTHOLE', media_urls: [HANDLE] } as any,
            { [HANDLE]: ORIGINAL },
        )).rejects.toThrow();
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('leaves every other failure to the caller', async () => {
        // Only this one 409 shape is recoverable here. A jurisdiction redirect,
        // a blocked photo, a validation error -- all of those still surface.
        const fetchMock = vi.fn().mockResolvedValue({
            ok: false,
            status: 400,
            json: async () => ({ detail: 'One of your photos appears to contain explicit content' }),
        } as unknown as Response);
        globalThis.fetch = fetchMock as any;

        await expect(api.createRequest({ service_code: 'POTHOLE' } as any, {}))
            .rejects.toThrow(/explicit content/);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('keeps a structured 409 body readable instead of stringifying it', async () => {
        // This used to become "[object Object]" in a resident-facing error,
        // because the error path assumed `detail` was always a string. The
        // jurisdiction-redirect 409 has the same shape.
        globalThis.fetch = vi.fn().mockResolvedValue({
            ok: false,
            status: 409,
            json: async () => ({
                detail: { error: 'redirected', jurisdiction: 'County', message: 'Call the county.' },
            }),
        } as unknown as Response) as any;

        const err = await api.createRequest({ service_code: 'POTHOLE' } as any)
            .then(() => null, (e) => e);
        expect(err.message).toBe('Call the county.');
        expect(err.detail.jurisdiction).toBe('County');
        expect(err.status).toBe(409);
    });
});
