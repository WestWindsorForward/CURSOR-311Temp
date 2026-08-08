// User types
export interface User {
    id: number;
    username: string;
    email: string;
    full_name: string | null;
    role: 'admin' | 'staff' | 'researcher';
    is_active: boolean;
    created_at?: string;
    departments?: { id: number; name: string }[];
}

export interface UserCreate {
    username: string;
    email: string;
    full_name?: string;
    role: 'admin' | 'staff' | 'researcher';
    password: string;
    department_ids?: number[];
}

/** Everything an admin can change about an existing staff member.
 *
 * Username is deliberately absent: it is the identity the audit log and the
 * identity provider key off, so renaming it would orphan history rather than
 * correct it. Password has its own reset endpoint.
 *
 * Every field is optional and only what is sent gets changed, so editing a
 * phone number cannot accidentally blank a role. */
export interface UserUpdate {
    email?: string;
    full_name?: string;
    role?: 'admin' | 'staff' | 'researcher';
    is_active?: boolean;
    phone?: string;
    department_ids?: number[];
}

// Department types
export interface Department {
    id: number;
    name: string;
    description: string | null;
    routing_email: string | null;
    is_active: boolean;
}

// Custom Question types
export interface CustomQuestion {
    id: string;           // UUID for tracking
    label: string;        // Question text
    type: 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'number' | 'date' | 'yes_no';
    options?: string[];   // For select/radio/checkbox types
    required: boolean;
    placeholder?: string;
}

// Service types
/** A third-party agency as the routing modal stores it: contact details flat on
 *  the entry, plus the roads and message that belong to that agency alone. */
export interface RoutingContact {
    name?: string;
    phone?: string;
    email?: string;
    url?: string;
    message?: string;
    /** Raw comma-separated text as the clerk types it. */
    road_list?: string;
    roads?: string[];
}

export interface ServiceDefinition {
    id: number;
    service_code: string;
    service_name: string;
    description: string | null;
    icon: string;
    is_active: boolean;
    display_order: number;
    /** Optional SLA target in hours from submission to closure. null = no SLA. */
    sla_hours?: number | null;
    departments: Department[];
    routing_mode?: 'township' | 'third_party' | 'road_based';
    routing_config?: {
        // Township mode
        route_to?: 'all_staff' | 'specific_staff';
        staff_ids?: number[];
        // Third party mode
        message?: string;
        third_party_name?: string;
        contacts?: RoutingContact[];
        // Road-based mode
        /** Who handles a road no rule names: 'township' for the municipality,
         *  or a configured agency's name. The old literal 'third_party' is
         *  still read for configs saved before agencies were named, and
         *  resolves only when exactly one agency exists. */
        default_handler?: string;
        exclusion_list?: string[];
        inclusion_list?: string[];
        third_party_message?: string;
        /** One entry per agency card in the routing modal. Each owns its own
         *  roads, message and contact details -- reading them as a single
         *  flattened list is what showed a resident every agency at once. */
        third_party_contacts?: RoutingContact[];
        /** Stretches a clerk switched off, keyed to the road publisher's own
         *  feature ids so a data refresh cannot orphan the corrections. */
        excluded_segments?: string[];
        /** How far from a centreline still counts as being on that road. */
        corridor_metres?: number;
        /** Partial coverage per stretch, as fractions of its length. Survives a
         *  data refresh that re-draws the line, where a point would not. */
        segment_trims?: Record<string, { start: number; end: number }>;
        // Custom questions (applies to all modes)
        custom_questions?: CustomQuestion[];
    };
    assigned_department_id?: number;
    assigned_department?: Department;
}

export interface ServiceCreate {
    service_code: string;
    service_name: string;
    description?: string;
    icon?: string;
    department_ids?: number[];
    routing_mode?: string;
    routing_config?: Record<string, any>;
    assigned_department_id?: number;
}

// Service Request types
export type RequestStatus = 'open' | 'in_progress' | 'closed';
export type ClosedSubstatus = 'no_action' | 'resolved' | 'third_party';
export type CommentVisibility = 'internal' | 'external';

