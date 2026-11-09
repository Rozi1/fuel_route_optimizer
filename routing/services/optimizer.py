"""Minimum-cost refuelling plan along a fixed route.

Classic "gas station problem" greedy, which is provably optimal for a single
fixed path with a tank of capacity R (miles of range):

At each stop with current fuel f:
  * If a cheaper station (or the destination) is reachable on a full tank,
    buy only enough to reach the *first* such station and drive there.
  * Otherwise this is the cheapest fuel within reach: fill the tank and drive
    to the cheapest station within range.

Everything is tracked in miles of range; gallons = miles / mpg.
"""

from dataclasses import dataclass, field


class InfeasibleRouteError(ValueError):
    """A gap between consecutive stations exceeds the vehicle's range."""


@dataclass
class Stop:
    station: dict          # must contain "route_mile" and "price"
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass
class Plan:
    stops: list = field(default_factory=list)
    total_gallons: float = 0.0
    total_cost: float = 0.0


def plan_fuel_stops(stations, total_miles, range_miles=500.0, mpg=10.0, start_fuel_miles=0.0):
    """Compute the cheapest set of purchases to drive ``total_miles``.

    ``stations`` must be sorted by ``route_mile``; the first one is where the
    vehicle starts (mile 0) when ``start_fuel_miles`` is 0. The vehicle is
    assumed to arrive at the destination with an empty tank.
    """
    nodes = [s for s in stations if 0 <= s["route_mile"] <= total_miles]
    # Destination behaves like an infinitely cheap station: never buy past it.
    nodes.append({"route_mile": total_miles, "price": float("-inf"), "_destination": True})

    plan = Plan()
    fuel = start_fuel_miles
    pos = 0.0
    i = -1  # -1 == the origin before any station

    # Drive from the origin to the first station using starting fuel.
    if nodes[0]["route_mile"] - pos > fuel + 1e-9:
        if nodes[0].get("_destination"):
            raise InfeasibleRouteError("Not enough starting fuel and no stations on the route.")
        raise InfeasibleRouteError(
            f"First station is {nodes[0]['route_mile']:.0f} mi away but only "
            f"{fuel:.0f} mi of fuel on board.")
    fuel -= nodes[0]["route_mile"] - pos
    i = 0

    while not nodes[i].get("_destination"):
        here = nodes[i]
        reach = here["route_mile"] + range_miles

        cheaper = next((j for j in range(i + 1, len(nodes))
                        if nodes[j]["route_mile"] <= reach + 1e-9
                        and nodes[j]["price"] < here["price"]), None)
        if cheaper is not None:
            target = cheaper
            needed = nodes[target]["route_mile"] - here["route_mile"]
            buy = max(0.0, needed - fuel)
        else:
            in_range = [j for j in range(i + 1, len(nodes)) if nodes[j]["route_mile"] <= reach + 1e-9]
            if not in_range:
                nxt = nodes[i + 1]["route_mile"]
                raise InfeasibleRouteError(
                    f"No fuel available for {nxt - here['route_mile']:.0f} miles after mile "
                    f"{here['route_mile']:.0f} (vehicle range is {range_miles:.0f} mi).")
            target = min(in_range, key=lambda j: (nodes[j]["price"], nodes[j]["route_mile"]))
            buy = range_miles - fuel

        if buy > 1e-9:
            gallons = buy / mpg
            cost = gallons * here["price"]
            plan.stops.append(Stop(here, gallons, cost, fuel / mpg))
            plan.total_gallons += gallons
            plan.total_cost += cost
            fuel += buy

        fuel -= nodes[target]["route_mile"] - here["route_mile"]
        i = target

    return plan


def plan_fuel_stops_dp(stations, total_miles, range_miles=500.0, mpg=10.0,
                       start_fuel_miles=0.0, stop_penalty=10.0):
    """Practical plan: minimise fuel cost + ``stop_penalty`` dollars per stop.

    The pure-cost optimum (``plan_fuel_stops``) often tops up 1-2 gallons to
    save a few cents, which no driver would do. Here each leg is fuelled at
    the station where it starts, so we choose the sequence of stops
    0 = s_0 < s_1 < ... < D with every leg <= range minimising

        sum(price(s_k) * leg_k / mpg) + stop_penalty * number_of_stops

    O(n * w) dynamic programme (w = stations within one tank), vectorised
    with numpy -- a few milliseconds for a coast-to-coast route.
    """
    import numpy as np

    nodes = [s for s in stations if 0 <= s["route_mile"] <= total_miles]
    pos = np.array([s["route_mile"] for s in nodes] + [total_miles], dtype=float)
    price = np.array([s["price"] for s in nodes] + [0.0], dtype=float)
    n = pos.size
    dest = n - 1
    INF = float("inf")

    best = np.full(n, INF)      # min cost to arrive at node j and (if not dest) stop there
    prev = np.full(n, -1, dtype=int)  # -1 == came straight from the origin

    # Nodes reachable from the origin on the starting fuel need no purchase.
    reachable = pos <= start_fuel_miles + 1e-9
    best[reachable] = 0.0
    best[:dest][reachable[:dest]] += stop_penalty

    lo = 0
    for j in range(n):
        while pos[j] - pos[lo] > range_miles + 1e-9:
            lo += 1
        if lo >= j:
            continue
        i = np.arange(lo, j)
        cand = best[lo:j] + price[lo:j] * (pos[j] - pos[lo:j]) / mpg
        k = int(np.argmin(cand))
        value = cand[k] + (stop_penalty if j != dest else 0.0)
        if value < best[j] - 1e-12:
            best[j], prev[j] = value, int(i[k])

    if not np.isfinite(best[dest]):
        gaps = np.diff(pos)
        worst = int(np.argmax(gaps))
        raise InfeasibleRouteError(
            f"No fuel available for {gaps[worst]:.0f} miles after mile {pos[worst]:.0f} "
            f"(vehicle range is {range_miles:.0f} mi).")

    chain = []
    j = dest
    while j != -1:
        chain.append(j)
        j = prev[j]
    chain.reverse()  # stations visited, ending with dest

    # The DP fixed *where* to stop. Now decide *how much* to buy at each of
    # those stops with the exact greedy restricted to them: it may fill up at
    # a cheap stop and carry fuel past a pricier one, so it is never more
    # expensive than the leg-by-leg purchases the DP assumed.
    chosen = [nodes[k] for k in chain[:-1]]
    return plan_fuel_stops(chosen, total_miles, range_miles, mpg, start_fuel_miles)
