# OpenBao Homelab Setup Guide

## Overview

This setup provides automated bootstrap + easy management for OpenBao.

- **One-time setup**: `00-bootstrap-openbao.sh` (handles init → unseal → config)
- **Ongoing management**: Use OpenBao UI or CLI
- **New apps**: `add-app-policy.sh myapp`

---

## Directory Structure

```
stacks/openbao/
├── docker-compose.yml          # OpenBao container
├── config.hcl                  # OpenBao server config
│
├── bootstrap/
│   ├── approles.conf           # AppRoles to create (edit this)
│   ├── lxc104-pihole/          # Generated credentials
│   │   ├── role-id
│   │   └── secret-id
│   ├── lxc105-splunk/
│   │   ├── role-id
│   │   └── secret-id
│   └── ... more roles
│
└── policies/
    ├── ui-admin-policy.hcl     # UI admin access
    ├── shared-policy.hcl       # Available to all apps
    ├── splunk-policy.hcl       # Splunk access
    ├── pihole-policy.hcl       # Pi-hole access
    ├── vaultwarden-policy.hcl  # Vaultwarden access
    └── ... more policies
```

---

## Setup: First Time (30 minutes)

### Step 1: Prepare

```bash
# Copy to your openbao stack directory
cd stacks/openbao/
mkdir -p bootstrap policies

# Copy policy files
cp /path/to/ui-admin-policy.hcl policies/
cp /path/to/shared-policy.hcl policies/
cp /path/to/splunk-policy.hcl policies/
cp /path/to/pihole-policy.hcl policies/
cp /path/to/vaultwarden-policy.hcl policies/

# Edit AppRoles config
cp /path/to/approles.conf.example bootstrap/approles.conf
# Edit bootstrap/approles.conf to match your LXCs
```

### Step 2: Deploy OpenBao Container

```bash
# Provision LXC 106
cd terraform/stacks/openbao
terraform init
terraform apply

# Deploy Docker stack
ansible-playbook ansible/playbooks/deploy-stack.yml -e stack_name=openbao

# Wait for container to be ready
sleep 10
```

### Step 3: Bootstrap

```bash
# Run complete bootstrap (interactive)
cd stacks/openbao
/path/to/00-bootstrap-openbao.sh
```

**What it does:**
1. Initializes OpenBao (generates unseal keys + root token)
2. Prompts you to unseal (enter 3 of 5 keys)
3. Loads policies from `policies/` directory
4. Creates UI admin user (interactive password)
5. Creates AppRoles from `bootstrap/approles.conf`
6. Saves AppRole credentials to `bootstrap/<role>/`

---

## Ongoing: Manage Secrets via UI

### Access the UI

```
URL: http://192.168.1.106:8200/ui/
Username: siva (or your UI_ADMIN_USER)
```

### Create Secrets (via UI)

1. Click **Secrets** → **secret** → **Create secret**
2. Path: `splunk` (or app name)
3. Add key-value pairs:
   - Key: `SPLUNK_PASSWORD`
   - Value: `your-password`
4. Click **Save**

Secrets are now available to apps with matching policy.

### View Policies (via UI)

Navigate to **Policies** to see what each app can access.

---

## Add New Application

### One-time per app (2 minutes)

```bash
# Run the add-app script
cd stacks/openbao
/path/to/add-app-policy.sh myapp
```

**What it does:**
1. Creates policy template: `policies/myapp-policy.hcl`
2. Loads policy into OpenBao
3. (Optional) Creates AppRole: `bootstrap/myapp/`
4. Saves credentials

### Edit Policy (if needed)

```bash
# Edit the policy file
nano policies/myapp-policy.hcl

# Reload (manual)
docker exec -i openbao bao policy write myapp-policy - < policies/myapp-policy.hcl
```

### Create Secrets for the App

Use the UI (see above) or CLI:

```bash
docker exec openbao bao kv put secret/myapp \
  DATABASE_PASSWORD=password123 \
  API_KEY=key456
```

---

## Adding a New LXC with Agents

### 1. Update AppRoles Config

```bash
# Edit bootstrap/approles.conf
# Add: lxc107-myservice=myapp-policy,shared-policy
```

### 2. Re-run Bootstrap

```bash
cd stacks/openbao
./00-bootstrap-openbao.sh
```

(It skips completed phases automatically.)

### 3. Distribute Credentials

Copy AppRole files to the new LXC:

```bash
# From controller, copy to LXC 107
scp -r bootstrap/lxc107-myservice/* root@192.168.1.107:/var/lib/openbao/
ssh root@192.168.1.107 chown 100:1000 /var/lib/openbao/{role-id,secret-id}
ssh root@192.168.1.107 chmod 600 /var/lib/openbao/{role-id,secret-id}
```

### 4. Deploy LXC + Agent

```bash
# Provision and deploy (as usual)
make apply STACK=myapp
ansible-playbook ansible/playbooks/deploy-stack.yml -e stack_name=myapp
```

Agent will authenticate automatically and fetch secrets.

---

## Workflow Summary

| Task | How | Time |
|---|---|---|
| **First-time setup** | `00-bootstrap-openbao.sh` | 30 min |
| **Add new app** | `add-app-policy.sh myapp` | 2 min |
| **Create secrets** | OpenBao UI → Secrets | 1 min |
| **Add new LXC** | Update `approles.conf` + re-run bootstrap | 5 min |

---

## Troubleshooting

### OpenBao not responding

```bash
docker logs openbao
docker inspect -f '{{.State.Running}}' openbao
```

### Agent can't connect

```bash
# Check from LXC
curl http://192.168.1.106:8200/v1/sys/health
docker logs openbao-agent  # if it's a container
```

### Forgot root token

You cannot unseal again without keys. Keep unseal keys secure (password manager).

### Need to unseal again

```bash
docker exec -it openbao bao operator unseal
# Enter 3 keys when prompted
```

---

## Security Notes

- ✅ **Unseal keys**: Store in password manager, separate from code
- ✅ **Root token**: Only used during setup, then discarded
- ✅ **AppRole credentials**: Stored in `bootstrap/<role>/`, git-ignored
- ✅ **Policies**: Git-tracked, reviewed before loading
- ❌ **Never**: Commit `.env` with secrets, tokens, or passwords

---

## Files Reference

| File | Purpose |
|---|---|
| `00-bootstrap-openbao.sh` | One-time init → unseal → setup |
| `add-app-policy.sh` | Add new app policies |
| `approles.conf.example` | Define AppRoles (copy + edit) |
| `policies/*.hcl` | Access policies (git-tracked) |
| `bootstrap/<role>/*` | Generated credentials (git-ignored) |

---

## Next Steps

1. Run bootstrap: `./00-bootstrap-openbao.sh`
2. Access UI: `http://192.168.1.106:8200/ui/`
3. Create secrets via UI
4. Deploy LXCs with agents
5. Add new apps: `./add-app-policy.sh myapp`

---

## Support

If issues arise:

```bash
# Check status
docker exec openbao bao status

# Check logs
docker logs openbao

# Re-run bootstrap (idempotent)
./00-bootstrap-openbao.sh
```

All phases are idempotent—safe to re-run.