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

resource "terraform_data" "docker_lxc_proxmox_config" {
  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    filesha256("${path.module}/docker-lxc-hook.sh")
  ]

  connection {
    type        = "ssh"
    host        = var.proxmox_ssh_host
    user        = "root"
    private_key = file(pathexpand(var.proxmox_ssh_private_key_file))
  }

  provisioner "file" {
    source      = "${path.module}/docker-lxc-hook.sh"
    destination = "/tmp/lxc-${var.vmid}-docker-hook.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "chmod 700 /tmp/lxc-${var.vmid}-docker-hook.sh",
      "/tmp/lxc-${var.vmid}-docker-hook.sh ${var.vmid} pre-start",

      "rm -f /tmp/lxc-${var.vmid}-docker-hook.sh",

      "if pct status ${var.vmid} | grep -q 'status: running'; then pct stop ${var.vmid}; pct start ${var.vmid}; fi"
    ]
  }
}


}
