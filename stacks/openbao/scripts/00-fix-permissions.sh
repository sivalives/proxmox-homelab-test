#!/usr/bin/env bash
# ============================================================
# OpenBao Docker permission repair
#
# OpenBao server: CT101
# OpenBao version: 2.6.x
# OpenBao container user: UID 100 / GID 1000
# Storage backend: Integrated Raft
#
# Run from:
# /opt/homelab/stacks/openbao/scripts
#
# Usage:
#   bash 00_fix_perms.sh
# ============================================================

set -euo pipefail

OPENBAO_DIR="/opt/homelab/stacks/openbao"

CONFIG_DIR="${OPENBAO_DIR}/config"
DATA_DIR="${OPENBAO_DIR}/data"
POLICIES_DIR="${OPENBAO_DIR}/policies"

OPENBAO_UID=100
OPENBAO_GID=1000

echo "=========================================="
echo " OpenBao permission repair"
echo "=========================================="
echo
echo "OpenBao directory : ${OPENBAO_DIR}"
echo "Config directory  : ${CONFIG_DIR}"
echo "Data directory    : ${DATA_DIR}"
echo "Policies directory: ${POLICIES_DIR}"
echo

# ------------------------------------------------------------
# Verify directories exist
# ------------------------------------------------------------

for dir in "$CONFIG_DIR" "$DATA_DIR" "$POLICIES_DIR"; do
    if [[ ! -d "$dir" ]]; then
        echo "ERROR: Directory does not exist: $dir"
        exit 1
    fi
done

# ------------------------------------------------------------
# Raft data
#
# OpenBao must be able to read and write its Raft database.
# ------------------------------------------------------------

echo "[1/3] Fixing Raft data permissions..."

chown -R "${OPENBAO_UID}:${OPENBAO_GID}" "$DATA_DIR"
chmod 700 "$DATA_DIR"

echo "      Data ownership:"
ls -ldn "$DATA_DIR"

# Show files below data directory for verification
find "$DATA_DIR" -maxdepth 2 -printf "      %M %u:%g %p\n"

# ------------------------------------------------------------
# Configuration
#
# OpenBao only needs to read the configuration.
# ------------------------------------------------------------

echo
echo "[2/3] Checking configuration..."

OPENBAO_CONFIG="${CONFIG_DIR}/openbao.hcl"

if [[ -f "$OPENBAO_CONFIG" ]]; then
    chown root:root "$OPENBAO_CONFIG"
    chmod 644 "$OPENBAO_CONFIG"
    echo "      Configuration permissions fixed:"
    ls -ln "$OPENBAO_CONFIG"
else
    echo "      WARNING: openbao.hcl not found"
fi

# ------------------------------------------------------------
# Policies
#
# Policy files are read-only from OpenBao's perspective.
# ------------------------------------------------------------

echo
echo "[3/3] Checking policies..."

find "$POLICIES_DIR" -type f -exec chown root:root {} \;
find "$POLICIES_DIR" -type f -exec chmod 644 {} \;

echo "      Policy files:"
find "$POLICIES_DIR" -type f -printf "      %M %u:%g %p\n"

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

echo
echo "=========================================="
echo " Permission repair completed"
echo "=========================================="
echo

echo "OpenBao data:"
ls -ldn "$DATA_DIR"

echo
echo "Config:"
ls -lan "$CONFIG_DIR"

echo
echo "Policies:"
ls -lan "$POLICIES_DIR"

echo
echo "Restart OpenBao with:"
echo
echo "  cd ${OPENBAO_DIR}"
echo "  docker compose down"
echo "  docker compose up -d"
echo

echo "Check logs:"
echo
echo "  docker logs openbao"
echo

echo "Check status:"
echo
echo "  docker exec -it openbao bao status"
echo