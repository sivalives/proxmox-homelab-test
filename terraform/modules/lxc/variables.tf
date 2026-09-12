
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
  description = "Proxmox storage"
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

variable "description" {
  type = string
}

variable "nesting" {
  type = bool
}

variable "unprivileged" {
  type = bool
}

variable "start_on_boot" {
  type = bool
}

variable "startup_order" {
  type = number
}

variable "startup_delay" {
  type = number
}

variable "stack_name" {
  type    = string
  default = "myapp"
}

variable "timezone" {
  type    = string
  default = "Asia/Kolkata"
}

variable "controller_ssh_public_key_file" {
  type    = string
  default = "/root/.ssh/id_ed25519_homelab.pub"
  description = "Controller ssh public key, so that controller can use its private key and login any of LXC controller creates"
}
