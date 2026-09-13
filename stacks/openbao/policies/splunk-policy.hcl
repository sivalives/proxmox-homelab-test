# ============================================================
# Splunk Policy
# Allows splunk container to access its own secrets
# ============================================================

path "secret/data/splunk" {
  capabilities = ["read"]
}

path "secret/data/splunk/*" {
  capabilities = ["read"]
}

path "secret/metadata/splunk" {
  capabilities = ["read", "list"]
}

path "secret/metadata/splunk/*" {
  capabilities = ["read", "list"]
}