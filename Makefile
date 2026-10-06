# Homelab Makefile - Terraform + Ansible + OpenBao + Dockhand/Hawser

.PHONY: help bootstrap \
        openbao-server-setup openbao-app-setup openbao-agent \
        new-lxc-setup \
        lxc-bootstrap docker-setup hawser-setup docker-exporter-setup \
        tailscale-setup deploy-stack \
        terraform-init plan apply destroy deploy clean

# -------------------------------------------------------------------
# Variables
# -------------------------------------------------------------------

STACK ?=
APP ?=
OPENBAO_DIR ?=

ROOT_DIR := $(CURDIR)
ANSIBLE_DIR := $(ROOT_DIR)/ansible
ANSIBLE_CONFIG := $(ANSIBLE_DIR)/ansible.cfg
ANSIBLE_PLAYBOOK := ansible-playbook
TERRAFORM_DIR := $(ROOT_DIR)/terraform/stacks
BOOTSTRAP_DIR := $(ANSIBLE_DIR)/.bootstrap

# Always run Ansible with the repository's configuration.
export ANSIBLE_CONFIG

# -------------------------------------------------------------------
# Helper: fail with a message if a required variable is empty.
# Usage:
#   $(call require,STACK,make docker-setup STACK=<stack_name>)
# -------------------------------------------------------------------

define require
	@if [ -z "$($(1))" ]; then \
		echo "ERROR: $(1) required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  $(2)"; \
		echo ""; \
		echo "See 'make help' for more examples."; \
		exit 1; \
	fi
endef

# -------------------------------------------------------------------
# Help
# -------------------------------------------------------------------

help:
	@echo ""
	@echo "Homelab - Terraform + Ansible + OpenBao + Dockhand/Hawser"
	@echo ""
	@echo "Usage:"
	@echo "  make <target> STACK=<stack_name>"
	@echo ""
	@echo "=================================================="
	@echo " New LXC / New Stack (start here)"
	@echo "=================================================="
	@echo "  make new-lxc-setup STACK=<stack_name>"
	@echo "      Everything for a brand-new stack, in order:"
	@echo "        git pull -> terraform apply -> lxc-bootstrap -> docker"
	@echo "        -> openbao-app-setup -> openbao-agent -> hawser"
	@echo "        -> docker-exporter -> tailscale (pihole only) -> deploy-stack"
	@echo ""
	@echo "  make deploy STACK=<stack_name>"
	@echo "      Same as new-lxc-setup (full deployment)"
	@echo ""
	@echo "=================================================="
	@echo " OpenBao"
	@echo "=================================================="
	@echo "  make openbao-server-setup"
	@echo "      Full OpenBao server setup"
	@echo ""
	@echo "  make bootstrap OPENBAO_DIR=<path>"
	@echo "      Legacy OpenBao bootstrap"
	@echo ""
	@echo "  make openbao-app-setup APP=<app_name>"
	@echo "      Create/update OpenBao app + AppRole"
	@echo ""
	@echo "  make openbao-agent STACK=<stack_name>"
	@echo "      Install OpenBao CLI + AppRole credentials"
	@echo ""
	@echo "=================================================="
	@echo " Individual Ansible Playbooks"
	@echo "=================================================="
	@echo "  make lxc-bootstrap STACK=ai"
	@echo "      Bootstrap an LXC"
	@echo ""
	@echo "  make docker-setup STACK=ai"
	@echo "      Install/configure Docker on one host"
	@echo ""
	@echo "  make hawser-setup STACK=ai"
	@echo "      Install Hawser on one host"
	@echo ""
	@echo "  make docker-exporter-setup STACK=dockership"
	@echo "      Install docker-exporter on one host"
	@echo ""
	@echo "  make tailscale-setup STACK=pihole"
	@echo "      Configure Tailscale on one host"
	@echo ""
	@echo "  make deploy-stack STACK=ai"
	@echo "      Run deploy-stack.yml directly"
	@echo ""
	@echo "=================================================="
	@echo " Terraform"
	@echo "=================================================="
	@echo "  make terraform-init"
	@echo "      Initialize all Terraform stacks"
	@echo ""
	@echo "  make plan STACK=ai"
	@echo "      Plan infrastructure changes"
	@echo ""
	@echo "  make apply STACK=ai"
	@echo "      Create/update infrastructure"
	@echo ""
	@echo "  make destroy STACK=ai"
	@echo "      Destroy infrastructure"
	@echo ""
	@echo "=================================================="
	@echo " Deployment"
	@echo "=================================================="
	@echo "  make deploy STACK=ai"
	@echo "      Full deployment of stack (runs new-lxc-setup)"
	@echo ""
	@echo "  make deploy STACK=ai APP=ollama"
	@echo "      Deploy only ollama (git pull + deploy-stack)"
	@echo ""
	@echo "  make deploy STACK=dockership APP=dockhand"
	@echo "      Deploy only dockhand"
	@echo ""
	@echo "=================================================="
	@echo " Other"
	@echo "=================================================="
	@echo "  make clean"
	@echo "      Clean Terraform cache"
	@echo ""

