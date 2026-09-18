output "lxc_bind_mounts" {
  description = "Host directory bind mounts configured for the LXC"
  value       = var.bind_mounts
}

output "immich" {
  description = "immich LXC"

  value = {
    vmid         = module.immich.lxc_vmid
    hostname     = module.immich.lxc_hostname
    ip            = module.immich.lxc_ip
    resources    = module.immich.lxc_resources
    mount_points = module.immich.lxc_mount_points
    bind_mounts  = module.immich.lxc_bind_mounts
  }
}