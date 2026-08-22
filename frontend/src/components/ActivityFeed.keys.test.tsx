// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ServiceRequest } from '../types';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The feed's list keys used to be built from `service_request_id` alone. That
 * id is not always there -- a manual intake can be saved before one is issued,
 * and a partially-hydrated row carries an empty string -- and every such
 * request then keyed to the same string. React answers a duplicate key by
 * rendering one child where two were asked for, so the clerk simply never sees
 * the second report: no error, no gap, just a shorter feed.
 *
 * So the assertion here is on what reaches the DOM, not on the key format: an
 * item per request, and React silent about it.
 */

/* framer-motion, reduced to plain elements. The feed's body lives inside an
 * `AnimatePresence`, and in jsdom no animation ever completes, so the subtree
 * it holds never settles into something assertable. */
vi.mock('framer-motion', async () => {
    const React = await import('react');
    const passthrough = (tag: string) => ({ children, ...props }: any) => {
        const {
            initial, animate, exit, transition, variants, whileHover, whileTap,
            whileInView, layout, layoutId, drag, onAnimationComplete, ...rest
        } = props;
        return React.createElement(tag, rest, children);
    };
    /* Cached per tag: a fresh component per access would hand React a new
     * element type every render and remount the subtree forever. */
    const cache = new Map<string, any>();
    const motion: any = new Proxy({}, {
        get: (_t, tag: string) => {
            if (!cache.has(tag)) cache.set(tag, passthrough(tag));
            return cache.get(tag);
        },
    });
    return {
        motion,
        AnimatePresence: ({ children }: any) => React.createElement(React.Fragment, null, children),
        useReducedMotion: () => true,
    };
});

let host: HTMLDivElement;
let root: Root;
let consoleError: ReturnType<typeof vi.spyOn>;

const minutesAgo = (m: number) => new Date(Date.now() - m * 60 * 1000).toISOString();

function makeRequest(over: Partial<ServiceRequest> & { id: number }): ServiceRequest {
    return {
        service_request_id: `SR-${over.id}`,
        service_code: 'POTHOLE',
        service_name: 'Pothole',
        description: '',
        status: 'open',
        priority: 3,
        address: null,
        lat: null,
        long: null,
        requested_datetime: minutesAgo(30),
        updated_datetime: null,
        source: 'web',
        flagged: false,
        assigned_department_id: null,
        assigned_to: null,
        closed_substatus: null,
        deleted_at: null,
        deleted_by: null,
        ...over,
    } as ServiceRequest;
}

async function mount(requests: ServiceRequest[], userDepartmentIds: number[] = []) {
    const ActivityFeed = (await import('./ActivityFeed')).default;
    await act(async () => {
        root.render(React.createElement(ActivityFeed as any, {
            isOpen: true,
            onClose: () => { },
            requests,
            userId: 'staff-1',   // an empty department list means all activity is relevant
            userDepartmentIds,
            onSelectRequest: () => { },
        }));
    });
}

/** The clerk-visible rows: the feed list's children, one button per entry.
 *  Scoped to the list so the header's close / "Mark all read" controls -- also
 *  buttons -- are not miscounted as activity. */
const items = () => [...(host.querySelector('div.divide-y')?.children ?? [])];

const titles = () => items().map(b => b.querySelector('p')?.textContent ?? '');

beforeEach(() => {
    localStorage.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => { });
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    consoleError.mockRestore();
});

describe('ActivityFeed keys', () => {
    it('renders every request when service_request_id is missing', async () => {
        await mount([
            makeRequest({ id: 1, service_name: 'Pothole' }),
            makeRequest({ id: 2, service_request_id: '', service_name: 'Streetlight out' }),
            makeRequest({ id: 3, service_request_id: '', service_name: 'Fallen branch' }),
            makeRequest({ id: 4, service_request_id: undefined as unknown as string, service_name: 'Graffiti' }),
        ]);

        expect(items().length).toBe(4);
        const rendered = titles();
        for (const name of ['Pothole', 'Streetlight out', 'Fallen branch', 'Graffiti']) {
            expect(rendered.some(t => t.includes(name))).toBe(true);
        }
    });

    it('emits no duplicate-key warning', async () => {
        await mount([
            makeRequest({ id: 5, service_request_id: '' }),
            makeRequest({ id: 6, service_request_id: '' }),
            makeRequest({ id: 7, service_request_id: '' }),
        ]);

        const complaints = consoleError.mock.calls
            .map(call => call.map(a => String(a)).join(' '))
            .filter(text => /same key|unique "key"/i.test(text));
        expect(complaints).toEqual([]);
        expect(items().length).toBe(3);
    });

    it('still separates items that share a request but not a type', async () => {
        /* One id-less request can produce two items -- an update and a
         * department nudge. They must survive as two rows, which is exactly
         * what a per-request key alone would not give us. */
        await mount([
            makeRequest({
                id: 8,
                service_request_id: '',
                service_name: 'Blocked drain',
                requested_datetime: minutesAgo(60 * 30),      // ~1.25 days old
                updated_datetime: minutesAgo(60),
                assigned_department_id: 4,
            }),
        ], [4]);

        expect(items().length).toBe(2);
        const rendered = titles().join(' | ');
        expect(rendered.includes('Updated: Blocked drain')).toBe(true);
        expect(rendered.includes('Needs attention: Blocked drain')).toBe(true);
    });
});
