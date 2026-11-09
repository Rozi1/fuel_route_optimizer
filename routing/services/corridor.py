"""Find truck stops near a route and their mile marker along it (pure numpy)."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from routing.models import FuelStation

from .geo import haversine_miles

MILES_PER_DEG_LAT = 69.055
RESAMPLE_STEP_MILES = 0.5
WINDOW_POINTS = 100  # 50 miles of route per window


@dataclass
class StationTable:
    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    price: np.ndarray
    records: dict  # opis_id -> dict(name, address, city, state, price)


@lru_cache(maxsize=1)
def station_table() -> StationTable:
    """All geocoded stations held in memory as numpy arrays (~6.6k rows).

    Loaded once per process; call ``station_table.cache_clear()`` after
    reloading the data.
    """
    rows = list(FuelStation.objects.exclude(lat=None).values(
        "opis_id", "name", "address", "city", "state", "price", "lat", "lon"))
    return StationTable(
        ids=np.array([r["opis_id"] for r in rows], dtype=np.int64),
        lat=np.array([r["lat"] for r in rows], dtype=float),
        lon=np.array([r["lon"] for r in rows], dtype=float),
        price=np.array([float(r["price"]) for r in rows], dtype=float),
        records={r["opis_id"]: r for r in rows},
    )


def resample_route(coordinates):
    """Resample OSRM [[lon, lat], ...] to a point every RESAMPLE_STEP_MILES.

    Returns (lat, lon, mile) arrays. Evenly spaced points make a nearest-vertex
    search accurate to +/- step/2 even on long straight interstate segments.
    """
    pts = np.asarray(coordinates, dtype=float)
    lon, lat = pts[:, 0], pts[:, 1]
    seg = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    miles = np.arange(0.0, total, RESAMPLE_STEP_MILES)
    miles = np.append(miles, total)
    return np.interp(miles, cum, lat), np.interp(miles, cum, lon), miles


def nearest_station(lat: float, lon: float) -> dict:
    """The truck stop closest (straight line) to a point."""
    table = station_table()
    d = haversine_miles(lat, lon, table.lat, table.lon)
    s = int(np.argmin(d))
    return {"opis_id": int(table.ids[s]), "price": float(table.price[s]), "off_route_miles": float(d[s]),
            "lat": float(table.lat[s]), "lon": float(table.lon[s])}


def stations_along_route(route_lat, route_lon, route_mile, corridor_miles):
    """Stations within ``corridor_miles`` of the route.

    Returns a list of dicts with opis_id, price, route_mile, off_route_miles,
    sorted by route_mile.
    """
    table = station_table()
    if table.ids.size == 0 or route_lat.size == 0:
        return []

    best_d2 = np.full(table.ids.size, np.inf)
    best_mile = np.zeros(table.ids.size)
    cos_lat = np.cos(np.radians(route_lat))
    margin_lat = corridor_miles / MILES_PER_DEG_LAT

    # Slide over the route in ~50 mile windows. Each window only looks at the
    # stations inside its (corridor-padded) bounding box, so the work is
    # proportional to route length, not route length x number of stations.
    for w in range(0, route_lat.size, WINDOW_POINTS):
        sl = slice(w, w + WINDOW_POINTS + 1)
        la, lo, cl = route_lat[sl], route_lon[sl], cos_lat[sl]
        margin_lon = corridor_miles / (MILES_PER_DEG_LAT * max(float(cl.min()), 0.05))
        idx = np.nonzero(
            (table.lat >= la.min() - margin_lat) & (table.lat <= la.max() + margin_lat)
            & (table.lon >= lo.min() - margin_lon) & (table.lon <= lo.max() + margin_lon)
        )[0]
        if idx.size == 0:
            continue
        dlat = (table.lat[idx, None] - la[None, :]) * MILES_PER_DEG_LAT
        dlon = (table.lon[idx, None] - lo[None, :]) * MILES_PER_DEG_LAT * cl[None, :]
        d2 = dlat * dlat + dlon * dlon
        nearest = np.argmin(d2, axis=1)
        d2min = d2[np.arange(idx.size), nearest]
        better = d2min < best_d2[idx]
        best_d2[idx[better]] = d2min[better]
        best_mile[idx[better]] = route_mile[sl][nearest[better]]

    hits = np.nonzero(best_d2 <= corridor_miles ** 2)[0]
    hits = hits[np.lexsort((table.price[hits], best_mile[hits]))]
    return [{
        "opis_id": int(table.ids[s]),
        "price": float(table.price[s]),
        "route_mile": float(best_mile[s]),
        "off_route_miles": float(np.sqrt(best_d2[s])),
        "lat": float(table.lat[s]),
        "lon": float(table.lon[s]),
    } for s in hits]
