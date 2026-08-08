import {
    AuthToken,
    User,
    ServiceDefinition,
    ServiceRequest,
    ServiceRequestDetail,
    ServiceRequestCreate,
    ManualIntakeCreate,
    SystemSettings,
    SystemSecret,
    Statistics,
    AdvancedStatistics,
    UserCreate,
    UserUpdate,
    ConnectorHealthReport,
    ClientErrorEntry,
    ServiceCreate,
    Department,
    RequestComment,
    PublicServiceRequest,
} from '../types';

const API_BASE = '/api';

/** Deployment-level shape the admin UI branches on, from GET /system/config. */
export interface SystemConfig {
    /** State-hosted: the orchestrator owns the infrastructure credentials. */
    managed_mode?: boolean;
    /** The address residents use, for callback URLs and for registration. */
    public_origin?: string | null;
    app_version?: string | null;
    translation_enabled?: boolean;
    /** An operator-hosted registration form, possibly carrying {token}
     *  placeholders this console fills in. Empty means none is configured. */
    contact_form_url?: string;
    /** Whether that form may be shown in a frame inside the console. */
    contact_form_embed?: boolean;
}

// GovTech platform integration types
export interface IntegrationFieldSpec {
    key: string;
    label: string;
    secret?: boolean;
    placeholder?: string;
    required?: boolean;
}

export interface IntegrationVendorAsk {
    to_hint: string;
    subject: string;
    body: string;
}

/** A platform where the admin signs in on the vendor's own site instead of
 *  pasting credentials into our form (currently Accela). */
export interface IntegrationOAuth {
    flow: 'authorization_code';
    start_path: string;
    button_label: string;
    explainer: string;
    fallback_label: string;
    credential_key: string;
}

export interface IntegrationPlatform {
    platform: string;
    name: string;
    vendor: string;
    category: string;
    // 'generic' is what the registry sends for the configurable REST connector.
    // It was missing here while the UI carried a label for it, so the value the
    // backend actually emits was the one the type said could not arrive.
    integration_mode: 'public_api' | 'open311' | 'partner_api' | 'generic';
    docs_url: string;
    description: string;
    capabilities: string[];
    credential_fields: IntegrationFieldSpec[];
    config_fields: IntegrationFieldSpec[];
    oauth?: IntegrationOAuth;
    setup_notes: string;
    // Clerk-friendly guidance
    plain_summary: string;
    what_you_need: string[];
    vendor_ask: IntegrationVendorAsk | null;
    field_help: Record<string, string>;
    recommended_sync_direction: string;
}

export interface IntegrationTestResult {
    ok: boolean;
    detail: string;
    friendly?: string;
    // False when the endpoint answered but nothing exercised the credentials —
    // an Open311 server that serves /services.json to anybody, or a vendor with
    // no key saved. Rendering these as "Connected" is what let a clerk walk away
    // from a connection that had never been authenticated.
    verified?: boolean;
    // Things that work today but will stop a report from being filed — e.g. a
    // SeeClickFix request type asking a required question we cannot answer.
    warnings?: string[];
}

export interface IntegrationConfig {
    id: number;
    platform: string;
    platform_name: string;
    display_name: string;
    enabled: boolean;
    sync_direction: 'push' | 'pull' | 'bidirectional';
    config: Record<string, unknown>;
    configured_credentials: string[];
    credentials_vaulted: boolean;
    // 'all' | 'partial' | 'none'. `credentials_vaulted` used to be an `any`, so a
    // vault write that failed for one field still reported the whole set as
    // vaulted; 'partial' is the state worth naming, because it means at least one
    // secret is in this app's database after all.
    credentials_vaulted_state?: 'all' | 'partial' | 'none';
    /** True once the admin has completed the vendor's own sign-in. */
    oauth_connected: boolean;
    webhook_path: string;
    last_sync_at: string | null;
    last_sync_status: string | null;
    last_sync_error: string | null;
    created_at: string | null;
}

export interface IntegrationSave {
    platform: string;
    display_name?: string;
    enabled?: boolean;
    sync_direction?: string;
    // A null value deletes that key. The backend merges config and the wizard
    // skipped empty strings, so between them a wrong jurisdiction_id could never
    // be blanked — it stayed in every outbound payload forever.
    config?: Record<string, unknown>;
    credentials?: Record<string, string>;
}

/** An external platform's record for one service request (staff view). */
export interface IntegrationRequestLink {
    platform: string;
    platform_name: string;
    external_id: string;
    external_status: string | null;
    direction: string;
    last_pushed_at: string | null;
    last_pulled_at: string | null;
    sync_error: string | null;
}

export interface IntegrationSyncLog {
    id: number;
    operation: string;
    status: string;
    detail: string | null;
    request_count: number;
    created_at: string | null;
}

// Pluggable service-provider types (AI / translation / identity)
export interface ProviderFieldSpec {
    key: string;
    label: string;
    secret?: boolean;
    /** Whether the provider cannot work without it. The backend has sent this
     *  on every field all along and the type did not model it, so the form drew
     *  a required box and an optional one identically — the only hint a clerk
     *  got was prose inside the label. */
    required?: boolean;
}

/** A credential this provider needs but does not collect, because another card
 *  already does. Photo redaction on Google and AWS runs on the service account
 *  and access keys entered elsewhere; asking twice would be two boxes writing
 *  one secret. `where` names the card to go to — without it the card says "not
 *  set up" and offers nothing to do about it. */
export interface BorrowedRequirement {
    key: string;
    label: string;
    where?: string;
}

export interface ProviderModelSpec {
    id: string;
    label: string;
    discovered?: boolean;  // newly found live, not in the curated list
}

export interface ProviderInfo {
    provider: string;
    name: string;
    description?: string;
    boundary?: string;
    models?: ProviderModelSpec[];
    default_model?: string;
    credential_fields: ProviderFieldSpec[];
    field_help?: Record<string, string>;
    /** Credentials needed but collected on another card. */
    requires?: BorrowedRequirement[];
    /** Alternative sets of `credential_fields`, any one of which is enough.
     *  Azure photo redaction needs an AI Face resource for faces or an AI
     *  Vision resource for plates, and either alone is a working setup — which
     *  no per-field flag can say. */
    requires_any?: string[][];
    models_source?: 'live' | 'curated';
    models_fetched_at?: number | null;  // epoch seconds
}

export interface ProviderCatalog {
    current_provider: string;
    default_provider?: string;
    current_model?: string | null;
    current_model_available?: boolean;
    configured?: Record<string, boolean>;
    providers: ProviderInfo[];
    /** What the last recorded check found, so a hard refresh does not reset
     *  every card to "not checked yet". Absent when none has been run. */
    last_result?: {
        ok: boolean;
        detail: string;
        status?: string;
        verifiable?: boolean | null;
    } | null;
    /** Which individual credential boxes have something stored against them.
     *  The form's "Saved" hint was per provider, so once a provider counted as
     *  configured every one of its boxes claimed to be saved -- including an
     *  optional one nobody had filled in. Presence only; no values. */
    stored_fields?: Record<string, boolean>;
    /** Whether this card may change the provider. False for the secret store:
     *  every credential the town has is in the current one and repointing the
     *  setting does not move them, so the switch belongs to the cloud-profile
     *  flow, which does. Absent means yes. */
    selectable?: boolean;
}

export interface AIModelRefreshResult {
    provider: string;
    models: ProviderModelSpec[];
    source: 'live' | 'curated';
    fetched_at?: number | null;
    current_model?: string | null;
    current_model_available?: boolean;
}

export interface ProviderSave {
    provider: string;
    model?: string;
    settings?: Record<string, string>;
}

/** Every capability with a provider catalog. The last four were already
 *  switchable in the backend and had no catalog, so nothing surfaced them. */
export type Capability =
    | 'ai' | 'translation' | 'identity' | 'maps'
    | 'email' | 'sms' | 'kms' | 'redaction'
    /** Where the credentials for all of the above are kept. Reported and
     *  tested here; switched by the cloud-profile flow, which moves them. */
    | 'secrets';

export interface RetentionPreviewRecord {
    service_request_id: string | null;
    service_name: string | null;
    address: string | null;
    closed_datetime: string | null;
    age_days: number | null;
    /** How long past eligibility it already is. */
    days_past_retention: number | null;
}

