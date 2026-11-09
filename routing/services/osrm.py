"""Driving route from the free OSRM API -- exactly one HTTP call per new route."""

import hashlib

import requests
from django.conf import settings
from django.core.cache import cache

METERS_PER_MILE = 1609.344


class RoutingError(RuntimeError):
    """The routing service failed or is unreachable."""


class NoRouteError(RoutingError):
    """The service answered, but there is no drivable road between the points."""


def decode_polyline(encoded: str, precision: int = 5):
    """Decode a Google encoded polyline into [[lon, lat], ...].

    OSRM's polyline output is ~7x smaller than GeoJSON for the same
    precision (1e-5 deg, about 1 m), which noticeably speeds up the call.
    """
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    length = len(encoded)
    while index < length:
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        coords.append([lon / factor, lat / factor])
    return coords


def fetch_route(start_lat, start_lon, end_lat, end_lon, stats: dict) -> dict:
    """Return {"distance_miles", "duration_hours", "coordinates": [[lon, lat], ...]}.

    Cached per coordinate pair, so repeating a query costs zero API calls.
    """
    cfg = settings.FUEL_ROUTE
    coords = f"{start_lon:.5f},{start_lat:.5f};{end_lon:.5f},{end_lat:.5f}"
    cache_key = "osrm:v2:" + hashlib.sha1(coords.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        stats["route_cache_hit"] = True
        return cached

    stats["external_calls"] += 1
    stats["routing_calls"] = stats.get("routing_calls", 0) + 1
    stats["route_cache_hit"] = False
    url = f"{cfg['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/{coords}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "polyline", "steps": "false"},
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    code = payload.get("code")
    if code in ("NoRoute", "NoSegment") or (code == "Ok" and not payload.get("routes")):
        raise NoRouteError(f"No drivable route between these points ({payload.get('message', code)}).")
    if code != "Ok":
        raise RoutingError(f"Routing service error ({code}: {payload.get('message', '')}).")

    route = payload["routes"][0]
    geometry = route["geometry"]
    result = {
        "distance_miles": route["distance"] / METERS_PER_MILE,
        "duration_hours": route["duration"] / 3600,
        "coordinates": decode_polyline(geometry) if isinstance(geometry, str) else geometry["coordinates"],
    }
    cache.set(cache_key, result)
    return result
