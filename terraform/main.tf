# This is root , where it uses modules and calls for each lxc

module "ai" {
  source = "./modules/lxc"
  stack_name = "ai"
  proxmox_node = var.proxmox_node

  vmid        = 105
  hostname    = "ai"
  description = "AI - Ollama, Open WebUI, Dockge"

  ip_address  = "192.168.1.105/24"
  gateway     = "192.168.1.1"
  dns_servers = ["1.1.1.1", "8.8.8.8"]

  cores     = 8
  memory_mb = 12288
  swap_mb   = 2048
  disk_gb   = 80

  storage  = "local-lvm"
  bridge   = "vmbr0"
  template = "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst"

  nesting      = true
  unprivileged  = false
  start_on_boot = true

  startup_order = 1
  startup_delay = 30

  # Create persistent docker data mounts - terraform destroy wont delete them
  mount_points = [
    {
      storage = "local-lvm"
      size_gb = 50
      mount   = "/opt/ai-data"
    }
  ] 

}