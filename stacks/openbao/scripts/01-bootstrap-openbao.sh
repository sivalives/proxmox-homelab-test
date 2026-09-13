#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# OpenBao Homelab Bootstrap
# One-time setup: Initialize → Unseal → Configure
#
# After this script, manage secrets via UI.
# Use add-app-policy.sh for new applications.
# ============================================================

OPENBAO_CONTAINER="openbao"
OPENBAO_ADDR="http://127.0.0.1:8200"
OPENBAO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${OPENBAO_DIR}/config"
DATA_DIR="${OPENBAO_DIR}/data"
BOOTSTRAP_DIR="${OPENBAO_DIR}/bootstrap"
POLICIES_DIR="${OPENBAO_DIR}/policies"

OPENBAO_UID=100
OPENBAO_GID=1000
AGENT_UID=100
AGENT_GID=1000

KV_MOUNT="secret"
UI_ADMIN_USER="siva"

# ============================================================
# HELPERS
# ============================================================

die() {
    echo
    echo "[ERROR] $*" >&2
    exit 1
}

info() {
    echo "[INFO] $*"
}

ok() {
    echo "[ OK ] $*"
}

header() {
    echo
    echo "============================================================"
    echo " $*"
    echo "============================================================"
    echo
}

bao() {
    docker exec -e BAO_ADDR="${OPENBAO_ADDR}" "${OPENBAO_CONTAINER}" bao "$@"
}

# ============================================================
# PHASE 0: PRECHECKS
# ============================================================

phase_prechecks() {
    header "Phase 0: Prechecks"

    command -v docker >/dev/null 2>&1 || die "docker not found"
    
    docker inspect "${OPENBAO_CONTAINER}" >/dev/null 2>&1 || \
        die "Container '${OPENBAO_CONTAINER}' does not exist"

    [[ -d "${DATA_DIR}" ]] || die "Data directory not found: ${DATA_DIR}"
    [[ -d "${POLICIES_DIR}" ]] || die "Policies directory not found: ${POLICIES_DIR}"

    ok "All prechecks passed"
}

# ============================================================
# PHASE 1: INITIALIZE
# ============================================================

phase_initialize() {
    header "Phase 1: Initialize OpenBao"

    if ! docker inspect "${OPENBAO_CONTAINER}" >/dev/null 2>&1; then
        die "Container '${OPENBAO_CONTAINER}' not found"
    fi

    if [[ "$(docker inspect -f '{{.State.Running}}' "${OPENBAO_CONTAINER}")" != "true" ]]; then
        info "Starting container..."
        docker start "${OPENBAO_CONTAINER}" >/dev/null
        sleep 5
    fi

    info "Waiting for OpenBao API..."
    for i in {1..30}; do
        if docker exec "${OPENBAO_CONTAINER}" sh -c \
            'wget -q -O- http://127.0.0.1:8200/v1/sys/health >/dev/null 2>&1'; then
            ok "OpenBao API ready"
            break
        fi
        sleep 2
    done

    info "Checking OpenBao status..."
    local status
    status="$(bao status 2>&1 || true)"

    echo "$status"
    echo

    if echo "$status" | grep -q "Initialized.*true"; then
        ok "OpenBao already initialized - skipping init phase"
        return
    fi

    read -rp "Initialize OpenBao? (generates 5 unseal keys, threshold 3) [y/N]: " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || die "Initialization cancelled"

    info "Running operator init..."
    docker exec -it "${OPENBAO_CONTAINER}" bao operator init

    ok "OpenBao initialized"
}

# ============================================================
# PHASE 2: UNSEAL
# ============================================================

