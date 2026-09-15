#!/usr/bin/env bash

set -euo pipefail

VMID="$1"
PHASE="$2"
CONFIG="/etc/pve/lxc/${VMID}.conf"

case "${PHASE}" in
  pre-start)

    # Allow all cgroup2 devices.
    if ! grep -q '^lxc.cgroup2.devices.allow: a$' "${CONFIG}"; then
      echo 'lxc.cgroup2.devices.allow: a' >> "${CONFIG}"
    fi

    # Disable AppArmor restrictions for Docker-in-LXC.
    if ! grep -q '^lxc.apparmor.profile: unconfined$' "${CONFIG}"; then
      echo 'lxc.apparmor.profile: unconfined' >> "${CONFIG}"
    fi

    ;;
esac

exit 0