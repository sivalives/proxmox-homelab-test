output "nextcloud" {
  description = "nextcloud LXC details"

  value = {
    vmid         = module.nextcloud.lxc_vmid
    hostname     = module.nextcloud.lxc_hostname
    ip           = module.nextcloud.lxc_ip
    resources    = module.nextcloud.lxc_resources
    mount_points = module.nextcloud.lxc_mount_points
  }
}