# terraform/stacks/immich/terraform.tfvars
proxmox_node  = "proxmox"
description   = "immich LXC"
vmid          = 103
hostname      = "immich"
memory_mb     = 8192
swap_mb       = 2048
disk_gb       = 30
cores         = 4
ip_address    = "192.168.1.103/24"
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
bind_mounts = [
  {
    host_path  = "/mnt/immich-data"
    guest_path = "/mnt/immich-data"
    read_only  = false
  }
]
#On your controller, set them to the actual Proxmox host and the existing homelab private key
proxmox_ssh_host = "192.168.1.100"
proxmox_ssh_private_key_file = "~/.ssh/id_ed25519_homelab"