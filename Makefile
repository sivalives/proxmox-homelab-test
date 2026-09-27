# Homelab Makefile - Terraform + Ansible + OpenBao + Dockhand/Hawser

.PHONY: help bootstrap openbao-server-setup openbao-app-setup openbao-agent \
        terraform-init plan apply destroy deploy docker-exporter clean

# -------------------------------------------------------------------
# Variables
# -------------------------------------------------------------------

STACK ?=
APP ?=

ROOT_DIR := $(CURDIR)

TERRAFORM_DIR := $(ROOT_DIR)/terraform/stacks
OPENBAO_DIR := $(ROOT_DIR)/stacks/openbao
BOOTSTRAP_DIR := $(OPENBAO_DIR)/bootstrap

ANSIBLE_DIR := $(ROOT_DIR)/ansible
ANSIBLE_CONFIG := $(ANSIBLE_DIR)/ansible.cfg
ANSIBLE_PLAYBOOK := ansible-playbook

# Always run Ansible with the repository's configuration.
export ANSIBLE_CONFIG

# -------------------------------------------------------------------
# Helper: fail with a message if a required variable is empty.
# Usage inside a recipe:  $(call require,STACK,make plan STACK=<stack_name>)
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
	@echo "  make deploy STACK=<stack_name> [APP=<app_name>]"
	@echo "  make openbao-app-setup APP=<app_name>"
	@echo ""
	@echo "Targets:"
	@echo "  openbao-server-setup                          One-time: full OpenBao server setup"
	@echo "                                          (permissions, init/unseal, policies, secrets)"
	@echo "  bootstrap                              (legacy) One-time OpenBao server setup"
	@echo "  openbao-app-setup APP=dockership            Create/update OpenBao app + AppRole"
	@echo "  openbao-agent STACK=splunk            Install OpenBao CLI + AppRole creds for a stack"
	@echo "  terraform-init                        Initialize all Terraform stacks"
	@echo "  plan STACK=splunk                     Plan infrastructure changes"
	@echo "  apply STACK=splunk                    Create/update infrastructure"
	@echo "  deploy STACK=ai                       Full deployment of stack (includes agent install)"
	@echo "  deploy STACK=ai APP=ollama            Deploy only ollama"
	@echo "  deploy STACK=dockership APP=dockhand  Deploy only dockhand"
	@echo "  docker-exporter                              Setup docker-exporter on all Docker hosts"
	@echo "  destroy STACK=splunk                  Destroy infrastructure"
	@echo "  clean                                  Clean Terraform cache"
	@echo ""

# -------------------------------------------------------------------
# OpenBao Bootstrap (legacy - kept as-is, do not remove)
# -------------------------------------------------------------------
#
# Pre-existing one-time setup target. Left untouched so anything that
# already calls `make bootstrap` keeps working. For new/rebuilt
# environments, prefer `make openbao-server-setup` below, which runs every
# step (permissions, init/unseal, secrets) instead of just this one
# script.
# -------------------------------------------------------------------

bootstrap:
	@echo "Starting OpenBao Bootstrap..."
	cd $(OPENBAO_DIR) && ./00-bootstrap-openbao.sh

# -------------------------------------------------------------------
# OpenBao Server - Full First-Time Setup
# -------------------------------------------------------------------
#
# The old numbered shell scripts (00-fix-permissions, 01-bootstrap,
# 03-setup-secrets) have been replaced by the openbao_server Ansible
# role - it covers all three phases (permissions, init/unseal/
# policies/ui-admin, placeholder secrets) in one idempotent run.
#
# Adjust ANSIBLE_DIR/playbooks/openbao-server-setup.yml below if you named
# the playbook differently when you copied it into this repo, and
# make sure it targets the right inventory group for your OpenBao
# server host.
#
# Run this ONCE per OpenBao server. Safe to re-run.
# -------------------------------------------------------------------

openbao-server-setup:
	@echo ""
	@echo "=================================================="
	@echo " OpenBao Server - Full First-Time Setup"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-server-setup.yml
	@echo ""
	@echo "=================================================="
	@echo " OpenBao first-time setup complete"
	@echo "=================================================="
	@echo ""
	@echo "Next: bootstrap each application's policy/secret/AppRole with:"
	@echo "  make openbao-app-setup APP=<app_name>"
	@echo ""


openbao-app-setup:
	@echo ""
	@echo "=================================================="
	@echo " OpenBao App setup - set per LXC level"
	@echo "=================================================="
	@echo ""
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-app-setup.yml
	@echo ""
	@echo "=================================================="

# -------------------------------------------------------------------
# OpenBao Application Bootstrap
# -------------------------------------------------------------------
#
# Creates/updates:
#   - secret/<app>
#   - <app>-policy
#   - AppRole <app>
#   - Role ID
#   - Secret ID
#
# Credentials are staged under:
#   stacks/openbao/bootstrap/<app>/
#
# This is intentionally NOT part of normal deploy.
# -------------------------------------------------------------------

