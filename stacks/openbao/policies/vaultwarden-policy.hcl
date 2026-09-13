# ============================================================
# Vaultwarden Policy
# Allows vaultwarden container to access its own secrets
# ============================================================

path "secret/data/vaultwarden" {
  capabilities = ["read"]
}

path "secret/data/vaultwarden/*" {
  capabilities = ["read"]
}

path "secret/metadata/vaultwarden" {
  capabilities = ["read", "list"]
}

path "secret/metadata/vaultwarden/*" {
  capabilities = ["read", "list"]
}