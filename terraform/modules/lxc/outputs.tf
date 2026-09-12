output "lxc_vmid" {
  description = "LXC VMID"
  value       = proxmox_virtual_environment_container.this.vm_id
}

output "lxc_hostname" {
  description = "LXC hostname"
  value       = var.hostname
}

output "lxc_ip" {
  description = "LXC IP address"
  value       = var.ip_address
}

output "lxc_resources" {
  description = "LXC resource allocation"
  value = {
    cores     = var.cores
    memory_mb = var.memory_mb
    swap_mb   = var.swap_mb
    disk_gb   = var.disk_gb
  }
}