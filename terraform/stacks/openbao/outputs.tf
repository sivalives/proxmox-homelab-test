output "openbao" {
  description = "OPENBAO LXC detopenbaols"

  value = {
    vmid         = module.openbao.lxc_vmid
    hostname     = module.openbao.lxc_hostname
    ip            = module.openbao.lxc_ip
    resources    = module.openbao.lxc_resources
    mount_points = module.openbao.lxc_mount_points
  }
}