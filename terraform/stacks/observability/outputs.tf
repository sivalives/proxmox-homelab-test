output "observability" {
  description = "observability LXC"

  value = {
    vmid         = module.observability.lxc_vmid
    hostname     = module.observability.lxc_hostname
    ip           = module.observability.lxc_ip
    resources    = module.observability.lxc_resources
    mount_points = module.observability.lxc_mount_points
  }
}