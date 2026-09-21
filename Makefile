# Homelab Makefile - Terraform + Ansible + OpenBao + Dockhand/Hawser

.PHONY: help bootstrap openbao-app terraform-init plan apply destroy deploy clean

# -------------------------------------------------------------------
# Variables
# -------------------------------------------------------------------

STACK ?=
APP ?=

TERRAFORM_DIR := terraform/stacks
BOOTSTRAP_DIR := stacks/openbao/bootstrap
ANSIBLE_DIR := ansible
ANSIBLE_PLAYBOOK := ansible-playbook

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
	@echo "  make openbao-app APP=<app_name>"
	@echo ""
	@echo "Targets:"
	@echo "  bootstrap                              One-time: Setup OpenBao server"
	@echo "  openbao-app APP=dockership            Create/update OpenBao app + AppRole"
	@echo "  terraform-init                        Initialize all Terraform stacks"
	@echo "  plan STACK=splunk                     Plan infrastructure changes"
	@echo "  apply STACK=splunk                    Create/update infrastructure"
	@echo "  deploy STACK=ai                       Deploy all applications in stack"
	@echo "  deploy STACK=ai APP=ollama            Deploy only ollama"
	@echo "  deploy STACK=dockership APP=dockhand  Deploy only dockhand"
	@echo "  destroy STACK=splunk                  Destroy infrastructure"
	@echo "  clean                                  Clean Terraform cache"
	@echo ""

# -------------------------------------------------------------------
# OpenBao Bootstrap
# -------------------------------------------------------------------

bootstrap:
	@echo "Starting OpenBao Bootstrap..."
	cd stacks/openbao && ./00-bootstrap-openbao.sh

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

openbao-app:
	@if [ -z "$(APP)" ]; then \
		echo "ERROR: APP required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make openbao-app APP=<app_name>"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make openbao-app APP=dockership"; \
		echo "  make openbao-app APP=ai"; \
		exit 1; \
	fi

	@echo ""
	@echo "=================================================="
	@echo " OpenBao Application Bootstrap: $(APP)"
	@echo "=================================================="
	@echo ""

	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao-app.yml \
		-e app_name=$(APP)

	@echo ""
	@echo "=================================================="
	@echo " OpenBao application bootstrap complete: $(APP)"
	@echo "=================================================="
	@echo ""

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
			cd "$$dir" && terraform init; \
			cd - >/dev/null; \
			echo ""; \
		fi; \
	done

	@echo "Terraform initialization complete."

# -------------------------------------------------------------------
# Terraform Plan
# -------------------------------------------------------------------

plan: terraform-init
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make plan STACK=<stack_name>"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make plan STACK=ai"; \
		echo "  make plan STACK=splunk"; \
		exit 1; \
	fi

	@echo ""
	@echo ">>> Terraform plan: $(STACK)"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform plan

# -------------------------------------------------------------------
# Terraform Apply
# -------------------------------------------------------------------

apply: terraform-init
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make apply STACK=<stack_name>"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make apply STACK=ai"; \
		echo "  make apply STACK=splunk"; \
		exit 1; \
	fi

	@echo ""
	@echo ">>> Terraform apply: $(STACK)"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply

# -------------------------------------------------------------------
# Full Deployment
# -------------------------------------------------------------------
#
# Deployment flow:
#
#   1. Git pull
#   2. Terraform apply
#   3. LXC bootstrap
#   4. Docker setup
#   5. Hawser agent setup
#   6. OpenBao CLI + credentials
#   7. Tailscale setup
#   8. Deploy application stack
#
# APP is optional:
#
#   make deploy STACK=ai
#       -> deploy all applications under ai
#
#   make deploy STACK=ai APP=ollama
#       -> deploy only ollama
#
# Dockhand is deployed as a normal application:
#
#   make deploy STACK=dockership APP=dockhand
#
# No separate Dockhand server setup is required.
# -------------------------------------------------------------------