openbao-server-setup:
	@echo ""
	@echo "=================================================="
	@echo " OpenBao Server - Full First-Time Setup"
	@echo "=================================================="
	@echo ""

	@echo ">>> Step 1/8: Pull latest Git changes"
	cd $(ROOT_DIR) && git pull --ff-only

	@echo ""
	@echo ">>> Step 2/8: Terraform apply"
	cd $(TERRAFORM_DIR)/openbao && terraform apply

	@echo ""
	@echo ">>> Waiting for LXC to become available..."
	sleep 10

	@echo ""
	@echo ">>> Step 3/8: LXC bootstrap"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
		-e stack_name=openbao

	@echo ""
	@echo ">>> Step 4/8: Docker setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
		-e stack_name=openbao

	@echo ""
	@echo ">>> Step 5/8: OpenBao server setup"
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
# OpenBao Agent - install CLI + AppRole credentials for one stack
# -------------------------------------------------------------------
#
# Standalone target so it can be run on its own (`make openbao-agent
# STACK=splunk`) AND reused by `deploy` for every stack, instead of
# duplicating the credential lookup in two places.
#
# Looks for staged credentials in either layout, so it works whether
# they were created by the old lxc-prefixed flow or the current one:
#   stacks/openbao/bootstrap/<stack>/role-id            (current)
#   stacks/openbao/bootstrap/<lxc_name>-<stack>/role-id  (legacy)
#
# Skipped automatically for STACK=openbao (the server doesn't need
# credentials to reach itself).
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
# Terraform Initialization
# -------------------------------------------------------------------
#
# Initialize every Terraform stack.
#
# Safe to run repeatedly.
# Does not modify infrastructure.
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
# Deploy
# -------------------------------------------------------------------
#
# Two deployment modes:
#
# 1. Full stack deployment:
#
#      make deploy STACK=ai
#
#    Runs:
#      Git pull
#      Terraform apply
#      LXC bootstrap
#      Docker setup
#      Hawser setup
#      OpenBao agent install (CLI + AppRole credentials, any stack)
#      Tailscale setup
#      Deploy all applications
#
# 2. Application-only deployment:
#
#      make deploy STACK=ai APP=ollama
#
#    Runs ONLY:
#      Git pull
#      Deploy requested application
# -------------------------------------------------------------------

deploy:
	$(call require,STACK,make deploy STACK=<stack_name> [APP=<app_name>])
	@if [ -n "$(APP)" ]; then \
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
		echo ""; \
		echo "=================================================="; \
		echo " Full Deployment: $(STACK)"; \
		echo " Applications: ALL"; \
		echo "=================================================="; \
		echo ""; \
		echo ">>> Step 1/8: Pull latest Git changes"; \
		cd $(ROOT_DIR) && git pull --ff-only; \
		echo ""; \
		echo ">>> Step 2/8: Terraform apply"; \
		(cd $(TERRAFORM_DIR)/$(STACK) && terraform apply); \
		echo ""; \
		echo ">>> Waiting for LXC to become available..."; \
		sleep 10; \
		echo ""; \
		echo ">>> Step 3/8: LXC bootstrap"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
			-e stack_name=$(STACK); \
		echo ""; \
		echo ">>> Step 4/8: Docker setup"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
			-e stack_name=$(STACK); \
		echo ""; \
		echo ">>> Step 5/8: OpenBao agent install (CLI + credentials)"; \
		$(MAKE) --no-print-directory openbao-agent STACK=$(STACK); \
		echo ""; \
		echo ">>> Step 6/8: Hawser Agent setup"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/hawser.yml \
			-e stack_name=$(STACK); \
		echo ""; \
		echo ">>> Step 7/8: Tailscale setup"; \
		if [ "$(STACK)" = "pihole" ]; then \
			echo "Configuring Tailscale on Pi-hole..."; \
			$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/tailscale-setup.yml \
				-e stack_name=$(STACK); \
		else \
			echo "Tailscale is only configured on pihole - skipping."; \
		fi; \
		echo ""; \
		echo ">>> Step 8/8: Deploy stack"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK); \
		echo ""; \
		echo "=================================================="; \
		echo " Full deployment complete: $(STACK)"; \
		echo " Applications deployed: ALL"; \
		echo "=================================================="; \
	fi

# -------------------------------------------------------------------
# docker-exporter
# -------------------------------------------------------------------

docker-exporter:
	@echo ""
	@echo "=================================================="
	@echo " docker-exporter Setup"
	@echo " Target: docker_hosts"
	@echo "=================================================="
	@echo ""
	@echo ">>> Running docker-exporter playbook..."
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker-exporter.yml
	@echo ""
	@echo "=================================================="
	@echo " docker-exporter setup complete"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# docker-exporter
# -------------------------------------------------------------------

dockhand-agent-setup:
	@echo ""
	@echo "=================================================="
	@echo " docker-exporter Setup"
	@echo " Target: docker_hosts"
	@echo "=================================================="
	@echo ""
	@echo ">>> Running dockhand -> hawser.yml playbook..."
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/hawser.yml -e stack_name=$(STACK);
	@echo ""
	@echo "=================================================="
	@echo " dockhand agent setup complete"
	@echo "=================================================="
	@echo ""



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