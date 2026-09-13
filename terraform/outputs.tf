output "ai" {
  value = var.stack == "all" || var.stack == "ai" ? {
    vmid         = module.ai[0].lxc_vmid
    hostname     = module.ai[0].lxc_hostname
    ip            = module.ai[0].lxc_ip
    resources    = module.ai[0].lxc_resources
    mount_points = module.ai[0].lxc_mount_points
  } : null
}