export interface ServiceRequest {
    id: number;
    service_request_id: string;
    service_code: string;
    service_name: string;
    description: string;
    status: RequestStatus;
    priority: number;
    address: string | null;
    lat: number | null;
    long: number | null;
    requested_datetime: string;
    updated_datetime: string | null;
    source: string;
    flagged: boolean;
    /** false = unlisted: hidden from public feeds/APIs (staff always see it).
     *  The RESIDENT's choice, made at submission. Staff never write it. */
    is_public?: boolean;
    /** true = staff took this off the public tracker and map. The report stays:
     *  the tracking link works, staff see it, research exports include it. */
    public_archived?: boolean;
    // Assignment
    assigned_department_id: number | null;
    assigned_to: string | null;
    // Closed sub-status
    closed_substatus: ClosedSubstatus | null;
    // Soft delete
    deleted_at: string | null;
    deleted_by: string | null;
    custom_fields?: Record<string, string | string[]>;
    // AI Analysis (optional, for sorting purposes - priority_score is in ai_analysis)
    ai_analysis?: Record<string, unknown> | null;
    manual_priority_score?: number | null;
}

// Public-facing request (no personal information)
export interface PublicServiceRequest {
    service_request_id: string;
    service_code: string;
    service_name: string;
    description: string;
    status: RequestStatus;
    address: string | null;
    lat: number | null;
    long: number | null;
    requested_datetime: string;
    updated_datetime: string | null;
    closed_substatus: ClosedSubstatus | null;
    media_urls: string[];  // Array of photo URLs
    photo_count?: number;  // Number of photos (from list response)
    completion_message: string | null;
    completion_photo_url: string | null;
    assigned_to: string | null;
    assigned_department_name: string | null;
}

export interface ServiceRequestDetail extends ServiceRequest {
    first_name: string | null;
    last_name: string | null;
    email: string;
    phone: string | null;
    media_urls: string[];  // Array of photo URLs
    /** Platforms this request exists in. Empty means the work-order
     *  refresh has nothing to pull, so the button is not offered. */
    external_links?: string[];
    ai_analysis: Record<string, unknown> | null;
    flag_reason: string | null;
    staff_notes: string | null;
    assigned_department_id: number | null;
    assigned_department: Department | null;  // Full department info
    assigned_to: string | null;
    closed_datetime: string | null;
    // Completion fields
    completion_message: string | null;
    completion_photo_url: string | null;
    delete_justification: string | null;
    // Vertex AI Analysis (priority_score is in ai_analysis JSON only)
    ai_summary: string | null;
    ai_classification: string | null;
    manual_priority_score: number | null;  // Human-approved priority
    ai_analyzed_at: string | null;
}

export interface RequestComment {
    id: number;
    service_request_id: number;
    user_id: number | null;
    username: string;
    content: string;
    visibility: CommentVisibility;
    created_at: string | null;
    updated_at: string | null;
}

// Audit Log Entry for tracking all changes to requests
export interface AuditLogEntry {
    id: number;
    service_request_id: number;
    action: 'submitted' | 'status_change' | 'department_assigned' | 'staff_assigned' | 'comment_added' | 'legal_hold' | 'public_archive' | 'deleted' | 'restored' | 'priority_accepted';
    old_value: string | null;
    new_value: string | null;
    actor_type: 'resident' | 'staff' | 'admin';
    actor_name: string | null;
    created_at: string | null;
    extra_data: {
        substatus?: string;
        completion_message?: string;
    } | null;
}

export interface ServiceRequestCreate {
    service_code: string;
    description: string;
    address?: string;
    lat?: number;
    long?: number;
    first_name?: string;
    last_name?: string;
    email: string;
    phone?: string;
    preferred_language?: string;  // ISO 639-1 language code (e.g. 'en', 'es', 'hi')
    media_urls?: string[];  // Up to 3 photo URLs/base64
    matched_asset?: {
        layer_name: string;
        layer_id: number;
        asset_id?: string;
        asset_type?: string;
        properties: Record<string, any>;
        distance_meters?: number;
    };
    custom_fields?: Record<string, string | string[]>;
    /** false = unlisted: excluded from public feeds/APIs, still viewable by direct link. */
    is_public?: boolean;
}


