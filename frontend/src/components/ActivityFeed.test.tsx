// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import ActivityFeed from './ActivityFeed';
import { markKeyRead, readIdsFromStorage } from './activityBell';
import { ServiceRequest } from '../types';

/**
 * ActivityFeed is mounted for the whole life of the dashboard (it just
 * returns null while closed), so its `readItems` state is a snapshot of
 * `activityFeedRead` taken once at mount. StaffDashboard's open-detail
 * handler (markKeyRead, in activityBell.ts) writes to that same storage key
 * independently, any time after mount, whenever a staff user opens a
 * request from a list.
 *
 * Before this fix, markAsRead/markAllAsRead rebuilt the *entire* stored set
 * from that stale snapshot: open eight requests from the list (their keys
 * land in storage via markKeyRead), then click one item in the feed (or hit
 * "Mark all read") -- and the feed's write clobbers storage back down to its
 * own mount-time view, undoing every markKeyRead that happened since. All
 * seven other requests silently go unread again and the bell re-lights.
 *
 * The fix has both writers read-then-write at the point of the write
 * instead of trusting an old snapshot. This test drives exactly that
 * interleaving and asserts the earlier key survives.
 */

function req(over: Partial<ServiceRequest>): ServiceRequest {
    return {
        service_request_id: 'REQ-2',
        service_code: 'pothole',
        service_name: 'Pothole',
        status: 'open',
        description: 'Deep pothole near the crosswalk',
        requested_datetime: new Date().toISOString(),
        assigned_department_id: null,
        assigned_to: null,
        ...over,
    } as ServiceRequest;
}

beforeEach(() => {
    localStorage.clear();
});
afterEach(cleanup);

describe('ActivityFeed markAsRead / markAllAsRead vs. a concurrent markKeyRead writer', () => {
    it('"Mark all read" does not roll back a key markKeyRead already committed', () => {
        // A staff user opened REQ-1's detail from the list before ever
        // opening the Activity Feed panel -- markKeyRead is the only writer
        // that has touched storage so far.
        expect(markKeyRead('new-REQ-1')).toBe(true);

        const other = req({ service_request_id: 'REQ-2' });
        render(
            <ActivityFeed
                isOpen={true}
                onClose={() => {}}
                requests={[other]}
                userId="pat"
                userDepartmentIds={[]}
                onSelectRequest={() => {}}
            />
        );

        fireEvent.click(screen.getByText('Mark all read'));

        const stored = readIdsFromStorage(localStorage.getItem('activityFeedRead'));
        expect(stored.has('new-REQ-1')).toBe(true); // survives
        expect(stored.has('new-REQ-2')).toBe(true); // and the feed's own write still lands
    });

    it('clicking a single feed item does not roll back an unrelated markKeyRead key', () => {
        expect(markKeyRead('new-REQ-1')).toBe(true);

        const other = req({ service_request_id: 'REQ-2', service_name: 'Streetlight Out' });
        render(
            <ActivityFeed
                isOpen={true}
                onClose={() => {}}
                requests={[other]}
                userId="pat"
                userDepartmentIds={[]}
                onSelectRequest={() => {}}
            />
        );

        fireEvent.click(screen.getByText(/New: Streetlight Out/));

        const stored = readIdsFromStorage(localStorage.getItem('activityFeedRead'));
        expect(stored.has('new-REQ-1')).toBe(true);
        expect(stored.has('new-REQ-2')).toBe(true);
    });
});
