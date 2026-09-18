# ============================================================
# Immich Policy
# Allows immich contimmichners to access its own secrets
# ============================================================

path "secret/data/immich" {
  capabilities = ["read"]
}

path "secret/data/immich/*" {
  capabilities = ["read"]
}

path "secret/metadata/immich" {
  capabilities = ["read", "list"]
}

path "secret/metadata/immich/*" {
  capabilities = ["read", "list"]
}