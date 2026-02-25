#!/usr/bin/env bash
# server-setup.sh — Run on a fresh Hetzner Ubuntu 24.04 VPS.
# Usage: ssh root@YOUR_IP 'bash -s' < scripts/server-setup.sh
set -euo pipefail

echo "=== [1/5] System update ==="
apt-get update && apt-get upgrade -y

echo "=== [2/5] Install Docker ==="
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "=== [3/5] Enable Docker ==="
systemctl enable --now docker

echo "=== [4/5] Firewall (ufw) ==="
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # OpenEMR HTTP
ufw allow 443/tcp   # OpenEMR HTTPS
# Agent (8000) and Jaeger (16686) are NOT exposed — access via SSH tunnel
ufw --force enable

echo "=== [5/5] Create app directory ==="
mkdir -p /opt/emr-agent
echo "=== Server setup complete ==="
