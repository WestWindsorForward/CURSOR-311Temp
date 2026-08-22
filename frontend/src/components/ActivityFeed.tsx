import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Bell, MessageSquare, UserPlus, AlertCircle, Clock, ChevronRight, Building2 } from 'lucide-react';
import { ServiceRequest } from '../types';
import { readIdsFromStorage } from './activityBell';

interface ActivityFeedProps {
    isOpen: boolean;
    onClose: () => void;
    requests: ServiceRequest[];
    userId: string;
    userDepartmentIds: number[];
    onSelectRequest: (request: ServiceRequest) => void;
}

interface FeedItem {
    id: string;
    type: 'new_request' | 'assigned_to_me' | 'assigned_to_dept' | 'status_change' | 'new_comment';
    title: string;
    description: string;
    timestamp: Date;
    request: ServiceRequest;
    isNew: boolean;
}

/**
 * A stable, unique handle for one request's feed items.
 *
 * `service_request_id` is the natural choice, but it can arrive empty -- an
 * intake that never got one, or a partially-hydrated row -- and every such
 * request then produced the same React key (`new-`, `dept-`), so React
 * collapsed them and dropped entries from the rendered feed. Fall back to the
 * numeric primary key, and to the list position only if even that is missing.
 */
function requestKey(request: ServiceRequest, index: number): string {
    if (request.service_request_id) return String(request.service_request_id);
    if (request.id !== undefined && request.id !== null) return `id-${request.id}`;
    return `idx-${index}`;
}

