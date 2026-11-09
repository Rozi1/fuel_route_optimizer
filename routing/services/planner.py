"""Glue: geocode -> one routing call -> corridor search -> optimal plan."""

import time

import numpy as np
from django.conf import settings

from . import geocoding, osrm
from .corridor import nearest_station, resample_route, station_table, stations_along_route
from .optimizer import plan_fuel_stops, plan_fuel_stops_dp

GEOMETRY_STEP_MILES = 2.0  # thinning of the returned polyline (keeps payload small)


def plan_trip(start_query: str, finish_query: str, corridor_miles: float | None = None,
              start_fuel_gallons: float | None = None, stop_penalty: float | None = None) -> dict:
    cfg = settings.FUEL_ROUTE
    t0 = time.perf_counter()
    corridor = corridor_miles or cfg["DEFAULT_CORRIDOR_MILES"]
    range_miles, mpg = cfg["VEHICLE_RANGE_MILES"], cfg["VEHICLE_MPG"]
    stats = {"external_calls": 0}

    start = geocoding.resolve(start_query, stats)
    finish = geocoding.resolve(finish_query, stats)
    route = osrm.fetch_route(start.lat, start.lon, finish.lat, finish.lon, stats)

    lat, lon, mile = resample_route(route["coordinates"])
    total_miles = float(mile[-1])
    candidates = stations_along_route(lat, lon, mile, corridor)

    # Starting fuel assumption. By default the truck starts (effectively) empty
    # and its first purchase is at the best-priced stop within the first
    # ORIGIN_STATION_MAX_MILES of the route, so the total cost covers the whole
    # trip. If the route has no stop that close to the start, the first
    # fill-up happens at the truck stop nearest to the start point.
    window = cfg["ORIGIN_STATION_MAX_MILES"]
    if start_fuel_gallons is not None:
        start_fuel_miles = min(start_fuel_gallons * mpg, range_miles)
        assumption = f"Vehicle starts with {start_fuel_miles / mpg:.1f} gallons on board."
    else:
        near_origin = [c for c in candidates if c["route_mile"] <= window]
        if near_origin:
            first = min(near_origin, key=lambda c: (c["price"], c["route_mile"]))
            start_fuel_miles = first["route_mile"]
            assumption = ("Vehicle starts empty and fills up at the cheapest truck stop within the first "
                          f"{window:.0f} route miles; it arrives at the finish empty.")
        else:
            origin = nearest_station(start.lat, start.lon)
            candidates.insert(0, {**origin, "route_mile": 0.0})
            start_fuel_miles = 0.0
            assumption = (f"No truck stop within {window:.0f} miles of the start along the route, so the "
                          f"first fill-up is at the nearest one ({origin['off_route_miles']:.0f} mi from the "
                          "start). The vehicle arrives at the finish empty.")

    penalty = cfg["DEFAULT_STOP_PENALTY"] if stop_penalty is None else stop_penalty
    exact = plan_fuel_stops(candidates, total_miles, range_miles, mpg, start_fuel_miles)
    if penalty > 0:
        plan = plan_fuel_stops_dp(candidates, total_miles, range_miles, mpg, start_fuel_miles, penalty)
        strategy = f"min fuel cost + ${penalty:g} per stop (dynamic programming)"
    else:
        plan = exact
        strategy = "pure minimum fuel cost (exact greedy)"
    records = station_table().records

    stops = []
    for n, s in enumerate(plan.stops, start=1):
        rec = records[s.station["opis_id"]]
        stops.append({
            "stop_number": n,
            "opis_id": rec["opis_id"],
            "name": rec["name"],
            "address": rec["address"],
            "city": rec["city"],
            "state": rec["state"],
            "location": {"lat": round(s.station["lat"], 5), "lon": round(s.station["lon"], 5)},
            "route_mile": round(s.station["route_mile"], 1),
            "off_route_miles": round(s.station["off_route_miles"], 1),
            "price_per_gallon": round(s.station["price"], 3),
            "fuel_on_arrival_gallons": round(s.fuel_on_arrival_gallons, 2),
            "gallons_purchased": round(s.gallons, 2),
            "cost": round(s.cost, 2),
        })

    step = max(1, int(GEOMETRY_STEP_MILES / (mile[1] - mile[0] if mile.size > 1 else 1)))
    keep = np.unique(np.append(np.arange(0, mile.size, step), mile.size - 1))
    line = np.round(np.column_stack([lon[keep], lat[keep]]), 5).tolist()

    return {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": round(route["distance_miles"], 1),
            "duration_hours": round(route["duration_hours"], 2),
            "geometry": {"type": "LineString", "coordinates": line},
        },
        "vehicle": {"range_miles": range_miles, "mpg": mpg, "tank_gallons": range_miles / mpg},
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost": round(plan.total_cost, 2),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "total_gallons_used": round(total_miles / mpg, 2),
            "number_of_stops": len(stops),
            "average_price_paid": round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None,
            "stations_in_corridor": len(candidates),
            "corridor_miles": corridor,
            "optimization": strategy,
            "theoretical_min_fuel_cost": round(exact.total_cost, 2),
            "theoretical_min_stops": len(exact.stops),
            "assumption": assumption,
        },
        "meta": {
            "external_api_calls": stats["external_calls"],
            "routing_api_calls": stats.get("routing_calls", 0),
            "route_cache_hit": stats.get("route_cache_hit", False),
            "routing_provider": "OSRM (OpenStreetMap)",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
