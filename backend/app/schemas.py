from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============ Enums ============
class UserRole(str, Enum):
    admin = "admin"
    staff = "staff"
    # The researcher role exists throughout the product -- get_current_researcher
    # gates /research on it, the Research Portal module toggle advertises it, and
    # the admin user editor offers it -- but it was missing from this enum, so
    # saving anyone as a researcher returned 422 and the role could never be
    # assigned to anybody. The whole Research Lab was admin-only in practice.
    researcher = "researcher"


class RequestStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


class ClosedSubstatus(str, Enum):
    no_action = "no_action"  # No action needed
    resolved = "resolved"    # Issue resolved
    third_party = "third_party"  # Third party support contacted


class CommentVisibility(str, Enum):
    internal = "internal"  # Staff only
    external = "external"  # Visible to resident


class RequestSource(str, Enum):
    resident_portal = "resident_portal"
    phone = "phone"
    walk_in = "walk_in"
    email = "email"


# ============ Auth ============
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


# ============ User ============
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    full_name: Optional[str] = None
    role: UserRole = UserRole.staff


class UserCreate(UserBase):
    department_ids: Optional[List[int]] = []


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    department_ids: Optional[List[int]] = None
    # A column on User, used for staff SMS alerts, that was missing here -- so
    # a staff member's phone number could be set at creation and never changed.
    phone: Optional[str] = None


class DepartmentBrief(BaseModel):
    id: int
    name: str
    
    class Config:
        from_attributes = True


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: Optional[datetime] = None
    departments: List[DepartmentBrief] = []
    notification_preferences: Optional[Dict[str, bool]] = None
    phone: Optional[str] = None

    class Config:
        from_attributes = True


class NotificationPreferencesUpdate(BaseModel):
    """Schema for updating staff notification preferences"""
    email_new_requests: Optional[bool] = None
    email_status_changes: Optional[bool] = None
    email_comments: Optional[bool] = None
    email_assigned_only: Optional[bool] = None
    sms_new_requests: Optional[bool] = None
    sms_status_changes: Optional[bool] = None
    phone: Optional[str] = None  # For SMS notifications


# ============ Department ============
class DepartmentBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    routing_email: Optional[EmailStr] = None


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentResponse(DepartmentBase):
    id: int
    is_active: bool

    class Config:
        from_attributes = True


# ============ Service Definition ============
class ServiceBase(BaseModel):
    service_code: str = Field(..., min_length=2, max_length=50)
    service_name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    icon: str = "AlertCircle"


class ServiceCreate(ServiceBase):
    department_ids: Optional[List[int]] = []
    routing_mode: Optional[str] = "township"  # township, third_party, road_based
    routing_config: Optional[Dict[str, Any]] = {}
    assigned_department_id: Optional[int] = None
    display_order: Optional[int] = 0
    # Optional SLA target in hours; omit or null for no SLA.
    sla_hours: Optional[int] = Field(default=None, ge=1, le=8760)


class ServiceUpdate(BaseModel):
    service_name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    is_active: Optional[bool] = None
    department_ids: Optional[List[int]] = None
    routing_mode: Optional[str] = None
    routing_config: Optional[Dict[str, Any]] = None
    assigned_department_id: Optional[int] = None
    display_order: Optional[int] = None
    # Send 0 to explicitly clear the SLA (omitting it leaves the value unchanged).
    sla_hours: Optional[int] = Field(default=None, ge=0, le=8760)


class ServiceResponse(ServiceBase):
    id: int
    is_active: bool
    display_order: int = 0
    sla_hours: Optional[int] = None
    departments: List[DepartmentResponse] = []
    routing_mode: Optional[str] = "township"
    routing_config: Optional[Dict[str, Any]] = {}
    assigned_department_id: Optional[int] = None
    assigned_department: Optional[DepartmentResponse] = None

    class Config:
        from_attributes = True


class ServiceReorderItem(BaseModel):
    id: int
    display_order: int


class ServiceReorderRequest(BaseModel):
    order: List[ServiceReorderItem]


# ============ Service Request (Open311) ============
class ServiceRequestCreate(BaseModel):
    service_code: str
    description: str = Field(..., min_length=10)
    address: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None
    preferred_language: Optional[str] = Field(default="en", max_length=10)  # ISO 639-1 code
    media_urls: Optional[List[str]] = []  # Up to 3 photo URLs/base64
    matched_asset: Optional[Dict[str, Any]] = None  # Nearby asset from map layers
    custom_fields: Optional[Dict[str, Any]] = {} # Standard custom question responses
    # Resident's choice: show this report in the public feed/map (default), or
    # keep it unlisted — still reachable by its tracking link, still worked by
    # staff, just excluded from public listings.
    is_public: Optional[bool] = True