export default function ActivityFeed({
    isOpen,
    onClose,
    requests,
    userId,
    userDepartmentIds,
    onSelectRequest
}: ActivityFeedProps) {
    const [readItems, setReadItems] = useState<Set<string>>(() => {
        const stored = localStorage.getItem('activityFeedRead');
        return stored ? new Set(JSON.parse(stored)) : new Set();
    });

    // Generate feed items from requests
    const feedItems = useMemo<FeedItem[]>(() => {
        const items: FeedItem[] = [];
        const now = Date.now();
        const twentyFourHours = 24 * 60 * 60 * 1000;
        const sevenDays = 7 * 24 * 60 * 60 * 1000;

        requests.forEach((request, index) => {
            const key = requestKey(request, index);
            const requestTime = new Date(request.requested_datetime).getTime();
            const requestAge = now - requestTime;

            // Skip old requests
            if (requestAge > sevenDays) return;

            // Relevance mirrors the notification logic: a request matters to you
            // if it's assigned to you or routed to your department. Staff with no
            // departments configured (e.g. admins) see all activity — the same
            // convention the dashboard uses, and safe because the request list is
            // already department-scoped server-side.
            const mine = request.assigned_to === userId;
            const deptMatch = !!request.assigned_department_id && userDepartmentIds.includes(request.assigned_department_id);
            const noDeptScope = userDepartmentIds.length === 0;
            if (!mine && !deptMatch && !noDeptScope) return;

            const updatedTime = request.updated_datetime ? new Date(request.updated_datetime).getTime() : null;
            const wasUpdated = updatedTime !== null && Math.abs(updatedTime - requestTime) > 60 * 1000;

            // New request attached to you or your department (< 24 hours)
            if (requestAge < twentyFourHours) {
                items.push({
                    id: `new-${key}`,
                    type: mine ? 'assigned_to_me' : 'new_request',
                    title: mine ? `New & assigned to you: ${request.service_name}` : `New: ${request.service_name}`,
                    description: (request.description?.substring(0, 80) + (request.description && request.description.length > 80 ? '...' : '')) || `Request #${request.service_request_id}`,
                    timestamp: new Date(request.requested_datetime),
                    request,
                    isNew: !readItems.has(`new-${key}`)
                });
            }
            // Otherwise, a recent status/activity update on a request relevant to you
            else if (wasUpdated && (now - (updatedTime as number)) < twentyFourHours * 2) {
                items.push({
                    id: `upd-${key}-${updatedTime}`,
                    type: 'status_change',
                    title: `Updated: ${request.service_name}`,
                    description: `Status: ${String(request.status).replace(/_/g, ' ')}${mine ? ' · assigned to you' : ''}`,
                    timestamp: new Date(request.updated_datetime as string),
                    request,
                    isNew: !readItems.has(`upd-${key}-${updatedTime}`)
                });
            }

            // Unassigned request in your department that still needs an owner
            if (deptMatch && !request.assigned_to &&
                requestAge >= twentyFourHours && requestAge < twentyFourHours * 3) {
                items.push({
                    id: `dept-${key}`,
                    type: 'assigned_to_dept',
                    title: `Needs attention: ${request.service_name}`,
                    description: 'Assigned to your department but no individual owner',
                    timestamp: new Date(request.requested_datetime),
                    request,
                    isNew: !readItems.has(`dept-${key}`)
                });
            }
        });

        // Sort by timestamp, newest first
        items.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());

        return items.slice(0, 50); // Limit to 50 items
    }, [requests, userId, userDepartmentIds, readItems]);

    const unreadCount = feedItems.filter(item => item.isNew).length;

    // Both of these are writers of the same `activityFeedRead` set that
    // StaffDashboard's open-detail handler (markKeyRead, in activityBell.ts)
    // also writes to -- and this component is mounted for the dashboard's
    // whole lifetime, so `readItems` is a snapshot taken once at mount, not
    // at each write. Building the next value from `readItems` here would
    // silently discard whatever markKeyRead (or the other writer) added to
    // storage since then: open eight requests from the list, then click one
    // feed item, and the rewrite from the stale in-memory set would put all
    // seven other requests back in the unread count. Re-reading storage
    // immediately before each write, and merging into that instead of into
    // the stale `readItems`, keeps both writers safe.
    const markAsRead = (itemId: string) => {
        const newRead = readIdsFromStorage(localStorage.getItem('activityFeedRead'));
        newRead.add(itemId);
        setReadItems(newRead);
        localStorage.setItem('activityFeedRead', JSON.stringify([...newRead]));
    };

    const markAllAsRead = () => {
        const newRead = readIdsFromStorage(localStorage.getItem('activityFeedRead'));
        feedItems.forEach(item => newRead.add(item.id));
        setReadItems(newRead);
        localStorage.setItem('activityFeedRead', JSON.stringify([...newRead]));
    };

    const handleItemClick = (item: FeedItem) => {
        markAsRead(item.id);
        onSelectRequest(item.request);
        onClose();
    };

    const getItemIcon = (type: FeedItem['type']) => {
        switch (type) {
            case 'new_request':
                return <AlertCircle className="w-4 h-4 text-emerald-400" />;
            case 'assigned_to_me':
                return <UserPlus className="w-4 h-4 text-primary-400" />;
            case 'assigned_to_dept':
                return <Building2 className="w-4 h-4 text-purple-400" />;
            case 'status_change':
                return <Clock className="w-4 h-4 text-amber-400" />;
            case 'new_comment':
                return <MessageSquare className="w-4 h-4 text-blue-400" />;
        }
    };

    const formatTimeAgo = (date: Date) => {
        const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
        if (seconds < 60) return 'Just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        return `${Math.floor(seconds / 86400)}d ago`;
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50"
                onClick={onClose}
            >
                <motion.div
                    initial={{ x: -320, opacity: 0 }}
                    animate={{ x: 0, opacity: 1 }}
                    exit={{ x: -320, opacity: 0 }}
                    transition={{ type: 'spring', damping: 25, stiffness: 300 }}
                    onClick={(e) => e.stopPropagation()}
                    className="absolute left-0 top-0 bottom-0 w-full max-w-sm bg-slate-900 border-r border-white/10 shadow-2xl flex flex-col"
                >
                    {/* Header */}
                    <div className="p-4 border-b border-white/10 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-full bg-primary-500/20 flex items-center justify-center">
                                <Bell className="w-5 h-5 text-primary-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-white">Activity Feed</h2>
                                <p className="text-sm text-white/50">{unreadCount} unread</p>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            {unreadCount > 0 && (
                                <button
                                    onClick={markAllAsRead}
                                    className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
                                >
                                    Mark all read
                                </button>
                            )}
                            <button
                                onClick={onClose}
                                className="p-2 hover:bg-white/10 rounded-lg transition-colors"
                                aria-label="Close activity feed"
                            >
                                <X className="w-5 h-5 text-white/60" aria-hidden="true" />
                            </button>
                        </div>
                    </div>

                    {/* Feed Items */}
                    <div className="flex-1 overflow-y-auto">
                        {feedItems.length === 0 ? (
                            <div className="p-8 text-center">
                                <Bell className="w-12 h-12 text-white/20 mx-auto mb-4" />
                                <p className="text-white/50">No recent activity</p>
                                <p className="text-sm text-white/30 mt-1">New requests and updates will appear here</p>
                            </div>
                        ) : (
                            <div className="divide-y divide-white/5">
                                {feedItems.map((item) => (
                                    <button
                                        key={item.id}
                                        onClick={() => handleItemClick(item)}
                                        className={`w-full p-4 text-left hover:bg-white/5 transition-colors flex items-start gap-3 ${item.isNew ? 'bg-primary-500/5' : ''
                                            }`}
                                    >
                                        <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${item.type === 'new_request' ? 'bg-emerald-500/20' :
                                            item.type === 'assigned_to_me' ? 'bg-primary-500/20' :
                                                item.type === 'assigned_to_dept' ? 'bg-purple-500/20' :
                                                    item.type === 'new_comment' ? 'bg-blue-500/20' :
                                                        'bg-amber-500/20'
                                            }`}>
                                            {getItemIcon(item.type)}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <p className={`text-sm font-medium truncate ${item.isNew ? 'text-white' : 'text-white/70'}`}>
                                                    {item.title}
                                                </p>
                                                {item.isNew && (
                                                    <span className="w-2 h-2 rounded-full bg-primary-400 flex-shrink-0" />
                                                )}
                                            </div>
                                            <p className="text-xs text-white/40 truncate mt-0.5">{item.description}</p>
                                            <p className="text-xs text-white/30 mt-1">{formatTimeAgo(item.timestamp)}</p>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-white/20 flex-shrink-0 mt-1" />
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}

// Export the unread count hook for use in the bell icon
export function useActivityFeedCount(
    requests: ServiceRequest[],
    userId: string,
    userDepartmentIds: number[]
): number {
    const [readItems] = useState<Set<string>>(() => {
        const stored = localStorage.getItem('activityFeedRead');
        return stored ? new Set(JSON.parse(stored)) : new Set();
    });

    return useMemo(() => {
        let count = 0;
        const now = Date.now();
        const twentyFourHours = 24 * 60 * 60 * 1000;

        requests.forEach((request, index) => {
            const key = requestKey(request, index);
            const requestTime = new Date(request.requested_datetime).getTime();
            const requestAge = now - requestTime;
            if (requestAge > twentyFourHours * 2) return;

            const mine = request.assigned_to === userId;
            const deptMatch = !!request.assigned_department_id && userDepartmentIds.includes(request.assigned_department_id);
            const noDeptScope = userDepartmentIds.length === 0;
            if (!mine && !deptMatch && !noDeptScope) return;

            const updatedTime = request.updated_datetime ? new Date(request.updated_datetime).getTime() : null;
            const wasUpdated = updatedTime !== null && Math.abs(updatedTime - requestTime) > 60 * 1000;

            // New request attached to you or your department
            if (requestAge < twentyFourHours) {
                if (!readItems.has(`new-${key}`)) count++;
            }
            // Recent status/activity update on a relevant request
            else if (wasUpdated && (now - (updatedTime as number)) < twentyFourHours * 2) {
                if (!readItems.has(`upd-${key}-${updatedTime}`)) count++;
            }
        });

        return count;
    }, [requests, userId, userDepartmentIds, readItems]);
}
