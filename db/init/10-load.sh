#!/bin/sh
# Local and CI only: load the web schema snapshot (structure, no data).
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /db/web_schema.sql
