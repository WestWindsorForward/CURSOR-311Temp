// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Every alert an admin can be paged about needs an "I know about this one".
 *
 * The connector cards had one. The proactive health checks -- disk, backups,
 * Redis, retention -- did not, so a town deliberately running near a disk
 * threshold, or one that has decided against off-site backups for now, got the
 * same mail every time the check crossed a line with no way to stop it short of
 * turning the whole scan off.
 *
 * The two halves that matter, and that these tests hold:
 *   - muting silences the *mail*, via the same endpoint the connector mute uses;
 *   - it does not silence the *screen*. A muted check keeps its status badge and
 *     stays in the list, greyed and labelled, because a dismiss button that
 *     removes the evidence is how a known problem becomes an unknown one.
 */

let muteCalls: Array<{ connector: string; days: number | undefined }> = [];
let muteResult: { muted_until: string | null } = { muted_until: null };
let muteFails = false;
let proactivePayload: any = null;
let connectorRows: any[] = [];

vi.mock('../services/api', () => {
    /* Proxy-based, like the other panel tests: a single method cannot be
     * reassigned afterwards, so the mutable behaviour lives in the module-level
     * flags above and is read at call time. */
    const handlers: Record<string, (...args: any[]) => Promise<any>> = {
        getHealthDashboard: async () => null,
        getConnectorHealth: async () => ({ connectors: connectorRows, needs_attention: [] }),
        getProactiveHealth: async () => proactivePayload,
        muteConnectorAlerts: async (connector: string, days?: number) => {
            muteCalls.push({ connector, days });
            if (muteFails) throw new Error('the server said no');
            return { connector, muted_until: muteResult.muted_until, muted_level: 'at_risk' };
        },
    };
    // muteHealthCheck delegates to muteConnectorAlerts in the real client; the
    // mock keeps that relationship so the test proves the `health:` key too.
    handlers.muteHealthCheck = async (key: string, days?: number) =>
        handlers.muteConnectorAlerts(`health:${key}`, days);

    const api: any = new Proxy({}, {
        get: (_t, prop: string) =>
            handlers[prop] ? vi.fn(handlers[prop]) : vi.fn().mockResolvedValue({}),
    });
    return { default: api, api };
});