export interface RetentionPreview {
    eligible: number;
    on_legal_hold: number;
    will_act_on?: number;
    mode: 'redact' | 'purge';
    /** The period this town set, in days. Null when it has not set one. */
    retention_days?: number | null;
    cutoff_date?: string | null;
    confirmation_required?: string | null;
    /** 'legal_hold' when an instance-wide hold freezes everything, or
     *  'unconfigured' when the town has not set a period and chosen what a run
     *  removes — with no period there is nothing to measure a record against. */
    blocked?: string;
    /** Why, in words a clerk can act on. Set alongside blocked. */
    detail?: string | null;
    /** What a run will actually empty, in the words the settings screen uses. */
    scrub_fields?: string[];
    /** The records themselves, oldest first. Empty under a legal hold. */
    records: RetentionPreviewRecord[];
    summary: {
        total: number;
        showing: number;
        truncated: boolean;
        retention_days: number;
        cutoff: string | null;
        oldest_age_days: number | null;
        newest_age_days: number | null;
    } | null;
    timezone?: string;
}

export interface PublicRecordsField {
    id: string;
    label: string;
    /** Identifies the person who reported it. Off unless deliberately chosen. */
    sensitive: boolean;
    selected: boolean;
    note?: string;
}

export interface PublicRecordsExportOptions {
    startDate?: string;
    endDate?: string;
    statuses?: string[];
    serviceCodes?: string[];
    requestIds?: string[];
    fields?: string[];
    includeArchived?: boolean;
}

class ApiClient {
    private token: string | null = null;
    private onUnauthorized: (() => void) | null = null;

    setToken(token: string | null) {
        this.token = token;
    }

    /** Register a callback invoked on any 401 response (e.g. auto-logout). */
    setOnUnauthorized(callback: (() => void) | null) {
        this.onUnauthorized = callback;
    }

    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<T> {
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            ...(options.headers as Record<string, string> || {}),
        };

        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        // Add Accept-Language header for automatic backend translation
        // Must match the LANGUAGE_STORAGE_KEY in TranslationContext.tsx
        const preferredLanguage = localStorage.getItem('preferred_language') || 'en';
        headers['Accept-Language'] = preferredLanguage;

