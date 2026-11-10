"""
Django settings for the Fuel Route Optimizer project.

Configuration is read from environment variables where it matters for
deployment (secret key, debug, hosts, routing backend).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-me-in-production",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Routes and geocodes are cached on disk so repeated queries never hit the
# external routing API again, even across server restarts.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(DATA_DIR / "cache"),
        "TIMEOUT": 60 * 60 * 24 * 7,  # one week
        "OPTIONS": {"MAX_ENTRIES": 10_000},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Django's default ("same-origin") strips the Referer from map tile requests,
# which tile servers such as OpenStreetMap's reject with HTTP 403.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer",
                                 "rest_framework.renderers.BrowsableAPIRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

# --- Fuel route optimizer -------------------------------------------------
FUEL_ROUTE = {
    # Free, key-less OSRM demo server (OpenStreetMap data). Swap for a
    # self-hosted OSRM instance in production.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "HTTP_TIMEOUT_SECONDS": 20,
    # Only used when a start/finish cannot be resolved from the offline
    # Census gazetteer (e.g. a street address).
    "NOMINATIM_URL": os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
    "USER_AGENT": "fuel-route-optimizer/1.0 (Django assessment project)",
    "VEHICLE_RANGE_MILES": 500.0,
    "VEHICLE_MPG": 10.0,
    "DEFAULT_CORRIDOR_MILES": 10.0,
    "MAX_CORRIDOR_MILES": 50.0,
    # Dollar value of the time lost per extra fuel stop. Stops the optimizer
    # from making 1-gallon top-ups to save cents. 0 = pure minimum cost.
    "DEFAULT_STOP_PENALTY": 10.0,
    # A station further than this from the start point is not treated as the
    # place where the trip's first fill-up happens.
    "ORIGIN_STATION_MAX_MILES": 30.0,
}