phase_unseal() {
    header "Phase 2: Unseal OpenBao"

    local status
    status="$(bao status 2>&1 || true)"

    echo "$status"
    echo

    if echo "$status" | grep -q "Sealed.*false"; then
        ok "OpenBao already unsealed - skipping unseal phase"
        return
    fi

    info "OpenBao is sealed. Three of five unseal keys required."
    echo

    for i in 1 2 3 4 5; do
        status="$(bao status 2>&1 || true)"

        if echo "$status" | grep -q "Sealed.*false"; then
            ok "OpenBao unsealed"
            break
        fi

        read -rsp "Enter Unseal Key $i: " key
        echo

        [[ -n "$key" ]] || { echo "Skipped"; continue; }

        result="$(docker exec "${OPENBAO_CONTAINER}" bao operator unseal "$key" 2>&1)" || {
            echo "[ERROR] Unseal failed"
            echo "$result"
            continue
        }

        echo "$result"
        echo
    done

    status="$(bao status 2>&1 || true)"
    if ! echo "$status" | grep -q "Sealed.*false"; then
        die "OpenBao is still sealed"
    fi

    ok "OpenBao unsealed successfully"
}

# ============================================================
# PHASE 3: AUTHENTICATE
# ============================================================

phase_authenticate() {
    header "Phase 3: Authenticate"

    if bao token lookup >/dev/null 2>&1; then
        ok "Already authenticated"
        return
    fi

    echo "Enter OpenBao root token for setup (NOT stored)"
    echo

    local root_token
    read -rsp "Root token: " root_token
    echo

    [[ -n "$root_token" ]] || die "Token cannot be empty"

    docker exec -e BAO_ADDR="${OPENBAO_ADDR}" \
        "${OPENBAO_CONTAINER}" bao login -no-print "$root_token" >/dev/null || \
        die "Authentication failed"

    unset root_token
    ok "Authenticated to OpenBao"
}

# ============================================================
# PHASE 4: SETUP INFRASTRUCTURE
# ============================================================

phase_setup_infrastructure() {
    header "Phase 4: Setup Infrastructure"

    # Fix permissions
    info "Fixing data permissions..."
    mkdir -p "${DATA_DIR}" "${BOOTSTRAP_DIR}"
    chown -R "${OPENBAO_UID}:${OPENBAO_GID}" "${DATA_DIR}"
    chmod 700 "${DATA_DIR}"
    chown -R "${AGENT_UID}:${AGENT_GID}" "${BOOTSTRAP_DIR}"
    chmod 700 "${BOOTSTRAP_DIR}"
    ok "Permissions fixed"

    # Enable KV
    info "Enabling KV v2..."
    if bao secrets list 2>/dev/null | grep -q "^${KV_MOUNT}/"; then
        ok "KV '${KV_MOUNT}/' already enabled"
    else
        bao secrets enable -path="${KV_MOUNT}" kv-v2 >/dev/null
        ok "KV '${KV_MOUNT}/' enabled"
    fi

    # Enable auth methods
    info "Enabling auth methods..."
    
    if bao auth list 2>/dev/null | grep -q "^approle/"; then
        ok "AppRole already enabled"
    else
        bao auth enable approle >/dev/null
        ok "AppRole enabled"
    fi

    if bao auth list 2>/dev/null | grep -q "^userpass/"; then
        ok "Userpass already enabled"
    else
        bao auth enable userpass >/dev/null
        ok "Userpass enabled"
    fi
}

# ============================================================
# PHASE 5: LOAD POLICIES
# ============================================================

