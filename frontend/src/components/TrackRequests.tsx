import { useState, useEffect, useMemo } from 'react';
import { motion } from 'framer-motion';
import {
    MapPin,
    Clock,
    CheckCircle,
    AlertCircle,
    Search,
    MessageSquare,
    Send,
    Image,
    Calendar,
    ArrowLeft,
    Share2,
    Copy,
    Check,
    ExternalLink,
    User,
    X,
    Star,
    Users,
    UserCircle,
} from 'lucide-react';
import { Card, Input, Button, Textarea } from './ui';
import { api } from '../services/api';
import { PublicServiceRequest, RequestComment, AuditLogEntry } from '../types';
import { TranslatedContent } from './TranslatedContent';
import { CommentCard, CommentEmptyState, CommentSkeleton } from './commentUI';
import RequestDetailMap from './RequestDetailMap';
import { RawMapsConfig, mapProviderReady, resolveMapProviderConfig } from '../maps';

type StatusFilter = 'all' | 'open' | 'in_progress' | 'closed';

interface TrackRequestsProps {
    initialRequestId?: string;
    selectedRequestId?: string | null;  // Parent-controlled: sync with hash (null = show list, string = show request)
    onRequestSelect?: (requestId: string | null) => void;  // Callback for when a request is selected/deselected
}

const STATUS_COLORS: Record<string, { bg: string; text: string; border: string; label: string; icon: React.ReactNode }> = {
    open: {
        bg: 'bg-gradient-to-r from-amber-500/20 to-yellow-500/20',
        text: 'text-amber-200',  // Was 300, now 200 for better contrast
        border: 'border-amber-500/30',
        label: "Open",
        icon: <AlertCircle className="w-4 h-4" />
    },
    in_progress: {
        bg: 'bg-gradient-to-r from-blue-500/20 to-cyan-500/20',
        text: 'text-blue-200',  // Was 300, now 200 for better contrast
        border: 'border-blue-500/30',
        label: "In Progress",
        icon: <Clock className="w-4 h-4" />
    },
    closed: {
        bg: 'bg-gradient-to-r from-emerald-500/20 to-green-500/20',
        text: 'text-emerald-200',  // Was 300, now 200 for better contrast
        border: 'border-emerald-500/30',
        label: "Resolved",
        icon: <CheckCircle className="w-4 h-4" />
    },
};

