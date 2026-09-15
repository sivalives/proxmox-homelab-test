# terraform/stacks/openbao/terraform.tfvars
proxmox_node  = "proxmox"
description   = "openbao dedicated LXC"
vmid          = 101
hostname      = "openbao"
memory_mb     = 4096
swap_mb       = 2048
disk_gb       = 10
cores         = 2
ip_address    = "192.168.1.101/24"
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
    size_gb = 1
    mount   = "/opt/openbao-data"
  }
]