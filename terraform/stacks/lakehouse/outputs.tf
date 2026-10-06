output "lakehouse" {
  description = "lakehouse LXC details"

  value = {
    vmid         = module.lakehouse.lxc_vmid
    hostname     = module.lakehouse.lxc_hostname
    ip           = module.lakehouse.lxc_ip
    resources    = module.lakehouse.lxc_resources
    mount_points = module.lakehouse.lxc_mount_points
  }
}