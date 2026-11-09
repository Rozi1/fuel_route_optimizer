from django.conf import settings
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST', 'lat,lon' or an address in the USA")
    finish = serializers.CharField(max_length=200)
    corridor_miles = serializers.FloatField(
        required=False, min_value=1.0, max_value=settings.FUEL_ROUTE["MAX_CORRIDOR_MILES"],
        help_text="How far off the route a truck stop may be (default 10).",
    )
    start_fuel_gallons = serializers.FloatField(
        required=False, min_value=0.0,
        max_value=settings.FUEL_ROUTE["VEHICLE_RANGE_MILES"] / settings.FUEL_ROUTE["VEHICLE_MPG"],
        help_text="Optional: fuel already in the tank. Default: start empty, fill near origin.",
    )
    stop_penalty = serializers.FloatField(
        required=False, min_value=0.0, max_value=1000.0,
        help_text="Dollar cost assigned to each stop (default 10). 0 = pure minimum fuel cost.",
    )
