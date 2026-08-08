"""
Research Suite API - Read-only analytics layer for researchers and staff

This module provides sanitized, PII-free access to service request data
for research, operational analysis, and planning purposes.

Research Focus Areas:
- Civil Engineering & Infrastructure: Asset types, infrastructure categories, maintenance patterns
- Equity & Equality: Geographic distribution, response time disparities, service accessibility
- Civics: Civic engagement patterns, submission channels, resolution outcomes

All endpoints:
- Check research_portal module is enabled
- Require researcher or admin role
- Query sanitized data (no PII)
- Log all access for audit purposes
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, extract
from sqlalchemy.orm import selectinload
from typing import Optional
from datetime import datetime, date, timedelta
import csv
import io
import json
import logging
import re
import hashlib
import hmac
from app.services.cdc_svi import get_cdc_svi

from app.db.session import get_db
from app.models import ServiceRequest, SystemSettings, ResearchAccessLog
from app.core.auth import get_current_admin, get_current_researcher
from app.core.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

# Infrastructure category mapping for civil engineering research
INFRASTRUCTURE_CATEGORIES = {
    "pothole": "roads_pavement",
    "streetlight": "lighting",
    "sidewalk": "pedestrian_infrastructure",
    "storm_drain": "stormwater",
    "water": "water_utilities",
    "sewer": "sewer_utilities",
    "traffic": "traffic_control",
    "sign": "signage",
    "tree": "green_infrastructure",
    "park": "parks_recreation",
    "trash": "solid_waste",
    "graffiti": "property_maintenance",
    "abandoned": "property_maintenance",
    "noise": "quality_of_life",
    "animal": "animal_services",
}


async def get_system_settings(db: AsyncSession) -> Optional[SystemSettings]:
    """The single settings row — module flags and per-pack switches live here."""
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    return result.scalar_one_or_none()


async def check_research_enabled(db: AsyncSession):
    """Check if research portal is enabled via Admin Console modules.

    The Admin Console flag is the ONLY switch. There used to be an
    `enable_research_suite` env short-circuit ahead of it, which meant an
    environment variable could silently overrule what the admin screen showed —
    an admin who turned the portal off had no way to see it was still on.
    """
    system_settings = await get_system_settings(db)
    if system_settings and system_settings.modules:
        return system_settings.modules.get("research_portal", False)

    return False


def research_visibility_conditions():
    """The row filter every research query must apply.

    Research outputs are public-facing (exports leave the building); a resident
    who asked for an unlisted report asked for it to stay off every public
    surface, and that includes the researcher CSV, not just the map. Soft-deleted
    rows are excluded for the same reason they are everywhere else.

    One function rather than four copies of the same two conditions, so a new
    research endpoint cannot forget one of them.

    Deliberately NOT filtered here: `public_archived`, and the town's
    `public_archive_days` policy. Both are decluttering -- a clerk deciding the
    public map does not need last spring's resolved potholes on it -- and
    neither is anybody asking for the record to be withheld. Dropping archived
    reports from the research set would silently truncate a town's history at
    whatever date its map got busy, and would do it to longitudinal analyses
    that are the whole point of the export. Only the resident's own unlisted
    choice keeps a row out. Please leave it that way.
    """
    return (
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.is_public.is_(True),
    )


def client_info(request: Optional[Request]) -> tuple:
    """(ip, user_agent) for the access log, honouring the proxy header."""
    if request is None:
        return None, None
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip = forwarded_for.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    return ip, (request.headers.get("User-Agent") or "")[:500] or None


async def log_research_access(
    db: AsyncSession,
    user_id: int,
    username: str,
    action: str,
    parameters: dict,
    record_count: int,
    privacy_mode: str = "fuzzed",
    request: Optional[Request] = None,
):
    """Log research data access for audit purposes"""
    ip_address, user_agent = client_info(request)
    log_entry = ResearchAccessLog(
        user_id=user_id,
        username=username,
        action=action,
        parameters=parameters,
        record_count=record_count,
        privacy_mode=privacy_mode,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(log_entry)
    await db.commit()


# Street suffixes used to spot in-text addresses ("123 Maple Ave").
_STREET_SUFFIX = (
    r'St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|'
    r'Pl|Place|Ter|Terrace|Way|Cir|Circle|Pkwy|Parkway|Hwy|Highway|Trl|Trail'
)


def sanitize_description(description: str) -> str:
    """Mask PII patterns in description text.

    Best-effort pattern redaction, not guaranteed de-identification. It now also
    catches street addresses, unit numbers, URLs, social handles, and untitled
    two-word capitalized names — the previous version only caught phones, emails,
    and names carrying a title (Mr./Dr.), so "call Sarah Whitman at 12 Maple Ave"
    passed through almost intact.

    Deliberately conservative on names: only a capitalized pair immediately after
    a person-referring cue word is redacted, so ordinary place names ("Maple Park
    Playground") survive for research use.
    """
    if not description:
        return ""
    result = description

    # Phone numbers
    for pattern in (
        r'\(\d{3}\)\s?\d{3}[-.\s]?\d{4}',
        r'\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b',
        r'\b\d{10}\b',
    ):
        result = re.sub(pattern, '[PHONE REDACTED]', result)

    # Email addresses
    result = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL REDACTED]', result)

    # URLs and social handles
    result = re.sub(r'https?://\S+|www\.\S+', '[URL REDACTED]', result)
    result = re.sub(r'(?<![\w@])@[A-Za-z0-9_]{3,}', '[HANDLE REDACTED]', result)

    # Street addresses: number + optional street name + suffix
    result = re.sub(
        rf'\b\d+[A-Za-z]?\s+(?:[A-Z][a-zA-Z]*\.?\s+){{0,3}}(?:{_STREET_SUFFIX})\b\.?',
        '[ADDRESS REDACTED]', result, flags=re.IGNORECASE,
    )

    # Unit / apartment identifiers
    result = re.sub(r'\b(?:Apt|Apartment|Unit|Suite|Ste|#)\s*\.?\s*[A-Za-z]?\d+[A-Za-z]?\b',
                    '[UNIT REDACTED]', result, flags=re.IGNORECASE)

    # Titled names ("Mr. Smith", "Dr. Jane Doe")
    result = re.sub(r'\b(?:Mr|Mrs|Ms|Dr|Miss|Sgt|Officer|Chief)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?',
                    '[NAME REDACTED]', result)

    # Untitled names, only after an explicit person cue.
    result = re.sub(
        r'\b(?:my name is|i am|i\'m|contact|ask for|spoke (?:to|with)|talked to|'
        r'resident|neighbor|owner|tenant|call)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        lambda m: m.group(0).replace(m.group(1), '[NAME REDACTED]'), result, flags=re.IGNORECASE,
    )

    return result


def fuzz_location(lat: float, long: float, grid_size: float = 0.0003) -> tuple:
    """Snap coordinates to grid (~100ft precision for privacy)"""
    if lat is None or long is None:
        return None, None
    fuzzed_lat = round(lat / grid_size) * grid_size
    fuzzed_long = round(long / grid_size) * grid_size
    return round(fuzzed_lat, 6), round(fuzzed_long, 6)


def anonymize_address(address: str, privacy_mode: str) -> str:
    """Anonymize address based on privacy mode.

    Fuzzed mode drops the STREET NAME as well as the house number. The previous
    form ("Main Street (Block), West Windsor") kept the street, and a named
    street joined against the ~100ft-snapped coordinates in the same row
    narrows a report to a handful of houses — the street name added
    re-identification risk while the coordinates already carried the research
    signal. What survives is the locality after the first comma:

        "123 Main Street, West Windsor" -> "Block near West Windsor"

    Admin `exact` mode is unchanged.
    """
    if not address:
        return ""

    if privacy_mode == "exact":
        return address

    parts = address.split(',')
    locality = ','.join(parts[1:]).strip()
    # Mask any digits that survive in the locality (ZIPs, route numbers).
    locality = re.sub(r'\d+', 'X', locality)
    if locality:
        return f"Block near {locality}"
    # Single-segment address: nothing safely coarse enough to keep.
    return "Block (street withheld)"


def get_infrastructure_category(service_code: str) -> str:
    """Map service code to infrastructure category for civil engineering research"""
    if not service_code:
        return "other"
    
    code_lower = service_code.lower()
    for key, category in INFRASTRUCTURE_CATEGORIES.items():
        if key in code_lower:
            return category
    return "other"


def calculate_business_hours(start: datetime, end: datetime) -> float:
    """Calculate business hours between two datetimes (Mon-Fri 8am-5pm)"""
    if not start or not end:
        return None
    
    total_hours = 0
    current = start
    
    while current < end:
        # Skip weekends
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            # Business hours: 8am to 5pm
            day_start = current.replace(hour=8, minute=0, second=0, microsecond=0)
            day_end = current.replace(hour=17, minute=0, second=0, microsecond=0)
            
            if current < day_start:
                work_start = day_start
            else:
                work_start = current
            
            if end < day_end:
                work_end = end
            else:
                work_end = day_end
            
            if work_start < work_end and work_start >= day_start:
                total_hours += (work_end - work_start).total_seconds() / 3600
        
        # Move to next day
        current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    return round(total_hours, 2) if total_hours > 0 else None


def get_time_period(dt: datetime) -> dict:
    """Extract time period information for temporal analysis"""
    if not dt:
        return {}
    
    return {
        "hour_of_day": dt.hour,
        "day_of_week": dt.strftime("%A"),
        "day_of_week_num": dt.weekday(),
        "month": dt.strftime("%B"),
        "month_num": dt.month,
        "quarter": f"Q{(dt.month - 1) // 3 + 1}",
        "year": dt.year,
        "is_weekend": dt.weekday() >= 5,
        "is_business_hours": 8 <= dt.hour < 17 and dt.weekday() < 5,
    }


def generate_zone_id(lat: float, long: float) -> str:
    """Generate anonymized zone ID for geographic clustering without revealing exact location"""
    if lat is None or long is None:
        return None
    
    # Create larger grid cells (~0.5 mile zones)
    zone_lat = round(lat / 0.007) * 0.007
    zone_long = round(long / 0.007) * 0.007

    # Keyed hash, not a bare MD5 of the coordinates. An unsalted digest over a
    # coarse coordinate grid is trivially reversible — an attacker can hash every
    # cell in a state (a few million) and look the value up, recovering the cell.
    # HMAC with the deployment's SECRET_KEY removes that offline attack.
    zone_str = f"{zone_lat:.3f},{zone_long:.3f}"
    secret = (getattr(settings, "secret_key", None) or "pinpoint-zone-salt").encode()
    zone_hash = hmac.new(secret, zone_str.encode(), hashlib.sha256).hexdigest()[:8]
    return f"ZONE-{zone_hash.upper()}"


def get_season(dt: datetime) -> str:
    """Determine season from datetime for infrastructure/weather correlation research"""
    if not dt:
        return None
    month = dt.month
    if month in [12, 1, 2]:
        return "winter"
    elif month in [3, 4, 5]:
        return "spring"
    elif month in [6, 7, 8]:
        return "summer"
    else:
        return "fall"


async def get_income_quintile_from_zone(zone_id: str, census_geoid: str = None) -> Optional[int]:
    """
    Get income quintile (1-5) from Census ACS median household income data.
    Uses Census ACS 5-year estimates table B19013 (Median Household Income).
    
    Quintiles based on national median income (~$75,000):
    1 = <$30k, 2 = $30-50k, 3 = $50-75k, 4 = $75-100k, 5 = >$100k
    """
    if not census_geoid:
        return None
    
    # Try to get from ACS data cache
    acs_data = await get_census_acs_data(census_geoid)
    if acs_data and acs_data.get("median_income"):
        income = acs_data["median_income"]
        if income < 30000:
            return 1
        elif income < 50000:
            return 2
        elif income < 75000:
            return 3
        elif income < 100000:
            return 4
        else:
            return 5
    
    return None


# Cache for Census ACS data (tract GEOID -> data dict)
_census_acs_cache: dict = {}


async def get_census_acs_data(census_geoid: str) -> Optional[dict]:
    """
    Get Census ACS 5-year estimates for a census tract.
    Includes: median income, total population, housing tenure, and land area.
    
    Uses Census Bureau API (free, requires API key stored in system_secrets).
    Results are cached to minimize API calls.
    
    Variables:
    - B19013_001E: Median household income
    - B01003_001E: Total population
    - B25003_001E: Total occupied housing units
    - B25003_003E: Renter-occupied units
    """
    if not census_geoid or len(census_geoid) < 11:
        return None
    
    # Check cache first
    if census_geoid in _census_acs_cache:
        return _census_acs_cache[census_geoid]
    
    try:
        import httpx
        
        # Parse GEOID: SSCCCTTTTTT (State 2 + County 3 + Tract 6)
        state_fips = census_geoid[:2]
        county_fips = census_geoid[2:5]
        tract = census_geoid[5:]
        
        # ACS 5-year estimates (most reliable for tract level)
        year = 2022  # Most recent complete ACS 5-year
        base_url = f"https://api.census.gov/data/{year}/acs/acs5"
        
        # Variables to fetch
        variables = "B19013_001E,B01003_001E,B25003_001E,B25003_003E"
        
        # Note: Census API works without key for limited requests
        # For production, add: &key={api_key}
        url = f"{base_url}?get={variables}&for=tract:{tract}&in=state:{state_fips}&in=county:{county_fips}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if len(data) >= 2:
                # First row is headers, second is data
                headers = data[0]
                values = data[1]
                
                result = {}
                for i, header in enumerate(headers):
                    if header == "B19013_001E":
                        result["median_income"] = int(values[i]) if values[i] and values[i] != "-666666666" else None
                    elif header == "B01003_001E":
                        result["total_population"] = int(values[i]) if values[i] else None
                    elif header == "B25003_001E":
                        result["total_housing_units"] = int(values[i]) if values[i] else None
                    elif header == "B25003_003E":
                        result["renter_units"] = int(values[i]) if values[i] else None
                
                # Calculate renter percentage
                if result.get("total_housing_units") and result.get("renter_units"):
                    result["renter_pct"] = round(result["renter_units"] / result["total_housing_units"], 2)
                
                _census_acs_cache[census_geoid] = result
                return result
                
    except Exception as e:
        logger.warning(f"Census ACS API error for {census_geoid}: {e}")

    # Transient failure: don't cache. A negative cache entry here would blank the
    # ACS-derived fields (income, SVI, tenure) for this tract for the rest of the
    # process, turning one timeout into permanently missing data.
    return None


async def get_population_density_category(zone_id: str, census_geoid: str = None) -> Optional[str]:
    """
    Get population density category from Census ACS population data.
    Uses Census ACS B01003 (Total Population) and tract land area.
    
    Categories based on population per square mile:
    - low: < 1,000 per sq mi (rural/suburban)
    - medium: 1,000 - 5,000 per sq mi (suburban)
    - high: > 5,000 per sq mi (urban)
    """
    if not census_geoid:
        return None
    
    acs_data = await get_census_acs_data(census_geoid)
    if acs_data and acs_data.get("total_population"):
        pop = acs_data["total_population"]
        
        # Estimate density - average US tract is ~1.5 sq mi
        # For accurate density, would need TIGER shapefile land area
        # Using population thresholds as proxy (typical tract ~4000 people)
        if pop < 2000:
            return "low"
        elif pop < 6000:
            return "medium"
        else:
            return "high"
    
    return None



# ============================================================================
# SOCIAL EQUITY PACK - For Sociologists
# ============================================================================

# Cache for Census GEOID lookups to avoid repeated API calls
_census_geoid_cache: dict = {}

async def get_census_tract_geoid(lat: float, lng: float) -> Optional[str]:
    """
    Get 11-digit FIPS code (Census Tract GEOID) from coordinates.
    Uses US Census Bureau Geocoder API (free, no key required).
    Returns format: SSCCCTTTTTT (State + County + Tract)
    
    Results are cached to avoid repeated API calls for same location.
    """
    # NOTE: this used to return a synthetic FIPS code derived from the
    # coordinates whenever demo mode was on. That fake tract then drove the
    # income/SVI/tenure lookups, so the export looked like real Census-linked
    # data. Demo mode is gone and there is no synthetic path: every deployment
    # resolves the real tract (the geocoder is free and keyless), and if it
    # can't be resolved the fields are simply empty. Never substitute a
    # plausible-looking tract for one that failed to resolve.
    if lat is None or lng is None:
        return None

    # Fuzz before egress: exact coordinates never leave for the Census geocoder,
    # in any privacy mode. A ~100ft grid snap almost never changes which tract a
    # point falls in, and the tract is the only thing we keep — so sending the
    # raw point bought nothing and disclosed a resident's location to a third
    # party. Snapped here, at the boundary, so no caller can forget.
    lat, lng = fuzz_location(lat, lng)

    # Round to reduce cache key variations (within ~100m)
    cache_key = f"{round(lat, 3)},{round(lng, 3)}"
    
    if cache_key in _census_geoid_cache:
        return _census_geoid_cache[cache_key]
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
                params={
                    "x": lng,
                    "y": lat,
                    "benchmark": "Public_AR_Current",
                    "vintage": "Current_Current",
                    "layers": "Census Tracts",
                    "format": "json"
                },
                timeout=3
            )
        if response.status_code == 200:
            data = response.json()
            geographies = data.get("result", {}).get("geographies", {})
            tracts = geographies.get("Census Tracts", [])
            if tracts:
                geoid = tracts[0].get("GEOID")
                _census_geoid_cache[cache_key] = geoid
                return geoid
            # A clean 200 with no tract is a real answer (e.g. offshore) — cache it.
            _census_geoid_cache[cache_key] = None
            return None
    except Exception as e:
        logger.warning(f"Census geocoder error: {e}")

    # Transient failure (timeout, non-200, network error): do NOT cache. Caching
    # it would permanently blank this location for the life of the process, so a
    # one-off blip would silently strip Census fields from every later export.
    return None


async def get_social_vulnerability_index(census_geoid: str) -> Optional[float]:
    """
    Calculate CDC-style Social Vulnerability Index (SVI) for a census tract.
    SVI ranges from 0 (lowest vulnerability) to 1 (highest vulnerability).
    
    Uses Census ACS data to approximate CDC SVI methodology:
    - Lower income = higher vulnerability
    - Higher renter % = higher vulnerability
    - Higher population = higher vulnerability (urban stress)
    
    CDC SVI uses 16 variables across 4 themes; this is a simplified approximation
    using the variables we already fetch from ACS.
    """
    if not census_geoid:
        return None
    
    acs_data = await get_census_acs_data(census_geoid)
    if not acs_data:
        return None
    
    # Calculate vulnerability score from available ACS data
    svi_components = []
    
    # Income vulnerability (lower income = higher vulnerability)
    # National median ~$75,000, poverty threshold ~$30,000
    median_income = acs_data.get("median_income")
    if median_income:
        # Normalize: $30k = 1.0 (high vuln), $150k = 0.0 (low vuln)
        income_vuln = max(0, min(1, (150000 - median_income) / 120000))
        svi_components.append(income_vuln)
    
    # Housing vulnerability (higher renter % = higher vulnerability)
    renter_pct = acs_data.get("renter_pct")
    if renter_pct is not None:
        svi_components.append(renter_pct)
    
    # Population density stress (rough proxy for urban challenges)
    total_pop = acs_data.get("total_population")
    if total_pop:
        # Normalize: 1000 = 0.0, 10000 = 1.0
        pop_vuln = max(0, min(1, (total_pop - 1000) / 9000))
        svi_components.append(pop_vuln * 0.5)  # Weight less than income/housing
    
    if svi_components:
        # Average all components for final SVI
        return round(sum(svi_components) / len(svi_components), 3)
    
    return None


async def get_housing_tenure_mix(census_geoid: str) -> Optional[float]:
    """
    Get percentage of renters vs owners in census tract.
    Returns renter percentage (0.0 to 1.0).
    
    Uses Census ACS 5-year estimates table B25003:
    - B25003_001E: Total occupied housing units
    - B25003_003E: Renter-occupied units
    
    Hypothesis: Renters may under-report infrastructure issues.
    """
    if not census_geoid:
        return None
    
    acs_data = await get_census_acs_data(census_geoid)
    if acs_data and acs_data.get("renter_pct") is not None:
        return acs_data["renter_pct"]
    
    return None


async def build_equity_map(requests, privacy_mode: str = "fuzzed", packs: Optional[dict] = None) -> dict:
    """Resolve the Census-derived equity fields for a set of requests, up front.

    These lookups are async (real Census Geocoder + ACS calls), but the CSV
    export streams from a *synchronous* generator where awaiting is impossible.
    Calling them there produced un-awaited coroutines that were stringified into
    the file, so every row shipped "<coroutine object ...>" instead of data.

    Resolving them here — once, before streaming — keeps the values real. Work is
    deduplicated by rounded coordinate so a batch of nearby requests costs one
    Census round-trip instead of one per row.

    Weather is resolved here too, for the same reason (its API call is async now,
    and it previously blocked the event loop from inside the request loop).

    `packs` is the {pack_id: bool} map from enabled_packs(). Off = the data is
    NEVER GENERATED, not generated-then-hidden: with social_equity off no call
    leaves for the Census geocoder, ACS or CDC SVI at all; with
    environmental_context off no weather lookup fires. Anything computed here
    exists in memory (and in third-party request logs) even if a column filter
    later drops it, and under public-records law data that exists can be
    requested — so a disabled pack's data must simply never come into being.
    None (the default) means "all packs on" for callers outside the export
    paths; both exports always pass the admin's real switches.

    Returns {request.id: {census_geoid, income_band, population_density,
                          social_vulnerability_index, housing_tenure_renter_pct,
                          weather}}.
    """
    equity_on = packs is None or packs.get("social_equity", True)
    weather_on = packs is None or packs.get("environmental_context", True)

    by_coord: dict = {}
    by_weather: dict = {}
    out: dict = {}

    if not equity_on and not weather_on:
        return {req.id: {} for req in requests}

    for req in requests:
        if req.lat is None or req.long is None:
            out[req.id] = {}
            continue
        # Fuzz before egress: every external lookup below (Census geocoder, and
        # weather further down) gets the ~100ft-snapped point, never the raw
        # one — regardless of the export's privacy mode, because what we KEEP
        # from these calls (tract, daily weather) is coarser than the snap.
        f_lat, f_long = fuzz_location(req.lat, req.long)
        key = (round(f_lat, 4), round(f_long, 4))
        if equity_on and key not in by_coord:
            geoid = await get_census_tract_geoid(f_lat, f_long)
            zone = generate_zone_id(req.lat, req.long)

            # Prefer the OFFICIAL CDC/ATSDR SVI (nationally ranked, 16 variables,
            # 4 themes). Only if CDC is unavailable do we fall back to the local
            # ACS approximation — and `svi_source` always records which one it is,
            # so the two are never silently conflated in analysis.
            cdc = await get_cdc_svi(geoid)
            if cdc:
                svi_value = cdc["overall"]
                svi_source = "cdc_svi_official"
                svi_themes = cdc.get("themes") or {}
            else:
                svi_value = await get_social_vulnerability_index(geoid)
                svi_source = "acs_approximation" if svi_value is not None else None
                svi_themes = {}

            by_coord[key] = {
                "census_geoid": geoid,
                "income_band": await get_income_quintile_from_zone(zone, geoid),
                "population_density": await get_population_density_category(zone, geoid),
                "social_vulnerability_index": svi_value,
                "svi_source": svi_source,
                "svi_themes": svi_themes,
                "housing_tenure_renter_pct": await get_housing_tenure_mix(geoid),
            }
        entry = dict(by_coord.get(key, {}))

        # One weather lookup per (date, rounded location) — a day's worth of
        # requests in one neighborhood costs a single call, not one per row.
        if weather_on:
            wkey = (req.requested_datetime.date() if req.requested_datetime else None, key)
            if wkey not in by_weather:
                by_weather[wkey] = await get_weather_context(req.requested_datetime, f_lat, f_long)
            entry["weather"] = by_weather[wkey]

        out[req.id] = entry

    # Percentile-rank the vulnerability scores across the tracts actually present
    # in this export, so the value is interpretable relative to the jurisdiction.
    _apply_svi_percentiles(out)
    if privacy_mode != "exact":
        _suppress_small_tracts(out)
    return out


# Minimum records sharing a census tract before tract-level attributes are
# released. Below this, a tract join can single out an individual report.
K_ANONYMITY_THRESHOLD = 5

# Tract-derived fields withheld together — releasing any one of them can
# re-identify the tract via a public ACS lookup, so they suppress as a group.
_TRACT_FIELDS = (
    "census_geoid",
    "income_band",
    "population_density",
    "social_vulnerability_index",
    "svi_source",
    "housing_tenure_renter_pct",
)


def _suppress_small_tracts(enrichment: dict, k: int = K_ANONYMITY_THRESHOLD) -> None:
    """Blank tract-level attributes for tracts with fewer than k records.

    Small-cell suppression: a census tract represented by one or two requests can
    be joined against public ACS data to narrow down who filed them. Withholding
    the tract block below a threshold is the standard mitigation and is what
    reviewers expect before tract-level equity data leaves the building.

    Admins exporting in `exact` privacy mode are exempt (already audit-logged).
    """
    counts: dict = {}
    for e in enrichment.values():
        geoid = e.get("census_geoid")
        if geoid:
            counts[geoid] = counts.get(geoid, 0) + 1

    for e in enrichment.values():
        geoid = e.get("census_geoid")
        if geoid and counts.get(geoid, 0) < k:
            for field in _TRACT_FIELDS:
                e[field] = None
            e["tract_suppressed"] = True


def _apply_svi_percentiles(enrichment: dict) -> None:
    """Convert raw vulnerability scores into 0-1 percentile ranks in-place.

    CDC's published SVI is a percentile rank against every US tract. We cannot
    reproduce that from per-tract API calls, so we rank within the tracts present
    in this export and label it accordingly in the data dictionary. Ranking makes
    the number comparable across categories in the same jurisdiction, which is
    what equity comparisons here actually need.
    """
    by_tract = {}
    for e in enrichment.values():
        # Official CDC values are ALREADY national percentiles — re-ranking them
        # against a handful of local tracts would destroy their meaning.
        if e.get("svi_source") != "acs_approximation":
            continue
        geoid, raw = e.get("census_geoid"), e.get("social_vulnerability_index")
        if geoid and raw is not None:
            by_tract[geoid] = raw
    if len(by_tract) < 2:
        return  # a single tract has no meaningful ranking; leave the raw score

    ordered = sorted(by_tract.items(), key=lambda kv: kv[1])
    ranks = {geoid: round(i / (len(ordered) - 1), 3) for i, (geoid, _) in enumerate(ordered)}
    for e in enrichment.values():
        geoid = e.get("census_geoid")
        if geoid in ranks:
            e["social_vulnerability_index"] = ranks[geoid]


# ============================================================================
# ENVIRONMENTAL CONTEXT PACK - For Urban Planners
# ============================================================================

async def get_weather_context(requested_datetime: datetime, lat: float, lng: float) -> dict:
    """
    Get observed weather for the report date from the Open-Meteo API (free, no key).

    Async + httpx: this used to call blocking `requests.get` from inside an async
    endpoint, stalling the whole event loop for up to 3s per record. When the call
    fails the fields are left empty — never estimated.
    """
    if not requested_datetime or lat is None or lng is None:
        return {"precip_24h_mm": None, "temp_max_c": None, "temp_min_c": None, "weather_code": None}

    # Fuzz before egress: daily weather is nearest-city-scale data, so two
    # decimals (~1 km) is all the precision the answer can use. Sending the
    # exact report coordinates to Open-Meteo disclosed a resident's location
    # for zero gain in accuracy. Rounded here, at the boundary.
    lat, lng = round(lat, 2), round(lng, 2)

    # Format date for API
    date_str = requested_datetime.strftime("%Y-%m-%d")
    
    try:
        # Open-Meteo Archive API (free, no key required) - for dates up to 5 days ago
        # For very recent dates, use forecast API with past_days parameter
        days_ago = (datetime.now() - requested_datetime).days
        
        if days_ago > 5:
            # Use archive API for historical data
            url = f"https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": lat,
                "longitude": lng,
                "start_date": date_str,
                "end_date": date_str,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "timezone": "America/New_York"
            }
        else:
            # Use forecast API with past_days for recent data
            url = f"https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lng,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "past_days": min(days_ago + 1, 7),
                "timezone": "America/New_York"
            }
        
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=3)

        if response.status_code == 200:
            data = response.json()
            daily = data.get("daily", {})
            
            # Find the matching date index
            dates = daily.get("time", [])
            if date_str in dates:
                idx = dates.index(date_str)
                temp_max = daily.get("temperature_2m_max", [None])[idx]
                temp_min = daily.get("temperature_2m_min", [None])[idx]
                precip = daily.get("precipitation_sum", [None])[idx]
                weather_code = daily.get("weather_code", [None])[idx]
                
                return {
                    "precip_24h_mm": round(precip, 1) if precip is not None else None,
                    "temp_max_c": round(temp_max, 1) if temp_max is not None else None,
                    "temp_min_c": round(temp_min, 1) if temp_min is not None else None,
                    "weather_code": weather_code
                }
    except Exception as e:
        logger.warning(f"Weather API error; leaving weather fields empty: {e}")

    # No usable observation. Return empty values rather than inventing them.
    #
    # This previously synthesized temperatures from a hardcoded seasonal table and
    # precipitation from an MD5 hash of the date, and wrote them into the same
    # columns as real readings with no way to tell them apart — researchers would
    # have analyzed fabricated weather as if it were observed. Missing data must
    # read as missing.
    return {
        "precip_24h_mm": None,
        "temp_max_c": None,
        "temp_min_c": None,
        "weather_code": None,
    }


def get_asset_age_years(matched_asset: dict) -> Optional[float]:
    """
    Extract asset installation age from matched_asset properties.
    Enables "Survival Analysis" on infrastructure.
    """
    if not matched_asset or not isinstance(matched_asset, dict):
        return None
    
    properties = matched_asset.get("properties", {})
    
    # Look for common installation date fields
    install_date = (
        properties.get("install_date") or
        properties.get("installation_date") or
        properties.get("installed") or
        properties.get("year_installed") or
        properties.get("date_installed")
    )
    
    if install_date:
        try:
            if isinstance(install_date, int) and 1900 < install_date < 2100:
                # Year only
                return datetime.now().year - install_date
            elif isinstance(install_date, str):
                # Try parsing date string
                for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y"]:
                    try:
                        parsed = datetime.strptime(install_date[:10], fmt)
                        return round((datetime.now() - parsed).days / 365.25, 1)
                    except Exception:
                        continue
        except Exception:
            pass  # Date parsing failed entirely
    
    return None


def get_matched_asset_attributes(matched_asset: dict) -> str:
    """
    Serialize the full matched_asset properties to JSON string.
    
    This allows researchers to access 100% of the asset data (e.g., hydrant pressure_psi,
    park acres, streetlight bulb type) without breaking CSV schema stability.
    
    Sanitizes by removing internal system keys if necessary.
    
    Example output: '{"bulb": "LED", "pole_id": "SL-99", "install_year": 2018}'
    """
    if not matched_asset or not isinstance(matched_asset, dict):
        return "{}"
    
    properties = matched_asset.get("properties", {})
    
    if not properties:
        return "{}"
    
    # Optionally remove internal/system keys (prefixed with _ or containing 'id')
    sanitized = {
        k: v for k, v in properties.items()
        if not k.startswith("_") and k not in ["internal_id", "system_id", "layer_internal_id"]
    }
    
    try:
        return json.dumps(sanitized, default=str)
    except Exception:
        return "{}"


# ============================================================================
# SENTIMENT & TRUST PACK - For Political Science
# ============================================================================

def sentiment_method() -> str:
    """Which sentiment implementation is active — reported in the data dictionary
    so researchers know what produced the score."""
    return "vader" if _vader() is not None else "keyword_fallback"


_VADER_ANALYZER = None
_VADER_TRIED = False


def _vader():
    """Lazily load VADER (MIT, pure-Python, no model download).

    VADER is a published, validated rule-based sentiment model that handles
    negation ("not good"), intensifiers ("very"), punctuation and capitalization —
    none of which the previous raw keyword count did. If the package isn't
    installed we fall back to the keyword scorer rather than failing the export.
    """
    global _VADER_ANALYZER, _VADER_TRIED
    if not _VADER_TRIED:
        _VADER_TRIED = True
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _VADER_ANALYZER = SentimentIntensityAnalyzer()
        except Exception as e:  # pragma: no cover - depends on optional dep
            logger.info(f"VADER unavailable, using keyword sentiment fallback: {e}")
            _VADER_ANALYZER = None
    return _VADER_ANALYZER


def analyze_sentiment(text: str) -> float:
    """
    Sentiment of the description, -1.0 (angry) to +1.0 (grateful).

    Uses VADER when available (handles negation/intensifiers); otherwise falls
    back to the original keyword count. `sentiment_method()` reports which ran.
    """
    if not text:
        return 0.0

    analyzer = _vader()
    if analyzer is not None:
        # compound is already normalized to [-1, 1].
        return round(analyzer.polarity_scores(text)["compound"], 2)

    return _keyword_sentiment(text)


def _keyword_sentiment(text: str) -> float:
    """Original keyword scorer — fallback only. No negation handling."""
    text_lower = text.lower()
    
    # Positive indicators
    positive_words = [
        "thank", "please", "appreciate", "grateful", "wonderful",
        "excellent", "great", "good", "helpful", "kind"
    ]
    
    # Negative indicators
    negative_words = [
        "angry", "frustrated", "unacceptable", "ridiculous", "terrible",
        "awful", "horrible", "incompetent", "useless", "waste",
        "disgrace", "pathetic", "outrageous", "absurd", "shame"
    ]
    
    # Urgency/frustration phrases
    frustration_phrases = [
        "again", "still", "nothing has been done", "weeks", "months",
        "multiple times", "how many times", "sick of", "fed up"
    ]
    
    positive_count = sum(1 for word in positive_words if word in text_lower)
    negative_count = sum(1 for word in negative_words if word in text_lower)
    frustration_count = sum(1 for phrase in frustration_phrases if phrase in text_lower)
    
    # Calculate score (-1 to +1)
    score = (positive_count - negative_count - frustration_count * 0.5) / 5.0
    return round(max(-1.0, min(1.0, score)), 2)


def days_to_first_staff_action(req) -> Optional[float]:
    """Days from submission to the FIRST staff action, from the audit log.

    Previously this used `updated_datetime`, which is the *most recent* update —
    so a request touched months later reported a huge "first response" time. The
    audit log is the only place the genuine first action is recorded.
    """
    if not req.requested_datetime or not getattr(req, "audit_logs", None):
        return None
    staff_times = [
        a.created_at for a in req.audit_logs
        if a.created_at and a.actor_type in ("staff", "admin") and a.created_at > req.requested_datetime
    ]
    if not staff_times:
        return None
    return round((min(staff_times) - req.requested_datetime).total_seconds() / 86400, 2)


def count_status_changes(req) -> int:
    """Number of actual status changes, from the audit log.

    Previously this was len(audit_logs) — every audit entry of any kind
    (comments, assignment, edits), which overstated the real count.
    """
    if not getattr(req, "audit_logs", None):
        return 0
    return sum(1 for a in req.audit_logs if a.action == "status_change")


def detect_trust_indicators(text: str) -> dict:
    """
    Detect phrases indicating prior interactions or eroding trust.
    
    Returns flags for common "repeat reporter" patterns.
    """
    if not text:
        return {"is_repeat_report": False, "prior_report_mentioned": False, "frustration_expressed": False}
    
    text_lower = text.lower()
    
    # Patterns indicating this has been reported before
    repeat_patterns = [
        r"(third|3rd|fourth|4th|fifth|5th|multiple) time",
        r"reported (this |it )?(before|already|previously|last)",
        r"(still|again) (waiting|nothing|broken|not fixed)",
        r"(weeks|months|years) (ago|later|now)",
        r"follow.?up",
        r"same (problem|issue|thing)"
    ]
    
    prior_mention_patterns = [
        r"ticket.?#?\d+",
        r"case.?#?\d+",
        r"request.?#?\d+",
        r"reference.?#?\d+",
        r"(last|previous) (report|request|complaint|ticket)"
    ]
    
    frustration_patterns = [
        r"(unacceptable|ridiculous|terrible|disgrace)",
        r"(do nothing|done nothing|no action)",
        r"(waste of|wasting) (time|money|tax)",
        r"(how (long|many)|when will)"
    ]
    
    is_repeat = any(re.search(p, text_lower) for p in repeat_patterns)
    prior_mentioned = any(re.search(p, text_lower) for p in prior_mention_patterns)
    frustration = any(re.search(p, text_lower) for p in frustration_patterns)
    
    return {
        "is_repeat_report": is_repeat,
        "prior_report_mentioned": prior_mentioned,
        "frustration_expressed": frustration
    }


# ============================================================================
# BUREAUCRATIC FRICTION PACK - For Public Administration
# ============================================================================

def calculate_time_to_triage(requested_datetime: datetime, audit_logs: list) -> Optional[float]:
    """
    Calculate hours from Submission to First Status Change (In Progress).
    Measures government responsiveness vs workload.
    """
    if not requested_datetime or not audit_logs:
        return None
    
    # Find first status change to 'in_progress'
    for log in sorted(audit_logs, key=lambda x: x.created_at if x.created_at else datetime.max):
        if log.action == "status_change" and log.new_value == "in_progress":
            if log.created_at:
                delta = log.created_at - requested_datetime
                return round(delta.total_seconds() / 3600, 2)
    
    return None


def count_reassignments(audit_logs: list) -> int:
    """
    Count how many times the request bounced between departments.
    Measures bureaucratic inefficiency.
    """
    if not audit_logs:
        return 0
    
    reassignments = sum(1 for log in audit_logs if log.action == "department_assigned")
    # First assignment isn't a "re"assignment
    return max(0, reassignments - 1)


def is_off_hours_submission(requested_datetime: datetime) -> bool:
    """
    Check if submitted outside normal hours (before 6am or after 10pm).
    Implies high urgency or shift-worker.
    """
    if not requested_datetime:
        return False
    
    hour = requested_datetime.hour
    return hour < 6 or hour >= 22


def calculate_escalation_occurred(audit_logs: list) -> bool:
    """
    Check if priority was manually escalated (increased).
    """
    if not audit_logs:
        return False
    
    for log in audit_logs:
        if log.action == "priority_change":
            try:
                old_priority = int(log.old_value) if log.old_value else 5
                new_priority = int(log.new_value) if log.new_value else 5
                # Lower number = higher priority
                if new_priority < old_priority:
                    return True
            except (ValueError, TypeError):
                continue
    
    return False


@router.get("/status")
async def research_status(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """Check if Research Suite is enabled"""
    enabled = await check_research_enabled(db)
    return {
        "enabled": enabled,
        "user": current_user.username,
        "role": current_user.role
    }


@router.get("/analytics")
async def get_analytics(
    request: Request,
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    service_code: Optional[str] = Query(None, description="Filter by service category"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """Get aggregate analytics (no PII exposed)"""
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    base_conditions = list(research_visibility_conditions())

    if start_date:
        base_conditions.append(ServiceRequest.requested_datetime >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        base_conditions.append(ServiceRequest.requested_datetime <= datetime.combine(end_date, datetime.max.time()))
    if service_code:
        base_conditions.append(ServiceRequest.service_code == service_code)
    
    # Total count
    total_query = select(func.count(ServiceRequest.id)).where(*base_conditions)
    total_result = await db.execute(total_query)
    total_count = total_result.scalar() or 0
    
    # Status distribution
    status_query = select(
        ServiceRequest.status,
        func.count(ServiceRequest.id)
    ).where(*base_conditions).group_by(ServiceRequest.status)
    status_result = await db.execute(status_query)
    status_distribution = {row[0]: row[1] for row in status_result.all()}
    
    # Average resolution time
    closed_conditions = base_conditions + [
        ServiceRequest.status == "closed",
        ServiceRequest.closed_datetime.isnot(None)
    ]
    avg_resolution_query = select(
        func.avg(
            func.extract('epoch', ServiceRequest.closed_datetime - ServiceRequest.requested_datetime) / 3600.0
        )
    ).where(*closed_conditions)
    avg_result = await db.execute(avg_resolution_query)
    avg_resolution_hours = avg_result.scalar()
    
    # Category distribution
    category_query = select(
        ServiceRequest.service_code,
        ServiceRequest.service_name,
        func.count(ServiceRequest.id)
    ).where(*base_conditions).group_by(
        ServiceRequest.service_code, 
        ServiceRequest.service_name
    ).order_by(func.count(ServiceRequest.id).desc())
    category_result = await db.execute(category_query)
    category_distribution = [
        {"code": row[0], "name": row[1], "count": row[2]} 
        for row in category_result.all()
    ]
    
    # Source distribution (civic engagement metric)
    source_query = select(
        ServiceRequest.source,
        func.count(ServiceRequest.id)
    ).where(*base_conditions).group_by(ServiceRequest.source)
    source_result = await db.execute(source_query)
    source_distribution = {row[0] or "unknown": row[1] for row in source_result.all()}
    
    # Temporal patterns (for equity/civics research)
    hour_query = select(
        extract('hour', ServiceRequest.requested_datetime).label('hour'),
        func.count(ServiceRequest.id)
    ).where(*base_conditions).group_by('hour').order_by('hour')
    hour_result = await db.execute(hour_query)
    hourly_distribution = {int(row[0]): row[1] for row in hour_result.all() if row[0] is not None}
    
    # Day of week distribution
    dow_query = select(
        extract('dow', ServiceRequest.requested_datetime).label('dow'),
        func.count(ServiceRequest.id)
    ).where(*base_conditions).group_by('dow').order_by('dow')
    dow_result = await db.execute(dow_query)
    dow_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    daily_distribution = {dow_names[int(row[0])]: row[1] for row in dow_result.all() if row[0] is not None}
    
    await log_research_access(
        db, current_user.id, current_user.username, "view_analytics",
        {"start_date": str(start_date), "end_date": str(end_date), "service_code": service_code},
        total_count, request=request
    )
    
    return {
        "total_requests": total_count,
        "status_distribution": status_distribution,
        "avg_resolution_hours": round(avg_resolution_hours, 2) if avg_resolution_hours else None,
        "category_distribution": category_distribution,
        "source_distribution": source_distribution,
        "hourly_distribution": hourly_distribution,
        "daily_distribution": daily_distribution,
        "filters_applied": {
            "start_date": str(start_date) if start_date else None,
            "end_date": str(end_date) if end_date else None,
            "service_code": service_code
        }
    }


@router.get("/export/csv")
async def export_csv(
    request: Request,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    service_code: Optional[str] = Query(None),
    privacy_mode: str = Query("fuzzed", description="Location privacy: 'fuzzed' or 'exact' (requires admin)"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """
    Export sanitized request data as CSV for research analysis.

    Rows come from build_dataset_row — the same builder the staff export uses —
    and columns from allowed_research_columns, so the admin's per-pack switches
    are enforced here at row build, not in any UI.
    """
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    if privacy_mode == "exact" and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Exact location export requires admin privileges"
        )

    query = select(ServiceRequest).options(
        selectinload(ServiceRequest.comments),
        selectinload(ServiceRequest.audit_logs)
    ).where(*research_visibility_conditions())

    if start_date:
        query = query.where(ServiceRequest.requested_datetime >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.where(ServiceRequest.requested_datetime <= datetime.combine(end_date, datetime.max.time()))
    if service_code:
        query = query.where(ServiceRequest.service_code == service_code)

    query = query.order_by(ServiceRequest.requested_datetime.desc())

    result = await db.execute(query)
    requests = result.scalars().all()

    await log_research_access(
        db, current_user.id, current_user.username, "export_csv",
        {"start_date": str(start_date), "end_date": str(end_date), "service_code": service_code},
        len(requests), privacy_mode, request=request
    )

    # Pack switches, enforced server-side at BOTH layers: a pack the admin
    # turned off is never computed (packs= short-circuits inside
    # build_equity_map/build_dataset_row — no Census/weather call fires, no
    # sentiment score is produced) AND its columns never enter the file
    # (fieldnames + extrasaction="ignore"). Never-computed is the load-bearing
    # half: generated data exists and can be records-requested even if a
    # column filter hid it.
    system_settings = await get_system_settings(db)
    packs = enabled_packs(system_settings)
    columns = allowed_research_columns(system_settings)

    # Resolve the async Census/equity lookups before streaming — the generator
    # below is synchronous and cannot await (see build_equity_map).
    equity_map = await build_equity_map(requests, privacy_mode, packs)

    def generate_csv():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for req in requests:
            writer.writerow(build_dataset_row(req, equity_map.get(req.id, {}), privacy_mode, packs=packs))
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    
    filename = f"research_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/export/geojson")
async def export_geojson(
    request: Request,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    service_code: Optional[str] = Query(None),
    privacy_mode: str = Query("fuzzed"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """
    Export sanitized request data as GeoJSON for GIS analysis.

    Properties are the SAME rows the CSV emits (build_dataset_row, filtered by
    the admin's pack switches); coordinates live in the geometry, so the
    latitude/longitude columns are dropped from properties.
    """
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    if privacy_mode == "exact" and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Exact location export requires admin privileges"
        )

    query = select(ServiceRequest).options(
        selectinload(ServiceRequest.comments),
        selectinload(ServiceRequest.audit_logs)
    ).where(
        *research_visibility_conditions(),
        ServiceRequest.lat.isnot(None),
        ServiceRequest.long.isnot(None)
    )

    if start_date:
        query = query.where(ServiceRequest.requested_datetime >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.where(ServiceRequest.requested_datetime <= datetime.combine(end_date, datetime.max.time()))
    if service_code:
        query = query.where(ServiceRequest.service_code == service_code)

    result = await db.execute(query)
    requests = result.scalars().all()

    await log_research_access(
        db, current_user.id, current_user.username, "export_geojson",
        {"start_date": str(start_date), "end_date": str(end_date), "service_code": service_code},
        len(requests), privacy_mode, request=request
    )

    system_settings = await get_system_settings(db)
    # Pack switches, enforced server-side — same double gate as the CSV:
    # off-pack data is never computed AND its columns never ship.
    packs = enabled_packs(system_settings)
    columns = allowed_research_columns(system_settings)

    # Real awaited Census/equity lookups, deduplicated by coordinate — skipped
    # entirely for packs the admin turned off.
    equity_map = await build_equity_map(requests, privacy_mode, packs)
    property_columns = [c for c in columns if c not in ("latitude", "longitude")]

    features = []
    for req in requests:
        # Privacy-aware location for the geometry.
        if privacy_mode == "fuzzed":
            lat, long = fuzz_location(req.lat, req.long)
        else:
            lat, long = req.lat, req.long

        if lat is None or long is None:
            continue

        row = build_dataset_row(req, equity_map.get(req.id, {}), privacy_mode, packs=packs)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [long, lat]
            },
            "properties": {k: row.get(k) for k in property_columns},
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "exported_at": datetime.now().isoformat(),
            "privacy_mode": privacy_mode,
            "record_count": len(features),
            "coordinate_precision": "fuzzed_100ft" if privacy_mode == "fuzzed" else "exact",
            # Disclose the suppression rule so a blank tract field reads as
            # "withheld for privacy", not "the lookup failed".
            "small_cell_suppression": (
                {"applied": True, "k": K_ANONYMITY_THRESHOLD,
                 "note": f"Census-tract fields are withheld for tracts with fewer than "
                         f"{K_ANONYMITY_THRESHOLD} records in this export."}
                if privacy_mode != "exact" else {"applied": False}
            ),
            # Derived from COLUMN_DICTIONARY (enabled packs only) — the previous
            # hand-written copy had already drifted from the dictionary.
            "research_packs": {
                pack_id: [f["name"] for f in meta["fields"]]
                for pack_id, meta in packs_with_fields(system_settings).items()
            },
        }
    }

    filename = f"research_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.geojson"

    return StreamingResponse(
        iter([json.dumps(geojson, indent=2)]),
        media_type="application/geo+json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/data-dictionary")
async def get_data_dictionary(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """
    Get data dictionary explaining all fields in exports.
    Essential for research documentation and analysis reproducibility.

    Derived entirely from COLUMN_DICTIONARY and the pack switch table — this
    used to be a second hand-written copy of the schema and had already
    drifted from what the exports actually emit. Columns whose pack the admin
    turned off are omitted here exactly as they are from the files.
    """
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    system_settings = await get_system_settings(db)
    allowed = set(allowed_research_columns(system_settings))
    packs = packs_with_fields(system_settings)

    response = {
        "version": "2.0",
        "fields": {
            name: {"type": col_type, "description": desc, "research_pack": pack}
            for name, col_type, desc, pack in COLUMN_DICTIONARY
            if name in allowed
        },
        "core_fields": [
            {"name": name, "type": col_type, "description": desc}
            for name, col_type, desc, pack in COLUMN_DICTIONARY
            if pack == "Core"
        ],
        "research_packs": packs,
        "privacy": {
            "fuzzed_mode": (
                "Coordinates snapped to ~100ft grid; house number and street "
                "name withheld; timestamps at day precision; census-tract fields "
                f"suppressed below {K_ANONYMITY_THRESHOLD} records per tract."
            ),
            "exact_mode": "Admin only, audit-logged: full coordinates, addresses and timestamps.",
        },
        "pack_switches": (
            "What the research pack switches do and do not control: a pack an "
            "administrator has switched OFF is never exported AND never computed "
            "— no sentiment score is produced, no Census/CDC or weather lookup "
            "is made, and nothing derived for that pack is stored anywhere, so "
            "no record of it exists to be disclosed under public-records law. "
            "Fields that exist for day-to-day operations (intake moderation "
            "flags, AI triage results) are stored by those operational features "
            "under their own feature switches; the pack switches control only "
            "whether research exports DISCLOSE those stored fields, and the "
            "stored operational records remain subject to records requests like "
            "any other operational record."
        ),
    }
    # Which sentiment implementation produced sentiment_score — only meaningful
    # when the pack is on.
    if packs.get("sentiment_trust", {}).get("enabled"):
        response["sentiment_method"] = sentiment_method()
    return response


# The pack switch table — the ONE place a pack's identity and default live.
# `id` is the key in system_settings.research_packs; an absent key means the
# pack's `default_on`. Two packs default OFF: their fields are town-authored
# characterizations of a resident's own message (tone scores, "possibly
# abusive" flags), which carry real OPRA/litigation exposure — a town enables
# those deliberately, it does not discover it shipped them.
RESEARCH_PACKS_DEF = {
    "social_equity": {
        "label": "Social Equity Pack",
        "audience": "Equity Analysts, Social Researchers",
        "default_on": True,
        "suggested_analyses": [
            "Join with Census ACS for demographic correlation",
            "SVI vs response time regression",
            "Renter vs owner reporting rate comparison",
            "Income quintile service disparity analysis",
        ],
    },
    "environmental_context": {
        "label": "Environmental Context Pack",
        "audience": "Planners, Engineers, Operations Staff",
        "default_on": True,
        "suggested_analyses": [
            "Freeze-thaw cycle pothole correlation",
            "Asset age survival analysis",
            "Precipitation-drainage issue linkage",
            "Seasonal maintenance optimization",
        ],
    },
    "sentiment_trust": {
        "label": "Sentiment & Trust Pack",
        "audience": "Civic Engagement Analysts, Administrators",
        "default_on": False,
        "why_default_off": (
            "Per-row tone and behavior labels are the town's own characterization "
            "of a resident's message. Enable deliberately."
        ),
        "suggested_analyses": [
            "Repeat report resolution success rates",
            "Trust erosion indicators over time",
            "Politeness variation by submission channel",
        ],
    },
    "bureaucratic_friction": {
        "label": "Bureaucratic Friction Pack",
        "audience": "Operations Managers, Process Analysts",
        "default_on": True,
        "suggested_analyses": [
            "Triage time vs resolution outcome",
            "Department routing efficiency audit",
            "Off-hours urgent issue patterns",
            "AI escalation accuracy study",
        ],
    },
    "ai_ml_research": {
        "label": "AI/ML Research Pack",
        "audience": "Data Scientists, AI/ML Engineers",
        "default_on": True,
        "suggested_analyses": [
            "AI-human priority alignment study",
            "Classification accuracy compared to final service_code",
        ],
    },
    "moderation": {
        "label": "Moderation Pack",
        "audience": "Data Scientists, Trust & Safety Researchers",
        "default_on": False,
        "why_default_off": (
            "A per-row record that the town's filter deemed a resident's message "
            "possibly abusive is a town-authored accusation. Enable deliberately."
        ),
        "suggested_analyses": [
            "Flagging accuracy and false positive rates",
        ],
    },
}

# pack label (as written in COLUMN_DICTIONARY) -> pack id
_PACK_LABEL_TO_ID = {meta["label"]: pack_id for pack_id, meta in RESEARCH_PACKS_DEF.items()}


def enabled_packs(system_settings) -> dict:
    """{pack_id: bool} resolved against the stored per-pack switches.

    Absent/NULL key = the pack's own default, so an upgrade changes nothing for
    the always-on packs while the two liability packs stay off until enabled.
    """
    stored = {}
    if system_settings is not None and getattr(system_settings, "research_packs", None):
        stored = system_settings.research_packs or {}
    return {
        pack_id: bool(stored.get(pack_id, meta["default_on"]))
        for pack_id, meta in RESEARCH_PACKS_DEF.items()
    }


def column_pack_id(column_name: str) -> Optional[str]:
    """Pack id a column belongs to, or None for Core/unlisted (always ships)."""
    for name, _type, _desc, pack in COLUMN_DICTIONARY:
        if name == column_name:
            return _PACK_LABEL_TO_ID.get(pack)
    return None


def allowed_research_columns(system_settings) -> list:
    """RESEARCH_COLUMNS filtered by the admin's pack switches.

    This is THE enforcement point semantics: every research output (CSV,
    GeoJSON properties, data dictionary, codebook, chat prompt) selects its
    columns through this, so a pack switched off disappears everywhere at once.
    Core identification/timing columns are in no pack and always ship.
    """
    packs = enabled_packs(system_settings)
    return [
        c for c in RESEARCH_COLUMNS
        if column_pack_id(c) is None or packs.get(column_pack_id(c), True)
    ]


def packs_with_fields(system_settings=None, include_disabled: bool = False) -> dict:
    """Pack metadata with field lists, derived from COLUMN_DICTIONARY.

    The one derivation the /data-dictionary block, the GeoJSON metadata, the
    admin toggles and the frontend pack display all share — the previous
    hand-written copies had already drifted apart on has_photos/moderation.
    """
    packs = enabled_packs(system_settings)
    out = {}
    for pack_id, meta in RESEARCH_PACKS_DEF.items():
        if not include_disabled and not packs[pack_id]:
            continue
        out[pack_id] = {
            "label": meta["label"],
            "audience": meta["audience"],
            "default_on": meta["default_on"],
            "enabled": packs[pack_id],
            "fields": [
                {"name": name, "type": col_type, "description": desc}
                for name, col_type, desc, pack in COLUMN_DICTIONARY
                if _PACK_LABEL_TO_ID.get(pack) == pack_id
            ],
            "suggested_analyses": meta.get("suggested_analyses", []),
            **({"why_default_off": meta["why_default_off"]} if "why_default_off" in meta else {}),
        }
    return out


# Single source of truth for exported columns: (name, type, description, pack).
# Used by the data-dictionary export AND the chat assistant's field counts so
# researchers are never quoted a number that disagrees with the actual export.
COLUMN_DICTIONARY = [
    # Core identifiers
    ("request_id", "string", "Unique identifier for the service request", "Core"),
    ("service_code", "string", "Category code for the type of issue (e.g., pothole, streetlight)", "Core"),
    ("service_name", "string", "Human-readable category name", "Core"),
    ("infrastructure_category", "string", "Grouped infrastructure type (roads_pavement, stormwater, etc.)", "Core"),
    ("matched_asset_type", "string", "Type of linked infrastructure asset from GIS layer", "Core"),
    ("matched_asset_attributes", "JSON string", "Full properties of matched asset (pressure_psi, install_year, etc.)", "Environmental Context Pack"),
    
    # Issue details. `description_sanitized` is deliberately NOT here: pattern
    # redaction over resident free text is best-effort, and one miss ships a
    # name or address in a file that leaves the building. Researchers get the
    # word count and the derived scores instead of the prose.
    ("description_word_count", "integer", "Word count of the resident's description (the text itself is not exported)", "Core"),
    ("has_photos", "boolean", "Whether request includes photo attachments", "Core"),
    ("photo_count", "integer", "Number of photos attached to request", "Core"),

    # AI Analysis. `ai_summary_sanitized` removed for the same reason as the
    # description: model-written prose can restate the PII it summarized.
    ("moderation_flagged", "boolean", "Flagged for staff review by the content-moderation wordlist at intake (not AI)", "Moderation Pack"),
    ("moderation_flag_reason", "string", "Flag reason, e.g. \"Auto-flagged: profanity\" (PII patterns redacted)", "Moderation Pack"),
    ("ai_priority_score", "float (1-10)", "AI-generated priority score (10=highest)", "AI/ML Research Pack"),
    ("ai_analyzed", "boolean", "Whether AI has processed this request", "AI/ML Research Pack"),
    ("ai_vs_manual_priority_diff", "float", "manual_priority - ai_priority (positive = human prioritized higher)", "AI/ML Research Pack"),
    
    # Status & Resolution
    ("status", "string", "Current status: open, in_progress, closed", "Core"),
    ("closed_substatus", "string", "How closed: resolved, no_action, third_party", "Core"),
    ("priority", "integer (1-10)", "Priority level (1-10 scale, 10=highest)", "Core"),
    ("resolution_outcome", "string", "Standardized outcome: completed, no_action_needed, referred_external, etc.", "Core"),
    
    # Location (privacy-aware)
    ("address_anonymized", "string", "Locality only in fuzzed mode (\"Block near <area>\" — house number AND street name withheld)", "Core"),
    ("latitude", "float", "Latitude coordinate (snapped to ~100ft grid in fuzzed mode)", "Core"),
    ("longitude", "float", "Longitude coordinate (snapped to ~100ft grid in fuzzed mode)", "Core"),
    ("zone_id", "string", "Anonymous geographic zone (~0.5 mile cells) for clustering", "Core"),
    
    # Social Equity Pack
    ("census_tract_geoid", "string", "11-digit FIPS code from US Census Bureau Geocoder API", "Social Equity Pack"),
    ("social_vulnerability_index", "float (0-1)", "Social vulnerability percentile, 0=least to 1=most vulnerable. Official CDC/ATSDR SVI when available (see svi_source)", "Social Equity Pack"),
    ("svi_source", "string", "Provenance of social_vulnerability_index: 'cdc_svi_official' (CDC/ATSDR, nationally ranked, 16 variables) or 'acs_approximation' (local ACS-derived fallback, ranked within this export only). Do not pool the two.", "Social Equity Pack"),
    ("housing_tenure_renter_pct", "float (0-1)", "Renter percentage in zone (derived from GEOID)", "Social Equity Pack"),
    ("income_quintile", "integer (1-5)", "Anonymized income quintile of zone (1=lowest)", "Social Equity Pack"),
    ("population_density", "string", "Density category: low, medium, high", "Social Equity Pack"),
    
    # Environmental Context Pack
    ("weather_precip_24h_mm", "float", "Precipitation in 24h before report (mm) from Open-Meteo API", "Environmental Context Pack"),
    ("weather_temp_max_c", "float", "Max temperature on report day (Celsius) from Open-Meteo API", "Environmental Context Pack"),
    ("weather_temp_min_c", "float", "Min temperature on report day (Celsius) from Open-Meteo API", "Environmental Context Pack"),
    ("weather_code", "integer", "WMO weather code (e.g., 0=clear, 61=rain, 71=snow)", "Environmental Context Pack"),
    ("nearby_asset_age_years", "float", "Age of matched infrastructure asset in years", "Environmental Context Pack"),
    
    # Sentiment & Trust Pack
    ("sentiment_score", "float (-1 to +1)", "VADER rule-based sentiment (handles negation/intensifiers)", "Sentiment & Trust Pack"),
    ("is_repeat_report", "boolean", "Text indicates prior report of same issue", "Sentiment & Trust Pack"),
    ("prior_report_mentioned", "boolean", "Text references a prior ticket/case number", "Sentiment & Trust Pack"),
    ("frustration_expressed", "boolean", "Text contains trust erosion indicators", "Sentiment & Trust Pack"),
    
    # Temporal fields. Day precision in fuzzed mode: an exact submission second
    # is a quasi-identifier (join it with a social post or a call log and the
    # reporter falls out), while the hour/day-of-week columns below already
    # carry the temporal signal research actually uses.
    ("submitted_datetime", "ISO8601", "When the request was submitted (day precision in fuzzed mode)", "Core"),
    ("closed_datetime", "ISO8601", "When the request was closed (null if still open; day precision in fuzzed mode)", "Core"),
    ("updated_datetime", "ISO8601", "When the request was last updated (day precision in fuzzed mode)", "Core"),
    ("submission_hour", "integer (0-23)", "Hour of day when submitted", "Core"),
    ("submission_day_of_week", "integer (0-6)", "Day of week when submitted (0=Monday)", "Core"),
    ("submission_month", "integer (1-12)", "Month when submitted", "Core"),
    ("submission_year", "integer", "Year when submitted", "Core"),
    ("is_weekend_submission", "boolean", "Whether submitted on Saturday or Sunday", "Core"),
    ("is_business_hours_submission", "boolean", "Whether submitted Mon-Fri 8am-5pm", "Core"),
    ("season", "string", "Season at time of submission: winter, spring, summer, fall", "Environmental Context Pack"),
    
    # Bureaucratic Friction Pack
    ("time_to_triage_hours", "float", "Hours from submission to first 'In Progress' status", "Bureaucratic Friction Pack"),
    ("reassignment_count", "integer", "Number of times request was reassigned between departments", "Bureaucratic Friction Pack"),
    ("off_hours_submission", "boolean", "Submitted before 6am or after 10pm", "Bureaucratic Friction Pack"),
    ("escalation_occurred", "boolean", "Priority was manually increased by staff", "Bureaucratic Friction Pack"),
    ("total_hours_to_resolve", "float", "Total clock hours from submission to closure", "Bureaucratic Friction Pack"),
    ("business_hours_to_resolve", "float", "Business hours only (Mon-Fri 8am-5pm) to resolve", "Bureaucratic Friction Pack"),
    ("days_to_first_update", "float", "Days from submission to the first staff action (from the audit log)", "Bureaucratic Friction Pack"),
    ("status_change_count", "integer", "Number of status changes (audit entries with action=status_change)", "Bureaucratic Friction Pack"),
    
    # Civic Engagement
    ("submission_channel", "string", "How submitted: portal, phone, walk_in, email", "Core"),
    ("department_id", "integer", "ID of assigned department", "Core"),
    ("comment_count", "integer", "Total comments on request (internal + external)", "Core"),
    ("public_comment_count", "integer", "Public/external comments visible to reporter", "Core"),
]


@router.get("/export/data-dictionary")
async def export_data_dictionary_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """
    Download data dictionary as CSV file.
    This companion file explains every column in the research exports.
    Download this alongside your data export for reference.
    """
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    # Column documentation lives at module scope (COLUMN_DICTIONARY) so the
    # dictionary export and the chat assistant report the SAME field counts.
    # Filtered by the pack switches: a codebook describing columns the export
    # withholds would read as a bug (or an invitation) to the researcher.
    allowed = set(allowed_research_columns(await get_system_settings(db)))
    rows = [r for r in COLUMN_DICTIONARY if r[0] in allowed]

    # The codebook download is research access like any other — it discloses
    # exactly what this town exports, so it leaves the same audit trail.
    await log_research_access(
        db, current_user.id, current_user.username, "export_data_dictionary",
        {}, len(rows), "n/a", request=request
    )

    def generate_csv():
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(["column_name", "data_type", "description", "research_pack"])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        # Data rows
        for col_name, col_type, description, pack in rows:
            writer.writerow([col_name, col_type, description, pack])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
    
    filename = f"data_dictionary_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/code-snippets")
async def get_code_snippets(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """Get R and Python code snippets for fetching and analyzing data"""
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")
    
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    system_settings = result.scalar_one_or_none()
    base_url = f"https://{system_settings.custom_domain}" if system_settings and system_settings.custom_domain else "https://your-311-domain.com"
    
    python_snippet = f'''# Python - Research Data Analysis
import requests
import pandas as pd
import geopandas as gpd
from datetime import datetime

API_URL = "{base_url}/api/research"
TOKEN = "your_jwt_token_here"
headers = {{"Authorization": f"Bearer {{TOKEN}}"}}

# 1. Get data dictionary
dictionary = requests.get(f"{{API_URL}}/data-dictionary", headers=headers).json()
print("Available fields:", list(dictionary['fields'].keys()))

# 2. Download CSV with research fields
response = requests.get(
    f"{{API_URL}}/export/csv",
    headers=headers,
    params={{"privacy_mode": "fuzzed"}}
)
with open("research_data.csv", "w") as f:
    f.write(response.text)

df = pd.read_csv("research_data.csv")

# 3. Civil Engineering Analysis
infra_counts = df.groupby('infrastructure_category').size().sort_values(ascending=False)
print("Issues by infrastructure type:\\n", infra_counts)

# 4. Equity Analysis - Response time by zone
zone_response = df.groupby('zone_id')['business_hours_to_resolve'].mean()
print("Avg response time by zone:\\n", zone_response.describe())

# 5. Civics Analysis - Submission patterns
hourly = df.groupby('submission_hour').size()
print("Submissions by hour:\\n", hourly)

# 6. GeoSpatial Analysis with GeoPandas
geojson_resp = requests.get(
    f"{{API_URL}}/export/geojson",
    headers=headers,
    params={{"privacy_mode": "fuzzed"}}
)
gdf = gpd.read_file(geojson_resp.text)
gdf.plot(column='infrastructure_category', legend=True, figsize=(10, 10))
'''

    r_snippet = f'''# R - Research Data Analysis
library(httr)
library(jsonlite)
library(dplyr)
library(ggplot2)
library(sf)

API_URL <- "{base_url}/api/research"
TOKEN <- "your_jwt_token_here"
headers <- add_headers(Authorization = paste("Bearer", TOKEN))

# 1. Get data dictionary
dict_resp <- GET(paste0(API_URL, "/data-dictionary"), headers)
dictionary <- fromJSON(content(dict_resp, "text"))

# 2. Download CSV
csv_resp <- GET(paste0(API_URL, "/export/csv"), headers, 
                query = list(privacy_mode = "fuzzed"))
write(content(csv_resp, "text"), "research_data.csv")
df <- read.csv("research_data.csv")

# 3. Civil Engineering Analysis
infra_counts <- df %>% 
  group_by(infrastructure_category) %>% 
  summarise(count = n()) %>%
  arrange(desc(count))
print(infra_counts)

# 4. Equity Analysis - Response time by zone
zone_response <- df %>%
  group_by(zone_id) %>%
  summarise(avg_response = mean(business_hours_to_resolve, na.rm = TRUE))
summary(zone_response$avg_response)

# 5. Civics - Hourly submission patterns
ggplot(df, aes(x = submission_hour)) +
  geom_histogram(binwidth = 1, fill = "steelblue") +
  labs(title = "Request Submissions by Hour", x = "Hour", y = "Count")

# 6. GeoSpatial with sf
geojson_resp <- GET(paste0(API_URL, "/export/geojson"), headers)
gdf <- st_read(content(geojson_resp, "text"))
plot(gdf["infrastructure_category"])
'''

    return {
        "python": python_snippet,
        "r": r_snippet
    }


@router.get("/packs")
async def get_research_pack_switches(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_admin)
):
    """Pack switch metadata for the Admin Console toggles.

    Admin-only, and deliberately NOT gated on the research_portal module flag:
    an admin decides which packs a town releases BEFORE turning the portal on,
    and the toggles have to render either way. Field lists come from
    COLUMN_DICTIONARY so the "contains:" disclosure can never disagree with
    what the export actually ships.
    """
    packs = packs_with_fields(await get_system_settings(db), include_disabled=True)
    return [
        {
            "id": pack_id,
            "label": meta["label"],
            "audience": meta["audience"],
            "enabled": meta["enabled"],
            "default_on": meta["default_on"],
            "contains": [f["name"] for f in meta["fields"]],
            **({"why_default_off": meta["why_default_off"]} if "why_default_off" in meta else {}),
        }
        for pack_id, meta in packs.items()
    ]


@router.get("/access-logs")
async def get_access_logs(
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """Get research access audit logs (admin only)"""
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")
    
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required to view access logs"
        )
    
    query = select(ResearchAccessLog).order_by(ResearchAccessLog.created_at.desc()).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return [
        {
            "id": log.id,
            "username": log.username,
            "action": log.action,
            "parameters": log.parameters,
            "record_count": log.record_count,
            "privacy_mode": log.privacy_mode,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "created_at": log.created_at.isoformat() if log.created_at else None
        }
        for log in logs
    ]


# ============================================================================
# RESEARCH AI CHAT — Conversational AI for researchers and staff
# ============================================================================

from pydantic import BaseModel
from typing import List

class ResearchChatMessage(BaseModel):
    role: str
    content: str

class ResearchChatRequest(BaseModel):
    message: str
    history: List[ResearchChatMessage] = []

class ResearchChatResponse(BaseModel):
    response: str
    context_used: List[str]


@router.post("/chat", response_model=ResearchChatResponse)
async def research_chat(
    request: Request,
    body: ResearchChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_researcher)
):
    """
    Conversational AI assistant — answers questions about
    data fields, methodology, research packs, export formats, and analysis techniques.
    Uses the township's Vertex AI (Gemini) credentials.
    """
    if not await check_research_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Research Suite is not enabled")

    context_used = []

    # Get township info
    sys_settings = await get_system_settings(db)
    township_name = getattr(sys_settings, 'township_name', 'the municipality') or 'the municipality'
    context_used.append("system_settings")

    # Get basic analytics for context. Same visibility rule as every research
    # query: unlisted reports stay out of the counts quoted to researchers.
    all_requests_result = await db.execute(
        select(ServiceRequest).where(*research_visibility_conditions())
    )
    all_requests = all_requests_result.scalars().all()
    total = len(all_requests)
    open_count = sum(1 for r in all_requests if r.status == "open")
    closed_count = sum(1 for r in all_requests if r.status == "closed")

    categories = {}
    for r in all_requests:
        cat = r.service_name or "Unknown"
        categories[cat] = categories.get(cat, 0) + 1

    context_used.append("request_analytics")

    # Field tables generated from COLUMN_DICTIONARY, filtered by the admin's
    # pack switches — the chat must describe exactly what the export ships, no
    # more. The previous hand-written tables had drifted (they still promised
    # ai_flagged/ai_flag_reason, which no export emits).
    allowed = set(allowed_research_columns(sys_settings))
    packs = packs_with_fields(sys_settings)
    core_rows = [c for c in COLUMN_DICTIONARY if c[3] == "Core"]
    core_field_count = len(core_rows)
    total_field_count = len(allowed)

    def _field_lines(rows):
        return chr(10).join(f"| {name} | {col_type} | {desc} |" for name, col_type, desc in rows)

    core_fields_section = (
        f"## CORE FIELDS ({core_field_count} fields, always included)\n"
        "| Field | Type | Description |\n|-------|------|-------------|\n"
        + _field_lines([(n, t, d) for n, t, d, _p in core_rows])
    )
    suggested_analyses_lines = [
        f"- {analysis}"
        for meta in packs.values()
        for analysis in meta.get("suggested_analyses", [])
    ]
    suggested_analyses_section = chr(10).join(suggested_analyses_lines) or "- Response-time and category distribution studies"

    pack_sections = chr(10).join(
        f"## RESEARCH PACK: {meta['label']} ({meta['audience']})\n"
        "| Field | Type | Description |\n|-------|------|-------------|\n"
        + _field_lines([(f["name"], f["type"], f["description"]) for f in meta["fields"]])
        for meta in packs.values()
    )

    system_prompt = f"""You are a data assistant for the {township_name} Pinpoint 311 Research & Analytics Lab. You help researchers and municipal staff understand the available data, methodology, and analysis techniques.

## YOUR ROLE
- Help users understand the {total_field_count} available data fields
- Explain research methodology and data sources
- Suggest analyses and statistical approaches
- Answer questions about data formats, privacy modes, and export options
- Be precise about field definitions — users need exact specifications
- Only describe the fields listed below; packs an administrator has switched off are not exported and must not be promised

## CURRENT DATASET
- **Township:** {township_name}
- **Total service requests:** {total}
- **Open:** {open_count} | **Closed:** {closed_count}
- **Categories:** {json.dumps(dict(sorted(categories.items(), key=lambda x: x[1], reverse=True)[:10]))}

{core_fields_section}

{pack_sections}

## PRIVACY MODES
- **Fuzzed (default):** Coordinates snapped to ~100ft grid, house number AND street name withheld, timestamps at day precision. Safe for most research.
- **Exact (admin only):** Full precision coordinates, addresses and timestamps. Use with appropriate data governance approval.

## EXPORT FORMATS
- **CSV:** Standard tabular format with all {total_field_count} fields. UTF-8 encoded.
- **GeoJSON:** Spatial data with geometry + properties. For QGIS, ArcGIS, Folium, etc.
- **Data Dictionary:** Column descriptions CSV for codebook generation.

## METHODOLOGY NOTES
- Resident free text (description, AI summary) is never exported — only derived metrics (word count, scores)
- Census data uses ACS 5-year estimates (most stable for tract-level analysis)
- Official CDC/ATSDR SVI is used when available; the local ACS approximation is marked in svi_source
- Location fuzzing uses grid-snapping (not random noise) — maintains relative spatial patterns
- Weather data from Open-Meteo Archive API (free, reliable for historical dates)
- Infrastructure categories are mapped from service_code via keyword matching

## SUGGESTED ANALYSES
{suggested_analyses_section}

## RESPONSE RULES
- Be precise about field definitions and data types
- Suggest specific statistical methods (e.g., "use Welch's t-test" not "compare means")
- Include code snippets in Python (pandas) or R when helpful
- Recommend appropriate visualization types
- Cite data source limitations honestly
- Never fabricate field names or capabilities
- NEVER use markdown tables (| col | col |) — the chat UI cannot render them. Use bullet lists or bold labels instead. For example, instead of a table use: **field_name** (type) — description
- Use **bold**, `code`, bullet points (- ), and code blocks (```) for formatting — these all render correctly
- Avoid markdown headers (#, ##) in responses — use **bold text** on its own line for section titles instead"""

    # Build conversation
    conversation = system_prompt + "\n\n## CONVERSATION\n"
    for msg in body.history[-20:]:
        role_label = "User" if msg.role == "user" else "Data Assistant"
        conversation += f"\n**{role_label}:** {msg.content}\n"
    conversation += f"\n**User:** {body.message}\n\n**Data Assistant:**"

    # Call Vertex AI
    try:
        import os
        from app.services.secret_manager import get_secret as sm_get_secret

        project_id = await sm_get_secret("VERTEX_AI_PROJECT")
        if not project_id:
            project_id = os.getenv("GOOGLE_VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")

        if not project_id:
            raise HTTPException(status_code=503, detail="Vertex AI not configured")

        service_account_json = await sm_get_secret("VERTEX_AI_SERVICE_ACCOUNT_KEY")

        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account as sa_module
        import aiohttp

        if service_account_json:
            sa_info = json.loads(service_account_json)
            credentials = sa_module.Credentials.from_service_account_info(
                sa_info,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
        else:
            credentials, _ = google.auth.default(
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )

        credentials.refresh(Request())

        endpoint = f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/global/publishers/google/models/gemini-3.1-flash-lite:generateContent"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": conversation}]}],
            "generationConfig": {
                "temperature": 0.3,
                "topP": 0.8,
                "maxOutputTokens": 4096,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Vertex AI research chat error: {error_text}")
                    raise HTTPException(status_code=502, detail=f"AI service error: {response.status}")

                result = await response.json()

        # Extract response text
        ai_response = ""
        if 'candidates' in result and result['candidates']:
            parts = result['candidates'][0].get('content', {}).get('parts', [])
            for part in parts:
                if 'text' in part:
                    ai_response += part['text']

        if not ai_response:
            ai_response = "I wasn't able to generate a response. Please try rephrasing your question."

        # Log the research chat access
        await log_research_access(
            db, current_user.id, current_user.username,
            "ai_chat", {"message_preview": body.message[:100]},
            0, "n/a", request=request
        )

        return ResearchChatResponse(response=ai_response, context_used=context_used)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Research chat error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get AI response")



# Ordered analytical schema shared by the research export and the staff export,
# so the two can never drift apart (drift is what produced the documented-vs-
# actual mismatches this module was audited for).
RESEARCH_COLUMNS = [
    "request_id",
    "service_code",
    "service_name",
    "infrastructure_category",
    "matched_asset_type",
    "matched_asset_attributes",
    # `description_sanitized` and `ai_summary_sanitized` are deliberately absent:
    # resident free text (and model prose derived from it) no longer ships in
    # analytical exports — redaction is best-effort and one miss is a disclosure.
    "description_word_count",
    "has_photos",
    "photo_count",
    "moderation_flagged",
    "moderation_flag_reason",
    "ai_priority_score",
    "ai_analyzed",
    "ai_vs_manual_priority_diff",
    "status",
    "closed_substatus",
    "priority",
    "resolution_outcome",
    "address_anonymized",
    "latitude",
    "longitude",
    "zone_id",
    "census_tract_geoid",
    "social_vulnerability_index",
    "svi_source",
    "housing_tenure_renter_pct",
    "income_quintile",
    "population_density",
    "weather_precip_24h_mm",
    "weather_temp_max_c",
    "weather_temp_min_c",
    "weather_code",
    "nearby_asset_age_years",
    "sentiment_score",
    "is_repeat_report",
    "prior_report_mentioned",
    "frustration_expressed",
    "submitted_datetime",
    "closed_datetime",
    "updated_datetime",
    "submission_hour",
    "submission_day_of_week",
    "submission_month",
    "submission_year",
    "is_weekend_submission",
    "is_business_hours_submission",
    "season",
    "time_to_triage_hours",
    "reassignment_count",
    "off_hours_submission",
    "escalation_occurred",
    "total_hours_to_resolve",
    "business_hours_to_resolve",
    "days_to_first_update",
    "status_change_count",
    "submission_channel",
    "department_id",
    "comment_count",
    "public_comment_count",
]


def build_dataset_row(req, _eq, privacy_mode, *, packs=None, operational=False, include_pii=False) -> dict:
    """Build one analytical row.

    Shared by the privacy-preserving research export and the staff operational
    export. `_eq` is this request's entry from build_equity_map().

    `packs` is the {pack_id: bool} map from enabled_packs(). A pack that is OFF
    is never computed here — analyze_sentiment never runs over the resident's
    text, the friction metrics are never derived, the AI/moderation fields are
    never read out of the record — its keys are simply emitted as None (and the
    column filter drops them from the file). Compute-then-omit is not enough:
    a characterization that was generated exists, and anything that exists can
    be pulled into a records request. None (the default) means "all packs on";
    both export paths always pass the admin's real switches.

    operational=True adds the columns staff legitimately need for their work
    (raw description, exact address/coordinates, assignee, staff notes) on top of
    the analytical schema. include_pii=True additionally adds reporter contact
    details — that path is admin-gated and audit-logged by the caller, exactly as
    before; nothing here widens who can see PII.
    """
    def _on(pack_id):
        return packs is None or packs.get(pack_id, True)

    lat, long = (req.lat, req.long) if privacy_mode == "exact" else fuzz_location(req.lat, req.long)
    address_anon = anonymize_address(req.address, privacy_mode)

    def _timestamp(dt):
        # Day precision outside exact mode: a full-second timestamp is a
        # quasi-identifier; the hour/day-of-week columns carry the signal.
        if not dt:
            return None
        return dt.isoformat() if privacy_mode == "exact" else dt.date().isoformat()

    photo_count = len(req.media_urls) if req.media_urls else 0
    has_photos = bool(req.media_urls and len(req.media_urls) > 0)
    desc_word_count = len(req.description.split()) if req.description else 0
    infra_category = get_infrastructure_category(req.service_code)
    asset_type = None
    if req.matched_asset and isinstance(req.matched_asset, dict):
        asset_type = req.matched_asset.get('asset_type') or req.matched_asset.get('layer_name')
    zone_id = generate_zone_id(req.lat, req.long)
    time_info = get_time_period(req.requested_datetime)

    # Environmental Context Pack — derived only when the pack is on.
    if _on("environmental_context"):
        asset_attributes = get_matched_asset_attributes(req.matched_asset)
        asset_age = get_asset_age_years(req.matched_asset)
        season = get_season(req.requested_datetime)
        weather = (_eq or {}).get("weather") or {}
    else:
        asset_attributes, asset_age, season, weather = None, None, None, {}

    # Sentiment & Trust Pack — the town's own characterization of a resident's
    # message. Pack off = the scores are never produced, not produced-and-hidden.
    if _on("sentiment_trust"):
        sentiment = analyze_sentiment(req.description)
        trust = detect_trust_indicators(req.description)
    else:
        sentiment, trust = None, {}

    total_comments = len(req.comments) if req.comments else 0
    public_comments = len([c for c in req.comments if c.visibility == 'external']) if req.comments else 0

    # Bureaucratic Friction Pack — derived from the audit log only when on.
    if _on("bureaucratic_friction"):
        resolution_hours = None
        if req.closed_datetime and req.requested_datetime:
            resolution_hours = round((req.closed_datetime - req.requested_datetime).total_seconds() / 3600, 2)
        business_hours = calculate_business_hours(req.requested_datetime, req.closed_datetime)
        time_to_triage = calculate_time_to_triage(req.requested_datetime, req.audit_logs)
        reassignments = count_reassignments(req.audit_logs)
        off_hours = is_off_hours_submission(req.requested_datetime)
        escalation = calculate_escalation_occurred(req.audit_logs)
        status_changes = count_status_changes(req)
        days_to_first_update = days_to_first_staff_action(req)
    else:
        resolution_hours = business_hours = time_to_triage = None
        reassignments = off_hours = escalation = status_changes = None
        days_to_first_update = None

    # AI/ML Research Pack — read out of the stored triage record only when on.
    if _on("ai_ml_research"):
        ai_analysis = req.ai_analysis if isinstance(req.ai_analysis, dict) else {}
        ai_priority = ai_analysis.get("priority_score")
        ai_priority_diff = (
            round(req.manual_priority_score - ai_priority, 2)
            if (ai_priority is not None and req.manual_priority_score is not None) else None
        )
        ai_analyzed = bool(req.ai_analyzed_at)
    else:
        ai_priority = ai_priority_diff = ai_analyzed = None

    # Moderation Pack — the stored intake flag is disclosed (redacted) only when on.
    if _on("moderation"):
        moderation_flagged = req.flagged
        # The wordlist quotes the offending text into the reason, so it can
        # carry the same PII as the description — redact before it ships.
        moderation_flag_reason = sanitize_description(req.flag_reason) if req.flag_reason else req.flag_reason
    else:
        moderation_flagged = moderation_flag_reason = None

    resolution_outcome = None
    if req.status == 'closed':
        resolution_outcome = {
            'resolved': 'completed', 'no_action': 'no_action_needed',
            'third_party': 'referred_external',
        }.get(req.closed_substatus, 'closed_other')
    elif req.status == 'in_progress':
        resolution_outcome = 'in_progress'
    else:
        resolution_outcome = 'pending'
    # Social Equity Pack — build_equity_map already made no Census/CDC calls
    # when the pack is off, so _eq carries no equity keys; the extra gate here
    # keeps the row clean even if a caller hands in a pre-built map.
    _eq = _eq if (_eq and _on("social_equity")) else {}
    census_geoid = _eq.get("census_geoid")
    svi = _eq.get("social_vulnerability_index")
    housing_tenure = _eq.get("housing_tenure_renter_pct")
    income_quintile = _eq.get("income_band")
    pop_density = _eq.get("population_density")

    row = {
        "request_id": req.service_request_id,
        "service_code": req.service_code,
        "service_name": req.service_name,
        "infrastructure_category": infra_category,
        "matched_asset_type": asset_type,
        "matched_asset_attributes": asset_attributes,
        "description_word_count": desc_word_count,
        "has_photos": has_photos,
        "photo_count": photo_count,
        "moderation_flagged": moderation_flagged,
        "moderation_flag_reason": moderation_flag_reason,
        "ai_priority_score": ai_priority,
        "ai_analyzed": ai_analyzed,
        "ai_vs_manual_priority_diff": ai_priority_diff,
        "status": req.status,
        "closed_substatus": req.closed_substatus,
        "priority": req.priority,
        "resolution_outcome": resolution_outcome,
        "address_anonymized": address_anon,
        "latitude": lat,
        "longitude": long,
        "zone_id": zone_id,
        "census_tract_geoid": census_geoid,
        "social_vulnerability_index": svi,
        "svi_source": _eq.get("svi_source"),
        "housing_tenure_renter_pct": housing_tenure,
        "income_quintile": income_quintile,
        "population_density": pop_density,
        "weather_precip_24h_mm": weather.get('precip_24h_mm'),
        "weather_temp_max_c": weather.get('temp_max_c'),
        "weather_temp_min_c": weather.get('temp_min_c'),
        "weather_code": weather.get('weather_code'),
        "nearby_asset_age_years": asset_age,
        "sentiment_score": sentiment,
        "is_repeat_report": trust.get('is_repeat_report'),
        "prior_report_mentioned": trust.get('prior_report_mentioned'),
        "frustration_expressed": trust.get('frustration_expressed'),
        "submitted_datetime": _timestamp(req.requested_datetime),
        "closed_datetime": _timestamp(req.closed_datetime),
        "updated_datetime": _timestamp(req.updated_datetime),
        "submission_hour": time_info.get('hour_of_day'),
        "submission_day_of_week": time_info.get('day_of_week'),
        "submission_month": time_info.get('month'),
        "submission_year": time_info.get('year'),
        "is_weekend_submission": time_info.get('is_weekend'),
        "is_business_hours_submission": time_info.get('is_business_hours'),
        "season": season,
        "time_to_triage_hours": time_to_triage,
        "reassignment_count": reassignments,
        "off_hours_submission": off_hours,
        "escalation_occurred": escalation,
        "total_hours_to_resolve": resolution_hours,
        "business_hours_to_resolve": business_hours,
        "days_to_first_update": days_to_first_update,
        "status_change_count": status_changes,
        "submission_channel": req.source,
        "department_id": req.assigned_department_id,
        "comment_count": total_comments,
        "public_comment_count": public_comments,
    }

    if operational:
        # Staff-only operational columns. Kept OUT of the research export.
        row.update({
            "description_raw": req.description,
            "address_exact": req.address,
            "latitude_exact": req.lat,
            "longitude_exact": req.long,
            "assigned_to": req.assigned_to or "",
            "staff_notes": req.staff_notes or "",
        })
    if include_pii:
        row.update({
            "reporter_name": f"{req.first_name or ''} {req.last_name or ''}".strip(),
            "reporter_email": req.email or "",
            "reporter_phone": req.phone or "",
        })
    return row


OPERATIONAL_COLUMNS = [
    "description_raw", "address_exact", "latitude_exact", "longitude_exact",
    "assigned_to", "staff_notes",
]
PII_COLUMNS = ["reporter_name", "reporter_email", "reporter_phone"]
