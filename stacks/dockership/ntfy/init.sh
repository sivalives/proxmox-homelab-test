#!/bin/sh
set -eu

AUTH_FILE="/etc/ntfy/user.db"

echo "Checking ntfy user: ${NTFY_USER}"

if ntfy user list --auth-file="$AUTH_FILE" 2>/dev/null \
    | awk '{print $1}' \
    | grep -Fxq "$NTFY_USER"; then

    echo "ntfy user '${NTFY_USER}' already exists"

else

    echo "Creating ntfy user '${NTFY_USER}'"

    ntfy user add \
      --auth-file="$AUTH_FILE" \
      "$NTFY_USER" \
      "$NTFY_PASSWORD"

    echo "ntfy user created"
fi