class ServiceRequestUpdate(BaseModel):
    status: Optional[RequestStatus] = None
    priority: Optional[int] = Field(None, ge=1, le=10)
    staff_notes: Optional[str] = None
    assigned_department_id: Optional[int] = None  # Assign to department
    assigned_to: Optional[str] = None  # Assign to specific staff
    manual_priority_score: Optional[float] = Field(None, ge=1, le=10)  # Human override priority
    # Closed sub-status fields (when status = closed)
    closed_substatus: Optional[ClosedSubstatus] = None
    completion_message: Optional[str] = None
    completion_photo_url: Optional[str] = None
    # Legal hold (admin only)
    flagged: Optional[bool] = None


class PublicArchiveUpdate(BaseModel):
    """Staff toggling one report off (or back onto) the public tracker and map.

    Its own endpoint rather than a field on ServiceRequestUpdate: this is not a
    change to the report, it is a change to where the report is listed, and
    keeping it separate means a bulk status edit can never carry it along.
    """
    public_archived: bool


class ServiceRequestDelete(BaseModel):
    """Schema for soft-deleting a service request with justification"""
    justification: str = Field(..., min_length=10, description="Reason for deleting this request")


class ServiceRequestResponse(BaseModel):
    id: int
    service_request_id: str
    service_code: str
    service_name: str
    description: str
    status: str
    priority: int
    address: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    requested_datetime: Optional[datetime] = None
    updated_datetime: Optional[datetime] = None
    source: str
    flagged: bool = False
    # Whether this report appears in public listings (False = unlisted).
    is_public: bool = True
    # Whether staff took it off the public tracker and map. Distinct from
    # is_public, which is the resident's own choice — see models.ServiceRequest.
    public_archived: bool = False

    @field_validator('is_public', mode='before')
    @classmethod
    def coalesce_is_public(cls, v):
        return True if v is None else v

    @field_validator('public_archived', mode='before')
    @classmethod
    def coalesce_public_archived(cls, v):
        return False if v is None else v

    @field_validator('flagged', mode='before')
    @classmethod
    def coalesce_flagged(cls, v):
        return v if v is not None else False
    
    matched_asset: Optional[Dict[str, Any]] = None
    # Platforms this request is actually linked to, if any.
    #
    # "Refresh work order" pulls the latest assignment and schedule from a
    # connected system. With no link there is nothing to pull, and the endpoint
    # answers "This request isn't linked to any external platform" -- so the
    # button existed to tell you it does nothing. Empty list means hide it.
    external_links: List[str] = []
    # Assignment
    assigned_department_id: Optional[int] = None
    assigned_to: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = {}
    # Closed sub-status
    closed_substatus: Optional[str] = None
    # Soft delete info
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None
    delete_justification: Optional[str] = None
    # Priority fields for sorting/filtering (AI score is in ai_analysis.priority_score)
    manual_priority_score: Optional[float] = None
    ai_analysis: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class PublicServiceRequestResponse(BaseModel):
    """Public-facing response that strips all personal information"""
    service_request_id: str
    service_code: str
    service_name: str
    description: str  # Could be truncated in API
    status: str
    address: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    requested_datetime: Optional[datetime] = None
    updated_datetime: Optional[datetime] = None
    closed_substatus: Optional[str] = None
    media_urls: Optional[List[str]] = []  # Array of photo URLs
    completion_message: Optional[str] = None
    completion_photo_url: Optional[str] = None

    class Config:
        from_attributes = True


class ServiceRequestDetailResponse(ServiceRequestResponse):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: str
    phone: Optional[str] = None
    media_urls: Optional[List[str]] = []  # Array of photo URLs
    ai_analysis: Optional[Dict[str, Any]] = None
    flag_reason: Optional[str] = None
    staff_notes: Optional[str] = None
    assigned_department_id: Optional[int] = None
    assigned_department: Optional[DepartmentResponse] = None  # Full department info
    assigned_to: Optional[str] = None
    closed_datetime: Optional[datetime] = None
    # Completion fields
    completion_message: Optional[str] = None
    completion_photo_url: Optional[str] = None
    # Delete justification (for admin view)
    delete_justification: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = {}
    # Vertex AI Analysis (priority_score is in ai_analysis JSON only)
    ai_summary: Optional[str] = None
    ai_classification: Optional[str] = None
    manual_priority_score: Optional[float] = None  # Human-approved priority
    ai_analyzed_at: Optional[datetime] = None


