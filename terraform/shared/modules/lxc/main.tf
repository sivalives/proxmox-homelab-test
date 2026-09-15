#
# Proxmox LXC
#
# All LXCs created by this module are intended to run Docker.
#
# Docker-in-LXC requirements:
#   - Privileged LXC
#   - Nesting enabled
#   - cgroup2 devices allowed
#   - AppArmor unconfined
#

resource "proxmox_virtual_environment_file" "docker_lxc_hook" {
  content_type = "snippets"
  datastore_id = var.hook_script_datastore
  node_name    = var.proxmox_node

  file_mode = "0700"

  source {
    path = "${path.module}/docker-lxc-hook.sh"
  }
}


resource "proxmox_virtual_environment_container" "this" {
  node_name = var.proxmox_node
  vm_id     = var.vmid

  description = var.description

  #
  # LXC initialization
  #
  initialization {
    hostname = var.hostname

    ip_config {
      ipv4 {
        address = var.ip_address
        gateway = var.gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      keys = [
        file(var.controller_ssh_public_key_file)
      ]
    }
  }

  #
  # CPU
  #
  cpu {
    cores = var.cores
  }

  #
  # Memory
  #
  memory {
    dedicated = var.memory_mb
    swap      = var.swap_mb
  }

  #
  # Root filesystem
  #
  disk {
    datastore_id = var.storage
    size         = var.disk_gb
  }

  #
  # Additional mount points
  #
  dynamic "mount_point" {
    for_each = var.mount_points

    content {
      volume = mount_point.value.storage
      size   = "${mount_point.value.size_gb}G"
      path   = mount_point.value.mount
    }
  }

  #
  # Network
  #
  network_interface {
    name   = "eth0"
    bridge = var.bridge
  }

  #
  # Operating system
  #
  operating_system {
    template_file_id = var.template
    type             = "ubuntu"
  }

  #
  # Docker-in-LXC
  #
  unprivileged = false

  features {
    nesting = true
  }

  #
  # Proxmox hook for Docker-in-LXC:
  #
  #   lxc.cgroup2.devices.allow: a
  #   lxc.apparmor.profile: unconfined
  #
  hook_script_file_id = proxmox_virtual_environment_file.docker_lxc_hook.id

  #
  # Startup
  #
  start_on_boot = true

  startup {
    order    = 1
    up_delay = 30
  }

  #
  # Allow Terraform to modify/destroy the container.
  #
  protection = false
}
