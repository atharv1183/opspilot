#!/bin/sh
# API container entrypoint: schema migrations -> first-run data bootstrap -> uvicorn.
set -e
alembic upgrade head
python -m opspilot.bootstrap
exec uvicorn opspilot.api.main:app --host 0.0.0.0 --port 8000
