#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# OpenBao Bootstrap (Runs on Controller LXC 110)
# Connects to OpenBao LXC 101 via network API
# ============================================================

OPENBAO_ADDR="http://192.168.1.101:8200"  # ← LXC 101's address
BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bootstrap"

info() {
    echo "[INFO] $*"
}

ok() {
    echo "[ OK ] $*"
}

die() {
    echo "[ERROR] $*" >&2
    exit 1
}


bao() {
    # Connect to LXC 101 via network
    BAO_ADDR="${OPENBAO_ADDR}" command bao "$@"
}

header() {
    echo
    echo "============================================================"
    echo " $*"
    echo "============================================================"
    echo
}

# ============================================================
# PHASE: AUTHENTICATE
# ============================================================

phase_authenticate() {
    header "Authenticate to OpenBao"

    # Check connection
    info "Checking OpenBao at ${OPENBAO_ADDR}..."
    
    if ! curl -s "${OPENBAO_ADDR}/v1/sys/health" >/dev/null 2>&1; then
        die "Cannot reach OpenBao at ${OPENBAO_ADDR}"
    fi
    
    ok "OpenBao is reachable"

    # Authenticate
    if bao token lookup >/dev/null 2>&1; then
        ok "Already authenticated"
        return
    fi

    echo
    echo "Enter OpenBao root token (NOT stored):"
    echo

    local root_token
    read -rsp "Root token: " root_token
    echo

    [[ -n "$root_token" ]] || die "Token cannot be empty"

    BAO_ADDR="${OPENBAO_ADDR}" bao login -no-print "$root_token" >/dev/null || \
        die "Authentication failed"

    unset root_token
    ok "Authenticated to OpenBao"
}

# ============================================================
# PHASE: SETUP APPROLES
# ============================================================

phase_setup_approles() {
    header "Setup AppRoles"

    [[ -f "${BOOTSTRAP_DIR}/approles.conf" ]] || \
        die "AppRoles config not found: ${BOOTSTRAP_DIR}/approles.conf"

    local role
    local policies

    while IFS='=' read -r role policies; do
        # Skip comments and empty lines
        [[ "$role" =~ ^#.*$ ]] && continue
        [[ -z "$role" ]] && continue

        info "Creating AppRole: ${role}"
        info "Policies: ${policies}"

        bao write "auth/approle/role/${role}" \
            token_policies="${policies}" \
            token_ttl="1h" \
            token_max_ttl="4h" \
            secret_id_ttl="0" \
            secret_id_num_uses="0" >/dev/null

        # Get and save role-id (SAVE LOCALLY ON CONTROLLER)
        local role_id
        role_id="$(bao read -field=role_id "auth/approle/role/${role}/role-id")"

        local role_dir="${BOOTSTRAP_DIR}/${role}"
        mkdir -p "${role_dir}"
        chmod 700 "${role_dir}"

        printf '%s\n' "${role_id}" > "${role_dir}/role-id"
        chmod 600 "${role_dir}/role-id"

        # Get and save secret-id (SAVE LOCALLY ON CONTROLLER)
        if [[ ! -f "${role_dir}/secret-id" ]] || [[ ! -s "${role_dir}/secret-id" ]]; then
            local secret_id
            secret_id="$(bao write -field=secret_id -f "auth/approle/role/${role}/secret-id")"
            printf '%s\n' "${secret_id}" > "${role_dir}/secret-id"
            chmod 600 "${role_dir}/secret-id"
            ok "AppRole '${role}' created with new Secret ID"
        else
            ok "AppRole '${role}' exists; Secret ID preserved"
        fi

        echo "  Saved to: ${role_dir}/"
        echo

    done < "${BOOTSTRAP_DIR}/approles.conf"
}

# ============================================================
# MAIN
# ============================================================

header "OpenBao Bootstrap (Controller)"
echo "OpenBao Address: ${OPENBAO_ADDR}"
echo "Bootstrap Dir: ${BOOTSTRAP_DIR}"
echo

phase_authenticate
phase_setup_approles

header "Bootstrap Complete"
echo
echo "Credentials saved locally on controller:"
echo "  ${BOOTSTRAP_DIR}/"
echo
echo "Now run Terraform:"
echo "  cd terraform/stacks/splunk && terraform apply"
echo
