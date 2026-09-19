output "dockership" {
  description = "dockership LXC"

  value = {
    vmid         = module.dockership.lxc_vmid
    hostname     = module.dockership.lxc_hostname
    ip           = module.dockership.lxc_ip
    resources    = module.dockership.lxc_resources
    mount_points = module.dockership.lxc_mount_points
  }
}