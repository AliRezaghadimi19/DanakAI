#!/bin/sh
set -eu
python -m django migrate --noinput
python -m django collectstatic --noinput
# Threads keep slow model calls (up to 30 s) from blocking login and health checks.
exec gunicorn nexa.wsgi --bind 0.0.0.0:8080 \
  --workers "${NEXA_WORKERS:-2}" --worker-class gthread --threads "${NEXA_THREADS:-8}" --timeout 60
