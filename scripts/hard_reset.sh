#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Stopping all containers ==="
docker compose down

echo "=== Removing named volumes (mysql_data, openemr_sites, openemr_logs) ==="
docker compose down -v

echo "=== Removing built agent image ==="
docker compose rm -f agent
docker rmi -f "$(docker compose images agent -q 2>/dev/null)" 2>/dev/null || true

echo "=== Pruning dangling images and build cache ==="
docker image prune -f
docker builder prune -f

echo "=== Rebuilding and starting everything ==="
systemctl start docker 2>/dev/null || true
docker compose up --build -d

echo "=== Done. Containers:"
docker compose ps
