# Fuel Route Optimizer (Django 6.1)

A REST API that takes a start and finish location in the USA and returns:

- the driving **route** (GeoJSON line + an interactive **map** page),
- the **cost-effective fuel stops** along it, for a vehicle with a **500 mile range** at **10 MPG** (50 gal tank),
- the **total money spent on fuel**.

Fuel prices come from the supplied OPIS truck-stop file (`data/fuel-prices-for-be-assessment.csv`).
Routing uses the free, key-less **OSRM** API (OpenStreetMap data).

| | |
|---|---|
| Routing API calls per request | **exactly 1** for a new route, **0** for a repeated one (cached) |
| Geocoding API calls | **0** for `City, ST` or `lat,lon` input (offline US Census gazetteer) |
| Response time | ~15–100 ms when cached; first call is dominated by OSRM (~0.6–3 s) |

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python manage.py migrate
python manage.py load_fuel_data   # ~10 s, offline: loads gazetteer + geocodes 6,626 truck stops
python manage.py runserver
```

Then:

```
GET http://127.0.0.1:8000/api/route/?start=New York, NY&finish=Los Angeles, CA
```

Import `postman_collection.json` into Postman; every request has a **Visualize** tab that draws
the route and stops on a map. Run the tests with `python manage.py test`.

## API

### `GET /api/route/` (or `POST` with a JSON body)

| param | required | description |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"`, `"City, State"`, `"lat,lon"`, or a US street address |
| `corridor_miles` | no | max distance of a truck stop from the route (default `10`, max `50`) |
| `stop_penalty` | no | dollar cost assigned to each stop (default `10`). `0` = pure minimum fuel cost |
| `start_fuel_gallons` | no | fuel already in the tank (default: starts empty, see assumptions) |

Response (abridged):

```json
{
  "start":  {"query": "New York, NY", "label": "New York, NY", "lat": 40.662712, "lon": -73.938677, "source": "gazetteer"},
  "finish": {"query": "Los Angeles, CA", "label": "Los Angeles, CA", "lat": 34.019394, "lon": -118.410825, "source": "gazetteer"},
  "route":  {"distance_miles": 2810.4, "duration_hours": 50.3,
             "geometry": {"type": "LineString", "coordinates": [[-73.93872, 40.6631], "..."]}},
  "vehicle": {"range_miles": 500.0, "mpg": 10.0, "tank_gallons": 50.0},
  "fuel_stops": [
    {"stop_number": 1, "opis_id": 62790, "name": "7-ELEVEN #40084", "address": "US-46/US-1/US-9",
     "city": "Palisades Park", "state": "NJ", "location": {"lat": 40.84702, "lon": -73.99706},
     "route_mile": 9.5, "off_route_miles": 8.2, "price_per_gallon": 3.099,
     "fuel_on_arrival_gallons": 0.0, "gallons_purchased": 38.6, "cost": 119.62},
    "... 6 more"
  ],
  "summary": {"total_fuel_cost": 855.86, "total_gallons_purchased": 279.65, "total_gallons_used": 280.6,
              "number_of_stops": 7, "average_price_paid": 3.06, "stations_in_corridor": 459,
              "optimization": "min fuel cost + $10 per stop (dynamic programming)",
              "theoretical_min_fuel_cost": 848.18, "theoretical_min_stops": 19, "assumption": "..."},
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=...",
  "meta": {"external_api_calls": 1, "routing_api_calls": 1, "route_cache_hit": false, "elapsed_ms": 2280.6}
}
```

Errors: `400` invalid or non-US input, `422` no drivable route or a gap longer than 500 mi with no fuel,
`502` routing service down.

### `GET /api/route/map/`

Same parameters; returns an interactive Leaflet map of the route and the numbered stops. It reuses
the cached route, so it makes no extra API calls.

## How it works

```
start/finish ──► geocode (offline gazetteer) ──► OSRM route (1 call, cached)
                                                     │
              6.6k truck stops in memory (numpy) ◄───┤ resample route every 0.5 mi
                                                     ▼
                       stations within N miles of the route + their mile marker
                                                     ▼
                         optimizer: where to stop and how much to buy
```

1. **Data load** (`load_fuel_data`). The CSV has no coordinates, so each truck stop is geocoded
   to its city using the **US Census 2023 Gazetteer** (places + county subdivisions), which is free and
   loaded once into the DB. Names are normalised so `Mc Calla` = `McCalla`, `Saint Johns` = `St. Johns`, etc.
   Duplicate OPIS IDs are collapsed (lowest price kept) and Canadian rows are dropped. **6,621 / 6,626**
   stops are matched, and every one lands inside its own state. 137 small unincorporated towns missing
   from the gazetteer were resolved once via Nominatim (`--nominatim-fallback`); those results are committed in
   `data/nominatim_cache.json`, so a normal load makes no API calls.
2. **Routing**: one OSRM call with an encoded polyline (7× smaller than GeoJSON). Cached on disk.
3. **Corridor search** (`services/corridor.py`): the route is resampled every 0.5 mi and scanned
   in 50-mile windows against the stations in each window's bounding box (vectorised numpy).
   Each station gets a route mile marker and an off-route distance.
4. **Optimizer** (`services/optimizer.py`):
   - `plan_fuel_stops`: the classic **gas-station greedy**, which is *provably optimal* for a fixed
     route. At each stop, if a cheaper station is reachable, buy just enough to reach it. Otherwise
     fill up and go to the cheapest station in range.
   - The pure optimum makes many 1-gallon top-ups to save cents (19 stops NY→LA), and no driver
     does that. So the default is a **dynamic programme** that minimises
     `fuel cost + stop_penalty × stops` to pick *where* to stop. The exact greedy, restricted
     to those stops, then decides *how much* to buy, including carrying cheap fuel past pricier
     stations. NY→LA: **7 stops, $855.86** vs. the theoretical $848.18 with 19 stops (+0.9%).
     The theoretical minimum is always returned for comparison; `stop_penalty=0` returns it directly.

## Assumptions

- The vehicle starts **empty** and makes its first fill-up at the cheapest truck stop within the first
  30 route miles (or the nearest stop to the start if none is that close). It **arrives at the finish
  empty**, so total fuel cost covers the whole trip. Pass `start_fuel_gallons` to override this.
- Truck stops are located at their **city centroid** (the file has no coordinates), so mile markers
  are accurate to a few miles. The detour to a stop (≤ `corridor_miles`) is not added to fuel use.
- Where a stop lists several prices, the lowest one is used.
- The public OSRM demo server is fine for a demo. Set `OSRM_BASE_URL` to a self-hosted instance in production.

## Project layout

```
config/                     settings (env-driven), urls
routing/
  models.py                 Place (gazetteer), FuelStation
  management/commands/load_fuel_data.py
  services/geocoding.py     input -> coordinates (offline first)
  services/osrm.py          the single routing call + polyline decoding + cache
  services/corridor.py      stations near the route (numpy)
  services/optimizer.py     exact greedy + practical DP
  services/planner.py       orchestration / response shape
  views.py, serializers.py  DRF API
  templates/routing/map.html  Leaflet map
  tests.py                  unit + API tests (OSRM mocked)
data/                       fuel CSV, gazetteer zips, geocode cache
postman_collection.json
```