# ============ Manual Intake ============
class ManualIntakeCreate(BaseModel):
    """Staff-entered request (call taker, walk-in, or email intake).

    Mirrors ServiceRequestCreate so manual intake runs through the exact same
    pipeline (assignment, AI triage, govtech push, notifications). The only
    difference is that resident contact details are optional — a caller may
    decline to give them — and the source records how it came in.
    """
    service_code: str
    description: str = Field(..., min_length=3)  # call takers write terse notes
    address: Optional[str] = None
    lat: Optional[float] = None
    long: Optional[float] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    preferred_language: Optional[str] = Field(default="en", max_length=10)
    media_urls: Optional[List[str]] = []
    matched_asset: Optional[Dict[str, Any]] = None
    custom_fields: Optional[Dict[str, Any]] = {}
    # Honor the same choice when a staffer takes the report by phone/walk-in.
    is_public: Optional[bool] = True
    # Staff may knowingly log a report on a road another agency maintains.
    # Residents cannot; this is the judgement call the role exists for.
    override_jurisdiction: bool = False
    source: RequestSource = RequestSource.phone


# ============ System Settings ============
class SystemSettingsBase(BaseModel):
    township_name: str = "Your Township"
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    hero_text: str = "How can we help?"
    primary_color: str = "#6366f1"
    # Product features with nothing to configure. `ai_analysis`, `sms_alerts`
    # and `email_notifications` moved to `system_settings.capability_switches`,
    # which is the one switch for anything with a provider behind it -- they
    # were a second answer to a question the setup page also owned, and the two
    # could disagree. See app/services/capability_switches.py.
    modules: Dict[str, bool] = {"unlisted_reports": False, "research_portal": False}
    # Per-pack research export switches: {pack_id: bool}. None/absent key means
    # the pack's own default (app/api/research.RESEARCH_PACKS_DEF). Optional so
    # exclude_unset keeps a settings save that never mentions packs from
    # clobbering the stored choices.
    research_packs: Optional[Dict[str, bool]] = None
    social_links: Optional[List[Dict[str, str]]] = []
    privacy_policy: Optional[str] = None  # Custom privacy policy (Markdown)
    terms_of_service: Optional[str] = None  # Custom terms of service (Markdown)
    accessibility_statement: Optional[str] = None  # Custom accessibility statement
    # Days a CLOSED report stays on the public tracker and map. None or 0 means
    # everything stays listed. Optional so exclude_unset keeps a settings save
    # that never mentions it from clearing a configured policy.
    public_archive_days: Optional[int] = None

    @field_validator('public_archive_days', mode='before')
    @classmethod
    def normalize_public_archive_days(cls, v):
        """0, "" and None all mean "no policy", and all store as NULL.

        The admin field is a text input, so an admin clearing it sends "". One
        stored representation of "unset" keeps `archive_cutoff` from having to
        know about three of them.
        """
        if v is None or v == "":
            return None
        try:
            days = int(v)
        except (TypeError, ValueError):
            raise ValueError("Days must be a whole number, or empty to keep everything listed.")
        if days == 0:
            return None
        if days < 1:
            raise ValueError("Days must be at least 1, or empty to keep everything listed.")
        if days > 36500:
            raise ValueError("Days must be 36,500 (100 years) or less.")
        return days


class SystemSettingsResponse(SystemSettingsBase):
    id: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============ System Secrets ============
class SecretBase(BaseModel):
    key_name: str
    description: Optional[str] = None


class SecretCreate(SecretBase):
    key_value: str


class SecretUpdate(BaseModel):
    key_value: str


class SecretResponse(SecretBase):
    id: int
    is_configured: bool
    key_value: Optional[str] = None  # Only returned for non-sensitive configuration secrets

    class Config:
        from_attributes = True


# ============ Statistics ============
class StatisticsResponse(BaseModel):
    total_requests: int
    open_requests: int
    in_progress_requests: int
    closed_requests: int
    requests_by_category: Dict[str, int]
    requests_by_status: Dict[str, int]
    recent_requests: List[ServiceRequestResponse]


class HeatmapPoint(BaseModel):
    lat: float
    lng: float
    weight: int


class HeatmapDataResponse(BaseModel):
    report_points: List[HeatmapPoint]
    reporter_points: List[HeatmapPoint]
    total_reports: int
    total_unique_reporters: int


class HotspotData(BaseModel):
    lat: float
    lng: float
    count: int
    cluster_id: int
    sample_address: Optional[str] = None  # Representative address for the cluster
    top_categories: Optional[List[str]] = None  # Most common issue types in cluster
    unique_reporters: Optional[int] = None  # Count of distinct reporters (for bias detection)
    oldest_days: Optional[int] = None  # Age of oldest open request in this cluster (in days)


class TrendData(BaseModel):
    period: str
    open: int
    in_progress: int
    closed: int
    total: int


class DepartmentMetrics(BaseModel):
    name: str
    total_requests: int
    open_requests: int
    avg_resolution_hours: Optional[float]
    resolution_rate: float


class PredictiveInsights(BaseModel):
    volume_forecast_next_week: int
    trend_direction: str  # "increasing", "stable", "decreasing"
    seasonal_peak_day: str
    seasonal_peak_month: str