# -------------------------------------------------------------------
# OpenBao Bootstrap (legacy - kept as-is, but now guards OPENBAO_DIR)
# -------------------------------------------------------------------

bootstrap:
	$(call require,OPENBAO_DIR,make bootstrap OPENBAO_DIR=<path>)
	@echo "Starting OpenBao Bootstrap..."
	cd $(OPENBAO_DIR) && ./00-bootstrap-openbao.sh

# -------------------------------------------------------------------
# OpenBao Server - Full First-Time Setup
# -------------------------------------------------------------------

openbao-server-setup:
	@echo ""
	@echo "=================================================="
	@echo " OpenBao Server - Full First-Time Setup"
	@echo "=================================================="
	@echo ""

	@echo ">>> Step 1/5: Pull latest Git changes"
	cd $(ROOT_DIR) && git pull --ff-only

	@echo ""
	@echo ">>> Step 2/5: Terraform apply"
	cd $(TERRAFORM_DIR)/openbao && terraform apply

	@echo ""
	@echo ">>> Waiting for LXC to become available..."
	sleep 10

	@echo ""
	@echo ">>> Step 3/5: LXC bootstrap"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
		-e stack_name=openbao

	@echo ""
	@echo ">>> Step 4/5: Docker setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
		-e stack_name=openbao

	@echo ""
	@echo ">>> Step 5/5: OpenBao server setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-server-setup.yml

	@echo ""
	@echo "=================================================="
	@echo " OpenBao first-time setup complete"
	@echo "=================================================="
	@echo ""

	@echo "Next: bootstrap each application's policy/secret/AppRole with:"
	@echo "  make openbao-app-setup APP=<app_name>"
	@echo ""

# -------------------------------------------------------------------
# OpenBao Application Setup
# -------------------------------------------------------------------

openbao-app-setup:
	$(call require,APP,make openbao-app-setup APP=<app_name>)
	@echo ""
	@echo "=================================================="
	@echo " OpenBao App Setup"
	@echo " App: $(APP)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-app-setup.yml \
		-e app_name=$(APP)
	@echo ""
	@echo "=================================================="
	@echo " OpenBao app setup complete"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# OpenBao Agent - one host
# -------------------------------------------------------------------

openbao-agent:
	$(call require,STACK,make openbao-agent STACK=<stack_name>)
	@if [ "$(STACK)" = "openbao" ]; then \
		echo "OpenBao server - skipping agent credential setup"; \
	else \
		CRED_DIR=""; \
		if [ -d "$(BOOTSTRAP_DIR)/$(STACK)" ]; then \
			CRED_DIR="$(BOOTSTRAP_DIR)/$(STACK)"; \
		else \
			CRED_DIR=$$(find $(BOOTSTRAP_DIR) -maxdepth 1 -type d -name "*-$(STACK)" 2>/dev/null | head -1); \
		fi; \
		if [ -z "$$CRED_DIR" ] || [ ! -f "$$CRED_DIR/role-id" ] || [ ! -f "$$CRED_DIR/secret-id" ]; then \
			echo "ERROR: Bootstrap credentials not found for $(STACK)"; \
			echo "Expected: $(BOOTSTRAP_DIR)/$(STACK)/ or $(BOOTSTRAP_DIR)/<lxc>-$(STACK)/"; \
			echo ""; \
			echo "Create/bootstrap the application first:"; \
			echo "  make openbao-app-setup APP=$(STACK)"; \
			exit 1; \
		fi; \
		ROLE_ID=$$(cat "$$CRED_DIR/role-id"); \
		SECRET_ID=$$(cat "$$CRED_DIR/secret-id"); \
		echo "Using credentials from $$CRED_DIR"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-agent-setup.yml \
			-e stack_name=$(STACK) \
			-e openbao_role_id="$$ROLE_ID" \
			-e openbao_secret_id="$$SECRET_ID"; \
	fi

