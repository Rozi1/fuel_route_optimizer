from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import GeocodingError
from .services.optimizer import InfeasibleRouteError
from .services.osrm import NoRouteError, RoutingError
from .services.planner import plan_trip


def _run(params):
    """Validate input and plan the trip. Returns ((data, validated), error_response)."""
    serializer = RouteRequestSerializer(data=params)
    if not serializer.is_valid():
        return None, Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    v = serializer.validated_data
    try:
        data = plan_trip(v["start"], v["finish"], v.get("corridor_miles"),
                         v.get("start_fuel_gallons"), v.get("stop_penalty"))
    except GeocodingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except (InfeasibleRouteError, NoRouteError) as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except RoutingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return (data, v), None


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops for a drive between two US locations.

    GET  /api/route/?start=New York, NY&finish=Los Angeles, CA
    POST /api/route/  {"start": "...", "finish": "..."}
    """

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, params):
        result, error = _run(params)
        if error:
            return error
        data, v = result
        query = {"start": v["start"], "finish": v["finish"]}
        for opt in ("corridor_miles", "start_fuel_gallons", "stop_penalty"):
            if opt in v:
                query[opt] = v[opt]
        data["map_url"] = request.build_absolute_uri(reverse("route-map") + "?" + urlencode(query))
        return Response(data)


def planner_page(request):
    """The web UI: form + map. Plans are fetched from the JSON API."""
    return render(request, "routing/app.html", {"plan": None})


class RouteMapView(APIView):
    """Shareable map page for a given start/finish (HTML).

    The plan is rendered server-side from the same cached route, so opening the
    map costs no extra API call. Invalid input falls back to the UI, which shows
    the error message inline.
    """

    def get(self, request):
        result, _ = _run(request.query_params)
        return render(request, "routing/app.html", {"plan": result[0] if result else None})
