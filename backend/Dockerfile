# syntax=docker/dockerfile:1.7
# Self-contained backend image — build context is ./backend
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the healthcheck; python-docx is pure Python otherwise
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first so the cache survives source-only changes
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Copy the Django project (including api/converter.py — no external files needed)
COPY . /app/

# Non-root user
RUN useradd --create-home --uid 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

ENV DJANGO_SETTINGS_MODULE=backend.settings \
    PYTHONPATH=/app \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/api/health/ || exit 1

CMD ["sh", "-c", "gunicorn backend.wsgi:application --bind 0.0.0.0:${PORT} --workers 2 --timeout 60 --access-logfile -"]
