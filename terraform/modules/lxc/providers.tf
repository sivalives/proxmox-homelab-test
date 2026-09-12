# This should exist in both root and module 
# This only tells Terraform which provider the module expects: without this it errors
terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
    }
  }
}