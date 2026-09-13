output "ai" {
  description = "AI LXC details"

  value = {
    vmid         = module.ai.lxc_vmid
    hostname     = module.ai.lxc_hostname
    ip            = module.ai.lxc_ip
    resources    = module.ai.lxc_resources
    mount_points = module.ai.lxc_mount_points
  }
}