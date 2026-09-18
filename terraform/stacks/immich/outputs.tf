output "immich" {
  description = "immich LXC"

  value = {
    vmid         = module.immich.lxc_vmid
    hostname     = module.immich.lxc_hostname
    ip           = module.immich.lxc_ip
    resources    = module.immich.lxc_resources
    mount_points = module.immich.lxc_mount_points
  }
}