#
# Proxmox LXC
#
# All LXCs created by this module are intended to run Docker.
#

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

  #Fix to handle normal mount and bind mount without hook script
  dynamic "mount_point" {
    for_each = concat(
      [
        for mount in var.mount_points : {
          volume = mount.storage
          size   = "${mount.size_gb}G"
          path   = mount.mount
          read_only = false
        }
      ],
      [
        for mount in var.bind_mounts : {
          volume = mount.host_path
          size   = null
          path   = mount.guest_path
          read_only = mount.read_only
        }
      ]
    )

    content {
      volume    = mount_point.value.volume
      path      = mount_point.value.path
      size      = mount_point.value.size
      read_only = mount_point.value.read_only
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

  #
  # All LXCs run Docker
  #
  unprivileged = false

  #To have all LXC start restart of Proxmox server itself
  start_on_boot = true

  startup {
    order    = 1
    up_delay = 30
  }

  protection = false

  #
  # Prevent the provider from trying to reconcile
  # Proxmox feature flags on subsequent runs.
  #
  lifecycle {
    ignore_changes = [
      features
    ]
  }
}


#
# Configure the Proxmox LXC for Docker and host bind mounts.
#
# This runs from the Terraform controller against
# the Proxmox host as root.
#
resource "terraform_data" "docker_lxc_proxmox_config" {
  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    filesha256("${path.module}/docker-lxc-hook.sh"),
    sha256(jsonencode(var.bind_mounts)),
    var.gpu_passthrough
  ]

  connection {
    type        = "ssh"
    host        = var.proxmox_ssh_host
    user        = "root"
    private_key = file(pathexpand(var.proxmox_ssh_private_key_file))
  }

  provisioner "file" {
    source      = "${path.module}/docker-lxc-hook.sh"
    destination = "/tmp/lxc-${var.vmid}-docker-config.sh"
  }

  provisioner "remote-exec" {
    inline = concat(
      [
        "chmod 700 /tmp/lxc-${var.vmid}-docker-config.sh",

        "/tmp/lxc-${var.vmid}-docker-config.sh ${var.vmid} ${var.gpu_passthrough}",

        "pct stop ${var.vmid} || true"
      ],
      [
        "pct start ${var.vmid}",

        "rm -f /tmp/lxc-${var.vmid}-docker-config.sh"
      ]
    )
  }

  depends_on = [
    proxmox_virtual_environment_container.this
  ]
}