export interface ManualIntakeCreate {
    service_code: string;
    description: string;
    address?: string;
    lat?: number;
    long?: number;
    first_name?: string;
    last_name?: string;
    email?: string;
    phone?: string;
    preferred_language?: string;
    media_urls?: string[];
    matched_asset?: Record<string, any> | null;
    custom_fields?: Record<string, string | string[]>;
    /** false = unlisted: excluded from public feeds/APIs, still viewable by direct link. */
    is_public?: boolean;
    /** Staff may knowingly log a report on a road another agency maintains. */
    override_jurisdiction?: boolean;
    source: 'phone' | 'walk_in' | 'email';
}

// System Settings types
export interface SocialLink {
    platform: string;  // website, facebook, instagram, youtube, twitter, linkedin, tiktok, nextdoor
    url: string;
    icon: string;      // Lucide icon name
}

export interface SystemSettings {
    id: number;
    township_name: string;
    logo_url: string | null;
    favicon_url: string | null;
    hero_text: string;
    primary_color: string;
    /* Product features with nothing to configure.
     *
     * `ai_analysis`, `sms_alerts` and `email_notifications` were here too, and
     * were the second (for email and SMS, third) place the same capability
     * could be switched. They are `capability_switches` on the server now --
     * see backend/app/services/capability_switches.py for which belongs where. */
    modules: {
        research_portal?: boolean;
        unlisted_reports?: boolean;
    };
    /* Per-pack research export switches: {pack_id: bool}. An absent key means
     * the pack's own server-side default (analytical packs on; sentiment and
     * moderation off). Enforced at row build on the server — this is display
     * and editing state only. */
    research_packs?: Record<string, boolean> | null;
    social_links?: SocialLink[];
    privacy_policy?: string | null;  // Custom privacy policy (Markdown)
    terms_of_service?: string | null;  // Custom terms of service (Markdown)
    accessibility_statement?: string | null;  // Custom accessibility statement
    /** Days a CLOSED report stays on the public tracker and map. null means
     *  everything stays listed. Applied at read time on the server, so changing
     *  it is retroactive in both directions. */
    public_archive_days?: number | null;
    updated_at: string | null;
}

// System Secret types
export interface SystemSecret {
    id: number;
    key_name: string;
    key_value?: string;  // Only returned for some secrets (not sensitive ones)
    description: string | null;
    is_configured: boolean;
}

// Statistics types
export interface Statistics {
    total_requests: number;
    open_requests: number;
    in_progress_requests: number;
    closed_requests: number;
    requests_by_category: Record<string, number>;
    requests_by_status: Record<string, number>;
    recent_requests: ServiceRequest[];
}

export interface HeatmapPoint {
    lat: number;
    lng: number;
    weight: number;
}

export interface HeatmapData {
    report_points: HeatmapPoint[];
    reporter_points: HeatmapPoint[];
    total_reports: number;
    total_unique_reporters: number;
}

export interface HotspotData {
    lat: number;
    lng: number;
    count: number;
    cluster_id: number;
    sample_address?: string;  // Representative address for the cluster
    top_categories?: string[];  // Most common issue types in cluster
    unique_reporters?: number;  // Count of distinct reporters (for bias detection)
    oldest_days?: number;  // Age of oldest open request in this cluster
}

export interface TrendData {
    period: string;
    open: number;
    in_progress: number;
    closed: number;
    total: number;
}

export interface DepartmentMetrics {
    name: string;
    total_requests: number;
    open_requests: number;
    avg_resolution_hours: number | null;
    resolution_rate: number;
}

export interface PredictiveInsights {
    volume_forecast_next_week: number;
    trend_direction: string;
    seasonal_peak_day: string;
    seasonal_peak_month: string;
}

