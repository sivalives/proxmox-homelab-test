# ============================================================
# Pi-hole Policy
# Allows pi-hole container to access its own secrets
# ============================================================

path "secret/data/pihole" {
  capabilities = ["read"]
}

path "secret/data/pihole/*" {
  capabilities = ["read"]
}

path "secret/metadata/pihole" {
  capabilities = ["read", "list"]
}

path "secret/metadata/pihole/*" {
  capabilities = ["read", "list"]
}