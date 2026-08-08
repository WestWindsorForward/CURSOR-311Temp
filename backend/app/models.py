from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Float, Text, Boolean, Table, UniqueConstraint, event, select
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from datetime import datetime, timezone
from app.db.session import Base
# encryption is imported lazily in hybrid properties to avoid circular imports


# Association table for ServiceDefinition-Department many-to-many
service_departments = Table(
    "service_departments",
    Base.metadata,
    Column("service_id", Integer, ForeignKey("service_definitions.id"), primary_key=True),
    Column("department_id", Integer, ForeignKey("departments.id"), primary_key=True)
)


# Association table for User-Department many-to-many
user_departments = Table(
    "user_departments",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("department_id", Integer, ForeignKey("departments.id"), primary_key=True)
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255))
    hashed_password = Column(String(255), nullable=True)  # Nullable for SSO users
    role = Column(String(20), default="staff")  # admin, staff, researcher
    is_active = Column(Boolean, default=True)
    
    # Auth0 SSO
    auth0_id = Column(String(255), unique=True, index=True)  # Auth0 user ID (sub claim)
    
    notification_preferences = Column(JSON, default={
        "email_new_requests": True,
        "email_status_changes": True,
        "email_comments": True,
        "email_assigned_only": False,
        "sms_new_requests": False,
        "sms_status_changes": False
    })
    phone = Column(String(50))  # Staff phone for SMS alerts
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Staff can be assigned to multiple departments
    departments = relationship(
        "Department",
        secondary=user_departments,
        back_populates="staff_members"
    )


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text)
    routing_email = Column(String(255))
    is_active = Column(Boolean, default=True)
    
    # Multi-language support
    translations = Column(JSON, default={})
    # Format: {"en": {"name": "Public Works", "description": "..."}, "es": {...}}
    
    services = relationship(
        "ServiceDefinition",
        secondary=service_departments,
        back_populates="departments"
    )
    
    staff_members = relationship(
        "User",
        secondary=user_departments,
        back_populates="departments"
    )


class ServiceDefinition(Base):
    __tablename__ = "service_definitions"

    id = Column(Integer, primary_key=True, index=True)
    service_code = Column(String(50), unique=True, index=True, nullable=False)
    service_name = Column(String(100), nullable=False)
    description = Column(Text)
    icon = Column(String(50), default="AlertCircle")  # Lucide icon name
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)  # Controls display ordering (lower = first)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Routing configuration
    # Optional service-level agreement: target hours from submission to closure.
    # NULL = no SLA set for this category (the feature is entirely opt-in).
    sla_hours = Column(Integer, nullable=True)

    routing_mode = Column(String(50), default="township")  # township, third_party, road_based
    routing_config = Column(JSON, default={})
    # For third_party: { "url": "...", "message": "..." }
    # For road_based: { 
    #   "default": "township|third_party", 
    #   "township_roads": ["Main St", ...],
    #   "county_roads": ["County Rd 1", ...],
    #   "third_party_url": "...", 
    #   "third_party_message": "..." 
    # }
    
    # Multi-language support
    translations = Column(JSON, default={})
    # Format: {
    #   "en": {"service_name": "Pothole Repair", "description": "Report road damage"},
    #   "es": {"service_name": "Reparación de Baches", "description": "Reportar daños"},
    #   ...
    # }
    
    assigned_department_id = Column(Integer, ForeignKey("departments.id"))
    assigned_department = relationship("Department", foreign_keys=[assigned_department_id])
    
    departments = relationship(
        "Department",
        secondary=service_departments,
        back_populates="services"
    )


