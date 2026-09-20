#!/usr/bin/env bash
set -euo pipefail

VMID="$1"
GPU_PASSTHROUGH="${2:-false}"

CONFIG="/etc/pve/lxc/${VMID}.conf"

#
# Docker LXC configuration
#

grep -q '^features:.*nesting=1' "${CONFIG}" || \
  echo 'features: nesting=1' >> "${CONFIG}"

grep -q '^lxc.cgroup2.devices.allow: a$' "${CONFIG}" || \
  echo 'lxc.cgroup2.devices.allow: a' >> "${CONFIG}"

grep -q '^lxc.apparmor.profile: unconfined$' "${CONFIG}" || \
  echo 'lxc.apparmor.profile: unconfined' >> "${CONFIG}"


#
# Intel GPU passthrough
#
if [[ "${GPU_PASSTHROUGH}" == "true" ]]; then

  grep -q '^lxc.cgroup2.devices.allow: c 226:128 rwm$' "${CONFIG}" || \
    echo 'lxc.cgroup2.devices.allow: c 226:128 rwm' >> "${CONFIG}"

  grep -q '^lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file$' "${CONFIG}" || \
    echo 'lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file' >> "${CONFIG}"

fi