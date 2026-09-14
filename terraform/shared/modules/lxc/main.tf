# shared/modules/lxc/main.tf
# COMPLETELY FIXED - No count in provisioners

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
  # INSTALL OPENBAO CLIENT (always)
  # ============================================================

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

  # ============================================================
  # COPY AGENT CONFIG TEMPLATE (always copy, setup conditionally)
  # ============================================================

  provisioner "file" {
    content = templatefile("${path.module}/openbao-agent.hcl.tpl", {
      openbao_addr = var.openbao_addr
    })
    destination = "/tmp/openbao-agent.hcl"

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  # ============================================================
  # COPY CREDENTIALS (always copy, setup conditionally)
  # ============================================================

  provisioner "file" {
    content     = var.openbao_role_id
    destination = "/tmp/openbao-role-id"

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  provisioner "file" {
    content     = var.openbao_secret_id
    destination = "/tmp/openbao-secret-id"

    connection {
      type        = "ssh"
      user        = "root"
      private_key = file(var.controller_ssh_private_key_file)
      host        = var.ip_address
      timeout     = "5m"
    }
  }

  # ============================================================
  # SETUP OPENBAO AGENT (shell conditionals, NO terraform count)
  # ============================================================

  provisioner "remote-exec" {
    inline = [
      # Always create base directories
      "mkdir -p /var/lib/openbao /var/run/openbao",
      "chmod 700 /var/lib/openbao /var/run/openbao",
      "",
      # Conditionally setup agent based on variable
      "if [ '${var.enable_openbao_agent}' = 'true' ]; then",
      "  echo '[INFO] Setting up OpenBao agent'",
      "  ",
      "  # Move files to proper locations",
      "  mv /tmp/openbao-agent.hcl /etc/openbao/agent.hcl",
      "  mv /tmp/openbao-role-id /var/lib/openbao/role-id",
      "  mv /tmp/openbao-secret-id /var/lib/openbao/secret-id",
      "  ",
      "  # Fix permissions",
      "  chmod 600 /var/lib/openbao/role-id /var/lib/openbao/secret-id",
      "  chmod 600 /etc/openbao/agent.hcl",
      "  ",
      "  # Create systemd service",
      "  cat > /etc/systemd/system/openbao-agent.service <<'SYSTEMD_EOF'",
      "[Unit]",
      "Description=OpenBao Agent",
      "After=network.target",
      "Wants=network-online.target",
      "",
      "[Service]",
      "Type=notify",
      "ExecStart=/usr/bin/bao agent -config=/etc/openbao/agent.hcl",
      "Restart=on-failure",
      "RestartSec=5",
      "StandardOutput=journal",
      "StandardError=journal",
      "User=root",
      "TimeoutStartSec=60",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "SYSTEMD_EOF",
      "  ",
      "  # Start service",
      "  systemctl daemon-reload",
      "  systemctl enable openbao-agent.service",
      "  systemctl start openbao-agent.service",
      "  sleep 2",
      "  systemctl status openbao-agent.service",
      "  echo '[OK] OpenBao agent started'",
      "else",
      "  echo '[INFO] OpenBao agent not enabled for this container'",
      "  rm -f /tmp/openbao-agent.hcl /tmp/openbao-role-id /tmp/openbao-secret-id",
      "fi"
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