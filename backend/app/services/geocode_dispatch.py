"""Server-side geocoding through whichever provider the town actually chose.

The browser has been able to geocode against its town's own provider for a
while: `/gis/config` reports `geocode_provider`, `map_provider.geocoder_for()`
decides it, and the frontend adapters implement it. The server ignored all of it
and went straight to Google, falling back to OpenStreetMap. Two consequences,
both of which a town would experience as "the address box is wrong" rather than
as a configuration problem:

* An Esri town's county address locator was never used. `ARCGIS_LOCATOR_URL` is
  offered on the setup page, and the provider catalogue calls it the single
  biggest local accuracy win available -- it geocodes against the same address
  points dispatch uses -- and the only thing that ever read it was the Test
  button.

* Nothing was biased to the town. `/gis/geocode?address=Main St` answered
  "Main St, Vancouver, BC, Canada" for a New Jersey township, because an
  unbiased worldwide geocode of a common street name is a coin toss. The intake
  form shows that answer to a clerk.

Every provider here is optional and every failure degrades rather than raises:
OpenStreetMap is the last resort, and a town whose provider cannot geocode
server-side (Apple signs its requests with a key that must not leave the server's
vault in a form this can use) lands there deliberately rather than by accident.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from app.services import connector_health
from app.services.geocoding import GeocodingResult

logger = logging.getLogger(__name__)

TIMEOUT = 12.0

# Bias is a nudge, not a filter. A resident can legitimately report something
# just over the border -- a shared road, a county park -- so a hard restriction
# would refuse addresses that are genuinely the town's business. Padding the
# town's own bounding box keeps the nudge from being too tight to include the
# far side of a boundary street.
BIAS_PAD_DEGREES = 0.05


async def _selected(db) -> Tuple[str, Dict[str, Optional[str]]]:
    """The town's geocoding provider, and only that provider's credentials.

    No capability switch is consulted, and that is the answer rather than an
    omission. Maps is in `capability_switches.ALWAYS_ON`: a resident cannot file
    a report without dropping a pin, so an off switch here would be an offer to
    break intake from the setup page. Every other dispatch path in this codebase
    does check -- see capability_switches.py -- and this one is listed there as
    the exception so that "was geocoding covered" has an answer.
    """
    from app.services import map_provider as mp
    from app.services.secret_manager import get_secret

    render = mp.normalize_provider(await get_secret(mp.MAP_PROVIDER_KEY))
    provider = mp.geocoder_for(render, await get_secret(mp.GEOCODE_PROVIDER_KEY))
    if provider == "backend":
        # "Pinpoint's own geocoder" means this module, whose own best answer is
        # OpenStreetMap -- there is nothing else underneath it.
        return "osm", {}
    credentials = await mp.resolve_credentials(provider, get_secret)
    # resolve_credentials only returns the browser-facing fields; the locator URL
    # is one of them but read it directly too so a rename cannot silently drop
    # the single most accurate source a town can have.
    if provider == "esri" and not credentials.get("locatorUrl"):
        credentials["locatorUrl"] = await get_secret("ARCGIS_LOCATOR_URL")
    return provider, credentials


async def _bias(db) -> Optional[Dict[str, float]]:
    """The town's own bounding box, padded, or None if it has not set a boundary."""
    try:
        from sqlalchemy import select

        from app.models import SystemSettings

        row = (await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))).scalar_one_or_none()
        boundary = getattr(row, "township_boundary", None) if row else None
        if not boundary:
            return None
        lats, lngs = [], []

        def walk(node: Any) -> None:
            if isinstance(node, (int, float)):
                return
            if (
                isinstance(node, (list, tuple))
                and len(node) == 2
                and all(isinstance(v, (int, float)) for v in node)
            ):
                lngs.append(float(node[0]))
                lats.append(float(node[1]))
                return
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    walk(value)

        walk(boundary)
        if not lats or not lngs:
            return None
        return {
            "south": min(lats) - BIAS_PAD_DEGREES,
            "north": max(lats) + BIAS_PAD_DEGREES,
            "west": min(lngs) - BIAS_PAD_DEGREES,
            "east": max(lngs) + BIAS_PAD_DEGREES,
        }
    except Exception as exc:
        logger.debug("could not derive a geocoding bias from the town boundary: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Providers. Each returns None rather than raising; the caller falls through.
# ---------------------------------------------------------------------------


# Google reports quota exhaustion in the body of a 200, not on the wire, so a
# status-code check alone would call an exhausted key "no results found".
_GOOGLE_QUOTA_STATUSES = ("OVER_QUERY_LIMIT", "RESOURCE_EXHAUSTED")


async def _flag_google_quota(r, body) -> bool:
    """True (after flagging the admin) when Google says the key is over quota.

    The caller still returns None and falls through to OpenStreetMap -- the
    resident's address box keeps answering, which is the point. The health row
    is how anybody finds out it is answering from the fallback.
    """
    status = body.get("status")
    if r.status_code != 429 and status not in _GOOGLE_QUOTA_STATUSES:
        return False
    await connector_health.note_quota_failure(
        "maps",
        f"Google geocoding over quota ({status or r.status_code}): "
        f"{body.get('error_message') or 'rate limit exceeded'}",
        provider="google",
    )
    return True


async def _google(client, address, creds, bias) -> Optional[GeocodingResult]:
    key = creds.get("apiKey")
    if not key:
        return None
    params: Dict[str, Any] = {"address": address, "key": key}
    if bias:
        params["bounds"] = f"{bias['south']},{bias['west']}|{bias['north']},{bias['east']}"
    r = await client.get("https://maps.googleapis.com/maps/api/geocode/json", params=params)
    body = r.json()
    await _flag_google_quota(r, body)
    if body.get("status") != "OK" or not body.get("results"):
        return None
    top = body["results"][0]
    loc = top["geometry"]["location"]
    return GeocodingResult(
        lat=loc["lat"], lng=loc["lng"],
        formatted_address=top.get("formatted_address", address),
        place_id=top.get("place_id"),
    )


async def _google_reverse(client, lat, lng, creds, _bias) -> Optional[GeocodingResult]:
    key = creds.get("apiKey")
    if not key:
        return None
    r = await client.get("https://maps.googleapis.com/maps/api/geocode/json",
                         params={"latlng": f"{lat},{lng}", "key": key})
    body = r.json()
    await _flag_google_quota(r, body)
    if body.get("status") != "OK" or not body.get("results"):
        return None
    top = body["results"][0]
    return GeocodingResult(
        lat=lat, lng=lng,
        formatted_address=top.get("formatted_address", ""),
        place_id=top.get("place_id"),
    )


def _esri_locator(creds) -> str:
    return (creds.get("locatorUrl") or
            "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer").rstrip("/")


async def _esri(client, address, creds, bias) -> Optional[GeocodingResult]:
    # A county locator is usually public, so unlike the others this is attempted
    # without a key -- an ArcGIS Enterprise locator behind the town's own
    # firewall is exactly the case where there is no token to send.
    params: Dict[str, Any] = {"SingleLine": address, "f": "json", "outFields": "*", "maxLocations": 1}
    if creds.get("apiKey"):
        params["token"] = creds["apiKey"]
    if bias:
        params["searchExtent"] = f"{bias['west']},{bias['south']},{bias['east']},{bias['north']}"
    r = await client.get(f"{_esri_locator(creds)}/findAddressCandidates", params=params)
    if r.status_code == 429:
        await connector_health.note_quota_failure(
            "maps", "Esri geocoding over quota (HTTP 429)", provider="esri")
    if r.status_code != 200:
        return None
    body = r.json()
    candidates = body.get("candidates") or []
    error = body.get("error")
    if isinstance(error, dict) and error.get("code") == 429:
        # ArcGIS reports throttling as a 200 with an error body, so the wire
        # status above never sees it.
        await connector_health.note_quota_failure(
            "maps", f"Esri geocoding over quota: {error.get('message') or 'HTTP 429'}",
            provider="esri")
    if error or not candidates:
        return None
    top = candidates[0]
    location = top.get("location") or {}
    if location.get("y") is None or location.get("x") is None:
        return None
    return GeocodingResult(
        lat=float(location["y"]), lng=float(location["x"]),
        formatted_address=top.get("address") or address,
    )


async def _esri_reverse(client, lat, lng, creds, _bias) -> Optional[GeocodingResult]:
    params: Dict[str, Any] = {"location": f"{lng},{lat}", "f": "json"}
    if creds.get("apiKey"):
        params["token"] = creds["apiKey"]
    r = await client.get(f"{_esri_locator(creds)}/reverseGeocode", params=params)
    if r.status_code == 429:
        await connector_health.note_quota_failure(
            "maps", "Esri reverse geocoding over quota (HTTP 429)", provider="esri")
    if r.status_code != 200:
        return None
    body = r.json()
    address = body.get("address") or {}
    label = address.get("LongLabel") or address.get("Match_addr")
    if body.get("error") or not label:
        return None
    return GeocodingResult(lat=lat, lng=lng, formatted_address=label)


async def _azure(client, address, creds, bias) -> Optional[GeocodingResult]:
    key = creds.get("apiKey")
    if not key:
        return None
    params: Dict[str, Any] = {"api-version": "1.0", "subscription-key": key, "query": address, "limit": 1}
    if bias:
        params["topLeft"] = f"{bias['north']},{bias['west']}"
        params["btmRight"] = f"{bias['south']},{bias['east']}"
    r = await client.get("https://atlas.microsoft.com/search/address/json", params=params)
    if r.status_code == 429:
        await connector_health.note_quota_failure(
            "maps", "Azure Maps geocoding over quota (HTTP 429)", provider="azure")
    if r.status_code != 200:
        return None
    results = (r.json().get("results") or [])
    if not results:
        return None
    top = results[0]
    pos = top.get("position") or {}
    if pos.get("lat") is None or pos.get("lon") is None:
        return None
    return GeocodingResult(
        lat=float(pos["lat"]), lng=float(pos["lon"]),
        formatted_address=(top.get("address") or {}).get("freeformAddress") or address,
    )


async def _azure_reverse(client, lat, lng, creds, _bias) -> Optional[GeocodingResult]:
    key = creds.get("apiKey")
    if not key:
        return None
    r = await client.get("https://atlas.microsoft.com/search/address/reverse/json",
                         params={"api-version": "1.0", "subscription-key": key, "query": f"{lat},{lng}"})
    if r.status_code == 429:
        await connector_health.note_quota_failure(
            "maps", "Azure Maps reverse geocoding over quota (HTTP 429)", provider="azure")
    if r.status_code != 200:
        return None
    addresses = (r.json().get("addresses") or [])
    if not addresses:
        return None
    label = (addresses[0].get("address") or {}).get("freeformAddress")
    if not label:
        return None
    return GeocodingResult(lat=lat, lng=lng, formatted_address=label)


async def _osm_search(client, address: str, viewbox: Optional[str], bounded: int):
    params: Dict[str, Any] = {"q": address, "format": "json", "limit": 1, "addressdetails": 1}
    if viewbox:
        params["viewbox"] = viewbox
        params["bounded"] = bounded
    r = await client.get("https://nominatim.openstreetmap.org/search", params=params,
                         headers={"User-Agent": "Pinpoint311/1.0"})
    if r.status_code != 200:
        return None
    results = r.json() or []
    return results[0] if results else None


async def _osm(client, address, _creds, bias) -> Optional[GeocodingResult]:
    """Inside the town first, then anywhere.

    Nominatim's `viewbox` is only a weak preference unless `bounded=1`, and weak
    was not enough: with the town's box merely preferred, "Main St" came back as
    Main Street in Toronto. Restricting outright is not right either, because a
    fallback that can only answer inside the boundary cannot resolve an address a
    clerk types from a neighbouring town.

    So: ask inside the box, and only widen if there is nothing there. Local
    answers win, distant ones remain reachable, and the second request only
    happens when the first found nothing.
    """
    viewbox = (
        f"{bias['west']},{bias['north']},{bias['east']},{bias['south']}" if bias else None
    )
    top = None
    if viewbox:
        top = await _osm_search(client, address, viewbox, 1)
    if not top:
        top = await _osm_search(client, address, viewbox, 0)
    if not top:
        return None
    return GeocodingResult(
        lat=float(top["lat"]), lng=float(top["lon"]),
        formatted_address=top.get("display_name", address),
    )


async def _osm_reverse(client, lat, lng, _creds, _bias) -> Optional[GeocodingResult]:
    r = await client.get("https://nominatim.openstreetmap.org/reverse",
                         params={"lat": lat, "lon": lng, "format": "json"},
                         headers={"User-Agent": "Pinpoint311/1.0"})
    if r.status_code != 200:
        return None
    body = r.json() or {}
    if not body.get("display_name"):
        return None
    return GeocodingResult(lat=lat, lng=lng, formatted_address=body["display_name"])


_FORWARD = {"google": _google, "esri": _esri, "azure": _azure, "osm": _osm}
_REVERSE = {"google": _google_reverse, "esri": _esri_reverse, "azure": _azure_reverse, "osm": _osm_reverse}


async def _run(db, kind: str, *args) -> Optional[GeocodingResult]:
    provider, creds = await _selected(db)
    bias = await _bias(db)
    table = _FORWARD if kind == "forward" else _REVERSE

    # The town's own provider first, OpenStreetMap second. Apple has no entry, so
    # an Apple town goes straight to OSM -- which is the honest answer rather
    # than a silent nothing.
    order = [provider] if provider in table else []
    if "osm" not in order:
        order.append("osm")

    for name in order:
        try:
            result = await table[name](*args, creds if name == provider else {}, bias)
            if result:
                if name != provider:
                    logger.info("geocoding fell back from %s to %s", provider, name)
                return result
        except Exception as exc:
            logger.warning("geocoder %s failed: %s", name, exc)
            # Only the town's own provider earns a health flag. The OSM
            # fallback is nobody's configured connector, and a Nominatim 429
            # on the "maps" row would read as the town's key being exhausted.
            if name == provider:
                await connector_health.note_quota_failure("maps", exc, provider=provider)
    return None


async def geocode(db, address: str) -> Optional[GeocodingResult]:
    if not (address or "").strip():
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await _run(db, "forward", client, address)


async def reverse_geocode(db, lat: float, lng: float) -> Optional[GeocodingResult]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await _run(db, "reverse", client, lat, lng)