phase_load_policies() {
    header "Phase 5: Load Policies"

    [[ -d "${POLICIES_DIR}" ]] || die "Policies directory not found: ${POLICIES_DIR}"

    local count=0
    for policy_file in "${POLICIES_DIR}"/*.hcl; do
        [[ -f "$policy_file" ]] || continue

        policy_name="$(basename "$policy_file" .hcl)"

        info "Installing policy: $policy_name"

        docker exec -i \
            -e BAO_ADDR="$OPENBAO_ADDR" \
            "$OPENBAO_CONTAINER" \
            bao policy write "$policy_name" - < "$policy_file"

        ok "Policy installed: $policy_name"

        count=$((count + 1))
        done

    [[ $count -gt 0 ]] || die "No policy files found in ${POLICIES_DIR}"

    ok "Loaded $count policies"
}

# ============================================================
# PHASE 6: CREATE UI ADMIN
# ============================================================

phase_ui_admin() {
    header "Phase 6: Create UI Admin"

    if bao read "auth/userpass/users/${UI_ADMIN_USER}" >/dev/null 2>&1; then
        ok "UI admin '${UI_ADMIN_USER}' already exists"
        return
    fi

    echo "Create OpenBao UI administrator (${UI_ADMIN_USER})"
    echo

    local password
    local password_confirm

    read -rsp "Password: " password
    echo

    [[ -n "$password" ]] || die "Password cannot be empty"

    read -rsp "Confirm: " password_confirm
    echo

    [[ "$password" == "$password_confirm" ]] || die "Passwords do not match"

    bao write "auth/userpass/users/${UI_ADMIN_USER}" \
        password="${password}" \
        token_policies="ui-admin" >/dev/null

    unset password password_confirm
    ok "UI admin '${UI_ADMIN_USER}' created"
}

# ============================================================
# PHASE 7: SETUP APPROLES
# ============================================================

phase_setup_approles() {
    header "Phase 7: Setup AppRoles"

    [[ -f "${BOOTSTRAP_DIR}/approles.conf" ]] || {
        die "AppRoles config not found: ${BOOTSTRAP_DIR}/approles.conf"
    }

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

        # Get and save role-id
        local role_id
        role_id="$(bao read -field=role_id "auth/approle/role/${role}/role-id")"

        local role_dir="${BOOTSTRAP_DIR}/${role}"
        mkdir -p "${role_dir}"
        chown "${AGENT_UID}:${AGENT_GID}" "${role_dir}"
        chmod 700 "${role_dir}"

        printf '%s\n' "${role_id}" > "${role_dir}/role-id"
        chown "${AGENT_UID}:${AGENT_GID}" "${role_dir}/role-id"
        chmod 600 "${role_dir}/role-id"

        # Get and save secret-id (if not exists)
        if [[ ! -f "${role_dir}/secret-id" ]] || [[ ! -s "${role_dir}/secret-id" ]]; then
            local secret_id
            secret_id="$(bao write -field=secret_id -f "auth/approle/role/${role}/secret-id")"
            printf '%s\n' "${secret_id}" > "${role_dir}/secret-id"
            chown "${AGENT_UID}:${AGENT_GID}" "${role_dir}/secret-id"
            chmod 600 "${role_dir}/secret-id"
            ok "AppRole '${role}' created with new Secret ID"
        else
            ok "AppRole '${role}' exists; Secret ID preserved"
        fi

        echo "  Role ID file : ${role_dir}/role-id"
        echo "  Secret ID file: ${role_dir}/secret-id"
        echo

    done < "${BOOTSTRAP_DIR}/approles.conf"
}

# ============================================================
# PHASE 8: VALIDATE & SUMMARY
# ============================================================

phase_validate() {
    header "Phase 8: Validation & Summary"

    info "OpenBao status"
    bao status
    echo

    info "KV secrets"
    bao secrets list
    echo

    info "Authentication methods"
    bao auth list
    echo

    info "Policies"
    bao policy list
    echo

    info "AppRoles"
    bao list auth/approle/role
    echo

    header "Bootstrap Complete"

    cat <<EOF
OpenBao UI:
  http://192.168.1.106:8200/ui/
  Username: ${UI_ADMIN_USER}

AppRole Bootstrap:
  ${BOOTSTRAP_DIR}/

Next steps:

1. Manage secrets via UI:
   http://192.168.1.106:8200/ui/vault/secrets

2. Add new applications:
   ./add-app-policy.sh myapp

3. Distribute AppRole credentials:
   Copy bootstrap/<role>/{role-id,secret-id} to LXCs

EOF
}

# ============================================================
# MAIN
# ============================================================

header "OpenBao Homelab Bootstrap"

phase_prechecks
phase_initialize
phase_unseal
phase_authenticate
phase_setup_infrastructure
phase_load_policies
phase_ui_admin
phase_setup_approles
phase_validate

ok "Bootstrap complete"