class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id = Column(Integer, primary_key=True, index=True)
    service_request_id = Column(String(50), unique=True, index=True, nullable=False)
    
    # Service info
    service_code = Column(String(50), index=True, nullable=False)
    service_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    
    # Status
    status = Column(String(20), default="open", index=True)  # open, in_progress, closed
    priority = Column(Integer, default=5)  # 1-10
    
    # Location
    address = Column(String(500))
    lat = Column(Float)
    long = Column(Float)
    location = Column(Geometry("POINT", srid=4326))
    
    # Reporter info (PII - encrypted with Google KMS or Fernet fallback)
    # These columns store encrypted values - use hybrid properties for access
    _first_name_encrypted = Column("first_name", String(500))  # Encrypted storage
    _last_name_encrypted = Column("last_name", String(500))   # Encrypted storage
    _email_encrypted = Column("email", String(500), nullable=False)  # Encrypted storage
    # 500 like the other three, not the 200 it started at. A KMS-wrapped value
    # is ~225 characters -- prefix, wrapped data key, nonce, ciphertext -- so at
    # 200 every phone write failed with StringDataRightTruncation the moment a
    # cloud key service was configured, and the nightly re-wrap could never
    # finish. Widening is the fix; truncating ciphertext is never recoverable.
    _phone_encrypted = Column("phone", String(500))  # Encrypted storage
    
    @hybrid_property
    def first_name(self):
        """Decrypt first name when accessing."""
        if self._first_name_encrypted:
            try:
                from app.core.encryption import decrypt_pii
                return decrypt_pii(self._first_name_encrypted)
            except Exception:
                return None  # never expose raw ciphertext
        return None
    
    @first_name.setter
    def first_name(self, value):
        """Encrypt first name when setting. Never stores plaintext PII."""
        if value:
            from app.core.encryption import encrypt_pii
            self._first_name_encrypted = encrypt_pii(value)
        else:
            self._first_name_encrypted = None
    
    @hybrid_property
    def last_name(self):
        """Decrypt last name when accessing."""
        if self._last_name_encrypted:
            try:
                from app.core.encryption import decrypt_pii
                return decrypt_pii(self._last_name_encrypted)
            except Exception:
                return None  # never expose raw ciphertext
        return None
    
    @last_name.setter
    def last_name(self, value):
        """Encrypt last name when setting. Never stores plaintext PII."""
        if value:
            from app.core.encryption import encrypt_pii
            self._last_name_encrypted = encrypt_pii(value)
        else:
            self._last_name_encrypted = None
    
    @hybrid_property
    def email(self):
        """Decrypt email when accessing."""
        if self._email_encrypted:
            try:
                from app.core.encryption import decrypt_pii
                return decrypt_pii(self._email_encrypted)
            except Exception:
                return ""  # never expose raw ciphertext
        return ""
    
    @email.setter
    def email(self, value):
        """Encrypt email when setting. Never stores plaintext PII."""
        if value:
            from app.core.encryption import encrypt_pii
            self._email_encrypted = encrypt_pii(value)
        else:
            self._email_encrypted = ""
    
    @hybrid_property
    def phone(self):
        """Decrypt phone when accessing."""
        if self._phone_encrypted:
            try:
                from app.core.encryption import decrypt_pii
                return decrypt_pii(self._phone_encrypted)
            except Exception:
                return None  # never expose raw ciphertext
        return None
    
    @phone.setter
    def phone(self, value):
        """Encrypt phone when setting. Never stores plaintext PII."""
        if value:
            from app.core.encryption import encrypt_pii
            self._phone_encrypted = encrypt_pii(value)
        else:
            self._phone_encrypted = None
    
    # Resident's preferred language (captured from UI at submission)
    preferred_language = Column(String(10), default="en")  # ISO 639-1 code (en, es, hi, etc.)
    
    # Metadata
    source = Column(String(50), default="resident_portal")  # resident_portal, phone, walk_in, email
    media_urls = Column(JSON, default=[])  # Array of up to 3 photo URLs/base64

    # Public-feed visibility, chosen by the resident at submission.
    #   True  (default) - appears in the public feed/map and public list APIs
    #   False ("unlisted") - excluded from every public listing, but still fully
    #          viewable by anyone holding the direct tracking link, and always
    #          visible to town staff. Never means "hidden from staff".
    is_public = Column(Boolean, default=True, server_default='true', nullable=False, index=True)

    # Staff's decision to take this report off the public tracker and map, so a
    # town's public listing does not become a decade of closed potholes.
    #   False (default) - listed, subject to is_public and the town's policy
    #   True  - excluded from public listings. Still reachable by its tracking
    #           link, still fully visible to staff, still in research exports.
    #
    # Deliberately NOT `is_public`. That column records what the RESIDENT asked
    # for, and staff overwriting it would both lose the resident's answer and
    # make "unarchive" republish a report somebody had asked to keep unlisted.
    # Two decisions, two columns, and the public listing requires both.
    public_archived = Column(Boolean, default=False, server_default='false', nullable=False, index=True)

    # AI Analysis
    ai_analysis = Column(JSON)
    flagged = Column(Boolean, default=False, server_default='false', nullable=False)
    flag_reason = Column(String(255))
    
    # Timestamps
    requested_datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_datetime = Column(DateTime(timezone=True), onupdate=func.now())
    closed_datetime = Column(DateTime(timezone=True))
    
    # Staff notes
    staff_notes = Column(Text)
    assigned_department_id = Column(Integer, ForeignKey("departments.id"))
    assigned_department = relationship("Department", foreign_keys=[assigned_department_id])
    assigned_to = Column(String(100))
    
    # Matched asset from map layers (detected on submit)
    matched_asset = Column(JSON)  # { layer_name, asset_id, asset_type, properties, distance_meters }
    
    # Custom question responses from resident portal
    custom_fields = Column(JSON)  # { question_id: answer }
    
    # Closed sub-status (when status = 'closed')
    closed_substatus = Column(String(30))  # no_action, resolved, third_party
    completion_message = Column(Text)  # Staff message when closing
    completion_photo_url = Column(String(500))  # Photo proof of resolution
    
    # Soft delete support
    deleted_at = Column(DateTime(timezone=True), index=True)
    deleted_by = Column(String(100))  # Username who deleted
    delete_justification = Column(Text)
    
    # Vertex AI Analysis
    ai_summary = Column(Text)  # AI-generated summary
    ai_classification = Column(String(100))  # AI category classification
    # NOTE: AI priority score is stored ONLY in ai_analysis JSON, not as a separate column
    # This ensures staff must explicitly accept AI suggestions before they take effect
    manual_priority_score = Column(Float)  # Human-approved priority (1-10), required for prioritization
    ai_analyzed_at = Column(DateTime(timezone=True))
    
    # Document retention / archival
    archived_at = Column(DateTime(timezone=True), index=True)  # When record was archived


