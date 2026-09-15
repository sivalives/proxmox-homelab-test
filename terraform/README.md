

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



# Docker LXC Proxmox Configuration

## Purpose

All LXCs in this homelab are intended to run Docker.

Docker inside an LXC requires the LXC to be configured differently from a standard unprivileged LXC. The Terraform `bpg/proxmox` provider can create the container and configure many of its normal properties, but Proxmox restricts some low-level LXC settings when they are changed through the API.

Because of this, the deployment uses **two separate Terraform mechanisms**:

1. **Proxmox API token** — creates and manages the normal LXC resource.
2. **SSH access to the Proxmox host** — applies the low-level Docker-in-LXC settings that the API token cannot change.

---

## Why the LXC must be privileged

The Docker containers running inside the LXC need capabilities that are restricted in an unprivileged LXC.

The homelab therefore creates Docker LXCs as:

```hcl
unprivileged = false
```

This matches the previously working Docker/Splunk LXC configuration.

The earlier Splunk LXC was created manually with:

```bash
pct create ... --unprivileged 0
```

That configuration allowed Docker and Splunk to perform operations such as recursive ownership changes inside the container.

When the same LXC was changed to unprivileged mode, Splunk failed during startup because it could not perform some required `chown` operations.

---

## Why nesting is required

Docker is itself a container runtime running inside the LXC.

Therefore the LXC needs nesting enabled:

```text
features: nesting=1
```

Without nesting, Docker-in-LXC can encounter restrictions from the outer LXC environment.

The Ubuntu 22.04 images used by this homelab also use Systemd 249, for which the Proxmox provider warns that nesting may be required.

---

## Additional low-level Docker settings

The old working Docker LXC also contained:

```text
lxc.cgroup2.devices.allow: a
lxc.apparmor.profile: unconfined
```

These settings are used to remove restrictions that can interfere with Docker running inside the LXC.

### `lxc.cgroup2.devices.allow: a`

This permits the LXC access to devices through the cgroup v2 device controller.

Docker creates and manages its own containers and requires access to various device-related operations. The broader device allowance avoids restrictions that can otherwise prevent Docker from functioning correctly.

### `lxc.apparmor.profile: unconfined`

This disables the LXC's AppArmor confinement.

Docker has its own container isolation/security model. Running Docker inside an additionally confined LXC can result in permission or capability conflicts. The previous working environment therefore used:

```text
lxc.apparmor.profile: unconfined
```

This setting is intentionally a homelab-specific choice and reduces the security isolation of the LXC.

---

## Why Terraform cannot simply set everything

The `bpg/proxmox` Terraform provider can configure standard LXC properties such as:

```hcl
unprivileged = false

cpu {
  cores = ...
}

memory {
  dedicated = ...
}

network_interface {
  ...
}
```

However, Proxmox applies special authorization restrictions to some low-level LXC configuration operations.

In particular, attempts to configure the LXC `hookscript` or certain feature flags through the API resulted in errors such as:

```text
changing the hookscript is only allowed for root@pam
```

and:

```text
changing feature flags for privileged container is only allowed for root@pam
```

The Terraform API token is `root@pam!root` and has full ACL permissions, but Proxmox still treats some of these operations as requiring the actual `root@pam` user rather than an API-token identity.

Therefore, increasing API-token permissions does not solve this particular restriction.

---

## Why SSH access to Proxmox is required

The Terraform process runs on the homelab controller.

The controller therefore uses:

```text
Terraform
   |
   | Proxmox API token
   v
Proxmox API :8006
```

for normal infrastructure creation.

For the low-level Docker LXC configuration, Terraform additionally uses:

```text
Terraform
   |
   | SSH as root
   v
Proxmox host :22
```

The SSH connection allows Terraform to modify:

```text
/etc/pve/lxc/<VMID>.conf
```

directly on the Proxmox host.

This is why the controller has a dedicated homelab SSH key:

```text
/home/ansible/.ssh/id_ed25519_homelab
```

The public key is installed in:

```text
/root/.ssh/authorized_keys
```

on the Proxmox host.

The private key remains on the controller and is never stored in Git.

---

## Why the configuration script exists

The low-level Proxmox configuration is kept in a separate script rather than embedding shell commands into `main.tf`.

Example:

```text
terraform/shared/modules/lxc/
├── main.tf
├── variables.tf
└── docker-lxc-hook.sh
```

The script modifies the LXC configuration:

```text
/etc/pve/lxc/<VMID>.conf
```

and ensures the required Docker settings exist.

Keeping this logic in a script has several advantages:

* Terraform HCL stays readable.
* The Proxmox-specific configuration is easy to inspect.
* The configuration can be version-controlled with the rest of the infrastructure.
* The same configuration can be applied to every Docker LXC.
* Terraform can detect changes to the script and re-run the configuration.

Although the file is named `docker-lxc-hook.sh`, **it is currently being used as a configuration script, not registered as a Proxmox `hookscript`**.

This distinction is important because registering it as an actual Proxmox hookscript caused the `root@pam` API restriction.

---

## Final deployment flow

The resulting architecture is:

```text
                     Homelab Controller
                     ------------------
                     Terraform
                     Ansible
                     Git
                     SSH keys
                           |
             +-------------+-------------+
             |                           |
             | HTTPS/API                 | SSH
             |                           |
             v                           v
       Proxmox API                 Proxmox Host
       :8006                       :22
             |                           |
             |                           |
             v                           v
       Create/configure LXC       Modify low-level
                                  LXC configuration
                                         |
                                         v
                                /etc/pve/lxc/<VMID>.conf
                                         |
                                         +-- privileged
                                         +-- nesting
                                         +-- cgroup2 device access
                                         +-- AppArmor unconfined
```

## Result

The Terraform module provides a standardized Docker-ready LXC:

```text
Privileged LXC
+ Nesting
+ Docker-compatible cgroup configuration
+ AppArmor unconfined
+ Terraform-managed
+ Ansible-managed software configuration
```

Terraform remains responsible for **infrastructure**.

Ansible remains responsible for **software installation and application deployment**.

SSH to Proxmox is used only for the small set of **host-level LXC configuration changes that Proxmox does not permit through the Terraform API-token path**.
