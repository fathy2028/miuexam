"""
Django settings for the docx → Moodle-XML converter service.
No database, no auth — one POST endpoint that converts and streams XML back.

Production-sensitive values come from environment variables:
  DJANGO_SECRET_KEY        required in production
  DJANGO_DEBUG             "1"/"true" to enable debug mode (default: off)
  DJANGO_ALLOWED_HOSTS     comma-separated list (default "*")
  CORS_ALLOWED_ORIGINS     comma-separated origins — leave empty when the
                           frontend reverse-proxies /api/ (same-origin)
  CSRF_TRUSTED_ORIGINS     comma-separated https:// origins
"""
import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(int(default))).lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [v.strip() for v in raw.split(",") if v.strip()] or default


BASE_DIR = Path(__file__).resolve().parent.parent      # .../backend
PROJECT_ROOT = BASE_DIR.parent                          # repo root

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret-change-for-prod")
DEBUG = _env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", ["*"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "corsheaders",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "backend.urls"
WSGI_APPLICATION = "backend.wsgi.application"

TEMPLATES = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS — empty in prod when the frontend reverse-proxies /api/ same-origin.
# Default allows the Vite dev server so local development still works.
CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)
CORS_ALLOW_METHODS = ["GET", "POST", "OPTIONS"]

# Needed by Django 4+ for any non-@csrf_exempt POST over HTTPS.
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS", [])

# Trust the X-Forwarded-* headers set by the frontend nginx.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

USE_TZ = True
