output "jellyfin" {
  description = "jellyfin LXC for media"

  value = {
    vmid         = module.jellyfin.lxc_vmid
    hostname     = module.jellyfin.lxc_hostname
    ip           = module.jellyfin.lxc_ip
    resources    = module.jellyfin.lxc_resources
    mount_points = module.jellyfin.lxc_mount_points
  }
}