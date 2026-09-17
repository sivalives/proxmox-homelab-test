# terraform/stacks/pihole/terraform.tfvars
proxmox_node  = "proxmox"
description   = "pihole LXC"
vmid          = 102
hostname      = "pihole"
memory_mb     = 4096
swap_mb       = 512
disk_gb       = 20
cores         = 2
ip_address    = "192.168.1.102/24"
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
    size_gb = 45
    mount   = "/opt/pihole-data"
  }
]
#On your controller, set them to the actual Proxmox host and the existing homelab private key
proxmox_ssh_host = "192.168.1.100"
proxmox_ssh_private_key_file = "~/.ssh/id_ed25519_homelab"