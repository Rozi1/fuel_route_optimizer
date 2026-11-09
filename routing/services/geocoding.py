"""Resolve user supplied start / finish locations to coordinates.

Resolution order (first hit wins):
1. "lat,lon" literal                      -> no external call
2. "City, ST" / "City, State" in gazetteer -> no external call (offline DB)
3. Nominatim free-text search (US only)    -> 1 external call, cached
"""

import re
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from routing.models import Place

from .geo import US_STATES, in_usa, normalize_state, place_key

_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodingError(ValueError):
    pass


@dataclass
class Location:
    query: str
    label: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self):
        return asdict(self)


def resolve(query: str, stats: dict) -> Location:
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Location is required.")

    m = _LATLON_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        _ensure_usa(query, lat, lon)
        return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")

    _reject_foreign_region(query)
    loc = _from_gazetteer(query)
    if loc:
        return loc
    return _from_nominatim(query, stats)


_FOREIGN = {"canada", "mexico", "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt"}


def _reject_foreign_region(query: str):
    """'Toronto, ON' must not silently become Toronto, Ohio."""
    parts = [p.strip().lower().rstrip(".") for p in query.split(",") if p.strip()]
    if len(parts) >= 2 and not any(normalize_state(p) for p in parts[1:]):
        if parts[-1] in _FOREIGN or (len(parts[-1]) == 2 and parts[-1] not in ("us",)):
            raise GeocodingError(f"'{query}' is not in the USA. Start and finish must both be in the USA.")


def _split_city_state(query: str):
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 2:
        # "Chicago, IL" or "Chicago, Illinois, USA"
        for candidate in parts[1:]:
            state = normalize_state(candidate)
            if state:
                return parts[0], state
        return None, None
    tokens = query.split()
    for n in (2, 1):  # "Salt Lake City UT", "Kansas City New York"? try 2-word states first
        if len(tokens) > n:
            state = normalize_state(" ".join(tokens[-n:]))
            if state:
                return " ".join(tokens[:-n]), state
    return None, None


def _from_gazetteer(query: str) -> Location | None:
    city, state = _split_city_state(query)
    if not city:
        return None
    place = (Place.objects.filter(state=state, key=place_key(city))
             .order_by("kind", "-land_area").first())  # 'place' sorts before 'cousub'
    if not place:
        return None
    return Location(query, f"{place.name}, {place.state}", place.lat, place.lon, "gazetteer")


def _from_nominatim(query: str, stats: dict) -> Location:
    cfg = settings.FUEL_ROUTE
    cache_key = f"nominatim:{query.lower()}"
    hit = cache.get(cache_key)
    if hit is None:
        stats["external_calls"] += 1
        stats["geocoding_calls"] = stats.get("geocoding_calls", 0) + 1
        try:
            resp = requests.get(
                cfg["NOMINATIM_URL"],
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": cfg["USER_AGENT"]},
                timeout=cfg["HTTP_TIMEOUT_SECONDS"],
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise GeocodingError(f"Could not geocode '{query}': {exc}") from exc
        hit = [float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]] if data else []
        cache.set(cache_key, hit)
    if not hit:
        raise GeocodingError(
            f"Could not find '{query}' in the USA. Try 'City, ST' (e.g. 'Dallas, TX') or 'lat,lon'."
        )
    lat, lon, label = hit
    _ensure_usa(query, lat, lon)
    return Location(query, label, lat, lon, "nominatim")


def _ensure_usa(query, lat, lon):
    if not in_usa(lat, lon):
        raise GeocodingError(f"'{query}' is outside the USA. Start and finish must both be in the USA.")


__all__ = ["resolve", "Location", "GeocodingError", "US_STATES"]
