

##Create token this way!!!!
#With privsep=1, the token's permissions are separately evaluated. With privsep=0, the token inherits the full permissions of root@pam. This is also the documented pattern for automation where the full user permissions are required

pveum user token add root@pam root --privsep=0

#Use this to assign more roles to root token 
root@proxmox:~# pveum user token list root@pam
┌─────────┬───────────────────────────────────┬────────┬─────────┐
│ tokenid │ comment                           │ expire │ privsep │
╞═════════╪═══════════════════════════════════╪════════╪═════════╡
│ root    │ Terraform API token must use root │      0 │ 1       │
└─────────┴───────────────────────────────────┴────────┴─────────┘
root@proxmox:~# pveum acl modify / --roles Administrator --tokens 'root@pam!root'
root@proxmox:~# pveum acl list
┌──────┬───────────────┬───────┬───────────────┬───────────┐
│ path │ roleid        │ type  │ ugid          │ propagate │
╞══════╪═══════════════╪═══════╪═══════════════╪═══════════╡
│ /    │ Administrator │ token │ root@pam!root │ 1         │
└──────┴───────────────┴───────┴───────────────┴───────────┘


#To run terraform 
# This file contains proxmox API token and other sensitive information. Do not commit this file to version control.
# Run as root user for now
terraform apply  -var-file="secret.tfvars"