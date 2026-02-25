#!/usr/bin/env bash
# railway-seed.sh — Seed the Railway MySQL database with test patient data.
#
# Prerequisites:
#   - Railway CLI installed and logged in
#   - mysql CLI available
#   - The Railway project is linked (run from the project directory)
#
# Usage: ./scripts/railway-seed.sh
set -euo pipefail

SEED_FILE="docker/seed_data.sql"

if [ ! -f "$SEED_FILE" ]; then
  echo "ERROR: $SEED_FILE not found. Run from the project root."
  exit 1
fi

command -v mysql >/dev/null 2>&1 || { echo "ERROR: mysql CLI not found."; exit 1; }

echo "=== Fetching MySQL connection details from Railway ==="
railway link --service MySQL

MYSQL_URL=$(railway variables get MYSQL_URL 2>/dev/null || true)
if [ -z "$MYSQL_URL" ]; then
  echo "ERROR: Could not get MYSQL_URL from Railway. Is the MySQL plugin added?"
  echo "Try: railway link --service MySQL && railway variables get MYSQL_URL"
  exit 1
fi

echo "=== Seeding database ==="
mysql "$MYSQL_URL" < "$SEED_FILE"

echo "=== Seed complete ==="
echo "Inserted test patients: Maria Santos, James Kowalski, Aisha Patel"
