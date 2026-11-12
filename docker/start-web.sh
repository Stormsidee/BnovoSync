#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py check

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