# -------------------------------------------------------------------
# Individual Ansible Playbooks (unchanged)
# -------------------------------------------------------------------

# -------------------------------------------------------------------
# LXC Bootstrap
# -------------------------------------------------------------------

lxc-bootstrap:
	$(call require,STACK,make lxc-bootstrap STACK=<stack_name>)
	@echo ""
	@echo "=================================================="
	@echo " LXC Bootstrap"
	@echo " Target: $(STACK)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
		-e stack_name=$(STACK)
	@echo ""
	@echo "=================================================="
	@echo " LXC bootstrap complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Docker Setup
# -------------------------------------------------------------------

docker-setup:
	$(call require,STACK,make docker-setup STACK=<stack_name>)
	@echo ""
	@echo "=================================================="
	@echo " Docker Setup"
	@echo " Target: $(STACK)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
		-e stack_name=$(STACK)
	@echo ""
	@echo "=================================================="
	@echo " Docker setup complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Hawser Setup
# -------------------------------------------------------------------

hawser-setup:
	$(call require,STACK,make hawser-setup STACK=<stack_name>)
	@echo ""
	@echo "=================================================="
	@echo " Hawser Agent Setup"
	@echo " Target: $(STACK)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/hawser.yml \
		-e stack_name=$(STACK)
	@echo ""
	@echo "=================================================="
	@echo " Hawser setup complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Docker Exporter Setup
# -------------------------------------------------------------------

docker-exporter-setup:
	$(call require,STACK,make docker-exporter-setup STACK=<stack_name>)
	@echo ""
	@echo "=================================================="
	@echo " Docker Exporter Setup"
	@echo " Target: $(STACK)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker-exporter.yml \
		-e stack_name=$(STACK)
	@echo ""
	@echo "=================================================="
	@echo " Docker exporter setup complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Tailscale Setup
# -------------------------------------------------------------------

tailscale-setup:
	$(call require,STACK,make tailscale-setup STACK=<stack_name>)
	@echo ""
	@echo "=================================================="
	@echo " Tailscale Setup"
	@echo " Target: $(STACK)"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/tailscale-setup.yml \
		-e stack_name=$(STACK)
	@echo ""
	@echo "=================================================="
	@echo " Tailscale setup complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Deploy Stack Playbook - direct execution
# -------------------------------------------------------------------

deploy-stack:
	$(call require,STACK,make deploy-stack STACK=<stack_name> [APP=<app_name>])
	@echo ""
	@echo "=================================================="
	@echo " Deploy Stack Playbook"
	@echo " Stack: $(STACK)"
	@echo " App:   $(if $(APP),$(APP),ALL)"
	@echo "=================================================="
	@echo ""

	@if [ -n "$(APP)" ]; then \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e app_name=$(APP); \
	else \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK); \
	fi

	@echo ""
	@echo "=================================================="
	@echo " Deploy stack playbook complete"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Terraform Initialization
# -------------------------------------------------------------------

