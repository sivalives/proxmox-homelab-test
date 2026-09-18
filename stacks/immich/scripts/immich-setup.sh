#!/bin/bash
# Immich OpenBao secrets initialization & deployment
# Run from LXC 102 (OpenBao server)

set -e

echo "=== Immich OpenBao Secrets Setup ==="

# Generate secure random passwords
DB_PASS=$(openssl rand -base64 24)
REDIS_PASS=$(openssl rand -base64 24)
ADMIN_PASS=$(openssl rand -base64 16)

echo "Generated passwords:"
echo "  DB Password: $DB_PASS"
echo "  Redis Password: $REDIS_PASS"
echo "  Admin Password: $ADMIN_PASS"
echo ""

# Create immich secret path
echo "Creating secret/immich in OpenBao..."
docker exec -it openbao bao kv put secret/immich \
  IMMICH_DB_USER="immich" \
  IMMICH_DB_PASSWORD="$DB_PASS" \
  IMMICH_REDIS_PASSWORD="$REDIS_PASS" \
  IMMICH_ADMIN_EMAIL="admin@immich.local" \
  IMMICH_ADMIN_PASSWORD="$ADMIN_PASS"

echo "✓ Created secret/immich"
echo ""

# Verify shared Splunk secrets exist
echo "Checking shared Splunk secrets..."
docker exec -it openbao bao kv get secret/shared | grep SPLUNK

if [ $? -eq 0 ]; then
  echo "✓ Splunk HEC credentials already configured"
else
  echo "⚠ Splunk HEC credentials not found in secret/shared"
  echo "  Update them manually:"
  echo "  docker exec -it openbao bao kv patch secret/shared \\"
  echo "    SPLUNK_HEC_URL='http://192.168.1.102:8088' \\"
  echo "    SPLUNK_HEC_TOKEN='<your_hec_token>'"
fi

echo ""
echo "=== Verify All Secrets ==="
echo "secret/immich:"
docker exec -it openbao bao kv get secret/immich
echo ""
echo "secret/shared (Splunk):"
docker exec -it openbao bao kv get secret/shared

echo ""
echo "=== Next Steps ==="
echo "1. Mount 1TB disk to LXC 107 at /mnt/immich-storage"
echo "2. Create storage directories:"
echo "   mkdir -p /mnt/immich-storage/{library,postgres,model-cache,import}"
echo "3. Run Ansible:"
echo "   make deploy STACK=immich"
echo ""