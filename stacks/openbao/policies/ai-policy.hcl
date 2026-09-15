# ============================================================
# AI Policy
# Allows AI containers to access its own secrets
# ============================================================

path "secret/data/ai" {
  capabilities = ["read"]
}

path "secret/data/ai/*" {
  capabilities = ["read"]
}

path "secret/metadata/ai" {
  capabilities = ["read", "list"]
}

path "secret/metadata/ai/*" {
  capabilities = ["read", "list"]
}