vi.mock('framer-motion', async () => {
    const React = await import('react');
    const passthrough = (tag: string) => ({ children, ...props }: any) => {
        const {
            initial, animate, exit, transition, variants, whileHover, whileTap,
            whileInView, layout, layoutId, drag, onAnimationComplete, ...rest
        } = props;
        return React.createElement(tag, rest, children);
    };
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

vi.mock('./DialogProvider', () => ({
    useDialog: () => ({ confirm: async () => true, alert: async () => undefined }),
}));

const check = (over: Record<string, unknown> = {}) => ({
    key: 'disk', label: 'Disk space', status: 'warning', value: 84,
    message: 'Disk is 84% full.', action: 'Free some space.',
    muted: false, muted_until: null, ...over,
});

const probe = (over: Record<string, unknown> = {}) => ({
    connector: 'system:backups', provider: null, status: 'down',
    summary: 'Failing repeatedly (4 in a row)', last_success_at: null,
    last_error_at: null, last_error: null, consecutive_failures: 4,
    last_result: null, verifiable: true, total_successes: 0, total_failures: 4,
    alerts_muted_until: null, ...over,
});

const payload = (checks: any[]) => ({
    overall_status: 'warning',
    summary: { level: 'warning', label: 'Minor issue', detail: '' },
    checks,
    timestamp: '2026-08-11T00:00:00Z',
});

let host: HTMLDivElement; let root: Root;
beforeEach(() => {
    muteCalls = []; muteFails = false; muteResult = { muted_until: null };
    proactivePayload = payload([check()]);
    connectorRows = [];
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.clearAllMocks(); });

async function mount() {
    const { default: OperationsPanel } = await import('./OperationsPanel');
    await act(async () => { root.render(React.createElement(OperationsPanel as any)); });
}

const row = (key = 'disk') =>
    host.querySelector(`[data-testid="health-check-${key}"]`) as HTMLElement | null;
const muteButton = (key = 'disk') =>
    row(key)?.querySelector('button') as HTMLButtonElement | null;

describe('muting a proactive health check', () => {
    it('offers a mute button on every check that needs attention', async () => {
        await mount();
        expect(row()).not.toBeNull();
        expect(muteButton()?.textContent).toContain('Mute alerts');
        expect(muteButton()?.getAttribute('aria-label')).toBe('Mute alerts for Disk space');
    });

    it('mutes through the same endpoint the connector cards use', async () => {
        muteResult = { muted_until: '2026-08-18T00:00:00Z' };
        await mount();
        await act(async () => { muteButton()!.click(); });

        // The `health:` namespace is what routes this to the shared mute row,
        // and `days: undefined` is what takes the default week.
        expect(muteCalls).toEqual([{ connector: 'health:disk', days: undefined }]);
    });

    it('greys the row and says who is no longer being emailed', async () => {
        muteResult = { muted_until: '2026-08-18T00:00:00Z' };
        await mount();
        await act(async () => { muteButton()!.click(); });

        expect(row()!.getAttribute('data-muted')).toBe('true');
        expect(host.textContent).toContain('nobody is being emailed about this');
        expect(muteButton()?.textContent).toContain('Unmute');
    });

    it('keeps the check visible and still failing', async () => {
        // The whole point. A muted check that vanished, or that went green,
        // would turn a problem somebody acknowledged into one nobody can see.
        proactivePayload = payload([check({ muted: true, muted_until: '2026-08-18T00:00:00Z' })]);
        await mount();

        expect(row()).not.toBeNull();
        expect(row()!.textContent).toContain('warning');
        expect(row()!.textContent).toContain('Disk is 84% full.');
        expect(row()!.textContent).toContain('It is still not passing.');
    });

    it('unmutes with days: 0', async () => {
        proactivePayload = payload([check({ muted: true, muted_until: '2026-08-18T00:00:00Z' })]);
        await mount();
        expect(muteButton()?.textContent).toContain('Unmute');

        await act(async () => { muteButton()!.click(); });
        expect(muteCalls).toEqual([{ connector: 'health:disk', days: 0 }]);
        expect(row()!.getAttribute('data-muted')).toBe('false');
    });

    it('does not claim to have muted anything when the save failed', async () => {
        // An admin who believes an alarm is silenced, and is then paged by it,
        // has been told something false by the UI -- worse than no button.
        muteFails = true;
        await mount();
        await act(async () => { muteButton()!.click(); });

        expect(row()!.getAttribute('data-muted')).toBe('false');
        expect(muteButton()?.textContent).toContain('Mute alerts');
        expect(host.textContent).toContain('Alerts were not paused');
    });

    it('scopes a failure message to the row that failed', async () => {
        // Two cards read the same error state. Rendering it under both would
        // accuse a probe of a failure that belongs to a disk check.
        muteFails = true;
        connectorRows = [probe()];
        await mount();
        await act(async () => { muteButton()!.click(); });

        const alerts = host.querySelectorAll('[role="alert"]');
        expect(alerts.length).toBe(1);
    });

    it('offers the button per check, not once for the card', async () => {
        proactivePayload = payload([
            check(),
            check({ key: 'backup', label: 'Backup freshness', status: 'critical', value: null }),
        ]);
        await mount();

        expect(row('backup')).not.toBeNull();
        await act(async () => { muteButton('backup')!.click(); });
        expect(muteCalls).toEqual([{ connector: 'health:backup', days: undefined }]);
    });
});

/* The gap that made this worth doing at all: the infrastructure probes ride
 * the connector health table, so they were already emailing admins through the
 * connector digest -- and they were rendered on no page in the app. An alert
 * with no card is an alert with no mute button, which is how a town ends up
 * with a daily email it has no way to stop. */
describe('muting an infrastructure probe', () => {
    const probeRow = (key = 'backups') =>
        host.querySelector(`[data-testid="system-probe-${key}"]`) as HTMLElement | null;
    const probeButton = (key = 'backups') =>
        probeRow(key)?.querySelector('button') as HTMLButtonElement | null;

    it('shows a failing probe at all, with a mute button', async () => {
        connectorRows = [probe()];
        await mount();

        expect(probeRow()).not.toBeNull();
        expect(probeRow()!.textContent).toContain('Failing repeatedly');
        expect(probeButton()?.textContent).toContain('Mute alerts');
    });

    it('mutes it under its own connector name', async () => {
        muteResult = { muted_until: '2026-08-19T00:00:00Z' };
        connectorRows = [probe()];
        await mount();
        await act(async () => { probeButton()!.click(); });

        expect(muteCalls).toEqual([{ connector: 'system:backups', days: undefined }]);
        expect(probeRow()!.getAttribute('data-muted')).toBe('true');
    });

    it('keeps a muted probe on screen, still down', async () => {
        connectorRows = [probe({ alerts_muted_until: '2026-08-19T00:00:00Z' })];
        await mount();

        expect(probeRow()!.textContent).toContain('down');
        expect(probeRow()!.textContent).toContain('It is still not working.');
        expect(probeButton()?.textContent).toContain('Unmute');
    });

    it('unmutes with days: 0', async () => {
        connectorRows = [probe({ alerts_muted_until: '2026-08-19T00:00:00Z' })];
        await mount();
        await act(async () => { probeButton()!.click(); });

        expect(muteCalls).toEqual([{ connector: 'system:backups', days: 0 }]);
    });

    it('stays quiet about probes that are working and unmuted', async () => {
        // A list of five green lines is noise of its own.
        connectorRows = [probe({ connector: 'system:cache', status: 'working' })];
        await mount();
        expect(host.textContent).not.toContain('Infrastructure probes');
    });

    it('does not list real integrations here', async () => {
        // Those have their own cards on the Setup page, with their own mute.
        connectorRows = [probe({ connector: 'sms', status: 'down' })];
        await mount();
        expect(host.textContent).not.toContain('Infrastructure probes');
    });
});
