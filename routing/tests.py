from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from routing.models import FuelStation, Place
from routing.services.corridor import station_table
from routing.services.geo import place_key, strip_lsad
from routing.services.optimizer import InfeasibleRouteError, plan_fuel_stops, plan_fuel_stops_dp


def st(mile, price, opis_id=None):
    return {"route_mile": mile, "price": price, "opis_id": opis_id or int(mile * 10) + 1}


class GeoNormalizationTests(SimpleTestCase):
    def test_city_spelling_variants_collide(self):
        self.assertEqual(place_key("Mc Calla"), place_key("McCalla"))
        self.assertEqual(place_key("Saint Johns"), place_key("St. Johns"))
        self.assertEqual(place_key("Mount Vernon"), place_key("Mt Vernon"))
        self.assertEqual(place_key("S Coffeyville"), place_key("South Coffeyville"))
        self.assertEqual(place_key("Cañon City"), place_key("Canon City"))

    def test_strip_gazetteer_descriptors(self):
        self.assertEqual(strip_lsad("Big Cabin town"), "Big Cabin")
        self.assertEqual(strip_lsad("Abanda CDP"), "Abanda")
        self.assertEqual(strip_lsad("Nashville-Davidson metropolitan government (balance)"), "Nashville-Davidson")


class ExactOptimizerTests(SimpleTestCase):
    def test_short_trip_buys_only_what_is_needed(self):
        plan = plan_fuel_stops([st(0, 3.0)], total_miles=100, range_miles=500, mpg=10)
        self.assertEqual(len(plan.stops), 1)
        self.assertAlmostEqual(plan.total_gallons, 10)
        self.assertAlmostEqual(plan.total_cost, 30)

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        stations = [st(0, 2.0), st(400, 5.0), st(800, 5.0)]
        plan = plan_fuel_stops(stations, total_miles=900, range_miles=500, mpg=10)
        # 50 gal at $2 (full tank), then only 40 gal at $5 to finish.
        self.assertAlmostEqual(plan.total_gallons, 90)
        self.assertAlmostEqual(plan.total_cost, 50 * 2 + 40 * 5)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        stations = [st(0, 4.0), st(100, 3.0)]
        plan = plan_fuel_stops(stations, total_miles=600, range_miles=500, mpg=10)
        self.assertAlmostEqual(plan.stops[0].gallons, 10)   # 100 mi at $4
        self.assertAlmostEqual(plan.stops[1].gallons, 50)   # 500 mi at $3
        self.assertAlmostEqual(plan.total_cost, 40 + 150)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([st(0, 3.0), st(600, 3.0)], total_miles=700, range_miles=500)


class PracticalOptimizerTests(SimpleTestCase):
    def test_penalty_removes_tiny_top_ups(self):
        # Prices fall slightly every 50 miles: the exact optimum stops everywhere.
        stations = [st(m, 3.5 - m / 10000) for m in range(0, 1000, 50)]
        exact = plan_fuel_stops(stations, 1000, 500, 10)
        practical = plan_fuel_stops_dp(stations, 1000, 500, 10, stop_penalty=10)
        self.assertGreater(len(exact.stops), len(practical.stops))
        self.assertLessEqual(len(practical.stops), 3)
        self.assertGreaterEqual(practical.total_cost, exact.total_cost - 1e-6)
        self.assertAlmostEqual(practical.total_gallons, 100)

    def test_carries_cheap_fuel_past_expensive_stop(self):
        # Fill up at $2.50 (mile 300) and carry 200 mi past the $3.20 stop.
        stations = [st(0, 3.0), st(300, 2.5), st(600, 3.2)]
        exact = plan_fuel_stops(stations, 900, 500, 10)
        dp = plan_fuel_stops_dp(stations, 900, 500, 10, stop_penalty=0)
        self.assertAlmostEqual(dp.total_cost, exact.total_cost)

    def test_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops_dp([st(0, 3.0), st(600, 3.0)], 700, 500, 10)