terraform-init:
	@echo ""
	@echo "=================================================="
	@echo " Initializing Terraform"
	@echo "=================================================="
	@echo ""
	@for dir in $(TERRAFORM_DIR)/*; do \
		if [ -d "$$dir" ]; then \
			echo ">>> terraform init: $$dir"; \
			(cd "$$dir" && terraform init); \
			echo ""; \
		fi; \
	done
	@echo "Terraform initialization complete."

# -------------------------------------------------------------------
# Terraform Plan
# -------------------------------------------------------------------

plan: terraform-init
	$(call require,STACK,make plan STACK=<stack_name>)
	@echo ""
	@echo ">>> Terraform plan: $(STACK)"
	(cd $(TERRAFORM_DIR)/$(STACK) && terraform plan)

# -------------------------------------------------------------------
# Terraform Apply
# -------------------------------------------------------------------

apply: terraform-init
	$(call require,STACK,make apply STACK=<stack_name>)
	@echo ""
	@echo ">>> Terraform apply: $(STACK)"
	(cd $(TERRAFORM_DIR)/$(STACK) && terraform apply)

# -------------------------------------------------------------------
# New LXC Setup - runs EVERY playbook, in dependency order.
#
#   1  git pull
#   2  terraform apply          (creates the LXC)
#   3  lxc-bootstrap.yml
#   4  docker.yml
#   5  openbao-app-setup.yml    (creates policy/secret/AppRole; APP = STACK)
#   6  openbao-agent-setup.yml  (needs the AppRole creds from step 5)
#   7  hawser.yml
#   8  docker-exporter.yml
#   9  tailscale-setup.yml      (pihole only)
#   10 deploy-stack.yml
#
# Each step is its own recipe line, so make aborts on the first failure.
# -------------------------------------------------------------------

new-lxc-setup:
	$(call require,STACK,make new-lxc-setup STACK=<stack_name>)
	@if [ "$(STACK)" = "openbao" ]; then \
		echo "ERROR: STACK=openbao is the secrets server itself."; \
		echo "Use: make openbao-server-setup"; \
		exit 1; \
	fi
	@if [ ! -d "$(TERRAFORM_DIR)/$(STACK)" ]; then \
		echo "ERROR: No Terraform stack found at $(TERRAFORM_DIR)/$(STACK)"; \
		exit 1; \
	fi
	@echo ""
	@echo "=================================================="
	@echo " New LXC Setup (all playbooks)"
	@echo " Stack: $(STACK)"
	@echo "=================================================="
	@echo ""

	@echo ">>> Step 1/10: Pull latest Git changes"
	cd $(ROOT_DIR) && git pull --ff-only

	@echo ""
	@echo ">>> Step 2/10: Terraform init + apply"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform init && terraform apply

	@echo ""
	@echo ">>> Waiting for LXC to become available..."
	sleep 10

	@echo ""
	@echo ">>> Step 3/10: LXC bootstrap"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 4/10: Docker setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 5/10: OpenBao app setup (policy + AppRole for $(STACK))"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-app-setup.yml \
		-e app_name=$(STACK)

	@echo ""
	@echo ">>> Step 6/10: OpenBao agent install (CLI + credentials)"
	$(MAKE) --no-print-directory openbao-agent STACK=$(STACK)

	@echo ""
	@echo ">>> Step 7/10: Hawser Agent setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/hawser.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 8/10: Docker exporter setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker-exporter.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 9/10: Tailscale setup"
	@if [ "$(STACK)" = "pihole" ]; then \
		echo "Configuring Tailscale on Pi-hole..."; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/tailscale-setup.yml \
			-e stack_name=$(STACK); \
	else \
		echo "Tailscale is only configured on pihole - skipping."; \
	fi

	@echo ""
	@echo ">>> Step 10/10: Deploy stack"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo "=================================================="
	@echo " New LXC setup complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Deploy
#   APP set   -> git pull + deploy only that app
#   APP empty -> full deployment (= new-lxc-setup)
# -------------------------------------------------------------------

deploy:
	$(call require,STACK,make deploy STACK=<stack_name> [APP=<app_name>])
	@if [ -n "$(APP)" ]; then \
		set -e; \
		echo ""; \
		echo "=================================================="; \
		echo " Application Deployment"; \
		echo " Stack: $(STACK)"; \
		echo " App:   $(APP)"; \
		echo "=================================================="; \
		echo ""; \
		echo ">>> Step 1/2: Pull latest Git changes"; \
		cd $(ROOT_DIR) && git pull --ff-only; \
		echo ""; \
		echo ">>> Step 2/2: Deploying application: $(APP)"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e app_name=$(APP); \
		echo ""; \
		echo "=================================================="; \
		echo " Application deployment complete"; \
		echo " Stack: $(STACK)"; \
		echo " App:   $(APP)"; \
		echo "=================================================="; \
	else \
		$(MAKE) --no-print-directory new-lxc-setup STACK=$(STACK); \
	fi

# -------------------------------------------------------------------
# Destroy
# -------------------------------------------------------------------

destroy: terraform-init
	$(call require,STACK,make destroy STACK=<stack_name>)
	@echo "WARNING: Destroying $(STACK) in 5 seconds..."
	@sleep 5
	(cd $(TERRAFORM_DIR)/$(STACK) && terraform destroy)

# -------------------------------------------------------------------
# Clean
# -------------------------------------------------------------------

clean:
	find $(TERRAFORM_DIR) -name ".terraform" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(TERRAFORM_DIR) -name ".terraform.lock.hcl" -delete 2>/dev/null || true
	find $(TERRAFORM_DIR) -name "*.tfstate*" -delete 2>/dev/null || true