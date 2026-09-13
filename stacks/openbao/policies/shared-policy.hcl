# ============================================================
# Shared Policy
# Accessible by all applications
# Examples: HEC tokens, common endpoints, etc.
# ============================================================

path "secret/data/shared" {
  capabilities = ["read"]
}

path "secret/data/shared/*" {
  capabilities = ["read"]
}

path "secret/metadata/shared" {
  capabilities = ["read", "list"]
}

path "secret/metadata/shared/*" {
  capabilities = ["read", "list"]
}