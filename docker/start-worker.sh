#!/bin/sh
set -eu

WORKER_CODE="${1:-}"

if [ -z "$WORKER_CODE" ]; then
  echo "worker code is required"
  exit 1
fi

until python - <<'PY' >/dev/null 2>&1
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bnovo_sync.settings")

import django

django.setup()

from django.db import connection

required_tables = {"django_migrations", "bnovo_integrationsettings", "bnovo_arrival"}
existing_tables = set(connection.introspection.table_names())

raise SystemExit(0 if required_tables.issubset(existing_tables) else 1)
PY
do
  echo "waiting for migrations to be applied..."
  sleep 2
done

exec python manage.py bnovo_worker --worker "$WORKER_CODE" --poll-seconds "${BNOVO_WORKER_POLL_SECONDS:-15}" --force
