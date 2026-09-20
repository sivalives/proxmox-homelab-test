# terraform/stacks/jellyfin/terraform.tfvars
proxmox_node  = "proxmox"
description   = "jellyfin LXC"
vmid          = 107
hostname      = "jellyfin"
memory_mb     = 4096
swap_mb       = 1024
disk_gb       = 30
cores         = 4
ip_address    = "192.168.1.107/24"
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
    size_gb = 200
    mount   = "/opt/jellyfin-data"
  }
]

#GPU very specific to jellyfin, all other dont have it, so false for them
gpu_passthrough = true

#On your controller, set them to the actual Proxmox host and the existing homelab private key
proxmox_ssh_host = "192.168.1.100"
proxmox_ssh_private_key_file = "~/.ssh/id_ed25519_homelab"