class RequestComment(Base):
    """Two-way comments on service requests with visibility control"""
    __tablename__ = "request_comments"

    id = Column(Integer, primary_key=True, index=True)
    service_request_id = Column(Integer, ForeignKey("service_requests.id"), nullable=False, index=True)
    
    # Author info
    user_id = Column(Integer, ForeignKey("users.id"))
    username = Column(String(100), nullable=False)
    
    # Comment content
    content = Column(Text, nullable=False)
    
    # Visibility: internal (staff only) or external (visible to resident)
    visibility = Column(String(20), default="internal")  # internal, external

    # Set when the comment was imported from an external platform:
    # "<integration_id>:<external_comment_id>". Prevents re-import and echo-back.
    external_ref = Column(String(200), index=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    service_request = relationship("ServiceRequest", backref="comments")


class RequestAuditLog(Base):
    """Audit trail for all changes to service requests"""
    __tablename__ = "request_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    service_request_id = Column(Integer, ForeignKey("service_requests.id"), nullable=False, index=True)
    
    # Action type: submitted, status_change, department_assigned, staff_assigned,
    # comment_added, legal_hold, public_archive
    action = Column(String(50), nullable=False)
    
    # What changed
    old_value = Column(String(255))  # Previous value (e.g., "open", department name)
    new_value = Column(String(255))  # New value (e.g., "in_progress", department name)
    
    # Who made the change
    actor_type = Column(String(20), nullable=False)  # "resident" or "staff"
    actor_name = Column(String(100))  # Username or "Resident"
    
    # When (Python-side default so the value is present at insert time for hashing)
    # `lambda` rather than `datetime.utcnow`, which is a *reference* and so
    # survived the sweep that fixed the calls. SQLAlchemy invokes it at insert
    # and gets a naive value for a timestamptz column -- and this one is hashed
    # into the audit chain, so the offset would be baked into the entry.
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        default=lambda: datetime.now(timezone.utc))

    # Additional context (JSON for flexibility)
    extra_data = Column(JSON)  # { substatus, completion_message, etc. }

    # Tamper-evidence: SHA-256 hash chain (immutable public-records audit trail).
    # entry_hash = SHA256(previous_hash + canonical(this row)); computed
    # automatically on insert (see the before_insert listener below). Any
    # alteration or deletion of a row breaks the chain and is detectable via
    # the audit-log verify endpoint.
    previous_hash = Column(String(64))
    entry_hash = Column(String(64))

    # Relationship
    service_request = relationship("ServiceRequest", backref="audit_logs")


class AuditAnchor(Base):
    """Append-only anchor of the request-audit hash-chain head.

    The HMAC chain detects tampering *within* the DB, but an attacker with full
    DB write access could rewrite the chain and re-anchor it. Periodically
    recording the chain head here AND emitting it to the application log (which
    ships to external aggregation in hosted mode) means the head at each point
    in time also lives outside this table — so a silent full-history rewrite
    would have to defeat the external log too. Never updated or deleted."""
    __tablename__ = "audit_anchors"

    id = Column(Integer, primary_key=True, index=True)
    # `lambda` rather than `datetime.utcnow`, which is a *reference* and so
    # survived the sweep that fixed the calls. SQLAlchemy invokes it at insert
    # and gets a naive value for a timestamptz column -- and this one is hashed
    # into the audit chain, so the offset would be baked into the entry.
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        default=lambda: datetime.now(timezone.utc))
    head_hash = Column(String(64))   # latest RequestAuditLog.entry_hash at anchor time
    entry_count = Column(Integer)    # number of hashed entries at anchor time


def _canonical_request_audit(action, old_value, new_value, actor_type,
                             actor_name, created_at, extra_data, previous_hash) -> str:
    """Deterministic serialization of a request-audit row for hashing."""
    import json as _json
    return _json.dumps({
        "action": action,
        "old_value": old_value,
        "new_value": new_value,
        "actor_type": actor_type,
        "actor_name": actor_name,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "extra_data": extra_data,
        "previous_hash": previous_hash,
    }, sort_keys=True, separators=(",", ":"), default=str)


def _audit_chain_key() -> bytes:
    """Server-held HMAC key for the audit chain, derived from SECRET_KEY.

    An unkeyed hash chain only detects *accidental* edits: anyone who can
    write to the DB can rewrite a row and recompute every hash after it with
    the public algorithm. Keying the chain with a secret the DB does not hold
    means a database-only attacker cannot forge a chain that verifies.
    """
    import hashlib as _hashlib
    from app.core.config import get_settings
    return _hashlib.sha256(b"pinpoint311-audit-chain:" + get_settings().secret_key.encode("utf-8")).digest()


def compute_request_audit_hash(row: "RequestAuditLog", previous_hash) -> str:
    """HMAC-SHA256 over the canonical row + previous hash (current scheme)."""
    import hmac as _hmac
    import hashlib as _hashlib
    canonical = _canonical_request_audit(
        row.action, row.old_value, row.new_value, row.actor_type,
        row.actor_name, row.created_at, row.extra_data, previous_hash,
    )
    return _hmac.new(_audit_chain_key(), canonical.encode("utf-8"), _hashlib.sha256).hexdigest()


