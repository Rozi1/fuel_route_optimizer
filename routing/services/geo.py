"""Small geographic helpers shared by the services."""

import re
import unicodedata

import numpy as np

EARTH_RADIUS_MILES = 3958.7613

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

# Rough bounding boxes (min_lat, max_lat, min_lon, max_lon) used to check that
# raw coordinates are inside the USA.
_US_BOXES = [
    (24.0, 49.5, -125.0, -66.5),   # contiguous states
    (51.0, 71.5, -180.0, -129.0),  # Alaska
    (18.8, 22.4, -160.5, -154.5),  # Hawaii
]

_TOKEN_ALIASES = {
    "saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft",
    "n": "north", "s": "south", "e": "east", "w": "west",
}
_LSAD_TRAILERS = {"cdp", "balance"}


def in_usa(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in _US_BOXES)


def normalize_state(value: str) -> str | None:
    value = (value or "").strip().rstrip(".")
    if value.upper() in US_STATES:
        return value.upper()
    return STATE_BY_NAME.get(value.lower())


def strip_lsad(gazetteer_name: str) -> str:
    """'Big Cabin town' -> 'Big Cabin'; 'Abanda CDP' -> 'Abanda'.

    Gazetteer names end with lowercase legal/statistical descriptors
    (city, town, village, borough, CDP, ...). Real names are capitalised.
    """
    name = re.sub(r"\(.*?\)", " ", gazetteer_name).strip()
    words = name.split()
    while len(words) > 1 and (words[-1].islower() or words[-1].lower() in _LSAD_TRAILERS):
        words.pop()
    return " ".join(words)


def place_key(name: str) -> str:
    """Normalise a city name so OPIS / gazetteer / user spellings collide.

    'Mc Calla' == 'McCalla', 'Saint Johns' == 'St. Johns', 'Mount Vernon' == 'Mt Vernon'.
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s-]", "", text).replace("-", " ")
    tokens = text.split()
    if tokens:
        tokens = [_TOKEN_ALIASES.get(t, t) if i == 0 or t in ("saint", "sainte", "mount", "fort")
                  else t for i, t in enumerate(tokens)]
    return "".join(tokens)


def haversine_miles(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in miles (numpy arrays or floats)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))
