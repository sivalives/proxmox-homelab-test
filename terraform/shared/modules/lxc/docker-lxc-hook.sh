#!/usr/bin/env bash

set -euo pipefail

VMID="$1"
CONFIG="/etc/pve/lxc/${VMID}.conf"

grep -q '^features:.*nesting=1' "${CONFIG}" || \
  echo 'features: nesting=1' >> "${CONFIG}"

grep -q '^lxc.cgroup2.devices.allow: a$' "${CONFIG}" || \
  echo 'lxc.cgroup2.devices.allow: a' >> "${CONFIG}"

grep -q '^lxc.apparmor.profile: unconfined$' "${CONFIG}" || \
  echo 'lxc.apparmor.profile: unconfined' >> "${CONFIG}"