        // Whether *this* request carried a session, captured before it goes
        // out. See the 401 branch below.
        const sentWithToken = !!this.token;

        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers,
        });

        if (!response.ok) {
            // Auto-logout on session expiry (401) -- but only for a request
            // that actually presented a session.
            //
            // A 401 on a request with no token does not mean "your session
            // ended". It means "that endpoint needs a login", and treating the
            // two the same is why people were being signed out at random.
            //
            // The mechanism: AuthProvider restores the token from
            // localStorage inside a useEffect. React runs child effects before
            // parent ones, so a page that loads data on mount can fire its
            // first request before the token has been put back. On the
            // protected routes ProtectedRoute holds children back until that
            // finishes, but the resident portal is public and ungated -- and
            // it calls at least one staff-only endpoint. The unauthenticated
            // request came back 401 and this handler deleted a perfectly good
            // token out of localStorage.
            //
            // Intermittent, because it is a race; "random, while navigating".
            if (response.status === 401 && this.onUnauthorized && sentWithToken) {
                this.onUnauthorized();
            }
            const error = await response.json().catch(() => ({ detail: 'Request failed' }));
            // Handle FastAPI validation errors (422)
            if (error.detail && Array.isArray(error.detail)) {
                const messages = error.detail.map((e: any) => e.msg || e.message || JSON.stringify(e)).join(', ');
                throw new Error(messages || 'Validation error');
            }
            throw new Error(error.detail || error.message || 'Request failed');
        }

        if (response.status === 204) {
            return undefined as T;
        }

        return response.json();
    }

    // Auth
    async login(username: string, password: string): Promise<AuthToken> {
        const formData = new URLSearchParams();
        formData.append('username', username);
        formData.append('password', password);

        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Login failed' }));
            throw new Error(error.detail || 'Login failed');
        }

        return response.json();
    }

    async getMe(): Promise<User> {
        return this.request<User>('/auth/me');
    }

    // Services (Public)
    async getServices(): Promise<ServiceDefinition[]> {
        return this.request<ServiceDefinition[]>('/services/');
    }

    // Services (Admin)
    async createService(data: ServiceCreate): Promise<ServiceDefinition> {
        return this.request<ServiceDefinition>('/services/', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async deleteService(id: number): Promise<void> {
        return this.request<void>(`/services/${id}`, { method: 'DELETE' });
    }

    // SLA performance per service category (staff). Only categories with an SLA
    // configured are scored; the rest are listed as uncovered.
    async getSlaPerformance(days: number = 90): Promise<SlaPerformance> {
        return this.request<SlaPerformance>(`/services/sla-performance?days=${days}`);
    }

    async updateService(id: number, data: Partial<ServiceDefinition>): Promise<ServiceDefinition> {
        return this.request<ServiceDefinition>(`/services/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    async reorderServices(order: { id: number; display_order: number }[]): Promise<{ status: string; count: number }> {
        return this.request<{ status: string; count: number }>('/services/reorder', {
            method: 'PUT',
            body: JSON.stringify({ order }),
        });
    }

    // Departments
    async getDepartments(): Promise<Department[]> {
        return this.request<Department[]>('/departments/');
    }

    async createDepartment(data: Partial<Department>): Promise<Department> {
        return this.request<Department>('/departments/', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async updateDepartment(id: number, data: Partial<Department>): Promise<Department> {
        return this.request<Department>(`/departments/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    async deleteDepartment(id: number): Promise<void> {
        return this.request<void>(`/departments/${id}`, { method: 'DELETE' });
    }

    // Service Requests (Public)
    async createRequest(data: ServiceRequestCreate): Promise<ServiceRequest> {
        return this.request<ServiceRequest>('/open311/v2/requests.json', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async getPublicRequests(status?: string, serviceCode?: string): Promise<PublicServiceRequest[]> {
        const params = new URLSearchParams();
        if (status) params.append('status', status);
        if (serviceCode) params.append('service_code', serviceCode);
        const queryString = params.toString() ? `?${params.toString()}` : '';
        return this.request<PublicServiceRequest[]>(`/open311/v2/public/requests${queryString}`);
    }

    async getPublicRequestDetail(requestId: string): Promise<PublicServiceRequest> {
        return this.request<PublicServiceRequest>(`/open311/v2/public/requests/${requestId}`);
    }

    async getPublicComments(requestId: string): Promise<RequestComment[]> {
        return this.request<RequestComment[]>(`/open311/v2/public/requests/${requestId}/comments`);
    }

    // The comment goes in the body, not the query string. Two reasons, and the
    // first one is that the query-string version never worked: the endpoint
    // declares `content` as Body(embed=True), so a POST with no body was
    // answered 422 and every comment a resident tried to leave was dropped.
    //
    // The second is why the endpoint is right to want a body. A URL is logged
    // everywhere -- the access log, the reverse proxy, the CDN, the browser's
    // history, the Referer header on the next click. A resident's comment can
    // name a neighbour or describe what happened to them, and none of those
    // places is somewhere a town has decided to keep that.
    async addPublicComment(requestId: string, content: string): Promise<RequestComment> {
        return this.request<RequestComment>(`/open311/v2/public/requests/${requestId}/comments`, {
            method: 'POST',
            body: JSON.stringify({ content }),
        });
    }

    // Service Requests (Staff)
    async getRequests(status?: string, includeDeleted?: boolean): Promise<ServiceRequest[]> {
        const params = new URLSearchParams();
        if (status) params.append('status', status);
        if (includeDeleted) params.append('include_deleted', 'true');
        const queryString = params.toString() ? `?${params.toString()}` : '';
        return this.request<ServiceRequest[]>(`/open311/v2/requests.json${queryString}`);
    }

    async getRequestDetail(requestId: string): Promise<ServiceRequestDetail> {
        return this.request<ServiceRequestDetail>(`/open311/v2/requests/${requestId}.json`);
    }

    async updateRequest(
        requestId: string,
        data: {
            status?: string;
            staff_notes?: string;
            priority?: number;
            assigned_department_id?: number;
            assigned_to?: string;
            closed_substatus?: string;
            completion_message?: string;
            completion_photo_url?: string;
            manual_priority_score?: number | null;
            flagged?: boolean;
        }
    ): Promise<ServiceRequestDetail> {
        return this.request<ServiceRequestDetail>(`/open311/v2/requests/${requestId}/status`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    /** Take one report off the public tracker and map, or put it back.
     *  Deliberately not part of updateRequest: this changes where the report is
     *  listed, not the report, and a bulk status edit must not carry it along. */
    async setPublicArchived(requestId: string, archived: boolean): Promise<ServiceRequestDetail> {
        return this.request<ServiceRequestDetail>(`/open311/v2/requests/${requestId}/public-archive`, {
            method: 'PATCH',
            body: JSON.stringify({ public_archived: archived }),
        });
    }

    async deleteRequest(requestId: string, justification: string): Promise<void> {
        return this.request<void>(`/open311/v2/requests/${requestId}`, {
            method: 'DELETE',
            body: JSON.stringify({ justification }),
        });
    }

    async restoreRequest(requestId: string): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/open311/v2/requests/${requestId}/restore`, {
            method: 'POST',
            body: JSON.stringify({}),
        });
    }

    async acceptAiPriority(requestId: string): Promise<{ message: string; priority_score: number }> {
        return this.request<{ message: string; priority_score: number }>(`/open311/v2/requests/${requestId}/accept-ai-priority`, {
            method: 'POST',
            body: JSON.stringify({}),
        });
    }

    async createManualIntake(data: ManualIntakeCreate): Promise<ServiceRequest> {
        return this.request<ServiceRequest>('/open311/v2/requests/manual', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    // Request Comments
    async getComments(requestId: number): Promise<RequestComment[]> {
        return this.request<RequestComment[]>(`/requests/${requestId}/comments`);
    }

    async createComment(
        requestId: number,
        content: string,
        visibility: 'internal' | 'external' = 'internal'
    ): Promise<RequestComment> {
        return this.request<RequestComment>(`/requests/${requestId}/comments`, {
            method: 'POST',
            body: JSON.stringify({ content, visibility }),
        });
    }

    async deleteComment(requestId: number, commentId: number): Promise<void> {
        return this.request<void>(`/requests/${requestId}/comments/${commentId}`, {
            method: 'DELETE',
        });
    }

    // Audit Log (Staff)
    async getAuditLog(requestId: string): Promise<import('../types').AuditLogEntry[]> {
        return this.request<import('../types').AuditLogEntry[]>(`/open311/v2/requests/${requestId}/audit-log`);
    }

    // Audit Log (Public)
    async getPublicAuditLog(requestId: string): Promise<import('../types').AuditLogEntry[]> {
        return this.request<import('../types').AuditLogEntry[]>(`/open311/v2/public/requests/${requestId}/audit-log`);
    }

    // Users (Admin)
    async getUsers(): Promise<User[]> {
        return this.request<User[]>('/users/');
    }

    // Staff members (accessible by any staff user)
    async getStaffMembers(): Promise<User[]> {
        return this.request<User[]>('/users/staff');
    }


    async createUser(data: UserCreate): Promise<User> {
        return this.request<User>('/users/', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    /** Browser crashes staff and residents have hit. The error screen promises
     *  a report; this is where that promise is kept. */
    async getClientErrors(limit = 50): Promise<{ errors: ClientErrorEntry[] }> {
        return this.request<{ errors: ClientErrorEntry[] }>(`/system/client-errors?limit=${limit}`);
    }

    /** What each connector is actually doing, as opposed to whether its
     *  credentials are stored. See /system/connectors/health. */
    async getConnectorHealth(): Promise<ConnectorHealthReport> {
        return this.request<ConnectorHealthReport>('/system/connectors/health');
    }

    /** "I know about this one." Stops the alert emails; leaves the badge
     *  alone. `days: 0` lifts a mute early. */
    async muteConnectorAlerts(connector: string, days?: number): Promise<{
        connector: string; muted_until: string | null; muted_level: string | null;
    }> {
        return this.request(`/system/connectors/${encodeURIComponent(connector)}/mute`, {
            method: 'POST',
            body: JSON.stringify(days === undefined ? {} : { days }),
        });
    }

    async updateUser(id: number, data: UserUpdate): Promise<User> {
        return this.request<User>(`/users/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    async deleteUser(id: number): Promise<void> {
        return this.request<void>(`/users/${id}`, { method: 'DELETE' });
    }

    async resetUserPassword(id: number, newPassword: string): Promise<User> {
        return this.request<User>(`/users/${id}/reset-password-json`, {
            method: 'POST',
            body: JSON.stringify({ new_password: newPassword }),
        });
    }

    // System Settings
    async getSettings(): Promise<SystemSettings> {
        return this.request<SystemSettings>('/system/settings');
    }

    async updateSettings(data: Partial<SystemSettings>): Promise<SystemSettings> {
        return this.request<SystemSettings>('/system/settings', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    // System Secrets (Admin)
    async getSecrets(): Promise<SystemSecret[]> {
        return this.request<SystemSecret[]>('/system/secrets');
    }

    async updateSecret(keyName: string, value: string): Promise<SystemSecret> {
        return this.request<SystemSecret>('/system/secrets', {
            method: 'POST',
            body: JSON.stringify({ key_name: keyName, key_value: value }),
        });
    }

    async syncSecrets(): Promise<{ status: string; added_secrets: string[]; count: number }> {
        return this.request('/system/secrets/sync', { method: 'POST' });
    }

    // GovTech Platform Integrations (Admin)
    async getIntegrationCatalog(): Promise<IntegrationPlatform[]> {
        return this.request<IntegrationPlatform[]>('/integrations/catalog');
    }

    async getIntegrations(): Promise<IntegrationConfig[]> {
        return this.request<IntegrationConfig[]>('/integrations');
    }

    async createIntegration(data: IntegrationSave): Promise<IntegrationConfig> {
        return this.request<IntegrationConfig>('/integrations', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async updateIntegration(id: number, data: Partial<IntegrationSave>): Promise<IntegrationConfig> {
        return this.request<IntegrationConfig>(`/integrations/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    async deleteIntegration(id: number): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/integrations/${id}`, { method: 'DELETE' });
    }

    async testIntegration(id: number): Promise<IntegrationTestResult> {
        return this.request<IntegrationTestResult>(`/integrations/${id}/test`, { method: 'POST' });
    }

    async syncIntegration(id: number): Promise<{ message: string; started?: Record<string, boolean> }> {
        return this.request<{ message: string; started?: Record<string, boolean> }>(
            `/integrations/${id}/sync`, { method: 'POST' }
        );
    }

    async regenerateIntegrationWebhookToken(id: number): Promise<IntegrationConfig & { message: string }> {
        return this.request<IntegrationConfig & { message: string }>(
            `/integrations/${id}/regenerate-webhook-token`, { method: 'POST' }
        );
    }

    /** Deployment shape the admin UI branches on (managed mode, public origin).
     *
     * Was a bare `fetch('/api/system/config')`, which sent no Authorization
     * header and bypassed the 401 handling every other call gets -- so on an
     * expired session it failed quietly and the page rendered as an unmanaged
     * deployment with no public origin, rather than sending anyone to log in. */
    async getSystemConfig(): Promise<SystemConfig> {
        return this.request<SystemConfig>('/system/config');
    }

    /** External platform records linked to one request. Staff-visible. */
    async getRequestIntegrationLinks(serviceRequestId: string): Promise<IntegrationRequestLink[]> {
        return this.request<IntegrationRequestLink[]>(
            `/integrations/requests/${serviceRequestId}/links`
        );
    }

    async syncIntegrationAssets(id: number): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/integrations/${id}/sync-assets`, { method: 'POST' });
    }

    /** Whether this deployment has an Accela developer app configured, and the
     *  callback URL registered against it. */
    async getAccelaOAuthStatus(): Promise<{ configured: boolean; redirect_uri: string; scope: string }> {
        return this.request('/integrations/accela/oauth/status');
    }

    /** Mint a signed, ten-minute authorization URL for this connection. */
    async startAccelaOAuth(integrationId: number): Promise<{ authorize_url: string; redirect_uri: string }> {
        return this.request('/integrations/accela/oauth/start', {
            method: 'POST',
            body: JSON.stringify({ integration_id: integrationId }),
        });
    }

    async getIntegrationLogs(id: number): Promise<IntegrationSyncLog[]> {
        return this.request<IntegrationSyncLog[]>(`/integrations/${id}/logs`);
    }

    // Pull the latest work-order state for one request from its linked platforms
    async refreshRequestWorkOrder(requestId: string): Promise<{ ok: boolean; detail: string }> {
        return this.request<{ ok: boolean; detail: string }>(
            `/integrations/requests/${requestId}/refresh`, { method: 'POST' }
        );
    }

    // Service providers (AI / translation / identity)
    async getProviderCatalog(capability: Capability): Promise<ProviderCatalog> {
        return this.request<ProviderCatalog>(`/system/${capability}/catalog`);
    }

    // Live-refresh an AI provider's model list from the provider itself.
    async refreshAIModels(provider: string): Promise<AIModelRefreshResult> {
        return this.request<AIModelRefreshResult>(`/system/ai/models/refresh`, {
            method: 'POST',
            body: JSON.stringify({ provider }),
        });
    }

    async saveProvider(capability: string, data: ProviderSave): Promise<{
        ok: boolean;
        provider: string;
        /** Whether the provider can actually be used. Saving a *selection*
         *  succeeds with no credentials, so ok:true is not readiness. */
        configured?: boolean;
        /** The required fields still empty, by label, when configured is false. */
        missing?: string[];
        /** Shape problems spotted in the pasted values. Advisory: the save has
         *  already happened by the time these arrive. */
        warnings?: { key: string; severity: 'error' | 'warn' | 'info'; message: string }[];
    }> {
        return this.request(`/system/providers/${capability}/save`, {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    /** `recorded: false` means the provider cannot be checked from here at all
     *  -- a generic HTTP SMS gateway cannot be exercised without sending a real
     *  text. That is not a failure, and the backend deliberately does not write
     *  it to connector health. Dropping the flag here is what made those show
     *  up as "Not working". */
    async testProvider(capability: string): Promise<{ ok: boolean; detail: string; recorded?: boolean; configured?: boolean; off?: boolean }> {
        return this.request(`/system/providers/${capability}/test`, { method: 'POST' });
    }

    /** Whether anybody has said this town is set up.
     *
     *  A marker rather than a derived answer. "Is everything configured" is the
     *  obvious proxy and it never goes true for a town that deliberately
     *  switches most things off, so the guide would greet it on every login
     *  forever. */
    async getSetupState(): Promise<{ completed: boolean; completed_at: string | null }> {
        return this.request('/system/setup/state');
    }

    /** "I am done here." */
    async markSetupComplete(): Promise<{ completed: boolean; completed_at: string | null }> {
        return this.request('/system/setup/state', { method: 'POST' });
    }

    /** Where this town's credentials are kept, and whether anyone said so.
     *
     *  `chosen: false` used to be unreachable: the backend answered "google"
     *  for a town that had never been asked, so nothing could tell a deliberate
     *  choice from silence. */
    async getSecretStore(): Promise<SecretStoreChoice> {
        return this.request<SecretStoreChoice>('/system/secrets/store');
    }

    /** Record it. Set-once — repointing the store does not move what is in the
     *  old one, so a second choice here would make every card unreadable. */
    async chooseSecretStore(store: string): Promise<SecretStoreChoice> {
        return this.request<SecretStoreChoice>('/system/secrets/store', {
            method: 'POST',
            body: JSON.stringify({ store }),
        });
    }

    /** Record which integrations the town wants, credentials aside.
     *
     *  A partial map: only what changed. The questionnaire posts the chip that
     *  was clicked, and a town that has never been asked about photo redaction
     *  must not get an answer to it from a click on backups. */
    async setCapabilitySwitches(switches: Record<string, boolean>): Promise<{ switches: Record<string, boolean> }> {
        return this.request('/system/capabilities', {
            method: 'PUT',
            body: JSON.stringify({ switches }),
        });
    }

    /** Which provider each capability is on, and which of its providers have
     *  their credentials stored. One request rather than eight, and answered
     *  per provider — "maps is configured" is not the same question as "Esri is
     *  configured", and conflating them made the setup guide skip a provider
     *  that had no credentials at all. */
    async getProviderStatus(): Promise<ProviderStatusMap> {
        return this.request<ProviderStatusMap>('/system/providers/status');
    }

    /** Whether this server already has an identity on its cloud, in which case
     *  the credential boxes for that cloud should be left empty rather than
     *  filled — the platform issues a short-lived token instead. */
    async getCloudIdentity(): Promise<CloudIdentity> {
        return this.request<CloudIdentity>('/system/providers/cloud-identity');
    }

    /* No cloud-profile methods here on purpose.
     *
     * POST /system/providers/cloud-profile applies a whole environment in one
     * choice, and part of what it sets is SECRETS_PROVIDER -- which it repoints
     * without moving anything. Every credential the town has already entered is
     * in the old store, and most have had their encrypted database copy
     * scrubbed after being verified there, so the pointer moving is enough to
     * make the mail relay, the map key and the identity provider all read as
     * absent.
     *
     * The endpoint is a deliberate operator action and stays. A button for it
     * on the setup page would be one click between a working town and an
     * apparently empty one, so it does not get a client method until the switch
     * migrates the secrets first. The page reads /providers/status instead,
     * which answers the same question -- which cloud is this town on -- from
     * what is actually in use.
     */

    // Statistics
    async getStatistics(): Promise<Statistics> {
        return this.request<Statistics>('/system/statistics');
    }

    // Advanced Statistics (PostGIS-powered)
    async getAdvancedStatistics(): Promise<AdvancedStatistics> {
        return this.request<AdvancedStatistics>('/system/advanced-statistics');
    }

    // Heatmap Data (for spatial bias detection)
    async getHeatmapData(): Promise<import('../types').HeatmapData> {
        return this.request<import('../types').HeatmapData>('/system/heatmap-data');
    }

    // AI Analytics Chat
    async analyticsChat(message: string, history: { role: string; content: string }[]): Promise<{ response: string; context_used: string[] }> {
        return this.request<{ response: string; context_used: string[] }>('/system/analytics-chat', {
            method: 'POST',
            body: JSON.stringify({ message, history }),
        });
    }

    // System Update (Admin)
    async updateSystem(): Promise<{ status: string; message: string }> {
        return this.request<{ status: string; message: string }>('/system/update', {
            method: 'POST',
        });
    }

    // Domain Configuration (Admin)
    async configureDomain(domain: string): Promise<{
        status: string;
        message: string;
        domain?: string;
        url?: string;
        reload_success?: boolean;
        next_step?: string | null;
    }> {
        return this.request(`/system/domain/configure?domain=${encodeURIComponent(domain)}`, {
            method: 'POST',
        });
    }

    async getDomainStatus(): Promise<{
        configured_domains: Array<{ domain: string; has_ssl: boolean }>;
        server_ip: string;
    }> {
        return this.request('/system/domain/status');
    }

    // GIS / Maps
    async getMapsConfig(): Promise<{
        has_google_maps: boolean;
        google_maps_api_key: string | null;
        google_maps_map_id: string | null;
        // The provider-neutral fields. `map_credentials` carries only the
        // credentials the selected provider actually uses, and
        // `map_provider_missing` is non-empty when it is not finished being set
        // up -- which is what the pages gate the map on.
        map_provider?: string | null;
        geocode_provider?: string | null;
        map_credentials?: Record<string, unknown> | null;
        map_provider_missing?: string[] | null;
        township_boundary: object | null;
        default_center: { lat: number; lng: number };
        default_zoom: number;
    }> {
        return this.request('/gis/config');
    }

    async searchOsmTownship(query: string): Promise<{
        results: Array<{
            osm_id: number;
            display_name: string;
            type: string;
            class: string;
            lat: string;
            lon: string;
            boundingbox: string[];
            geojson?: object;  // Boundary GeoJSON from Nominatim polygon_geojson=1
        }>;
    }> {
        return this.request(`/gis/osm/search?query=${encodeURIComponent(query)}`);
    }


    async fetchOsmBoundary(osmId: number): Promise<{
        geojson: object;
        osm_id: number;
    }> {
        return this.request(`/gis/osm/boundary/${osmId}`);
    }

    
    async seedRoads(force: boolean = true): Promise<{
        ok: boolean;
        segments?: number;
        source?: string;
        reason?: string;
    }> {
        return this.request("/roads/seed?force=" + force, {
            method: "POST",
        });
    }

    async saveTownshipBoundary(geojsonData: object, name?: string, centerLat?: number, centerLng?: number): Promise<{
        status: string;
        message: string;
    }> {
        const params = new URLSearchParams();
        if (name) params.append('name', name);
        if (centerLat !== undefined && centerLng !== undefined) {
            params.append('center_lat', centerLat.toString());
            params.append('center_lng', centerLng.toString());
        }
        const queryString = params.toString();
        const url = queryString ? `/gis/township-boundary?${queryString}` : '/gis/township-boundary';
        return this.request(url, {
            method: 'POST',
            body: JSON.stringify(geojsonData),
        });
    }


    async geocodeAddress(address: string): Promise<{
        lat: number;
        lng: number;
        formatted_address: string;
        place_id?: string;
    } | null> {
        try {
            return await this.request(`/gis/geocode?address=${encodeURIComponent(address)}`);
        } catch {
            return null;
        }
    }

    /**
     * Would a report at this point be redirected to another agency, and which
     * road decided that? The create endpoint re-evaluates the same rules
     * server-side, so this is a courtesy that lets someone find out before they
     * type a description -- never the enforcement point.
     */
    async roadCheck(serviceCode: string, lat?: number | null, lng?: number | null): Promise<{
        blocked: boolean;
        block_type: string | null;
        jurisdiction: string | null;
        message: string;
        /** True when `message` is the generated sentence, not the clerk's own. */
        message_is_default?: boolean;
        contacts: { name?: string; phone?: string; email?: string; url?: string }[];
        road: string | null;
        detected_road: { name: string; distance_m: number } | null;
    }> {
        return this.request('/road-check', {
            method: 'POST',
            body: JSON.stringify({ service_code: serviceCode, lat: lat ?? null, long: lng ?? null }),
        });
    }

    /** GeoJSON for the selected roads, one feature per segment so a clerk can
     *  switch an individual piece off. Never dissolved. */
    async getRoadGeometry(names: string[]): Promise<{
        type: 'FeatureCollection';
        available?: boolean;
        features: {
            type: 'Feature';
            geometry: { type: string; coordinates: number[][] };
            properties: { segment_id: number; feature_id: string; name: string | null; ref: string | null };
        }[];
    }> {
        return this.request(`/roads/geometry?names=${encodeURIComponent(names.join(','))}`);
    }

    /** Corridors that run alongside each other at a given width. Crossings are
     *  not reported -- every intersection overlaps. */
    async checkCorridors(routingConfig: Record<string, unknown>, corridorM: number): Promise<{
        available?: boolean;
        corridor_metres: number;
        issues: { severity: 'error' | 'warning' | 'info'; kind: string; message: string; roads: string[] }[];
    }> {
        return this.request(`/roads/corridor-check?corridor_m=${corridorM}`, {
            method: 'POST',
            body: JSON.stringify({ routing_config: routingConfig }),
        });
    }

    async setCorridorWidth(corridorMetres: number): Promise<{ corridor_metres: number }> {
        return this.request('/roads/corridor-width', {
            method: 'PUT',
            body: JSON.stringify({ corridor_metres: corridorMetres }),
        });
    }

    /** Distinct road NAMES for the clerk's routing autocomplete, not segments. */
    async searchRoads(q: string): Promise<{
        available: boolean;
        roads: { name: string; ref: string | null; segments: number }[];
    }> {
        return this.request(`/roads/search?q=${encodeURIComponent(q)}`);
    }

    /** Conflicts in a routing config, checked before it can be saved. */
    async checkRoutingConfig(routingConfig: Record<string, unknown>): Promise<{
        issues: { severity: 'error' | 'warning' | 'info'; kind: string; message: string; roads: string[] }[];
        can_save: boolean;
        roads_known: number;
    }> {
        return this.request('/roads/config-check', {
            method: 'POST',
            body: JSON.stringify({ routing_config: routingConfig }),
        });
    }

    /** Redirect counts for the statistics page. */
    async getRedirectedStatistics(days = 30): Promise<{
        days: number;
        total: number;
        road_based: number;
        category: number;
        by_jurisdiction: { label: string; count: number }[];
        by_road: { label: string; count: number }[];
        by_service: { label: string; count: number }[];
    }> {
        return this.request(`/system/statistics/redirected?days=${days}`);
    }

    async reverseGeocode(lat: number, lng: number): Promise<{
        lat: number;
        lng: number;
        formatted_address: string;
    }> {
        return this.request(`/gis/reverse-geocode?lat=${lat}&lng=${lng}`);
    }


    async searchCensusBoundary(townName: string, stateAbbr: string, layerType: string = 'township'): Promise<{
        results: Array<{
            name: string;
            full_name: string;
            geoid: string;
            state: string;
            layer_type: string;
            geometry: any;
        }>;
        message?: string;
    }> {
        return this.request(`/gis/census-boundary-search?town_name=${encodeURIComponent(townName)}&state_abbr=${stateAbbr}&layer_type=${layerType}`);
    }

    async saveCensusBoundary(name: string, geometry: any): Promise<{ status: string; message: string }> {
        return this.request(`/gis/boundaries/save-census?name=${encodeURIComponent(name)}`, {
            method: 'POST',
            body: JSON.stringify(geometry),
        });
    }

    // ========== Map Layers ==========

    async getMapLayers(): Promise<MapLayer[]> {
        return this.request('/map-layers/');
    }

    async getAllMapLayers(): Promise<MapLayer[]> {
        return this.request('/map-layers/all');
    }

    async createMapLayer(layerData: {
        name: string;
        description?: string;
        layer_type?: string;
        fill_color?: string;
        stroke_color?: string;
        fill_opacity?: number;
        stroke_width?: number;
        service_codes?: string[];
        geojson: object;
        routing_mode?: string;
        routing_config?: object | null;
        visible_on_map?: boolean;
    }): Promise<MapLayer> {
        return this.request('/map-layers/', {
            method: 'POST',
            body: JSON.stringify(layerData),
        });
    }

    async updateMapLayer(layerId: number, layerData: {
        name?: string;
        description?: string;
        layer_type?: string;
        fill_color?: string;
        stroke_color?: string;
        fill_opacity?: number;
        stroke_width?: number;
        is_active?: boolean;
        service_codes?: string[];
        geojson?: object;
        routing_mode?: string;
        routing_config?: object | null;
        visible_on_map?: boolean;
    }): Promise<MapLayer> {
        return this.request(`/map-layers/${layerId}`, {
            method: 'PUT',
            body: JSON.stringify(layerData),
        });
    }

    async deleteMapLayer(layerId: number): Promise<void> {
        return this.request(`/map-layers/${layerId}`, {
            method: 'DELETE',
        });
    }

    // Image Upload
    async uploadImage(file: File): Promise<{ url: string; filename: string }> {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${API_BASE}/system/upload/image`, {
            method: 'POST',
            headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
            body: formData,
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || 'Upload failed');
        }

        return response.json();
    }

    // Asset Related Requests
    async getAssetRelatedRequests(assetId: string, excludeRequestId?: string): Promise<{
        service_request_id: string;
        service_name: string;
        status: string;
        requested_datetime: string;
        address: string;
        description: string;
    }[]> {
        const params = new URLSearchParams();
        if (excludeRequestId) params.append('exclude_request_id', excludeRequestId);
        const queryString = params.toString() ? `?${params.toString()}` : '';
        return this.request(`/open311/v2/requests/asset/${encodeURIComponent(assetId)}/related${queryString}`);
    }

    // Notification Preferences
    async getNotificationPreferences(): Promise<NotificationPreferences> {
        return this.request<NotificationPreferences>('/users/me/notification-preferences');
    }

    async updateNotificationPreferences(prefs: Partial<NotificationPreferences>): Promise<NotificationPreferences> {
        return this.request<NotificationPreferences>('/users/me/notification-preferences', {
            method: 'PUT',
            body: JSON.stringify(prefs),
        });
    }

    // Research Suite endpoints
    async getResearchStatus(): Promise<ResearchStatus> {
        return this.request<ResearchStatus>('/research/status');
    }

    async getResearchAnalytics(params?: { start_date?: string; end_date?: string; service_code?: string }): Promise<ResearchAnalytics> {
        const queryParams = new URLSearchParams();
        if (params?.start_date) queryParams.append('start_date', params.start_date);
        if (params?.end_date) queryParams.append('end_date', params.end_date);
        if (params?.service_code) queryParams.append('service_code', params.service_code);
        const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';
        return this.request<ResearchAnalytics>(`/research/analytics${queryString}`);
    }

    async getResearchCodeSnippets(): Promise<ResearchCodeSnippets> {
        return this.request<ResearchCodeSnippets>('/research/code-snippets');
    }

    async exportResearchCSV(params?: { start_date?: string; end_date?: string; service_code?: string; privacy_mode?: string }): Promise<Blob> {
        const queryParams = new URLSearchParams();
        if (params?.start_date) queryParams.append('start_date', params.start_date);
        if (params?.end_date) queryParams.append('end_date', params.end_date);
        if (params?.service_code) queryParams.append('service_code', params.service_code);
        if (params?.privacy_mode) queryParams.append('privacy_mode', params.privacy_mode);
        const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

        const response = await fetch(`${API_BASE}/research/export/csv${queryString}`, {
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
        });
        if (!response.ok) throw new Error('Export failed');
        return response.blob();
    }

    async exportResearchGeoJSON(params?: { start_date?: string; end_date?: string; service_code?: string; privacy_mode?: string }): Promise<Blob> {
        const queryParams = new URLSearchParams();
        if (params?.start_date) queryParams.append('start_date', params.start_date);
        if (params?.end_date) queryParams.append('end_date', params.end_date);
        if (params?.service_code) queryParams.append('service_code', params.service_code);
        if (params?.privacy_mode) queryParams.append('privacy_mode', params.privacy_mode);
        const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

        const response = await fetch(`${API_BASE}/research/export/geojson${queryString}`, {
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
        });
        if (!response.ok) throw new Error('Export failed');
        return response.blob();
    }

    /** JSON data dictionary — packs and fields as the server actually exports
     *  them (per-pack switches applied), so the Research Lab renders truth
     *  rather than a hardcoded copy. */
    async getResearchDataDictionary(): Promise<ResearchDataDictionary> {
        return this.request<ResearchDataDictionary>('/research/data-dictionary');
    }

    /** Admin only: every pack (including disabled ones) with its field list,
     *  for the Admin Console toggles. */
    async getResearchPacks(): Promise<ResearchPackSwitch[]> {
        return this.request<ResearchPackSwitch[]>('/research/packs');
    }

    async exportDataDictionary(): Promise<Blob> {
        const response = await fetch(`${API_BASE}/research/export/data-dictionary`, {
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
        });
        if (!response.ok) throw new Error('Export failed');
        return response.blob();
    }

    // Research AI Chat
    async researchChat(message: string, history: { role: string; content: string }[]): Promise<{ response: string; context_used: string[] }> {
        return this.request<{ response: string; context_used: string[] }>('/research/chat', {
            method: 'POST',
            body: JSON.stringify({ message, history }),
        });
    }

    // ========== Document Retention ==========

    async getRetentionPolicy(): Promise<RetentionPolicyConfig> {
        return this.request<RetentionPolicyConfig>('/system/retention/policy');
    }

    async updateRetentionPolicy(params: {
        /** How long a closed request is kept. 0 clears it, which stops
         *  retention running — there is no schedule to fall back to. */
        retention_days?: number;
        mode?: 'redact' | 'purge';
        scrub_fields?: string[];
    }): Promise<{
        status: string; configured: boolean; reason: string | null;
        detail: string | null; retention_days: number | null; mode: string;
    }> {
        const queryParams = new URLSearchParams();
        if (params.retention_days !== undefined) queryParams.append('retention_days', params.retention_days.toString());
        if (params.mode) queryParams.append('mode', params.mode);
        // Repeated key rather than a joined string: FastAPI reads a list that
        // way, and an empty selection has to survive the trip as an explicit
        // "none" rather than vanishing.
        (params.scrub_fields || []).forEach(f => queryParams.append('scrub_fields', f));
        return this.request(`/system/retention/policy?${queryParams.toString()}`, { method: 'POST' });
    }

    async getTownTimezone(): Promise<{
        timezone: string; offset: string; configured: boolean;
        common: { id: string; offset: string }[];
    }> {
        return this.request('/system/timezone');
    }

    async setTownTimezone(timezone: string): Promise<{ timezone: string; offset: string }> {
        return this.request('/system/timezone', { method: 'POST', body: JSON.stringify({ timezone }) });
    }

    /** What "Run now" would actually do, before it does it. */
    /** What "Run now" would do, and the records it would do it to. */
    async previewRetentionRun(limit = 50): Promise<RetentionPreview> {
        return this.request<RetentionPreview>(`/system/retention/preview?limit=${limit}`);
    }

    async runRetentionNow(confirm?: string): Promise<{ status: string; task_id: string; message: string }> {
        return this.request('/system/retention/run', {
            method: 'POST',
            body: JSON.stringify(confirm ? { confirm } : {}),
        });
    }

    async getLegalHoldRequests(): Promise<{
        count: number;
        requests: Array<{
            id: number;
            service_request_id: string;
            service_name: string;
            description: string;
            status: string;
            address: string;
            requested_datetime: string;
            closed_datetime: string | null;
        }>;
    }> {
        return this.request('/system/retention/legal-hold');
    }

    /** The catalog a records custodian picks from, and which fields are sensitive. */
    async getPublicRecordsFields(): Promise<{ fields: PublicRecordsField[] }> {
        return this.request<{ fields: PublicRecordsField[] }>('/system/retention/export/fields');
    }

    async exportForPublicRecords(options: PublicRecordsExportOptions = {}): Promise<void> {
        const params = new URLSearchParams();
        if (options.startDate) params.append('start_date', options.startDate);
        if (options.endDate) params.append('end_date', options.endDate);
        // Repeated keys, which is how FastAPI reads a List[str] query param.
        options.statuses?.forEach(v => params.append('statuses', v));
        options.serviceCodes?.forEach(v => params.append('service_codes', v));
        options.requestIds?.forEach(v => params.append('request_ids', v));
        options.fields?.forEach(v => params.append('fields', v));
        if (options.includeArchived === false) params.append('include_archived', 'false');
        const queryString = params.toString() ? `?${params.toString()}` : '';

        const response = await fetch(`/api/system/retention/export${queryString}`, {
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
        });

        if (!response.ok) throw new Error('Export failed');

        // Trigger download
        const blob = await response.blob();
        const contentDisposition = response.headers.get('Content-Disposition');
        const filename = contentDisposition?.match(/filename=(.+)/)?.[1] || 'public_records_export.csv';
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        window.URL.revokeObjectURL(url);
    }

    // ========== Database Backups ==========

    async getBackupStatus(): Promise<BackupStatus> {
        return this.request<BackupStatus>('/system/backups/status');
    }

    async listBackups(): Promise<BackupList> {
        return this.request<BackupList>('/system/backups');
    }

    async createBackup(): Promise<BackupResult> {
        return this.request<BackupResult>('/system/backups/create', { method: 'POST' });
    }

    async deleteBackup(backupName: string): Promise<{ status: string; deleted: string }> {
        return this.request(`/system/backups/${encodeURIComponent(backupName)}`, { method: 'DELETE' });
    }

    async cleanupBackups(): Promise<{ status: string; deleted_count: number; deleted: string[] }> {
        return this.request('/system/backups/cleanup', { method: 'POST' });
    }

    // ========== Setup ==========

    async getSetupStatus(): Promise<{
        auth0_configured: boolean;
        gcp_configured: boolean;
        auth0_details?: any;
        gcp_details?: any;
    }> {
        return this.request('/setup/status');
    }

    async configureAuth0(data: {
        domain: string;
        management_client_id: string;
        management_client_secret: string;
        callback_url: string;
    }): Promise<{
        success: boolean;
        message: string;
        domain: string;
        client_id: string;
    }> {
        return this.request('/setup/auth0/configure', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async configureGCP(data: {
        project_id: string;
        service_account_json: string;
    }): Promise<{
        success: boolean;
        message: string;
        project_id: string;
    }> {
        return this.request('/setup/gcp/configure', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async verifySetup(): Promise<{
        auth0: { configured: boolean; reachable: boolean; error: string | null; domain?: string };
        gcp: { configured: boolean; reachable: boolean; error: string | null };
    }> {
        return this.request('/setup/verify', { method: 'POST' });
    }

    async reencryptPii(): Promise<{
        reencrypted: number;
        rows: number;
        fields: number;
        errors: number;
    }> {
        return this.request('/setup/reencrypt-pii', { method: 'POST' });
    }

    /** What is not yet on the storage this town chose. Drives one advisory
     *  line; the work itself happens on a schedule without being asked. */
    async getStorageStatus(): Promise<StorageStatus> {
        return this.request<StorageStatus>('/setup/storage-status');
    }

    /** Generate and store a backup passphrase. Returns it once, in the clear,
     *  because a copy has to end up somewhere other than this server. */
    async generateBackupKey(): Promise<{ key: string }> {
        return this.request('/setup/backup-key', { method: 'POST' });
    }

    // ========== Health Dashboard & Runbook (Bus Factor Mitigation) ==========

    async getHealthDashboard(): Promise<HealthDashboard> {
        return this.request<HealthDashboard>('/system/health-dashboard');
    }

    // Proactive (leading-indicator) health — per-check detail for admins.
    async getProactiveHealth(): Promise<ProactiveHealth> {
        return this.request<ProactiveHealth>('/health/proactive');
    }

    async executeRunbook(action: string, backupName?: string): Promise<RunbookResult> {
        const params = backupName ? `?backup_name=${encodeURIComponent(backupName)}` : '';
        return this.request<RunbookResult>(`/system/runbook/${action}${params}`, { method: 'POST' });
    }

    // ========== Secret Manager Migration ==========

    async migrateToSecretManager(): Promise<{
        status: string;
        migrated: number;
        migrated_keys: string[];
        scrubbed: number;
        scrubbed_keys: string[];
        skipped: number;
        skipped_keys: string[];
        failed: number;
        failed_keys: Array<{ key: string; error: string }>;
        reason?: string;
        error?: string;
    }> {
        return this.request('/system/secrets/migrate-to-secret-manager', { method: 'POST' });
    }

    // ========== Uptime Monitoring ==========

    async getUptimeHistory(hours: number = 24): Promise<UptimeHistory> {
        return this.request<UptimeHistory>(`/health/uptime/history?hours=${hours}`);
    }

    async getUptimeStats(): Promise<UptimeStats> {
        return this.request<UptimeStats>('/health/uptime/stats');
    }

    async triggerUptimeCheck(): Promise<{
        checked: number;
        results: Record<string, { status: string; response_time_ms: number }>;
    }> {
        return this.request('/health/uptime/check-now', { method: 'POST' });
    }

    // ========== API Cost Tracking ==========

    async getCostEstimate(days: number = 30): Promise<CostEstimate> {
        return this.request<CostEstimate>(`/system/api-usage/cost-estimate?days=${days}`);
    }

    async getApiUsage(days: number = 30, service?: string): Promise<ApiUsageResponse> {
        const params = new URLSearchParams();
        params.append('days', days.toString());
        if (service) params.append('service', service);
        return this.request<ApiUsageResponse>(`/system/api-usage/usage?${params.toString()}`);
    }

    async getDailyUsage(days: number = 30): Promise<DailyUsageResponse> {
        return this.request<DailyUsageResponse>(`/system/api-usage/daily?days=${days}`);
    }

    async getApiPricing(): Promise<Record<string, any>> {
        return this.request<Record<string, any>>('/system/api-usage/pricing');
    }


    // Data Export
    async exportRequests(params: {
        format?: 'csv' | 'json' | 'geojson';
        start_date?: string;
        end_date?: string;
        status?: string;
        category_id?: number;
        include_pii?: boolean;
    } = {}): Promise<void> {
        const query = new URLSearchParams();
        if (params.format) query.append('format', params.format);
        if (params.start_date) query.append('start_date', params.start_date);
        if (params.end_date) query.append('end_date', params.end_date);
        if (params.status) query.append('status', params.status);
        if (params.category_id) query.append('category_id', params.category_id.toString());
        if (params.include_pii !== undefined) query.append('include_pii', params.include_pii.toString());

        const response = await fetch(`${API_BASE}/export/requests?${query}`, {
            headers: {
                'Authorization': `Bearer ${this.token}`,
            },
        });

        if (!response.ok) {
            throw new Error('Export failed');
        }

        // Trigger download
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition');
        const filenameMatch = disposition?.match(/filename=(.+)/);
        const filename = filenameMatch?.[1] || `export.${params.format || 'csv'}`;

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();
    }

    async exportStatistics(params: {
        format?: 'csv' | 'json';
        start_date?: string;
        end_date?: string;
    } = {}): Promise<void> {
        const query = new URLSearchParams();
        if (params.format) query.append('format', params.format);
        if (params.start_date) query.append('start_date', params.start_date);
        if (params.end_date) query.append('end_date', params.end_date);

        const response = await fetch(`${API_BASE}/export/statistics?${query}`, {
            headers: {
                'Authorization': `Bearer ${this.token}`,
            },
        });

        if (!response.ok) {
            throw new Error('Export failed');
        }

        // Trigger download
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition');
        const filenameMatch = disposition?.match(/filename=(.+)/);
        const filename = filenameMatch?.[1] || `statistics.${params.format || 'json'}`;

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();
    }

}


// Notification Preferences type
export interface NotificationPreferences {
    email_new_requests: boolean;
    email_status_changes: boolean;
    email_comments: boolean;
    email_assigned_only: boolean;
    sms_new_requests: boolean;
    sms_status_changes: boolean;
    phone: string | null;
}

// Research Suite types
export interface ResearchAnalytics {
    total_requests: number;
    status_distribution: Record<string, number>;
    avg_resolution_hours: number | null;
    category_distribution: Array<{ code: string; name: string; count: number }>;
    source_distribution: Record<string, number>;
    filters_applied: {
        start_date: string | null;
        end_date: string | null;
        service_code: string | null;
    };
}

export interface ResearchCodeSnippets {
    python: string;
    r: string;
}

export interface ResearchStatus {
    enabled: boolean;
    user: string;
    role: string;
}

export interface ResearchDictionaryField {
    name: string;
    type: string;
    description: string;
}

export interface ResearchPackInfo {
    label: string;
    audience: string;
    default_on: boolean;
    enabled: boolean;
    fields: ResearchDictionaryField[];
    suggested_analyses: string[];
    why_default_off?: string;
}

/** GET /research/data-dictionary — the fields and packs as the server actually
 *  exports them, per-pack switches already applied. */
export interface ResearchDataDictionary {
    version: string;
    fields: Record<string, { type: string; description: string; research_pack: string }>;
    core_fields: ResearchDictionaryField[];
    research_packs: Record<string, ResearchPackInfo>;
    privacy: { fuzzed_mode: string; exact_mode: string };
    sentiment_method?: string;
}

/** GET /research/packs (admin) — every pack including disabled ones, for the
 *  Admin Console toggles. */
export interface ResearchPackSwitch {
    id: string;
    label: string;
    audience: string;
    enabled: boolean;
    default_on: boolean;
    contains: string[];
    why_default_off?: string;
}

export const api = new ApiClient();
export default api;

// Type for MapLayer
export interface MapLayer {
    id: number;
    name: string;
    description?: string;
    layer_type?: string;
    fill_color: string;
    stroke_color: string;
    fill_opacity: number;
    stroke_width: number;
    geojson: object;
    is_active: boolean;
    show_on_resident_portal: boolean;
    service_codes?: string[];  // Categories this layer applies to (empty = all)
    routing_mode?: 'none' | 'log' | 'block';  // Polygon behavior
    visible_on_map?: boolean;  // Whether to render the layer visually
    routing_config?: {
        message?: string;
        contacts?: { name: string; phone: string; url: string }[];
    };
    created_at?: string;
    updated_at?: string;
}

// Document Retention types
//
// There is no RetentionState any more. It described one row of a table of
// retention periods and public-records statutes for all 51 US jurisdictions
// that nobody had verified, and it fed a state picker. A municipality's
// retention schedule comes from its own clerk.

export interface ScrubField {
    id: string;
    label: string;
    detail: string;
    selected: boolean;
}

export interface RetentionPolicyConfig {
    /** Whether this town has set both halves: how long a closed request is
     *  kept, and what a run removes when that expires. False means nothing is
     *  being archived or deleted at all — and therefore that resident personal
     *  data is being kept indefinitely, which is what `detail` says in words. */
    configured: boolean;
    /** 'no_settings' | 'no_period' | 'no_fields', when not configured. */
    reason?: string | null;
    /** Plain-language explanation: what is not happening, what that costs, and
     *  where to go. The only place that sentence exists. */
    detail?: string | null;
    /** The period the town set, in days. Present even when unconfigured, so
     *  the screen can render the half-finished form it is asking about. */
    retention_days: number | null;
    /** The catalog and this town's choice in one object, so the screen never
     *  holds its own copy of what the fields are called. Nothing is selected
     *  until the town selects it. */
    scrub_fields?: ScrubField[];
    mode: 'redact' | 'purge';
    stats: {
        retention_days: number;
        cutoff_date: string;
        eligible_for_archival: number;
        under_legal_hold: number;
        already_archived: number;
        next_run: string;
    } | null;
}

// Database Backup types
export interface BackupStatus {
    configured: boolean;
    message?: string;
    required_secrets?: string[];
    optional_secrets?: string[];
    bucket?: string;
    endpoint?: string;
    last_backup?: {
        name: string;
        size_bytes: number;
        created_at: string;
        age_days: number;
    } | null;
    total_backups?: number;
    next_scheduled?: string;
}

export interface BackupList {
    status: string;
    count: number;
    message?: string;
    backups: Array<{
        name: string;
        size_bytes: number;
        created_at: string;
        age_days: number;
    }>;
}

export interface BackupResult {
    status: string;
    message?: string;
    backup_name?: string;
    size_bytes?: number;
    unencrypted_size_bytes?: number;
    created_at?: string;
    bucket?: string;
}

// Health Dashboard (Bus Factor Mitigation)
export interface SlaCategory {
    service_code: string;
    service_name: string;
    sla_hours: number;
    resolved: number;
    met: number;
    breached: number;
    open_overdue: number;
    open_at_risk: number;
    open_on_track: number;
    /** null when nothing has been resolved yet — unknown, not 0%. */
    compliance_rate: number | null;
    avg_resolution_hours: number | null;
    avg_vs_target_hours: number | null;
}

export interface SlaPerformance {
    period_days: number;
    generated_at: string;
    overall: {
        categories_with_sla: number;
        resolved: number;
        met: number;
        breached: number;
        open_overdue: number;
        open_at_risk: number;
        compliance_rate: number | null;
    };
    categories: SlaCategory[];
    categories_without_sla: { service_code: string; service_name: string }[];
}

export interface HealthCheck {
    key: string;
    label: string;
    status: 'ok' | 'warning' | 'critical' | 'unknown';
    value: number | null;
    message: string;
    action: string;
}

export interface HealthSummary {
    level: 'ok' | 'warning' | 'critical';
    label: string;
    detail: string;
}

/** An identity attached to the compute by the cloud itself. When present, the
 *  listed keys need no value — and leaving them empty is the better answer, not
 *  merely an allowed one. */
/** Per capability: the provider in use, and which providers are set up.
 *
 *  `current_provider` is what dispatch resolves, not what is stored — photo
 *  redaction infers its detector from the moderation and AI settings, so a
 *  blank secret there does not mean no detector.
 *
 *  `ready` is the whole question the setup checklist asks: does the provider
 *  this capability is actually on have what it needs. Computed on the server so
 *  there is one answer. The page used to work it out from hard-coded secret
 *  names ORed across providers and disagreed with this endpoint in both
 *  directions. */
export type ProviderStatusMap = Record<string, {
    current_provider?: string | null;
    configured?: Record<string, boolean>;
    /** Whether the town wants this at all, independent of whether it is set up.
     *
     *  The third fact, and the one that had nowhere to live: wanted-ness was a
     *  `Set<string>` in the setup page's React state, initialised to
     *  everything, so unticking a feature hid part of the guide and switched
     *  nothing off. Reported beside `configured` rather than folded into it,
     *  because "switched off with the key still saved" and "never set up" are
     *  different states and looked identical.
     *
     *  `backups` and `errors` appear here with only this field — they are
     *  switchable but have no provider to choose. */
    enabled?: boolean;
    ready?: boolean;
}>;

/** Where a town's credentials are kept, and whether it was asked.
 *
 *  The gate on the setup page. A credential saved before this is answered lands
 *  in the encrypted database; the live row is later swept into the store and
 *  scrubbed, but a database backup taken in between keeps it, and backups go
 *  off-site. So the question is asked first. `database` is one of the answers --
 *  the gate is about consent, not about having a cloud vault. */
export interface SecretStoreChoice {
    chosen: boolean;
    store: string | null;
    options: string[];
    /** Whether the chosen store can be contacted. Not a blocker: the
     *  credentials that make a vault reachable are entered on the same page. */
    reachable: boolean;
}

export interface CloudIdentity {
    attached: boolean;
    provider: string | null;
    identity: string | null;
    skippable_keys: string[];
}

/** Counts of what has not yet reached the storage the town selected. */
export interface StorageStatus {
    secrets: { count: number; store: string | null; reachable: boolean };
    pii: { total: number; stale: number; on_application_key: number; legacy: number; current: string | null };
    needs_attention: boolean;
}

export interface ProactiveHealth {
    overall_status: 'ok' | 'warning' | 'critical';
    summary: HealthSummary;
    checks: HealthCheck[];
    timestamp: string;
}

export interface HealthDashboard {
    timestamp: string;
    overall_status: 'healthy' | 'degraded' | 'critical';
    services: Record<string, {
        status: 'running' | 'stopped' | 'unknown';
        uptime?: string;
        error?: string;
    }>;
    database: {
        status: 'healthy' | 'error';
        size?: string;
        connections?: number;
        error?: string;
    };
    cache: {
        status: 'healthy' | 'error' | 'not_configured';
        used_memory?: string;
        connected_clients?: number;
        error?: string;
    };
    last_backup: {
        name?: string;
        created?: string;
        size?: string;
        status?: string;
    } | null;
}

export interface RunbookResult {
    action: string;
    executed_by: string;
    timestamp: string;
    status: 'success' | 'error' | 'timeout' | 'skipped' | 'partial' | 'unavailable';
    details: Record<string, unknown>;
}

// Uptime Monitoring types
export interface UptimeHistory {
    period_hours: number;
    since: string;
    services: Record<string, Array<{
        status: string;
        response_time_ms: number | null;
        error: string | null;
        checked_at: string;
    }>>;
}

/** One service over one period.
 *
 * `reliable` is false when too little of the period was sampled to draw a
 * conclusion. The sampler runs inside the backend, so a backend outage leaves
 * a hole rather than a run of "down" rows -- and a percentage computed over
 * the rows that exist gets *higher* the worse the outage was. Never print
 * `uptime_percent` as a headline without checking this. */
export interface UptimePeriod {
    uptime_percent: number;
    checks: number;
    healthy: number;
    expected_checks?: number;
    missed_checks?: number;
    coverage_percent?: number;
    reliable?: boolean;
    summary?: string;
}

export interface UptimeStats {
    services: Record<string, {
        '24h'?: UptimePeriod;
        '7d'?: UptimePeriod;
        '30d'?: UptimePeriod;
    }>;
}

// API Cost Tracking types
export interface ServiceUsage {
    tokens_input: number;
    tokens_output: number;
    characters: number;
    api_calls: number;
    record_count: number;
}

export interface ServiceCost {
    description: string;
    usage: ServiceUsage;
    estimated_cost: number;
    pricing_info: Record<string, unknown>;
}

export interface CostEstimate {
    period_days: number;
    services: Record<string, ServiceCost>;
    total_estimated_cost: number;
    monthly_projection: number;
    pricing_disclaimer: string;
}

export interface ApiUsageResponse {
    period_days: number;
    services: Record<string, ServiceUsage>;
    available_services: string[];
}

export interface DailyUsageResponse {
    period_days: number;
    data: Array<{
        date: string;
        service_name: string;
        tokens_input: number;
        tokens_output: number;
        characters: number;
        api_calls: number;
    }>;
}