class CostEstimate(BaseModel):
    category: str
    avg_hours: float
    estimated_cost: float
    open_tickets: int
    total_estimated_cost: float


class RepeatLocation(BaseModel):
    address: str
    lat: float
    lng: float
    request_count: int


class AdvancedStatisticsResponse(BaseModel):
    # Summary counts
    total_requests: int
    open_requests: int
    in_progress_requests: int
    closed_requests: int
    
    # Temporal analytics
    requests_by_hour: Dict[int, int]  # {0: 5, 1: 2, ..., 23: 8}
    requests_by_day_of_week: Dict[str, int]  # {"Monday": 10, ...}
    requests_by_month: Dict[str, int]  # {"2024-01": 50, ...}
    avg_resolution_hours_by_category: Dict[str, float]
    
    # Geospatial analytics (PostGIS) - using imperial units for US municipalities
    hotspots: List[HotspotData]
    geographic_center: Optional[Dict[str, float]]  # {lat, lng}
    geographic_spread_miles: Optional[float]  # Standard deviation of request locations in miles
    total_coverage_sq_miles: Optional[float]  # Total area covered by all requests
    avg_distance_from_center_miles: Optional[float]  # Average distance from geographic center
    furthest_request_miles: Optional[float]  # Distance of furthest request from center
    requests_density_by_zone: Dict[str, int]  # If zones are defined
    
    # Department analytics
    department_metrics: List[DepartmentMetrics]
    top_staff_by_resolutions: Dict[str, int]  # {username: count}
    
    # Performance metrics
    avg_resolution_hours: Optional[float]
    avg_first_response_hours: Optional[float]
    backlog_by_age: Dict[str, int]  # {"<1 day": 5, "1-3 days": 8, ...}
    resolution_rate: float  # Closed / Total
    
    # Infrastructure-focused metrics
    backlog_by_priority: Dict[int, int]  # {1: 5, 2: 10, ...} - Current open/in_progress by priority
    workload_by_staff: Dict[str, int]  # {username: active_count} - Current assignments
    open_by_age_sla: Dict[str, int]  # Same as backlog_by_age but only "open" status for SLA tracking
    
    # Predictive & Government Analytics
    predictive_insights: PredictiveInsights
    cost_estimates: List[CostEstimate]
    avg_response_time_hours: Optional[float]  # Time to first staff action
    repeat_locations: List[RepeatLocation]
    aging_high_priority_count: int  # P1-P3 open > 7 days
    
    # Category analytics
    requests_by_category: Dict[str, int]
    flagged_count: int
    
    # Trends
    weekly_trend: List[TrendData]
    monthly_trend: List[TrendData]
    
    # Cache info
    cached_at: Optional[datetime] = None




# ============ Map Layers ============
class MapLayerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    layer_type: Optional[str] = None  # polygon, line, point
    fill_color: str = "#3b82f6"
    stroke_color: str = "#1d4ed8"
    fill_opacity: float = 0.3
    stroke_width: int = 2
    show_on_resident_portal: bool = True
    visible_on_map: bool = True  # Whether to show layer visually on map
    routing_mode: Optional[str] = "log"  # log, block
    service_codes: Optional[List[str]] = None  # Categories this layer applies to (empty = all)
    routing_config: Optional[Dict[str, Any]] = None  # { message, contacts }



class MapLayerCreate(MapLayerBase):
    geojson: Dict[str, Any]


class MapLayerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layer_type: Optional[str] = None
    fill_color: Optional[str] = None
    stroke_color: Optional[str] = None
    fill_opacity: Optional[float] = None
    stroke_width: Optional[int] = None
    is_active: Optional[bool] = None
    show_on_resident_portal: Optional[bool] = None
    visible_on_map: Optional[bool] = None
    routing_mode: Optional[str] = None
    geojson: Optional[Dict[str, Any]] = None
    service_codes: Optional[List[str]] = None
    routing_config: Optional[Dict[str, Any]] = None



class MapLayerResponse(MapLayerBase):
    id: int
    geojson: Dict[str, Any]
    is_active: bool
    visible_on_map: Optional[bool] = True
    routing_mode: Optional[str] = "log"
    service_codes: Optional[List[str]] = None
    routing_config: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============ Request Comments ============
class RequestCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, description="Comment content")
    visibility: CommentVisibility = CommentVisibility.internal


class RequestCommentResponse(BaseModel):
    id: int
    service_request_id: int
    user_id: Optional[int] = None
    username: str
    content: str
    visibility: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============ Request Audit Log ============
class RequestAuditLogResponse(BaseModel):
    id: int
    service_request_id: int
    action: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    actor_type: str
    actor_name: Optional[str] = None
    created_at: Optional[datetime] = None
    extra_data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