export interface CostEstimate {
    category: string;
    avg_hours: number;
    estimated_cost: number;
    open_tickets: number;
    total_estimated_cost: number;
}

export interface RepeatLocation {
    address: string;
    lat: number;
    lng: number;
    request_count: number;
}

export interface AdvancedStatistics {
    // Summary counts
    total_requests: number;
    open_requests: number;
    in_progress_requests: number;
    closed_requests: number;

    // Temporal analytics
    requests_by_hour: Record<number, number>;
    requests_by_day_of_week: Record<string, number>;
    requests_by_month: Record<string, number>;
    avg_resolution_hours_by_category: Record<string, number>;

    // Geospatial analytics (PostGIS) - imperial units
    hotspots: HotspotData[];
    geographic_center: { lat: number; lng: number } | null;
    geographic_spread_miles: number | null;  // Standard deviation in miles
    total_coverage_sq_miles: number | null;  // Total area covered by requests
    avg_distance_from_center_miles: number | null;  // Average distance from center
    furthest_request_miles: number | null;  // Distance of furthest request
    requests_density_by_zone: Record<string, number>;

    // Department analytics
    department_metrics: DepartmentMetrics[];
    top_staff_by_resolutions: Record<string, number>;

    // Performance metrics
    avg_resolution_hours: number | null;
    avg_first_response_hours: number | null;
    backlog_by_age: Record<string, number>;
    resolution_rate: number;

    // Infrastructure-focused metrics
    backlog_by_priority: Record<number, number>;
    workload_by_staff: Record<string, number>;
    open_by_age_sla: Record<string, number>;

    // Predictive & Government Analytics
    predictive_insights: PredictiveInsights;
    cost_estimates: CostEstimate[];
    avg_response_time_hours: number | null;
    repeat_locations: RepeatLocation[];
    aging_high_priority_count: number;

    // Category analytics
    requests_by_category: Record<string, number>;
    flagged_count: number;

    // Trends
    weekly_trend: TrendData[];
    monthly_trend: TrendData[];

    // Cache info
    cached_at: string | null;
}

// Auth types
export interface LoginCredentials {
    username: string;
    password: string;
}

export interface AuthToken {
    access_token: string;
    token_type: string;
}

export interface AuthState {
    user: User | null;
    token: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
}


/** One connector's live state.
 *
 * `status` has four values and the important one is "unknown": nothing has
 * called this connector, so we do not know it works. Rendering that as either
 * green or red is how a revoked key keeps a healthy badge, so the UI has to
 * carry the distinction the backend went to the trouble of making.
 */
export interface ConnectorHealth {
    connector: string;
    provider: string | null;
    status: 'working' | 'failing' | 'down' | 'stale' | 'unknown';
    summary: string;
    last_success_at: string | null;
    last_error_at: string | null;
    last_error: string | null;
    consecutive_failures: number;
    total_successes: number;
    total_failures: number;
    /** When alert emails for this connector resume. Null means nobody has
     *  muted it. The badge is never affected -- a mute that hid the problem as
     *  well as the email would be indistinguishable from broken alerting. */
    alerts_muted_until?: string | null;
    /** What the last check said, either way. A card that knows only *when* it
     *  was checked cannot say what it found. */
    last_result?: string | null;
    /** False when this provider cannot be checked from here at all. Stored, so
     *  the answer survives the session that produced it -- otherwise the card
     *  reverts to "not checked yet" and invites another press of a button that
     *  can never succeed. */
    verifiable?: boolean | null;
}

export interface ConnectorHealthReport {
    connectors: ConnectorHealth[];
    needs_attention: string[];
}


/** A browser crash, as the admin console shows it.
 *
 * `occurrences` matters: identical crashes collapse onto one row, so a render
 * loop reads as "seen 400 times" rather than burying every other fault under
 * 400 identical entries. */
export interface ClientErrorEntry {
    id: number;
    kind: string;
    message: string;
    stack: string | null;
    component_stack: string | null;
    url: string | null;
    occurrences: number;
    first_seen_at: string | null;
    last_seen_at: string | null;
}