deploy: terraform-init
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make deploy STACK=<stack_name>"; \
		echo "  make deploy STACK=<stack_name> APP=<app_name>"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make deploy STACK=ai"; \
		echo "  make deploy STACK=ai APP=ollama"; \
		echo "  make deploy STACK=dockership APP=dockhand"; \
		exit 1; \
	fi

	@echo ""
	@echo "=================================================="
	@echo " Deploying: $(STACK)"
	@if [ -n "$(APP)" ]; then \
		echo " Application: $(APP)"; \
	else \
		echo " Applications: ALL"; \
	fi
	@echo "=================================================="
	@echo ""

	@echo ">>> Step 1/8: Pull latest Git changes"
	git pull --ff-only

	@echo ""
	@echo ">>> Step 2/8: Terraform apply"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply

	@echo ""
	@echo ">>> Waiting for LXC to become available..."
	@sleep 10

	@echo ""
	@echo ">>> Step 3/8: LXC bootstrap"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/lxc-bootstrap.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 4/8: Docker setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/docker.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 5/8: Hawser Agent setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/hawser.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 6/8: OpenBao CLI + credentials"

	@if [ "$(STACK)" = "openbao" ]; then \
		echo "OpenBao server - skipping client credentials setup"; \
	else \
		CRED_DIR=$$(find $(BOOTSTRAP_DIR) -maxdepth 1 -type d -name "*-$(STACK)" | head -1); \
		if [ -z "$$CRED_DIR" ] || [ ! -f "$$CRED_DIR/role-id" ] || [ ! -f "$$CRED_DIR/secret-id" ]; then \
			echo "ERROR: Bootstrap credentials not found for $(STACK)"; \
			echo "Expected: $(BOOTSTRAP_DIR)/<app>/ or $(BOOTSTRAP_DIR)/lxc*-$(STACK)/"; \
			echo ""; \
			echo "Create/bootstrap the application first:"; \
			echo "  make openbao-app APP=$(STACK)"; \
			exit 1; \
		fi; \
		ROLE_ID=$$(cat "$$CRED_DIR/role-id"); \
		SECRET_ID=$$(cat "$$CRED_DIR/secret-id"); \
		echo "Using credentials from $$CRED_DIR"; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/openbao.yml \
			-e stack_name=$(STACK) \
			-e openbao_role_id="$$ROLE_ID" \
			-e openbao_secret_id="$$SECRET_ID"; \
	fi

	@echo ""
	@echo ">>> Step 7/8: Tailscale setup"

	@if [ "$(STACK)" = "pihole" ]; then \
		echo "Configuring Tailscale on Pi-hole..."; \
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/tailscale-setup.yml \
			-e stack_name=$(STACK); \
	else \
		echo "Tailscale is only configured on pihole - skipping."; \
	fi

	@echo ""
	@echo ">>> Step 8/8: Deploy stack"

	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
		-e stack_name=$(STACK) \
		$(if $(APP),-e app_name=$(APP))

	@echo ""
	@echo "=================================================="
	@echo " Deployment complete: $(STACK)"
	@if [ -n "$(APP)" ]; then \
		echo " Application deployed: $(APP)"; \
	else \
		echo " Applications deployed: ALL"; \
	fi
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Destroy
# -------------------------------------------------------------------

destroy: terraform-init
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make destroy STACK=<stack_name>"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make destroy STACK=ai"; \
		echo "  make destroy STACK=splunk"; \
		exit 1; \
	fi

	@echo "WARNING: Destroying $(STACK) in 5 seconds..."
	@sleep 5

	cd $(TERRAFORM_DIR)/$(STACK) && terraform destroy

# -------------------------------------------------------------------
# Clean
# -------------------------------------------------------------------

clean:
	find $(TERRAFORM_DIR) -name ".terraform" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(TERRAFORM_DIR) -name ".terraform.lock.hcl" -delete 2>/dev/null || true
	find $(TERRAFORM_DIR) -name "*.tfstate*" -delete 2>/dev/null || true