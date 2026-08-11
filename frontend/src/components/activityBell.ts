import { ServiceRequest } from '../types';

/**
 * What the bell is counting, and why it is now coloured.
 *
 * The badge existed but the bell did not change: a grey bell with a small red
 * dot beside it, on a dark sidebar, next to several other grey icons. The
 * thing a clerk is meant to notice was rendered in the same colour as
 * everything they are meant to ignore.
 *
 * The count had a gap too, and it was the wrong way round. It required
 * `assigned_department_id` to be one of yours -- so a report that had just
 * arrived and had not been routed to any department yet counted for nobody.
 * The reports nobody has picked up are exactly the ones worth a bell.
 *
 * And a second gap, found live: most towns auto-route each service to a
 * department (service_definitions.assigned_department_id), while their staff
 * belong to no department at all -- the demo site is one admin with zero
 * memberships. Every new report arrived pre-routed, the admin's department
 * list was empty, and the bell stayed grey forever. The request list is
 * already scoped server-side to what this user may see (admins: everything;
 * staff: their departments + unrouted + assigned-to-them), so a user with no
 * memberships must treat everything the server sent as theirs -- the same
 * `noDeptScope` convention ActivityFeed already uses.
 */

/** Anything older than this is not news; it is the backlog. */
export const NEW_FOR_MS = 24 * 60 * 60 * 1000;

export interface UnreadInput {
    requests: ServiceRequest[];
    /** Ids already seen, as stored under `activityFeedRead`. */
    readIds: Set<string>;
    /** Departments the signed-in user belongs to. */
    departmentIds: number[];
    /** Signed-in username, so a report assigned to you by name always counts. */
    username?: string | null;
    now: number;
}

/** The key a request is marked read under. One definition, used by both sides. */
export function readKey(request: Pick<ServiceRequest, 'service_request_id'>): string {
    return `new-${request.service_request_id}`;
}

/**
 * How many recent reports this user has not looked at.
 *
 * Yours means: routed to a department you are in, assigned to you by name,
 * **or** not routed anywhere yet -- an unrouted report is everybody's until
 * somebody claims it, and showing it to nobody is how it sits for a day.
 * A user with no department memberships owns everything the server sent:
 * the list endpoint already scoped it to what they may see, and re-filtering
 * by an empty membership list is how the admin's bell never lit.
 */
export function unreadCount({ requests, readIds, departmentIds, username, now }: UnreadInput): number {
    const mine = new Set(departmentIds);
    let count = 0;

    for (const request of requests) {
        if (!request.requested_datetime) continue;

        const submitted = new Date(request.requested_datetime).getTime();
        // An unparseable date must not become "0", which is 1970 and would
        // read as ancient -- or worse, arithmetic on NaN, which is never < the
        // window and would silently drop the report.
        if (!Number.isFinite(submitted)) continue;
        if (now - submitted >= NEW_FOR_MS) continue;

        // Closed reports are not news. Somebody already dealt with it.
        if (request.status === 'closed') continue;

        const dept = request.assigned_department_id;
        const isMine =
            dept == null ||
            mine.has(dept) ||
            mine.size === 0 ||
            (username != null && request.assigned_to === username);
        if (!isMine) continue;

        if (!readIds.has(readKey(request))) count++;
    }

    return count;
}

/**
 * Mark a single feed key seen and persist it under `activityFeedRead`.
 *
 * Used when a staff user opens a request's detail view: that request's own
 * "new" notification should stop counting toward the bell, the same way
 * clicking it in the Activity Feed already does. Returns whether the key was
 * actually newly marked, so a caller can skip re-rendering when there was
 * nothing unread to clear -- opening a request with no unread notification
 * is a no-op.
 *
 * Reads the current stored set immediately before writing rather than
 * trusting a snapshot taken earlier (e.g. at a component's mount): this and
 * the Activity Feed's own markAsRead/markAllAsRead are independent writers
 * of the same key, and a write built from a stale snapshot would silently
 * undo whichever of the two ran first. Read-then-write here, at the point
 * closest to the actual write, is what keeps both writers safe without
 * making either the single owner of the set.
 *
 * The set has no eviction: every request ever opened leaves a `new-<id>` key
 * behind permanently, even long-closed ones. Bounded by realistic use --
 * unreadCount and the feed itself both use it only in relation to requests
 * still inside a several-day window -- but if that ever mattered for storage
 * size, the fix belongs at write time here (or in the feed), not in the
 * readers.
 */
export function markKeyRead(key: string): boolean {
    const readIds = readIdsFromStorage(localStorage.getItem('activityFeedRead'));
    if (readIds.has(key)) return false;
    readIds.add(key);
    try {
        localStorage.setItem('activityFeedRead', JSON.stringify([...readIds]));
    } catch {
        // Safari private mode, or a full storage quota: this must not throw
        // out of a request-detail open. Report "nothing changed" so the
        // caller skips the re-render its normal success path would trigger.
        return false;
    }
    return true;
}

/** Read the seen-ids list without letting a corrupted entry break the header. */
export function readIdsFromStorage(raw: string | null): Set<string> {
    try {
        const parsed = JSON.parse(raw || '[]');
        return new Set(Array.isArray(parsed) ? parsed.filter(v => typeof v === 'string') : []);
    } catch {
        // Somebody's localStorage has a half-written value in it. That is not a
        // reason to throw inside a render and blank the whole dashboard.
        return new Set();
    }
}

/** How the bell itself should look. Colour, not just a dot beside it. */
export function bellAppearance(count: number): { icon: string; label: string } {
    if (count <= 0) {
        return { icon: 'text-white/60', label: 'Open activity feed' };
    }
    return {
        icon: 'text-amber-300',
        label: `Open activity feed — ${count} new ${count === 1 ? 'report' : 'reports'}`,
    };
}
