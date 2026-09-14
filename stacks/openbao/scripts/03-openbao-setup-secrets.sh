#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# OpenBao Secret Setup
# LXC 106 / Openbao
#
# Creates secret templates only.
# Existing secrets are NEVER overwritten.
#
# Add new applications/secrets in the CONFIGURATION section.
# ============================================================

# ============================================================
# CONFIGURATION
# ============================================================

OPENBAO_CONTAINER="openbao"
KV_MOUNT="secret"

# ------------------------------------------------------------
# Shared secrets
#
# Available to applications whose policy allows:
#   secret/data/shared
# ------------------------------------------------------------

SHARED_SECRETS=(
    "SPLUNK_HEC_URL=http://192.168.1.105:8088/services/collector"
    "SPLUNK_HEC_TOKEN=CHANGE_ME_SPLUNK_HEC_TOKEN"
)

# ------------------------------------------------------------
# Splunk secrets
#
# Stored at:
#   secret/splunk
#
# ------------------------------------------------------------

SPLUNK_SECRETS=(
    "SPLUNK_PASSWORD=CHANGE_ME_SPLUNK_PASSWORD"
    "SPLUNK_HEC_TOKEN=CHANGE_ME_SPLUNK_HEC_TOKEN"
)

# ------------------------------------------------------------
# Pi-hole secrets
#
# Stored at:
#   secret/pihole
#
# Add/change fields here as required.
# ------------------------------------------------------------

PIHOLE_SECRETS=(
    "PIHOLE_PASSWORD=CHANGE_ME_PIHOLE_PASSWORD"
)

# ------------------------------------------------------------
# Future application secrets
#
# Example:
#
# MYAPP_SECRETS=(
#     "DATABASE_PASSWORD=CHANGE_ME_DATABASE_PASSWORD"
#     "API_TOKEN=CHANGE_ME_API_TOKEN"
# )
#
# Then add the application to create_secrets below.
# ------------------------------------------------------------

# MYAPP_SECRETS=(
#     "DATABASE_PASSWORD=CHANGE_ME_DATABASE_PASSWORD"
#     "API_TOKEN=CHANGE_ME_API_TOKEN"
# )

# ============================================================
# FUNCTIONS
# ============================================================

die() {
    echo
    echo "[ERROR] $*"
    exit 1
}

info() {
    echo "[INFO] $*"
}

ok() {
    echo "[ OK ] $*"
}

bao() {
    docker exec "${OPENBAO_CONTAINER}" bao "$@"
}

# ============================================================
# CHECK OPENBAO
# ============================================================

check_openbao() {
    command -v docker >/dev/null 2>&1 || \
        die "Docker is not installed."

    docker inspect "${OPENBAO_CONTAINER}" >/dev/null 2>&1 || \
        die "OpenBao container '${OPENBAO_CONTAINER}' does not exist."

    [[ "$(docker inspect -f '{{.State.Running}}' "${OPENBAO_CONTAINER}")" == "true" ]] || \
        die "OpenBao container is not running."

    info "Checking OpenBao status..."

    local status
    status="$(bao status 2>&1 || true)"

    echo
    echo "${status}"
    echo

    echo "${status}" | grep -q "Initialized.*true" || \
        die "OpenBao is not initialized."

    echo "${status}" | grep -q "Sealed.*false" || \
        die "OpenBao is sealed."

    echo "${status}" | grep -q "Storage Type.*raft" || \
        die "OpenBao is not using Raft."

    ok "OpenBao is initialized, unsealed and using Raft."
}

# ============================================================
# CHECK KV
# ============================================================

check_kv() {
    info "Checking KV mount '${KV_MOUNT}/'..."

    bao secrets list 2>/dev/null | grep -q "^${KV_MOUNT}/" || \
        die "KV mount '${KV_MOUNT}/' does not exist."

    ok "KV mount '${KV_MOUNT}/' exists."
}

# ============================================================
# CREATE ONE SECRET
# ============================================================

create_secret() {
    local path="$1"
    shift

    echo
    info "Checking secret/${path}..."

    if bao kv get "${KV_MOUNT}/${path}" >/dev/null 2>&1; then
        ok "secret/${path} already exists. Existing values preserved."
        return
    fi

    info "Creating secret/${path}..."

    bao kv put "${KV_MOUNT}/${path}" "$@" >/dev/null

    ok "Created secret/${path}."
}

# ============================================================
# CREATE SHARED SECRETS
# ============================================================

setup_shared() {
    create_secret "shared" "${SHARED_SECRETS[@]}"
}

# ============================================================
# CREATE SPLUNK SECRETS
# ============================================================

setup_splunk() {
    create_secret "splunk" "${SPLUNK_SECRETS[@]}"
}

# ============================================================
# CREATE PI-HOLE SECRETS
# ============================================================

setup_pihole() {
    create_secret "pihole" "${PIHOLE_SECRETS[@]}"
}

# ============================================================
# FUTURE APPLICATION EXAMPLE
# ============================================================

# setup_myapp() {
#     create_secret "myapp" "${MYAPP_SECRETS[@]}"
# }

# ============================================================
# VALIDATE POLICIES
# ============================================================

check_policies() {
    echo
    info "Checking policies..."

    bao policy read shared-policy >/dev/null 2>&1 || \
        die "shared-policy does not exist."

    bao policy read splunk-policy >/dev/null 2>&1 || \
        die "splunk-policy does not exist."

    bao policy read pihole-policy >/dev/null 2>&1 || \
        die "pihole-policy does not exist."

    ok "Required policies exist."
}

# ============================================================
# DISPLAY SECRET PATHS
# ============================================================

show_summary() {
    echo
    echo "============================================================"
    echo " Secret Setup Complete"
    echo "============================================================"
    echo
    echo "Created / preserved:"
    echo
    echo "  secret/shared"
    echo "  ├── SPLUNK_HEC_URL"
    echo "  └── SPLUNK_HEC_TOKEN"
    echo
    echo "  secret/splunk"
    echo "  ├── SPLUNK_PASSWORD"
    echo "  └── SPLUNK_HEC_TOKEN"
    echo
    echo "  secret/pihole"
    echo "  └── PIHOLE_PASSWORD"
    echo
    echo "Existing secrets were NOT overwritten."
    echo
    echo "OpenBao UI:"
    echo "  http://192.168.1.106:8200/ui/"
    echo
}

# ============================================================
# MAIN
# ============================================================

echo
echo "============================================================"
echo " OpenBao Homelab Secret Setup"
echo "============================================================"
echo " LXC 106 / Openbao"
echo "============================================================"
echo

check_openbao
check_kv
check_policies

setup_shared
setup_splunk
setup_pihole

# Future applications:
#
# setup_myapp

show_summary
