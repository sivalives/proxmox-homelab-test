# shared/modules/lxc/main.tf
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

  features {
    nesting = true
  }

  unprivileged  = var.unprivileged
  start_on_boot = var.start_on_boot

  startup {
    order    = var.startup_order
    up_delay = var.startup_delay
  }

  protection = false

  # ============================================================
  # OPENBAO AGENT SETUP (conditional)
  # ============================================================

  # Install OpenBao
  provisioner "remote-exec" {
    inline = [
      "apt-get update",
      "apt-get install -y openbao wget curl"
    ]

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  # Create agent config (only if enabled)
  provisioner "remote-exec" {
    inline = [
      "if [ '${var.enable_openbao_agent}' = 'true' ]; then",
      "  # Setup agent",
      "  mkdir -p /var/lib/openbao",
      "  mv /tmp/openbao-agent.hcl /etc/openbao/agent.hcl",
      "  # ... more setup",
      "else",
      "  echo 'Agent not enabled'",
      "fi"
    ]
  }

  # Copy role-id
  provisioner "file" {
    count       = var.enable_openbao_agent ? 1 : 0
    content     = var.openbao_role_id
    destination = "/var/lib/openbao/role-id"

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  # Copy secret-id
  provisioner "file" {
    count       = var.enable_openbao_agent ? 1 : 0
    content     = var.openbao_secret_id
    destination = "/var/lib/openbao/secret-id"

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  # Setup directories and systemd service
  provisioner "remote-exec" {
    count = var.enable_openbao_agent ? 1 : 0
    inline = [
      "mkdir -p /var/lib/openbao /var/run/openbao",
      "chmod 700 /var/lib/openbao /var/run/openbao",
      "chmod 600 /var/lib/openbao/role-id /var/lib/openbao/secret-id",

      # Create systemd service
      "cat > /etc/systemd/system/openbao-agent.service <<'EOF'\n[Unit]\nDescription=OpenBao Agent\nAfter=network.target\nWants=network-online.target\n\n[Service]\nType=notify\nExecStart=/usr/bin/bao agent -config=/etc/openbao/agent.hcl\nRestart=on-failure\nRestartSec=5\nStandardOutput=journal\nStandardError=journal\nUser=root\nTimeoutStartSec=60\n\n[Install]\nWantedBy=multi-user.target\nEOF",

      "systemctl daemon-reload",
      "systemctl enable openbao-agent.service",
      "systemctl start openbao-agent.service",
      "sleep 3",
      "systemctl status openbao-agent.service"
    ]

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }
}