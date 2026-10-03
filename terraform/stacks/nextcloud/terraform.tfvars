# terraform/stacks/nextcloud/terraform.tfvars
proxmox_node  = "proxmox"
description   = "nextcloud LXC"
vmid          = 109
hostname      = "nextcloud"
memory_mb     = 12288
swap_mb       = 2048
disk_gb       = 20
cores         = 8
ip_address    = "192.168.1.109/24"
gateway       = "192.168.1.1"
dns_servers   = ["1.1.1.1", "8.8.8.8"]
storage       = "local-lvm"
bridge        = "vmbr0"
template      = "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
nesting       = true
unprivileged  = false
start_on_boot = true
startup_order = 1
startup_delay = 30
mount_points = [
  {
    storage = "local-lvm"
    size_gb = 100
    mount   = "/opt/nextcloud-data"
  }
]
#On your controller, set them to the actual Proxmox host and the existing homelab private key
proxmox_ssh_host = "192.168.1.100"
proxmox_ssh_private_key_file = "~/.ssh/id_ed25519_homelab"