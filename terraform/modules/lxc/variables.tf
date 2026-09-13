
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


variable "timezone" {
  type    = string
  default = "Asia/Kolkata"
}

# controller runs under ansible user, under new LXC we will use root
variable "controller_ssh_public_key_file" {
  description = "Controller SSH public key used to access provisioned LXCs"
  type        = string
  default     = "/home/ansible/.ssh/id_ed25519_homelab.pub"
}

variable "mount_points" {
  description = "Additional persistent mount points for the LXC"

  type = list(object({
    storage = string
    size_gb = number
    mount   = string
  }))

  default = []
}

#Conditional variable - this helps run the main.tf in root to selectively run for a stack like ai or splunk
variable "stack" {
  description = "Stack to deploy. Use 'all' to deploy everything."
  type        = string
  default     = "all"

  validation {
    condition = contains([
      "all",
      "ai",
      "pihole",
      "splunk"
    ], var.stack)

    error_message = "stack must be one of: all, ai, pihole, splunk."
  }
}