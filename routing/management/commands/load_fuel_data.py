"""Load the OPIS fuel price file and geocode every truck stop offline.

    python manage.py load_fuel_data [--csv PATH] [--nominatim-fallback]

Steps
1. Download the US Census 2023 Gazetteer (places + county subdivisions) once
   into ./data. Free, public-domain, ~2.7 MB zipped.
2. Load the gazetteer into the Place table (used for offline geocoding of
   both truck stops and API start/finish inputs).
3. Read the fuel CSV, drop non-US rows (Canadian provinces), collapse
   duplicate OPIS IDs (keeping the lowest retail price), and assign each
   truck stop the coordinates of its city.
"""

import csv
import io
import json
import time
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation, Place
from routing.services.geo import US_STATES, place_key, strip_lsad

GAZETTEER_BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
GAZETTEER_FILES = {
    Place.KIND_PLACE: "2023_Gaz_place_national.zip",
    Place.KIND_COUSUB: "2023_Gaz_cousubs_national.zip",
}


class Command(BaseCommand):
    help = "Load gazetteer + fuel prices and geocode truck stops."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.DATA_DIR / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument(
            "--nominatim-fallback", action="store_true",
            help="Geocode the few cities missing from the gazetteer via Nominatim (1 req/s, cached).",
        )

    def handle(self, *args, **opts):
        csv_path = Path(opts["csv"])
        if not csv_path.exists():
            raise CommandError(f"Fuel CSV not found: {csv_path}")
        settings.DATA_DIR.mkdir(exist_ok=True)

        started = time.perf_counter()
        index = self.load_gazetteer()
        stations = self.read_fuel_csv(csv_path)
        unmatched = self.geocode(stations, index)
        if unmatched:
            # Committed cache answers offline; the network is only used with the flag.
            unmatched = self.geocode_with_nominatim(stations, unmatched, allow_network=opts["nominatim_fallback"])

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(FuelStation(**s) for s in stations.values())

        located = sum(1 for s in stations.values() if s["lat"] is not None)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)} US truck stops, {located} geocoded "
            f"({len(unmatched)} cities unmatched) in {time.perf_counter() - started:.1f}s"
        ))
        for state, city in sorted(unmatched)[:50]:
            self.stdout.write(f"  unmatched: {city}, {state}")

    # -- gazetteer ---------------------------------------------------------
    def load_gazetteer(self):
        rows = []
        for kind, filename in GAZETTEER_FILES.items():
            path = settings.DATA_DIR / filename
            if not path.exists():
                self.stdout.write(f"Downloading {filename} ...")
                resp = requests.get(GAZETTEER_BASE + filename, timeout=120)
                resp.raise_for_status()
                path.write_bytes(resp.content)
            with zipfile.ZipFile(path) as zf:
                member = next(n for n in zf.namelist() if n.endswith(".txt"))
                raw = zf.read(member)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            reader.fieldnames = [f.strip() for f in reader.fieldnames]
            for r in reader:
                state = r["USPS"].strip()
                if state not in US_STATES:
                    continue
                name = strip_lsad(r["NAME"].strip())
                if not name or name.lower().startswith(("precinct", "district", "township ")):
                    continue
                rows.append(Place(
                    state=state, name=name, key=place_key(name), kind=kind,
                    land_area=int(r["ALAND"] or 0),
                    lat=float(r["INTPTLAT"]), lon=float(r["INTPTLONG"].strip()),
                ))
                # Aliases: 'Nashville-Davidson' -> 'Nashville', 'Boise City' -> 'Boise',
                # 'Methuen Town' -> 'Methuen'.
                aliases = set()
                if "-" in name:
                    aliases.add(name.split("-")[0].strip())
                for suffix in (" City", " Town", " Village"):
                    if name.endswith(suffix) and len(name) > len(suffix):
                        aliases.add(name[: -len(suffix)])
                for alias in aliases:
                    rows.append(Place(state=state, name=alias, key=place_key(alias), kind=kind,
                                      land_area=int(r["ALAND"] or 0),
                                      lat=float(r["INTPTLAT"]), lon=float(r["INTPTLONG"].strip())))

        with transaction.atomic():
            Place.objects.all().delete()
            Place.objects.bulk_create(rows, batch_size=5000)
        self.stdout.write(f"Gazetteer: {len(rows)} places loaded")

        # Best candidate per (state, key): incorporated places beat county
        # subdivisions, then larger land area wins.
        index = {}
        for p in sorted(rows, key=lambda p: (p.kind != Place.KIND_PLACE, -p.land_area)):
            index.setdefault((p.state, p.key), (p.lat, p.lon))
        return index

    # -- fuel prices -------------------------------------------------------
    def read_fuel_csv(self, path):
        stations, skipped = {}, 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                state = r["State"].strip().upper()
                if state not in US_STATES:
                    skipped += 1
                    continue
                opis_id = int(r["OPIS Truckstop ID"])
                price = Decimal(r["Retail Price"].strip())
                existing = stations.get(opis_id)
                if existing and existing["price"] <= price:
                    continue
                stations[opis_id] = {
                    "opis_id": opis_id,
                    "name": r["Truckstop Name"].strip(),
                    "address": r["Address"].strip(),
                    "city": r["City"].strip(),
                    "state": state,
                    "rack_id": int(r["Rack ID"]) if r["Rack ID"].strip() else None,
                    "price": price,
                    "lat": None,
                    "lon": None,
                }
        self.stdout.write(f"Fuel CSV: {len(stations)} unique US truck stops ({skipped} non-US rows skipped)")
        return stations

    def geocode(self, stations, index):
        unmatched = set()
        for s in stations.values():
            hit = index.get((s["state"], place_key(s["city"])))
            if hit:
                s["lat"], s["lon"] = hit
            else:
                unmatched.add((s["state"], s["city"]))
        return unmatched

    def geocode_with_nominatim(self, stations, unmatched, allow_network=False):
        cfg = settings.FUEL_ROUTE
        cache_path = settings.DATA_DIR / "nominatim_cache.json"
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        still_missing = set()
        for state, city in sorted(unmatched):
            key = f"{city}|{state}"
            if key not in cache and not allow_network:
                still_missing.add((state, city))
                continue
            if key not in cache:
                time.sleep(1.1)  # Nominatim usage policy: max 1 request / second
                resp = requests.get(cfg["NOMINATIM_URL"], params={
                    "city": city, "state": US_STATES[state], "country": "USA",
                    "format": "json", "limit": 1,
                }, headers={"User-Agent": cfg["USER_AGENT"]}, timeout=cfg["HTTP_TIMEOUT_SECONDS"])
                data = resp.json() if resp.ok else []
                cache[key] = [float(data[0]["lat"]), float(data[0]["lon"])] if data else None
            if cache[key]:
                for s in stations.values():
                    if s["state"] == state and s["city"] == city:
                        s["lat"], s["lon"] = cache[key]
            else:
                still_missing.add((state, city))
        if allow_network:
            cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))
        return still_missing