export default function TrackRequests({ initialRequestId, selectedRequestId, onRequestSelect }: TrackRequestsProps) {
    const statusColors = STATUS_COLORS;
    const [requests, setRequests] = useState<PublicServiceRequest[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
    const [searchQuery, setSearchQuery] = useState('');
    const [selectedRequest, setSelectedRequest] = useState<PublicServiceRequest | null>(null);
    const [comments, setComments] = useState<RequestComment[]>([]);
    const [newComment, setNewComment] = useState('');
    const [isSubmittingComment, setIsSubmittingComment] = useState(false);
    // Why a post failed, in the resident's words. The moderation rejection is
    // written to be read by the person who typed the comment, and until now it
    // went to console.error -- so a rejected comment, a broken endpoint and a
    // dropped network request all looked the same from the outside: the button
    // stops spinning and nothing appears.
    const [commentError, setCommentError] = useState<string | null>(null);
    const [isLoadingComments, setIsLoadingComments] = useState(false);
    const [copied, setCopied] = useState(false);
    const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
    const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([]);
    // The whole payload: this used to keep only the Google key and render a
    // hardcoded Google Maps Embed iframe, so a town on any other provider got no
    // map here at all.
    const [mapsRaw, setMapsRaw] = useState<RawMapsConfig | null>(null);
    const mapConfig = useMemo(() => resolveMapProviderConfig(mapsRaw), [mapsRaw]);

    // Read "my requests" IDs from localStorage
    const myRequestIds = useMemo<Set<string>>(() => {
        try {
            const stored = JSON.parse(localStorage.getItem('my_requests') || '[]');
            return new Set(Array.isArray(stored) ? stored : []);
        } catch { return new Set(); }
    }, [requests]); // re-read when requests reload (after submission)

    // Load the town's map configuration on mount
    useEffect(() => {
        api.getMapsConfig().then(setMapsRaw).catch(() => { });
    }, []);

    useEffect(() => {
        loadRequests();
    }, []);

    /* Auto-load the report a tracking link points at.
     *
     * Falls back to fetching it directly when it is not in the loaded list. A
     * link is shared by the person who filed the report and by staff, and it
     * has to work for an unlisted report -- which by definition is not in the
     * public list -- and for one filed on a different device, where
     * localStorage knows nothing about it. Previously either case left the page
     * showing the list with no indication that the link had failed. */
    useEffect(() => {
        if (!initialRequestId) return;
        const known = requests.find(r => r.service_request_id === initialRequestId);
        if (known) {
            setSelectedRequest(known);
            return;
        }
        if (isLoading) return;  // the list may still bring it in
        let cancelled = false;
        api.getPublicRequestDetail(initialRequestId)
            .then(r => {
                if (cancelled || !r) return;
                setRequests(prev => prev.some(p => p.service_request_id === r.service_request_id)
                    ? prev : [r, ...prev]);
                setSelectedRequest(r);
            })
            .catch(() => { /* genuinely not found, or deleted */ });
        return () => { cancelled = true; };
    }, [initialRequestId, requests, isLoading]);

    useEffect(() => {
        if (selectedRequest) {
            loadComments(selectedRequest.service_request_id);
            loadAuditLog(selectedRequest.service_request_id);
        }
    }, [selectedRequest]);

    // Sync internal state with parent-controlled selectedRequestId (for back/forward navigation)
    useEffect(() => {
        if (selectedRequestId === null) {
            // Parent says to clear selection (user navigated back)
            setSelectedRequest(null);
        } else if (selectedRequestId && requests.length > 0) {
            // Parent says to select a specific request
            const request = requests.find(r => r.service_request_id === selectedRequestId);
            if (request && (!selectedRequest || selectedRequest.service_request_id !== selectedRequestId)) {
                setSelectedRequest(request);
            }
        }
    }, [selectedRequestId, requests]);

    const loadRequests = async () => {
        setIsLoading(true);
        try {
            // Always load all requests - filtering happens client-side
            const data = await api.getPublicRequests();

            /* The public list excludes reports the resident marked unlisted,
             * which is the whole point of the setting -- but it is also the
             * list this component searches for "my reports" and for the report
             * a tracking link points at. So a resident who filed an unlisted
             * report could not see it in their own list, and their own link did
             * nothing: the id was not in the array, `find` returned undefined,
             * and the component sat there.
             *
             * Unlisted means "not in the town's public listing", not "hidden
             * from the person who filed it". The by-id endpoint deliberately
             * does not filter on is_public for exactly this reason, so anything
             * of ours that is missing from the list is fetched directly and
             * merged in. */
            const mine: string[] = (() => {
                try {
                    const stored = JSON.parse(localStorage.getItem('my_requests') || '[]');
                    return Array.isArray(stored) ? stored : [];
                } catch { return []; }
            })();

            const present = new Set(data.map(r => r.service_request_id));
            const missing = mine.filter(id => !present.has(id));
            const fetched = missing.length
                ? (await Promise.all(missing.map(id =>
                    api.getPublicRequestDetail(id).catch(() => null))))
                    .filter((r): r is PublicServiceRequest => r !== null)
                : [];

            setRequests(fetched.length ? [...fetched, ...data] : data);
        } catch (err) {
            console.error('Failed to load requests:', err);
        } finally {
            setIsLoading(false);
        }
    };

    const loadComments = async (requestId: string) => {
        setIsLoadingComments(true);
        try {
            const data = await api.getPublicComments(requestId);
            setComments(data);
        } catch (err) {
            console.error('Failed to load comments:', err);
            setComments([]);
        } finally {
            setIsLoadingComments(false);
        }
    };

    const loadAuditLog = async (requestId: string) => {
        try {
            // The public endpoint, because this is the public tracker. The
            // staff one requires a session, so for a logged-out resident --
            // which is everybody following a tracking link -- it answered 401
            // and the timeline was permanently empty. It also redacts staff
            // usernames, which is the correct behaviour on a page anyone with
            // the link can open.
            const log = await api.getPublicAuditLog(requestId);
            setAuditLog(log);
        } catch (err) {
            console.error('Failed to load audit log:', err);
            setAuditLog([]);
        }
    };

    const handleAddComment = async () => {
        if (!selectedRequest || !newComment.trim()) return;

        setIsSubmittingComment(true);
        setCommentError(null);
        try {
            await api.addPublicComment(selectedRequest.service_request_id, newComment.trim());
            // Only cleared once it is actually saved. Clearing first loses
            // something somebody typed to a network blip, with no copy of it.
            setNewComment('');
            await loadComments(selectedRequest.service_request_id);
        } catch (err) {
            setCommentError(
                err instanceof Error && err.message
                    ? err.message
                    : "Your comment could not be posted. Please try again."
            );
        } finally {
            setIsSubmittingComment(false);
        }
    };

    const copyLink = () => {
        if (!selectedRequest) return;
        // Use hash-based URL for sharing
        const url = `${window.location.origin}/#track/${selectedRequest.service_request_id}`;
        navigator.clipboard.writeText(url);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const handleSelectRequest = async (request: PublicServiceRequest) => {
        // Set initial data from list (without media)
        setSelectedRequest(request);
        // Notify parent of selection for hash update
        onRequestSelect?.(request.service_request_id);
        // Scroll to top of the page
        window.scrollTo({ top: 0, behavior: 'instant' });

        // Fetch full details with media in background
        try {
            const fullDetails = await api.getPublicRequestDetail(request.service_request_id);
            setSelectedRequest(fullDetails);
        } catch (err) {
            console.error('Failed to load full request details:', err);
            // Keep using list data if detail fetch fails
        }
    };

    const handleBackToList = () => {
        setSelectedRequest(null);
        // Notify parent to update hash back to track list
        onRequestSelect?.(null);
        // Scroll to top of the page
        window.scrollTo({ top: 0, behavior: 'instant' });
    };


    const filteredRequests = requests.filter((r) => {
        // Filter by status
        if (statusFilter !== 'all' && r.status !== statusFilter) return false;

        // Filter by search query
        if (!searchQuery) return true;
        const query = searchQuery.toLowerCase();
        return (
            r.service_request_id.toLowerCase().includes(query) ||
            r.service_name.toLowerCase().includes(query) ||
            r.address?.toLowerCase().includes(query) ||
            r.description.toLowerCase().includes(query)
        );
    });

    const formatDate = (dateString: string | null) => {
        if (!dateString) return '';
        return new Date(dateString).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    };

    const formatShortDate = (dateString: string | null) => {
        if (!dateString) return '';
        return new Date(dateString).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
        });
    };

    // Detail View
    if (selectedRequest) {
        const status = statusColors[selectedRequest.status] || statusColors.open;

        return (
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                className="min-h-screen"
            >
                {/* Back Button & Actions Bar */}
                <div className="flex items-center justify-between mb-8">
                    <button
                        onClick={handleBackToList}
                        className="flex items-center gap-2 text-white/60 hover:text-white transition-colors group"
                    >
                        <ArrowLeft className="w-5 h-5 group-hover:-translate-x-1 transition-transform" />
                        <span className="font-medium">{"Back to all requests"}</span>
                    </button>

                    <button
                        onClick={copyLink}
                        className={`flex items-center gap-2 px-4 py-2 rounded-xl transition-all ${copied
                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                            : 'bg-white/10 hover:bg-white/20 text-white border border-white/10 hover:border-white/20'
                            }`}
                    >
                        {copied ? (
                            <>
                                <Check className="w-4 h-4" />
                                <span className="font-medium">Link Copied!</span>
                            </>
                        ) : (
                            <>
                                <Share2 className="w-4 h-4" />
                                <span className="font-medium">Share</span>
                            </>
                        )}
                    </button>
                </div>

                {/* Hero Header */}
                <div className={`relative overflow-hidden rounded-2xl ${status.bg} ${status.border} border p-8 mb-8`}>
                    <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent" />
                    <div className="relative">
                        <div className="flex flex-wrap items-center gap-4 mb-4">
                            <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold ${status.bg} ${status.text} border ${status.border}`}>
                                {status.icon}
                                {status.label}
                            </span>
                            <span className="text-white/50 text-sm flex items-center gap-2">
                                <Calendar className="w-4 h-4" />
                                {formatDate(selectedRequest.requested_datetime)}
                            </span>
                        </div>

                        {/* Assignment Info */}
                        {(selectedRequest.assigned_department_name || selectedRequest.assigned_to) && (
                            <div className="flex flex-wrap items-center gap-3 mb-4">
                                {selectedRequest.assigned_department_name && (
                                    <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-primary-500/10 border border-primary-500/20 text-sm text-primary-300">
                                        <Users className="w-3.5 h-3.5" aria-hidden="true" />
                                        {selectedRequest.assigned_department_name}
                                    </span>
                                )}
                                {selectedRequest.assigned_to && (
                                    <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 text-sm text-blue-300">
                                        <UserCircle className="w-3.5 h-3.5" aria-hidden="true" />
                                        Assigned to: {selectedRequest.assigned_to}
                                    </span>
                                )}
                            </div>
                        )}
                        <h1 className="text-3xl font-bold text-white mb-3">
                            {selectedRequest.service_name}
                        </h1>
                        <p className="text-primary-300 font-mono text-sm inline-flex items-center gap-2 bg-white/5 px-3 py-1.5 rounded-lg">
                            {selectedRequest.service_request_id}
                            <button
                                onClick={copyLink}
                                className="text-white/40 hover:text-white transition-colors"
                                aria-label="Copy link to this request"
                            >
                                <Copy className="w-3.5 h-3.5" aria-hidden="true" />
                            </button>
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Location & Map */}
                    {selectedRequest.address && (
                        <Card className="overflow-hidden">
                            <div className="p-5">
                                <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
                                    <div className="p-2 rounded-lg bg-primary-500/20">
                                        <MapPin className="w-5 h-5 text-primary-400" />
                                    </div>
                                    {"Location"}
                                </h3>
                                <p className="text-white/70">{selectedRequest.address}</p>
                            </div>

                            {selectedRequest.lat && selectedRequest.long && mapProviderReady(mapsRaw) && (
                                <div className="h-48 bg-white/5 border-t border-white/10">
                                    {/* The real map rather than a vendor iframe: it
                                        draws with whichever provider the town chose,
                                        and gets the same pin as every other map. */}
                                    <RequestDetailMap
                                        config={mapConfig}
                                        lat={selectedRequest.lat}
                                        lng={selectedRequest.long}
                                        mapLayers={[]}
                                    />
                                </div>
                            )}
                        </Card>
                    )}

                    {/* Timeline & Status */}
                    <Card className="p-5">
                        <h3 className="font-semibold text-white mb-4 flex items-center gap-2">
                            <Clock className="w-5 h-5 text-white/50" />
                            {"Timeline & Status"}
                            {auditLog.length > 0 && (
                                <span className="ml-auto px-2 py-0.5 rounded-full text-xs bg-white/10 text-white/60">{auditLog.length} {"events"}</span>
                            )}
                        </h3>

                        {/* Full Audit Log Timeline */}
                        <div className="relative">
                            <div className="space-y-3">
                                {/* Always show submitted event first, even if not in audit log */}
                                {(() => {
                                    const hasSubmittedEvent = auditLog.some(e => e.action === 'submitted');
                                    const timelineEntries = hasSubmittedEvent ? auditLog : [
                                        {
                                            id: -1,
                                            service_request_id: 0,
                                            action: 'submitted',
                                            new_value: 'open',
                                            old_value: null,
                                            actor_type: 'resident',
                                            actor_name: 'Resident',
                                            created_at: selectedRequest.requested_datetime,
                                            extra_data: null
                                        } as AuditLogEntry,
                                        ...auditLog
                                    ];

                                    return timelineEntries.length > 0 ? (
                                        timelineEntries.map((entry, idx) => {
                                            let actionConfig: { color: string; text: string };

                                            if (entry.action === 'submitted') {
                                                actionConfig = { color: 'bg-emerald-500', text: "Request submitted" };
                                            } else if (entry.action === 'status_change') {
                                                const newStatus = entry.new_value || 'unknown';
                                                const oldStatus = entry.old_value || 'unknown';
                                                let statusText = '';

                                                if (newStatus === 'closed') {
                                                    const substatus = entry.extra_data?.substatus;
                                                    statusText = `Closed ${substatus === 'resolved' ? '- Resolved' : substatus === 'no_action' ? '- No Action Needed' : substatus === 'third_party' ? '- Third Party' : ''}`;
                                                } else if (newStatus === 'in_progress') {
                                                    statusText = oldStatus === 'closed' ? 'Reopened as In Progress' : oldStatus === 'open' ? 'Marked as In Progress' : `Status: ${oldStatus} → In Progress`;
                                                } else if (newStatus === 'open') {
                                                    statusText = oldStatus === 'closed' ? 'Reopened' : oldStatus === 'in_progress' ? 'Reverted to Open' : 'Status set to Open';
                                                } else {
                                                    statusText = `Status: ${oldStatus} → ${newStatus}`;
                                                }

                                                actionConfig = {
                                                    color: newStatus === 'closed' ? 'bg-emerald-500' : newStatus === 'in_progress' ? 'bg-blue-500' : 'bg-purple-500',
                                                    text: statusText
                                                };
                                            } else if (entry.action === 'department_assigned') {
                                                actionConfig = { color: 'bg-purple-500', text: `${"Assigned to"} ${entry.new_value}` };
                                            } else if (entry.action === 'staff_assigned') {
                                                actionConfig = { color: 'bg-indigo-500', text: "Assigned to staff" };
                                            } else if (entry.action === 'comment_added') {
                                                actionConfig = { color: 'bg-teal-500', text: "Comment added" };
                                            } else {
                                                actionConfig = { color: 'bg-gray-500', text: entry.action };
                                            }

                                            const isLast = idx === auditLog.length - 1;

                                            return (
                                                <div key={entry.id} className="relative flex items-start gap-3">
                                                    <div className={`w-3 h-3 rounded-full ${actionConfig.color} shadow-sm ${isLast ? 'ring-2 ring-white/30' : ''}`} />
                                                    <div className="flex-1 min-w-0 -mt-0.5">
                                                        <div className="flex items-center gap-2 flex-wrap">
                                                            <span className="text-white/90 text-sm font-medium">{actionConfig.text}</span>
                                                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${entry.actor_type === 'staff' ? 'bg-purple-500/20 text-purple-300' : 'bg-blue-500/20 text-blue-300'}`}>
                                                                {entry.actor_type === 'staff' ? 'Staff' : 'Resident'}
                                                            </span>
                                                        </div>
                                                        <div className="text-white/40 text-xs mt-0.5">
                                                            {entry.created_at ? new Date(entry.created_at).toLocaleString() : 'No timestamp'}
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })
                                    ) : (
                                        <div className="relative flex items-start gap-3">
                                            <div className="w-3 h-3 rounded-full bg-emerald-500 shadow-sm ring-2 ring-white/30" />
                                            <div className="flex-1 min-w-0 -mt-0.5">
                                                <div className="flex items-center gap-2 flex-wrap">
                                                    <span className="text-white/90 text-sm font-medium">{"Request submitted"}</span>
                                                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/20 text-blue-300">Resident</span>
                                                </div>
                                                <div className="text-white/40 text-xs mt-0.5">{new Date(selectedRequest.requested_datetime).toLocaleString()}</div>
                                            </div>
                                        </div>
                                    )
                                })()}
                            </div>
                        </div>

                        {/* Closed status box if applicable */}
                        {selectedRequest.status === 'closed' && (
                            <div className="mt-4 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                                <p className="text-emerald-300 font-medium flex items-center gap-2">
                                    <CheckCircle className="w-4 h-4" />
                                    {selectedRequest.closed_substatus === 'no_action' ? 'No Action Needed' :
                                        selectedRequest.closed_substatus === 'resolved' ? 'Issue Resolved' :
                                            selectedRequest.closed_substatus === 'third_party' ? 'Referred to Third Party' :
                                                'Closed'}
                                </p>
                                {selectedRequest.completion_message && (
                                    <p className="text-white/60 text-sm mt-2">{selectedRequest.completion_message}</p>
                                )}
                                {selectedRequest.completion_photo_url && (
                                    <img
                                        src={selectedRequest.completion_photo_url}
                                        alt="Completion photo"
                                        className="mt-3 rounded-lg max-h-48 object-contain cursor-pointer hover:opacity-90 transition-opacity"
                                        onClick={() => selectedRequest.completion_photo_url && window.open(selectedRequest.completion_photo_url, '_blank')}
                                    />
                                )}
                            </div>
                        )}
                    </Card>
                </div>

                {/* Photos if available */}
                {selectedRequest.media_urls && selectedRequest.media_urls.length > 0 && (
                    <div className="mt-6">
                        <Card className="p-4">
                            <h3 className="font-semibold text-white flex items-center gap-2 mb-4">
                                <Image className="w-5 h-5 text-primary-400" />
                                {"Submitted Photos"} ({selectedRequest.media_urls.length})
                            </h3>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {selectedRequest.media_urls.map((url, index) => (
                                    <div
                                        key={index}
                                        onClick={() => setLightboxUrl(url)}
                                        className="block group cursor-pointer"
                                    >
                                        <div className="relative overflow-hidden rounded-xl aspect-square">
                                            <img
                                                src={url}
                                                alt={`Submitted photo ${index + 1}`}
                                                className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                                            />
                                            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
                                                <ExternalLink className="w-8 h-8 text-white" />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </Card>
                        {selectedRequest.completion_photo_url && (
                            <Card className="overflow-hidden mt-4">
                                <div className="p-4 border-b border-white/10">
                                    <h3 className="font-semibold text-white flex items-center gap-2">
                                        <CheckCircle className="w-5 h-5 text-emerald-400" />
                                        {"Completion Photo"}
                                    </h3>
                                </div>
                                <a
                                    href={selectedRequest.completion_photo_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="block group"
                                >
                                    <div className="relative overflow-hidden">
                                        <img
                                            src={selectedRequest.completion_photo_url}
                                            alt="Completion photo"
                                            className="w-full max-h-64 object-cover transition-transform duration-300 group-hover:scale-105"
                                        />
                                    </div>
                                </a>
                            </Card>
                        )}
                    </div>
                )}

                {/* Description - Full Width */}
                <Card className="p-6 mt-6">
                    <h3 className="font-semibold text-white mb-4 flex items-center gap-2">
                        <div className="p-2 rounded-lg bg-white/10">
                            <ExternalLink className="w-5 h-5 text-white/70" />
                        </div>
                        {"Description"}
                    </h3>
                    <p className="text-white/80 text-lg leading-relaxed whitespace-pre-wrap">
                        <TranslatedContent
                            text={selectedRequest.description}
                            contentId={`desc_${selectedRequest.service_request_id}`}
                        />
                    </p>
                </Card>

                {/* Comments Section - Full Width */}
                <Card className="p-6 mt-6">
                    <h3 className="font-semibold text-white mb-6 flex items-center gap-2">
                        <div className="p-2 rounded-lg bg-primary-500/20">
                            <MessageSquare className="w-5 h-5 text-primary-400" />
                        </div>
                        {"Community Discussion"}
                        <span className="ml-auto text-sm font-normal text-white/40">
                            {comments.length} {comments.length !== 1 ? "comments" : "comment"}
                        </span>
                    </h3>

                    {/* Add Comment */}
                    <div className="mb-6 p-4 rounded-xl bg-white/5 border border-white/10">
                        <Textarea
                            placeholder={"Share your thoughts or updates..."}
                            value={newComment}
                            onChange={(e) => {
                                setNewComment(e.target.value);
                                // The message described the last attempt. Once
                                // they start rewording it, it is stale advice.
                                if (commentError) setCommentError(null);
                            }}
                            rows={3}
                            className="mb-3 bg-transparent border-white/20 focus:border-primary-500"
                        />
                        {commentError && (
                            <div
                                role="alert"
                                className="mb-3 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200"
                            >
                                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                                <span>{commentError}</span>
                            </div>
                        )}
                        <div className="flex justify-end">
                            <Button
                                onClick={handleAddComment}
                                disabled={!newComment.trim() || isSubmittingComment}
                                size="sm"
                            >
                                <Send className="w-4 h-4 mr-2" />
                                {isSubmittingComment ? "Posting..." : "Post Comment"}
                            </Button>
                        </div>
                    </div>

                    {/* Comment List */}
                    <div className="space-y-4 max-h-[500px] overflow-y-auto">
                        {isLoadingComments ? (
                            <CommentSkeleton rows={2} />
                        ) : comments.length === 0 ? (
                            <CommentEmptyState
                                title="No comments yet"
                                hint="Be the first to share an update!"
                            />
                        ) : (
                            comments.map((comment, index) => (
                                <motion.div
                                    key={comment.id}
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: index * 0.05 }}
                                >
                                    <CommentCard comment={comment}>
                                        <TranslatedContent
                                            text={comment.content}
                                            contentId={`comment_${comment.id}`}
                                        />
                                    </CommentCard>
                                </motion.div>
                            ))
                        )}
                    </div>
                </Card>

                {/* Premium Photo Lightbox Modal */}
                {lightboxUrl && (
                    <div
                        className="fixed inset-0 z-[100] flex items-center justify-center p-4 md:p-8"
                        onClick={() => setLightboxUrl(null)}
                    >
                        {/* Backdrop with blur */}
                        <div className="absolute inset-0 bg-black/95 backdrop-blur-xl" />

                        {/* Close button */}
                        <button
                            onClick={() => setLightboxUrl(null)}
                            className="absolute top-4 right-4 md:top-6 md:right-6 z-20 p-2 rounded-full bg-white/10 hover:bg-white/20 border border-white/20 transition-all duration-300 group"
                            aria-label="Close image preview"
                        >
                            <X className="w-6 h-6 text-white group-hover:rotate-90 transition-transform duration-300" aria-hidden="true" />
                        </button>

                        {/* Image container with premium styling */}
                        <div
                            className="relative z-10 max-w-[90vw] max-h-[85vh] rounded-2xl overflow-hidden shadow-2xl ring-1 ring-white/20"
                            onClick={(e) => e.stopPropagation()}
                        >
                            {/* Gradient glow effect behind image */}
                            <div className="absolute -inset-1 bg-gradient-to-r from-primary-500/30 via-purple-500/30 to-primary-500/30 blur-xl opacity-50" />

                            {/* Image */}
                            <img
                                src={lightboxUrl}
                                alt="Full size preview"
                                className="relative max-w-full max-h-[85vh] object-contain bg-gray-900/50 rounded-2xl"
                            />
                        </div>

                        {/* Instructions */}
                        <p className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/50 text-sm">
                            Click anywhere to close
                        </p>
                    </div>
                )}
            </motion.div>
        );
    }

    // Split filtered requests into "mine" and "community"
    const myFilteredRequests = filteredRequests.filter(r => myRequestIds.has(r.service_request_id));
    const communityFilteredRequests = filteredRequests.filter(r => !myRequestIds.has(r.service_request_id));

    // Shared card renderer
    const renderRequestCard = (request: PublicServiceRequest, index: number, isMine: boolean) => {
        const status = statusColors[request.status] || statusColors.open;
        return (
            <motion.div
                key={request.service_request_id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(index * 0.01, 0.2) }}
                onClick={() => handleSelectRequest(request)}
                className="cursor-pointer group"
            >
                <Card className={`p-5 hover:ring-2 transition-all group-hover:bg-white/[0.03] ${
                    isMine
                        ? 'hover:ring-purple-500/50 border-purple-500/20'
                        : 'hover:ring-primary-500/50'
                }`}>
                    <div className="flex gap-5">
                        {(request.photo_count || 0) > 0 && (
                            <div className="w-20 h-20 flex-shrink-0 rounded-xl overflow-hidden bg-primary-500/20 flex flex-col items-center justify-center">
                                <Image className="w-8 h-8 text-primary-400" />
                                <span className="text-primary-300 text-xs mt-1">{request.photo_count} {request.photo_count !== 1 ? "photos" : "photo"}</span>
                            </div>
                        )}
                        <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-4 mb-3">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="font-semibold text-white text-xl group-hover:text-primary-300 transition-colors">
                                            <TranslatedContent
                                                text={request.service_name}
                                                contentId={`service_${request.service_code}`}
                                            />
                                        </h3>
                                        {isMine && (
                                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-500/20 text-purple-300 border border-purple-500/30 uppercase tracking-wider">
                                                <Star className="w-3 h-3" />
                                                Yours
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-xs text-primary-400/70 font-mono mt-1">
                                        {request.service_request_id}
                                    </p>
                                </div>
                                <span className={`flex-shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium ${status.bg} ${status.text} ${status.border} border`}>
                                    {status.icon}
                                    {status.label}
                                </span>
                            </div>
                            {request.address && (
                                <div className="flex items-center gap-2 text-white/60 mb-3">
                                    <MapPin className="w-4 h-4 flex-shrink-0" />
                                    <span className="truncate">{request.address}</span>
                                </div>
                            )}
                            <p className="text-white/50 line-clamp-2 mb-4">
                                <TranslatedContent
                                    text={request.description}
                                    contentId={`list_desc_${request.service_request_id}`}
                                />
                            </p>
                            <div className="flex items-center gap-4 text-sm text-white/40">
                                <span className="flex items-center gap-1.5">
                                    <Calendar className="w-4 h-4" />
                                    {formatShortDate(request.requested_datetime)}
                                </span>
                            </div>
                        </div>
                    </div>
                </Card>
            </motion.div>
        );
    };

    // List View
    return (
        <div className="min-h-screen">
            {/* Compact Header - Mobile Optimized */}
            <div className="mb-6">
                <h2 className="text-2xl md:text-3xl font-bold text-white mb-1">{"Track Requests"}</h2>
                <p className="text-white/60 text-sm md:text-lg">{"View the status of community-reported issues"}</p>
            </div>

            {/* Search - Full width on mobile */}
            <div className="mb-4">
                <div className="relative">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-white/40" />
                    <Input
                        placeholder={"Search by ID, category, or address..."}
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="pl-12 h-12 text-base"
                    />
                </div>
            </div>

            {/* Status Filter Tabs - Grid layout to fit all buttons */}
            <div className="mb-4">
                <div className="grid grid-cols-4 gap-1.5 md:flex md:gap-2 md:flex-wrap">
                    {([
                        { key: 'all', label: 'All Requests', mobileLabel: 'All' },
                        { key: 'open', label: 'Open', mobileLabel: 'Open' },
                        { key: 'in_progress', label: 'In Progress', mobileLabel: 'In Prog' },
                        { key: 'closed', label: 'Resolved', mobileLabel: 'Done' },
                    ] as const).map(({ key, label, mobileLabel }) => {
                        const colors = statusColors[key as StatusFilter];
                        const isActive = statusFilter === key;
                        return (
                            <button
                                key={key}
                                onClick={() => setStatusFilter(key as StatusFilter)}
                                className={`px-2 md:px-4 py-2 rounded-lg md:rounded-xl text-xs md:text-sm font-medium transition-all text-center ${isActive
                                    ? key === 'all'
                                        ? 'bg-primary-500 text-white'
                                        : `${colors?.bg} ${colors?.text} ${colors?.border} border`
                                    : 'bg-white/5 text-white/60 hover:bg-white/10 border border-transparent'
                                    }`}
                            >
                                <span className="hidden md:inline">{label}</span>
                                <span className="md:hidden">{mobileLabel}</span>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Stats Summary - Compact on mobile */}
            <div className="grid grid-cols-3 gap-2 md:gap-4 mb-6">
                {[
                    { status: 'open', color: 'amber', label: "Open", mobileLabel: "Open" },
                    { status: 'in_progress', color: 'blue', label: "In Progress", mobileLabel: "In Prog" },
                    { status: 'closed', color: 'emerald', label: "Resolved", mobileLabel: "Done" },
                ].map(({ status, color, label, mobileLabel }) => (
                    <button
                        key={status}
                        onClick={() => setStatusFilter(status as StatusFilter)}
                        className={`p-2 md:p-4 rounded-xl md:rounded-2xl bg-gradient-to-br from-${color}-500/10 to-${color}-500/5 border border-${color}-500/20 hover:border-${color}-500/40 transition-all text-center group overflow-hidden`}
                    >
                        <div className={`text-xl md:text-4xl font-bold text-${color}-400 group-hover:scale-110 transition-transform`}>
                            {requests.filter(r => r.status === status).length}
                        </div>
                        <div className="text-[10px] md:text-sm text-white/50 mt-1 truncate">
                            <span className="hidden md:inline">{label}</span>
                            <span className="md:hidden">{mobileLabel}</span>
                        </div>
                    </button>
                ))}
            </div>

            {/* Request List */}
            {isLoading ? (
                <div className="flex justify-center py-16">
                    <div className="w-10 h-10 border-3 border-primary-500 border-t-transparent rounded-full animate-spin" />
                </div>
            ) : filteredRequests.length === 0 ? (
                <Card className="p-12">
                    <div className="text-center">
                        <AlertCircle className="w-16 h-16 mx-auto mb-4 text-white/20" />
                        <p className="text-white/50 text-lg">No requests found</p>
                        <p className="text-white/30 text-sm mt-1">Try adjusting your search or filters</p>
                    </div>
                </Card>
            ) : (
                <div className="space-y-6">
                    {/* Your Submissions Section */}
                    {myRequestIds.size > 0 && (
                        <div>
                            <div className="flex items-center gap-3 mb-4">
                                <div className="p-2 rounded-xl bg-gradient-to-br from-purple-500/20 to-indigo-500/20 border border-purple-500/20">
                                    <Star className="w-5 h-5 text-purple-400" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-bold text-white">Your Submissions</h3>
                                    <p className="text-xs text-white/40">{myFilteredRequests.length} of {myRequestIds.size} matching filters</p>
                                </div>
                            </div>
                            {myFilteredRequests.length > 0 ? (
                                <div className="space-y-4 pl-0 md:pl-1">
                                    {myFilteredRequests.map((request, index) => renderRequestCard(request, index, true))}
                                </div>
                            ) : (
                                <div className="p-4 rounded-xl bg-white/5 border border-white/10 text-center">
                                    <p className="text-white/40 text-sm">None of your submissions match the current filters</p>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Community Requests Section */}
                    {communityFilteredRequests.length > 0 && (
                        <div>
                            {myRequestIds.size > 0 && (
                                <div className="flex items-center gap-3 mb-4 pt-2">
                                    <div className="p-2 rounded-xl bg-white/5 border border-white/10">
                                        <User className="w-5 h-5 text-white/40" />
                                    </div>
                                    <h3 className="text-lg font-bold text-white/70">Community Requests</h3>
                                </div>
                            )}
                            <div className="space-y-4">
                                {communityFilteredRequests.map((request, index) => renderRequestCard(request, index, false))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Premium Photo Lightbox Modal */}
            {lightboxUrl && (
                <div
                    className="fixed inset-0 z-[100] flex items-center justify-center p-4 md:p-8"
                    onClick={() => setLightboxUrl(null)}
                >
                    {/* Backdrop with blur */}
                    <div className="absolute inset-0 bg-black/95 backdrop-blur-xl" />

                    {/* Close button */}
                    <button
                        onClick={() => setLightboxUrl(null)}
                        className="absolute top-4 right-4 md:top-6 md:right-6 z-20 p-2 rounded-full bg-white/10 hover:bg-white/20 border border-white/20 transition-all duration-300 group"
                        aria-label="Close image preview"
                    >
                        <X className="w-6 h-6 text-white group-hover:rotate-90 transition-transform duration-300" aria-hidden="true" />
                    </button>

                    {/* Image container with premium styling */}
                    <div
                        className="relative z-10 max-w-[90vw] max-h-[85vh] rounded-2xl overflow-hidden shadow-2xl ring-1 ring-white/20"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Gradient glow effect behind image */}
                        <div className="absolute -inset-1 bg-gradient-to-r from-primary-500/30 via-purple-500/30 to-primary-500/30 blur-xl opacity-50" />

                        {/* Image */}
                        <img
                            src={lightboxUrl}
                            alt="Full size preview"
                            className="relative max-w-full max-h-[85vh] object-contain bg-gray-900/50 rounded-2xl"
                        />
                    </div>

                    {/* Instructions */}
                    <p className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/50 text-sm">
                        Click anywhere to close
                    </p>
                </div>
            )}
        </div>
    );
}