def compute_request_audit_hash_legacy(row: "RequestAuditLog", previous_hash) -> str:
    """Unkeyed SHA-256 (pre-HMAC scheme) — verification accepts it for rows
    written before the upgrade so historical chains still validate."""
    import hashlib as _hashlib
    canonical = _canonical_request_audit(
        row.action, row.old_value, row.new_value, row.actor_type,
        row.actor_name, row.created_at, row.extra_data, previous_hash,
    )
    return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


import itertools as _itertools
from sqlalchemy.orm import Session as _Session

# Monotonic creation counter → deterministic chain order even when several
# audit rows are created in the same commit (e.g. a status change that also
# reassigns a department in one transaction).
_audit_seq = _itertools.count()


@event.listens_for(RequestAuditLog, "init")
def _stamp_audit_seq(target, args, kwargs):
    target._seq = next(_audit_seq)


@event.listens_for(_Session, "before_flush")
def _request_audit_hash_chain(session, flush_context, instances):
    """Chain new request-audit rows into a SHA-256 hash chain at flush time.

    Runs once per flush over all pending RequestAuditLog inserts (in creation
    order), seeding from the last persisted entry_hash. Works under the async
    engine and needs no changes at the ~15 call sites that create audit rows.
    """
    pending = [o for o in session.new if isinstance(o, RequestAuditLog)]
    if not pending:
        return
    pending.sort(key=lambda o: getattr(o, "_seq", 0))
    tbl = RequestAuditLog.__table__
    running = session.execute(
        select(tbl.c.entry_hash).order_by(tbl.c.id.desc()).limit(1)
    ).scalar()
    for row in pending:
        if row.created_at is None:
            row.created_at = datetime.now(timezone.utc)
        row.previous_hash = running
        row.entry_hash = compute_request_audit_hash(row, running)
        running = row.entry_hash



class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    township_name = Column(String(200), default="Your Township")
    logo_url = Column(String(500))
    favicon_url = Column(String(500))
    hero_text = Column(String(500), default="How can we help?")
    primary_color = Column(String(7), default="#6366f1")
    custom_domain = Column(String(255))  # For custom domain configuration
    # Product features with nothing to configure: no provider, no credentials,
    # no card on the setup page. Anything that *does* have a provider behind it
    # lives in `capability_switches` below -- see capability_switches.py for why
    # the two are separate and which belongs where.
    #
    # `ai_analysis`, `sms_alerts` and `email_notifications` used to be here as
    # well, duplicating a decision the setup page also owned.
    modules = Column(JSON, default={"unlisted_reports": False, "research_portal": False})
    # Which integrations the town wants, independent of whether they are set up.
    #
    # The third fact about a capability, and the one that had nowhere to live:
    # credentials being stored and the town intending to use them are different
    # claims, and without this an admin could only stop a configured integration
    # by deleting the credential. {} means "never answered", which reads as the
    # behaviour that shipped before the switch existed rather than as "off".
    capability_switches = Column(JSON, default=dict)
    # Which research field packs this town releases: {pack_id: bool}. An absent
    # key means the pack's own default (see research.RESEARCH_PACKS_DEF) — ON
    # for the analytical packs so an upgrade changes nothing, OFF for the two
    # packs whose fields characterize a resident's own message. Enforced
    # server-side at row build (allowed_research_columns), never in a UI.
    research_packs = Column(JSON, default=dict)
    # When somebody said they were finished setting this town up. NULL means
    # nobody has, which is what opens the setup guide on sign-in.
    #
    # A marker rather than a derived answer. "Is everything configured" is the
    # obvious proxy and it is wrong in the direction that matters: a town that
    # deliberately switches most things off never satisfies it, so the guide
    # would greet it on every login forever -- and a banner that never goes away
    # is one people stop reading. Being finished is a thing a person says.
    setup_completed_at = Column(DateTime(timezone=True))
    township_boundary = Column(JSON)  # GeoJSON boundary from OpenStreetMap
    
    # Multi-language support
    translations = Column(JSON, default={})
    # Format: {"en": {"township_name": "...", "hero_text": "..."}, "es": {...}}
    
    # Social media links for resident portal footer
    social_links = Column(JSON, default=[])
    # Format: [{"platform": "facebook", "url": "https://...", "icon": "Facebook"}, ...]
    
    # Customizable legal documents (Markdown, null = use default)
    privacy_policy = Column(Text)  # Custom privacy policy content
    terms_of_service = Column(Text)  # Custom terms of service content
    accessibility_statement = Column(Text)  # Custom accessibility statement
    
    # Document retention configuration
    #
    # No defaults, deliberately, on either of the two columns that decide what
    # gets destroyed. The product used to supply both — a per-state period from
    # a table of 51 unverified entries, and a fixed list of seven fields — so
    # every town ran a destruction schedule it had never chosen. NULL on either
    # is read as "not configured", and an unconfigured town archives nothing at
    # all; see app/services/retention_config.py.
    #
    # How long a closed request is kept, in days. One period for every record,
    # not a schedule broken down by record class — the UI says so, because a
    # real retention schedule distinguishes a routine pothole report from one
    # attached to a claim and this does not.
    retention_days = Column(Integer)
    retention_mode = Column(String(20), default="redact")  # "redact" or "purge"

    # How many days a CLOSED report stays on the public tracker and map. This is
    # decluttering, not retention and not privacy: nothing is deleted, redacted
    # or hidden from staff, the tracking link keeps working, and research
    # exports still include the report. NULL or 0 means everything stays listed.
    #
    # Applied at query time (app/services/public_visibility.py), never stamped
    # onto rows, so changing the number is instantly retroactive in both
    # directions and clearing it puts the whole history back.
    public_archive_days = Column(Integer)
    # Which fields a retention run clears. NULL means never configured, and
    # that is all it means: there is no list we can supply on a town's behalf,
    # because the list is what is permanently destroyed.
    retention_scrub_fields = Column(JSON)

    # The town's own clock, for display only. Everything is stored in UTC and
    # stays that way; this is what a clerk's screen converts into.
    timezone = Column(String(64))

    # Instance-wide legal / litigation hold. When true, ALL retention purging is
    # suspended (nothing is deleted or anonymized) until it is lifted. Either the
    # town or the state can place it; the state sets it via the provisioning API.
    legal_hold = Column(Boolean, default=False, server_default='false', nullable=False)
    # State-pushed managed policy (retention, legal hold, PII mode, …). When a key
    # is present here it is state-controlled and the town cannot override it.
    managed_policy = Column(JSON, default={})
    # Live AI model discovery cache: {provider: {models, source, fetched_at}}.
    # Refreshed on demand (admin "Refresh models") and by a daily Celery task.
    ai_models_cache = Column(JSON, default={})

    # Last-seen status per proactive health check ({check_key: status}), so the
    # alerting task only emails admins when a check crosses into a worse state.
    health_alert_state = Column(JSON, default={})

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DisclaimerAcknowledgment(Base):
    """Log of user acknowledgments that 311 is for non-emergency use only.
    Stored for legal protection and audit purposes."""
    __tablename__ = "disclaimer_acknowledgments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), index=True, nullable=False)  # Browser session/fingerprint
    ip_address = Column(String(45))  # IPv4 or IPv6
    user_agent = Column(String(500))
    acknowledged_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    disclaimer_version = Column(String(10), default="1.0")  # Track disclaimer version for updates


