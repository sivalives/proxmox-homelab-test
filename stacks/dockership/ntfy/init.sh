#!/bin/sh
set -eu

export NTFY_AUTH_FILE="/etc/ntfy/user.db"

echo "Ensuring ntfy user '${NTFY_USER}' exists"

NTFY_PASSWORD="$NTFY_PASSWORD" \
  ntfy user add \
  --ignore-exists \
  "$NTFY_USER"

echo "ntfy user '${NTFY_USER}' is ready"