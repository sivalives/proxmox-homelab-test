module "ai" {
  source = "../../shared/modules/lxc"

  proxmox_node                   = var.proxmox_node
  vmid                           = var.vmid
  hostname                       = var.hostname
  description                    = var.description
  ip_address                     = var.ip_address
  gateway                        = var.gateway
  dns_servers                    = var.dns_servers
  memory_mb                      = var.memory_mb
  swap_mb                        = var.swap_mb
  cores                          = var.cores
  disk_gb                        = var.disk_gb
  storage                        = var.storage
  bridge                         = var.bridge
  template                       = var.template
  nesting                        = var.nesting
  unprivileged                   = var.unprivileged
  start_on_boot                  = var.start_on_boot
  startup_order                  = var.startup_order
  startup_delay                  = var.startup_delay
  timezone                       = var.timezone
  controller_ssh_public_key_file = var.controller_ssh_public_key_file
  mount_points                   = var.mount_points
  proxmox_ssh_host               = var.proxmox_ssh_host
  proxmox_ssh_private_key_file   = var.proxmox_ssh_private_key_file
}