class SystemSecret(Base):
    __tablename__ = "system_secrets"

    id = Column(Integer, primary_key=True, index=True)
    key_name = Column(String(100), unique=True, index=True, nullable=False)
    key_value = Column(Text)  # Should be encrypted in production
    description = Column(String(255))
    is_configured = Column(Boolean, default=False)


class MapLayer(Base):
    """Custom GeoJSON layers for township assets (parks, storm drains, utilities, etc.)"""
    __tablename__ = "map_layers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # "Parks", "Storm Drains", etc.
    description = Column(String(500))
    layer_type = Column(String(50))  # polygon, line, point, or auto-detected
    
    # Styling
    fill_color = Column(String(20), default="#3b82f6")
    stroke_color = Column(String(20), default="#1d4ed8")
    fill_opacity = Column(Float, default=0.3)
    stroke_width = Column(Integer, default=2)
    
    # GeoJSON data
    geojson = Column(JSON, nullable=False)
    
    # Visibility
    is_active = Column(Boolean, default=True)
    show_on_resident_portal = Column(Boolean, default=True)
    
    # Category association - which service categories this layer applies to
    service_codes = Column(JSON, default=list)  # ["streetlight", "pothole", etc.] - empty = all categories
    
    # Polygon routing behavior
    routing_mode = Column(String(20), default="none")  # none, log, block
    visible_on_map = Column(Boolean, default=True)  # Whether to render the layer visually
    
    # Routing for polygons (redirect requests within polygon to third-party)
    routing_config = Column(JSON)  # { "message": "...", "contacts": [{ "name": "...", "phone": "...", "url": "..." }] }
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ResearchAccessLog(Base):
    """Audit trail for research data access - tracks who downloaded what and when"""
    __tablename__ = "research_access_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    username = Column(String(100), nullable=False)
    
    # Action type: export_csv, export_geojson, query, view_analytics
    action = Column(String(50), nullable=False)
    
    # Query parameters used (filters, date range, etc.)
    parameters = Column(JSON)
    
    # Number of records accessed
    record_count = Column(Integer)
    
    # Whether fuzzed (privacy mode) or exact location was used
    privacy_mode = Column(String(20), default="fuzzed")  # fuzzed, exact

    # Where from. "Who downloaded the dataset" is only half an answer when the
    # account is shared or compromised — the address and client string are what
    # an investigation actually correlates against.
    ip_address = Column(String(45))  # IPv4 or IPv6
    user_agent = Column(String(500))

    # When
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship
    user = relationship("User", backref="research_access_logs")


