# shared/modules/lxc/variables.tf

# ==================== MODULE VARIABLES ====================

variable "proxmox_node" {
  description = "Proxmox node name"
  type        = string
}

variable "vmid" {
  description = "LXC VMID"
  type        = number
}

variable "hostname" {
  description = "LXC hostname"
  type        = string
}

variable "description" {
  description = "LXC description"
  type        = string
}

variable "ip_address" {
  description = "IPv4 address with CIDR"
  type        = string
}

variable "gateway" {
  description = "IPv4 gateway"
  type        = string
}

variable "dns_servers" {
  description = "DNS servers"
  type        = list(string)
}

variable "memory_mb" {
  description = "RAM in MB"
  type        = number
}

variable "swap_mb" {
  description = "Swap in MB"
  type        = number
}

variable "cores" {
  description = "Number of CPU cores"
  type        = number
}

variable "disk_gb" {
  description = "Root disk size in GB"
  type        = number
}

variable "storage" {
  description = "Proxmox storage pool"
  type        = string
}

variable "bridge" {
  description = "Proxmox network bridge"
  type        = string
}

variable "template" {
  description = "Ubuntu LXC template file ID"
  type        = string
}

variable "nesting" {
  description = "Enable nesting (for Docker in LXC)"
  type        = bool
  default     = true
}

variable "unprivileged" {
  description = "Unprivileged container (recommended: true for better Proxmox compatibility)"
  type        = bool
  default     = true
}

variable "start_on_boot" {
  description = "Start on boot"
  type        = bool
}

variable "startup_order" {
  description = "Startup order"
  type        = number
}

variable "startup_delay" {
  description = "Startup delay in seconds"
  type        = number
}

variable "timezone" {
  description = "Container timezone"
  type        = string
  default     = "Asia/Kolkata"
}

variable "controller_ssh_public_key_file" {
  description = "Controller SSH public key for LXC access"
  type        = string
  default     = "/home/ansible/.ssh/id_ed25519_homelab.pub"
}

variable "controller_ssh_private_key_file" {
  description = "Controller SSH private key for provisioning"
  type        = string
  default     = "/home/ansible/.ssh/id_ed25519_homelab"
  sensitive   = true
}

variable "mount_points" {
  description = "Additional persistent mount points"
  type = list(object({
    storage = string
    size_gb = number
    mount   = string
  }))
  default = []
}

variable "proxmox_ssh_host" {
  description = "Proxmox host used for SSH configuration."
  type        = string
}

variable "proxmox_ssh_private_key_file" {
  description = "Private SSH key used to access Proxmox."
  type        = string
  sensitive   = true
}
