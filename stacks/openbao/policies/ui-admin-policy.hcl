# ============================================================
# UI Admin Policy
# Allows full access to KV secrets and AppRole management
# ============================================================

# Full KV access
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "secret/data/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "secret/metadata/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

# Policy management
path "sys/policy" {
  capabilities = ["read", "list"]
}

path "sys/policy/*" {
  capabilities = ["read"]
}

# AppRole management
path "auth/approle/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

# Auth method listing
path "auth/*" {
  capabilities = ["read", "list"]
}