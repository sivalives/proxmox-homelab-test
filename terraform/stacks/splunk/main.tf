# terraform/stacks/splunk/main.tf
# Example stack with OpenBao agent and bootstrap validation

locals {
  role_id_file   = "${path.module}/../../stacks/openbao/bootstrap/lxc102-splunk/role-id"
  secret_id_file = "${path.module}/../../stacks/openbao/bootstrap/lxc102-splunk/secret-id"
}

# Validate bootstrap files exist before proceeding
resource "null_resource" "bootstrap_validation" {
  provisioner "local-exec" {
    command = <<-EOT
      if [[ ! -f "${local.role_id_file}" ]] || [[ ! -f "${local.secret_id_file}" ]]; then
        echo ""
        echo "ERROR: OpenBao bootstrap files not found!"
        echo ""
        echo "Missing files:"
        [[ ! -f "${local.role_id_file}" ]] && echo "  - ${local.role_id_file}"
        [[ ! -f "${local.secret_id_file}" ]] && echo "  - ${local.secret_id_file}"
        echo ""
        echo "Run bootstrap FIRST:"
        echo "  cd stacks/openbao"
        echo "  ./00-bootstrap-openbao.sh"
        echo ""
        exit 1
      fi
    EOT
  }
}

module "splunk" {
  source = "../../shared/modules/lxc"

  depends_on = [null_resource.bootstrap_validation]

  # Proxmox configuration
  proxmox_node = var.proxmox_node
  vmid         = var.vmid
  hostname     = var.hostname
  description  = var.description

  # Network configuration
  ip_address = var.ip_address
  gateway    = var.gateway
  dns_servers = var.dns_servers

  # Resource configuration
  memory_mb = var.memory_mb
  swap_mb   = var.swap_mb
  cores     = var.cores
  disk_gb   = var.disk_gb

  # Storage and template
  storage  = var.storage
  bridge   = var.bridge
  template = var.template

  # Container settings
  nesting        = var.nesting
  unprivileged   = var.unprivileged
  start_on_boot  = var.start_on_boot
  startup_order  = var.startup_order
  startup_delay  = var.startup_delay
  timezone       = var.timezone

  # SSH keys for provisioning
  controller_ssh_public_key_file  = var.controller_ssh_public_key_file
  controller_ssh_private_key_file = var.controller_ssh_private_key_file

  # Mount points (if any)
  mount_points = var.mount_points

  # ========== OPENBAO AGENT CONFIGURATION ==========
  enable_openbao_agent = true
  openbao_addr         = "http://192.168.1.101:8200"  # LXC 101
  
  # Read credentials from bootstrap (created by 00-bootstrap-openbao.sh)
  openbao_role_id   = file(local.role_id_file)
  openbao_secret_id = file(local.secret_id_file)
}