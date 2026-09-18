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

  #
  # All LXCs run Docker
  #
  unprivileged = false
  
  #This is set to false for Immich 
  start_on_boot = false

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
# Configure the Proxmox LXC for Docker.
#
# This runs from the Terraform controller against
# the Proxmox host.
#
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
    destination = "/tmp/lxc-${var.vmid}-docker-config.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "chmod 700 /tmp/lxc-${var.vmid}-docker-config.sh",

      #
      # Apply Proxmox LXC configuration.
      #
      "/tmp/lxc-${var.vmid}-docker-config.sh ${var.vmid}",

      #
      # Restart the LXC so new device/configuration
      # settings are applied.
      #
      "pct stop ${var.vmid}",
      "pct start ${var.vmid}",

      #
      # Remove temporary script.
      #
      "rm -f /tmp/lxc-${var.vmid}-docker-config.sh"
    ]
  }

  depends_on = [
    proxmox_virtual_environment_container.this
  ]
}

# This resource is very specific for immich for it to mount the 1Tb external HDD
resource "terraform_data" "lxc_bind_mounts" {
  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    sha256(jsonencode(var.bind_mounts))
  ]

  connection {
    type        = "ssh"
    host        = var.proxmox_ssh_host
    user        = "root"
    private_key = file(pathexpand(var.proxmox_ssh_private_key_file))
  }

  provisioner "remote-exec" {
    inline = concat(
      [
        "pct stop ${var.vmid} || true"
      ],
      [
        for index, mount in var.bind_mounts :
        "pct set ${var.vmid} -mp${index} ${mount.host_path},mp=${mount.guest_path}${mount.read_only ? ",ro=1" : ""}"
      ]
    )
  }

  depends_on = [
  proxmox_virtual_environment_container.this,
  terraform_data.lxc_bind_mounts
]
}