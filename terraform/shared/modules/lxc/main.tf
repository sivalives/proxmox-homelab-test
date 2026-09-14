# shared/modules/lxc/main.tf
# SIMPLIFIED: No provisioners - Ansible handles all setup

resource "proxmox_virtual_environment_container" "this" {
  node_name = var.proxmox_node
  vm_id     = var.vmid

  description = var.description

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

  cpu {
    cores = var.cores
  }

  memory {
    dedicated = var.memory_mb
    swap      = var.swap_mb
  }

  disk {
    datastore_id = var.storage
    size         = var.disk_gb
  }

  dynamic "mount_point" {
    for_each = var.mount_points

    content {
      volume = mount_point.value.storage
      size   = "${mount_point.value.size_gb}G"
      path   = mount_point.value.mount
    }
  }

  network_interface {
    name   = "eth0"
    bridge = var.bridge
  }

  operating_system {
    template_file_id = var.template
    type             = "ubuntu"
  }

  # Features block only for privileged containers
  # Unprivileged containers with nesting enabled in Proxmox settings
  dynamic "features" {
    for_each = var.unprivileged ? [] : [1]
    content {
      nesting = true
    }
  }

  unprivileged  = var.unprivileged
  start_on_boot = var.start_on_boot

  startup {
    order    = var.startup_order
    up_delay = var.startup_delay
  }

  protection = false
}