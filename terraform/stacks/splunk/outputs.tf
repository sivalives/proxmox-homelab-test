output "splunk" {
  description = "SPLUNK LXC"

  value = {
    vmid         = module.splunk.lxc_vmid
    hostname     = module.splunk.lxc_hostname
    ip            = module.splunk.lxc_ip
    resources    = module.splunk.lxc_resources
    mount_points = module.splunk.lxc_mount_points
  }
}