class AuditLog(Base):
    """Government-compliant audit logging for all authentication events (NIST 800-53)"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    
    # User info (nullable for failed login attempts where user doesn't exist)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    username = Column(String(100), index=True)  # Username attempted (even if failed)
    
    # Event classification
    event_type = Column(String(50), nullable=False, index=True)
    # Event types: login_success, login_failed, logout, session_expired,
    #              mfa_enrolled, mfa_disabled, password_changed, role_changed,
    #              account_locked, account_unlocked, token_refreshed
    
    # Event outcome
    success = Column(Boolean, nullable=False, index=True)
    failure_reason = Column(String(255))  # Why authentication failed
    
    # Request context
    ip_address = Column(String(45), index=True)  # IPv4 or IPv6
    user_agent = Column(String(500))  # Browser/client info
    
    # Session tracking
    session_id = Column(String(255), index=True)  # Auth0 session ID or JWT jti
    
    # Additional event details (flexible JSON for event-specific data)
    details = Column(JSON)
    # Examples:
    # - MFA type used (totp, sms, email)
    # - Role change: {"old_role": "staff", "new_role": "admin", "changed_by": "admin_user"}
    # - Password change method: {"method": "forgot_password", "reset_token_used": true}
    
    # Timestamps
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    
    # Tamper detection (hash of previous log entry for integrity chain)
    previous_hash = Column(String(64))  # SHA-256 of previous audit log
    entry_hash = Column(String(64))  # SHA-256 of this entry
    
    # Relationship
    user = relationship("User", backref="audit_logs")


class Translation(Base):
    """Database-cached translations to minimize API calls.
    
    Flow:
    1. Check database first for cached translation
    2. If not found, call Google Translate API
    3. Store result in database for future use
    """
    __tablename__ = "translations"

    id = Column(Integer, primary_key=True, index=True)
    
    # Source text (original English text)
    source_text = Column(Text, nullable=False, index=True)
    source_lang = Column(String(10), default="en", nullable=False)
    
    # Target language and translation
    target_lang = Column(String(10), nullable=False, index=True)
    translated_text = Column(Text, nullable=False)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Ensure unique translation per source/target combo
    __table_args__ = (
        # Create unique constraint on hash of source_text + target_lang combo
        # Using a generated column or application-level enforcement
        {"sqlite_autoincrement": True},
    )


class UptimeRecord(Base):
    """Records health check results over time for uptime monitoring dashboard."""
    __tablename__ = "uptime_records"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String(50), nullable=False, index=True)  # backend, frontend, db, auth0, etc.
    status = Column(String(20), nullable=False)  # healthy, degraded, down
    response_time_ms = Column(Integer)  # Response time in milliseconds
    error_message = Column(String(500))  # Error details if status is not healthy
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class IntegrationConfig(Base):
    """Connection settings for an external govtech platform (Accela, Tyler, CivicPlus, etc.).

    Credentials are stored encrypted (Fernet via SECRET_KEY) as a JSON blob and
    only decrypted when a connector needs them.
    """
    __tablename__ = "integration_configs"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(50), nullable=False, index=True)  # accela, tyler, civicplus, sdl, edmunds, govpilot, fasttrackgov, polimorphic, open311
    display_name = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=False, nullable=False)

    # Non-secret settings: base_url, jurisdiction/agency ids, field & status mappings, share_pii flag
    config = Column(JSON, default={})

    # Encrypted JSON of secrets (api keys, client secrets, passwords)
    _credentials_encrypted = Column("credentials", Text)

    # push (Pinpoint -> platform), pull (platform -> Pinpoint), bidirectional
    sync_direction = Column(String(20), default="push", nullable=False)

    # Token authenticating inbound webhooks from this platform
    webhook_token = Column(String(64), unique=True, index=True)

    last_sync_at = Column(DateTime(timezone=True))
    last_sync_status = Column(String(20))  # success, error
    last_sync_error = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    @hybrid_property
    def credentials(self):
        """Decrypt credential JSON when accessing. Returns a dict."""
        if not self._credentials_encrypted:
            return {}
        try:
            from app.core.encryption import decrypt
            import json as _json
            return _json.loads(decrypt(self._credentials_encrypted))
        except Exception as exc:
            # Logged, because the two causes look identical from here and lead
            # somewhere completely different. A rotated SECRET_KEY makes every
            # integration's credentials undecryptable at once, and returning a
            # bare {} presented that as "someone deleted the credentials" -- so
            # the advice on screen became "go back and fill them in", which
            # overwrites the vault references and makes the damage permanent.
            #
            # The id and the exception type only. Never the ciphertext, and never
            # the exception's own text, which for a Fernet failure can quote the
            # token back.
            import logging
            logging.getLogger(__name__).error(
                "[Integrations] could not decrypt credentials for integration %s "
                "(%s). If SECRET_KEY was rotated, restore the previous key or "
                "re-enter these credentials deliberately.",
                self.id, type(exc).__name__,
            )
            return {}

    @credentials.setter
    def credentials(self, value):
        """Encrypt credential dict when setting."""
        if value:
            from app.core.encryption import encrypt
            import json as _json
            self._credentials_encrypted = encrypt(_json.dumps(value))
        else:
            self._credentials_encrypted = None


class IntegrationLink(Base):
    """Maps a local service request to its record on an external platform."""
    __tablename__ = "integration_links"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Integer, ForeignKey("integration_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    service_request_id = Column(Integer, ForeignKey("service_requests.id", ondelete="CASCADE"), nullable=False, index=True)

    external_id = Column(String(200), nullable=False, index=True)
    external_status = Column(String(50))
    direction = Column(String(10), default="pushed")  # pushed (we created it there) or pulled (it originated there)

    last_pushed_at = Column(DateTime(timezone=True))
    last_pulled_at = Column(DateTime(timezone=True))
    sync_error = Column(Text)

    # External comment ids created by our pushes — skipped on pull to avoid echo
    pushed_comment_ids = Column(JSON, default=list)
    # How many media items have been pushed — lets photos added after the
    # initial push sync on a later run (push only media beyond this count).
    #
    # There was a `documents_pushed` boolean beside this, written on every push
    # and read by nothing. The count answers the same question and one more the
    # boolean could not: "three photos, all attached" and "no photos on this
    # report" were both simply True. Dropped in a7029676a2bc.
    documents_pushed_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    integration = relationship("IntegrationConfig")
    service_request = relationship("ServiceRequest")


class IntegrationSyncLog(Base):
    """Audit trail of sync operations against external platforms."""
    __tablename__ = "integration_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Integer, ForeignKey("integration_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    operation = Column(String(30), nullable=False)  # test, push, push_status, pull, webhook
    status = Column(String(20), nullable=False)  # success, warning, error
    detail = Column(Text)
    request_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ApiUsageRecord(Base):
    """Track API calls to external services for cost estimation and monitoring."""
    __tablename__ = "api_usage_records"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String(100), nullable=False, index=True)  # vertex_ai, translation, maps_geocode, maps_static, secret_manager, kms
    operation = Column(String(100))  # e.g., "analyze", "translate", "geocode", "reverse_geocode"
    
    # Usage metrics (different services use different metrics)
    tokens_input = Column(Integer, default=0)  # For AI services (Gemini)
    tokens_output = Column(Integer, default=0)  # For AI services (Gemini)
    characters = Column(Integer, default=0)  # For translation API
    api_calls = Column(Integer, default=1)  # Count of API calls (for per-call pricing)
    
    # Request context
    request_id = Column(String(50), index=True)  # Optional link to service_request_id
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)



class RoadSegment(Base):
    """A single stretch of road, used to answer "which road is this pin on".

    Road-based routing used to read a road name out of a reverse-geocoded
    address string. That answers "what is the nearest *address*", which is a
    different question: a corner lot's address belongs to the cross street, and
    a park's mailing address puts its street name on a pin sitting 30 m inside
    the park. Both wrongly BLOCKED residents from filing a report.

    Measuring distance to real centreline geometry answers the question that was
    actually being asked. Every road inside the boundary is stored, not only the
    ones a clerk assigned to a jurisdiction -- without the unlisted ones,
    "nearest road wins" is not a comparison. A pin 5 m from a residential street
    and 18 m from a county road must resolve to the residential street, and it
    can only do that if the residential street is in the table.
    """

    __tablename__ = "road_segments"

    id = Column(Integer, primary_key=True, index=True)

    # Identity in the upstream dataset. Clerk corrections key to this rather
    # than to `id`, so a full table swap on refresh cannot orphan them. Not
    # osm_way_id: the source may be a state NG911 layer (RCL_NGUID) or Census
    # TIGER (LINEARID) rather than OpenStreetMap.
    source_id = Column(String(40), nullable=False, index=True)
    source_feature_id = Column(String(120), nullable=False, index=True)

    name = Column(String(255), index=True)
    name_norm = Column(String(255), index=True)
    ref = Column(String(80))
    ref_norm = Column(String(80), index=True)
    highway_class = Column(String(40))

    # WGS84. Distances cast to geography at query time, which returns true
    # metres anywhere without picking a per-state projected SRID.
    geom = Column(Geometry(geometry_type="LINESTRING", srid=4326), nullable=False)

    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_id", "source_feature_id", name="uq_road_segment_source"),
    )


class RoadDataStatus(Base):
    """One row: where this town's road data came from and how fresh it is.

    Read-only in admin System Health. Two timestamps matter and are easy to
    conflate: `fetched_at` is when we last pulled, `source_updated_at` is when
    the publisher last changed anything. Those diverging is the real signal --
    re-fetching unchanged data forever looks healthy while the county quietly
    stops maintaining the layer.
    """

    __tablename__ = "road_data_status"

    id = Column(Integer, primary_key=True)
    state_code = Column(String(2))
    source_id = Column(String(40))
    source_name = Column(String(255))
    endpoint = Column(String(500))
    segment_count = Column(Integer, default=0)
    fetched_at = Column(DateTime(timezone=True))
    source_updated_at = Column(DateTime(timezone=True))
    consecutive_failures = Column(Integer, default=0)
    last_error = Column(Text)
    # Corridor half-width in metres, per town: a dense borough with 8 m
    # rights-of-way and a rural township with wide shoulders want different
    # numbers, and it also has to absorb disagreement between the road data and
    # whatever basemap the resident is looking at.
    corridor_metres = Column(Integer, default=20)


class BlockedRequestLog(Base):
    """A resident who was redirected instead of filing.

    Deliberately NOT a ServiceRequest. A service request is something staff
    work: it appears in queues, feeds, exports and the public map. A blocked
    request was never filed and must appear in none of those. The town still
    needs the count -- twenty redirects a month on one road is either evidence
    for a conversation with the county or a sign the config is wrong.

    No name, no email, no description.
    """

    __tablename__ = "blocked_request_log"

    id = Column(Integer, primary_key=True)
    service_code = Column(String(50), index=True)
    service_name = Column(String(255))
    jurisdiction_name = Column(String(255), index=True)
    road_name = Column(String(255), index=True)
    # "road_based" (this road belongs to someone else) vs "category" (the whole
    # service is handled by an outside agency). Reported separately because they
    # mean different things to a clerk.
    block_type = Column(String(20), index=True)
    lat = Column(Float)
    long = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ConnectorHealth(Base):
    """Whether an integration is actually working, as opposed to configured.

    Every badge on the setup page answered "are the credentials stored", which
    is a question about our own database. A clerk reading a green tick assumes
    something stronger -- that reports are reaching the county, that emails are
    going out -- and those are the same colour right up until someone
    complains.

    The distinction that makes this useful is `last_success_at` versus
    `last_attempt_at`. A connector nobody has called in three weeks is not
    healthy, it is unknown, and a system that reports those identically is why
    an expired key gets discovered by a resident. Anything relying only on a
    manual Test button has the same problem: it proves the credential worked
    once, at a moment chosen by the person least likely to be surprised.

    One row per connector, updated in place. This is operational state, not
    history -- the audit log is where "what happened" lives, and keeping a row
    per call here would grow without bound for no benefit.
    """

    __tablename__ = "connector_health"

    id = Column(Integer, primary_key=True)

    # "ai", "maps", "identity", "translation", "email", "sms", "govtech:accela".
    # Free-form rather than an enum so a new connector reports health without a
    # migration -- the cost of an enum here is that the newest integration, the
    # one most likely to be misconfigured, is the one that cannot report.
    connector = Column(String(64), nullable=False, unique=True, index=True)
    provider = Column(String(64))

    last_attempt_at = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    last_error_at = Column(DateTime(timezone=True))

    # The provider's own message, truncated. Generic text ("request failed")
    # sends a clerk to us; "SES is in sandbox mode" or "21608: unverified
    # number" sends them to the actual fix.
    last_error = Column(Text)

    # Reset to zero on success. Drives the difference between a blip and an
    # outage without needing per-call history.
    consecutive_failures = Column(Integer, default=0, nullable=False)

    # Counted since first use. Cheap, and answers "is this connector used at
    # all", which decides whether a failure matters today.
    total_successes = Column(Integer, default=0, nullable=False)
    total_failures = Column(Integer, default=0, nullable=False)

    # What the last alert email said about this connector, and when.
    #
    # Without these the daily sweep would either say nothing -- which is what
    # it did, so a broken connector waited for someone to open the settings
    # page -- or say the same thing every morning until it was filtered into a
    # folder, taking the one that mattered with it. Null means nothing has been
    # announced, which is also what a recovery resets it to.
    alerted_level = Column(String(16))
    alerted_at = Column(DateTime(timezone=True))

    # "I know, stop emailing me about it."
    #
    # Silences the alert, never the badge. A connector whose card went quiet
    # along with its email would be the precise failure this whole subsystem
    # exists to catch, so the card stays red and says it is muted and until
    # when. The level is recorded alongside the deadline because muting is
    # consent to a known problem, not to whatever that problem becomes: an
    # at-risk connector that goes fully down is new information and breaks
    # through.
    alert_muted_until = Column(DateTime(timezone=True))
    alert_muted_level = Column(String(16))

    # What the last check actually said, whichever way it went.
    #
    # A failure kept its message in `last_error`; a success kept nothing but a
    # timestamp. So "Twilio credentials accepted, nothing was sent" or "SES
    # reachable, 12 of 50,000 sent today" was shown once and gone on reload,
    # leaving a card that says "checked 6 hours ago" and cannot say what it
    # found. The evidence is the useful part.
    last_result = Column(Text)

    # False when the provider cannot be checked from here at all -- a generic
    # HTTP SMS gateway needs a real message sent. Stored, because otherwise
    # that answer lives only in the browser session that ran the test and the
    # card reverts to "not checked yet" on reload, inviting somebody to press
    # a button that can never succeed.
    #
    # Deliberately not a status: it is not healthy, not broken, and not
    # unknown, and folding it into any of those loses the distinction.
    verifiable = Column(Boolean)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ClientErrorLog(Base):
    """A crash in someone's browser, kept where an administrator can see it.

    These were only ever written to the application log. That is fine if a
    Sentry DSN is configured and somebody watches it; for a self-hosted town it
    means the error screen says "reported" and the report goes into a container
    log that nobody will ever read, and that is rotated away in days.

    So they are persisted and shown in the admin console. Bounded on write --
    see prune_client_errors -- because this is written by an endpoint the public
    can reach and unbounded growth would be a denial-of-service with extra
    steps.

    Deliberately no user id and no request body: a crash report needs the stack
    and the route, not who was looking at what. `url` is stored because the
    route is most of the diagnosis, and it is already visible in the access log.
    """

    __tablename__ = "client_error_log"

    id = Column(Integer, primary_key=True)
    kind = Column(String(64))                      # react_error_boundary | window_error | ...
    message = Column(Text, nullable=False)
    stack = Column(Text)
    component_stack = Column(Text)
    url = Column(String(500))
    user_agent = Column(String(300))

    # Identical crashes collapse onto one row with a count. A render loop
    # produces hundreds of the same error, and a list of hundreds of identical
    # rows hides every other fault on the page.
    fingerprint = Column(String(64), index=True)
    occurrences = Column(Integer, default=1, nullable=False)

    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
