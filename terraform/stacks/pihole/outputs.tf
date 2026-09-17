output "pihole" {
  description = "pihole LXC"

  value = {
    vmid         = module.pihole.lxc_vmid
    hostname     = module.pihole.lxc_hostname
    ip           = module.pihole.lxc_ip
    resources    = module.pihole.lxc_resources
    mount_points = module.pihole.lxc_mount_points
  }
}