def fake_osrm_response(*args, **kwargs):
    """Straight-ish drive along latitude 35 from lon -100 to -90 (~565 mi)."""
    resp = mock.Mock()
    resp.json.return_value = {
        "code": "Ok",
        "routes": [{
            "distance": 565 * 1609.344,
            "duration": 9 * 3600,
            "geometry": {"type": "LineString",
                         "coordinates": [[-100 + i * 0.5, 35.0] for i in range(21)]},
        }],
    }
    return resp


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RouteApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="TX", name="Amarillo", key=place_key("Amarillo"), kind="place",
                             lat=35.0, lon=-100.0)
        Place.objects.create(state="TN", name="Memphis", key=place_key("Memphis"), kind="place",
                             lat=35.0, lon=-90.0)
        for i, (lon, price) in enumerate([(-99.98, 3.40), (-97.0, 2.90), (-95.0, 3.60), (-93.0, 3.10)]):
            FuelStation.objects.create(opis_id=i + 1, name=f"Stop {i}", address="I-40", city="X",
                                       state="OK", price=Decimal(str(price)), lat=35.01, lon=lon)

    def setUp(self):
        cache.clear()
        station_table.cache_clear()

    @mock.patch("routing.services.osrm.requests.get", side_effect=fake_osrm_response)
    def test_plan_route_and_cache(self, get):
        url = reverse("route-plan")
        resp = self.client.get(url, {"start": "Amarillo, TX", "finish": "Memphis, TN"})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["meta"]["external_api_calls"], 1)
        self.assertGreaterEqual(data["summary"]["number_of_stops"], 2)
        self.assertAlmostEqual(data["summary"]["total_gallons_purchased"],
                               data["summary"]["total_gallons_used"], delta=0.5)
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertIn("/api/route/map/", data["map_url"])
        for stop in data["fuel_stops"]:
            self.assertLessEqual(stop["gallons_purchased"], 50 + 1e-6)

        # Same trip again: served from cache, zero external calls.
        data2 = self.client.get(url, {"start": "Amarillo, TX", "finish": "Memphis, TN"}).json()
        self.assertEqual(data2["meta"]["external_api_calls"], 0)
        self.assertEqual(get.call_count, 1)

        # Map page reuses the cached route too.
        page = self.client.get(reverse("route-map"), {"start": "Amarillo, TX", "finish": "Memphis, TN"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "leaflet")
        self.assertEqual(get.call_count, 1)

    def test_post_requires_start_and_finish(self):
        resp = self.client.post(reverse("route-plan"), {"start": "Amarillo, TX"}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json()["errors"])

    def test_rejects_locations_outside_usa(self):
        resp = self.client.get(reverse("route-plan"), {"start": "51.5072,-0.1276", "finish": "Memphis, TN"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("outside the USA", resp.json()["error"])

    def test_rejects_canadian_province_without_calling_geocoder(self):
        with mock.patch("routing.services.geocoding.requests.get") as get:
            resp = self.client.get(reverse("route-plan"), {"start": "Toronto, ON", "finish": "Memphis, TN"})
        self.assertEqual(resp.status_code, 400)
        get.assert_not_called()

    @mock.patch("routing.services.osrm.requests.get")
    def test_no_drivable_route_is_422(self, get):
        get.return_value.json.return_value = {"code": "NoRoute", "message": "Impossible route"}
        resp = self.client.get(reverse("route-plan"), {"start": "Amarillo, TX", "finish": "Memphis, TN"})
        self.assertEqual(resp.status_code, 422)


class PolylineTests(SimpleTestCase):
    def test_decode_reference_polyline(self):
        from routing.services.osrm import decode_polyline
        # Example from Google's polyline algorithm documentation.
        coords = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        expected = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]
        for (lon, lat), (elon, elat) in zip(coords, expected):
            self.assertAlmostEqual(lon, elon, places=5)
            self.assertAlmostEqual(lat, elat, places=5)
