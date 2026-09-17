# Homelab Makefile - Terraform + Ansible + OpenBao

.PHONY: help bootstrap plan apply destroy deploy clean

# -------------------------------------------------------------------
# Variables
# -------------------------------------------------------------------

STACK ?=
TERRAFORM_DIR := terraform/stacks
BOOTSTRAP_DIR := stacks/openbao/bootstrap
ANSIBLE_DIR := ansible
ANSIBLE_PLAYBOOK := ansible-playbook

# -------------------------------------------------------------------
# Help
# -------------------------------------------------------------------

help:
	@echo ""
	@echo "Homelab - Terraform + Ansible + OpenBao"
	@echo ""
	@echo "Usage: make <target> STACK=<stack_name>"
	@echo ""
	@echo "Targets:"
	@echo "  bootstrap              One-time: Setup OpenBao + create AppRoles"
	@echo "  plan STACK=splunk      Plan infrastructure changes"
	@echo "  apply STACK=splunk     Create/update infrastructure"
	@echo "  deploy STACK=splunk    Full deployment (Git pull + Terraform + Ansible)"
	@echo "  destroy STACK=splunk   Destroy infrastructure"
	@echo "  clean                  Clean Terraform cache"
	@echo ""

# -------------------------------------------------------------------
# OpenBao Bootstrap
# -------------------------------------------------------------------

bootstrap:
	@echo "Starting OpenBao Bootstrap..."
	cd stacks/openbao && ./00-bootstrap-openbao.sh

# -------------------------------------------------------------------
# Terraform
# -------------------------------------------------------------------

plan:
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		exit 1; \
	fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform plan

apply:
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		exit 1; \
	fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply

# -------------------------------------------------------------------
# Full Deployment
# -------------------------------------------------------------------

deploy:
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
		exit 1; \
	fi

	@echo ""
	@echo "=================================================="
	@echo " Deploying: $(STACK)"
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
	@echo ">>> Step 5/8: Dockge setup"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/dockge.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo ">>> Step 6/8: OpenBao CLI + credentials"

	@if [ "$(STACK)" = "openbao" ]; then \
		echo "OpenBao server - skipping client credentials setup"; \
	else \
		CRED_DIR=$$(find $(BOOTSTRAP_DIR) -maxdepth 1 -type d -name "*-$(STACK)" | head -1); \
		if [ -z "$$CRED_DIR" ] || [ ! -f "$$CRED_DIR/role-id" ]; then \
			echo "ERROR: Bootstrap credentials not found for $(STACK)"; \
			echo "Expected: $(BOOTSTRAP_DIR)/lxc*-$(STACK)/"; \
			echo "Run: make bootstrap"; \
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
		$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/tailscale.yml \
			-e stack_name=$(STACK); \
	else \
		echo "Tailscale is only configured on pihole - skipping."; \
	fi

	@echo ""
	@echo ">>> Step 8/8: Deploy stack"
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/deploy-stack.yml \
		-e stack_name=$(STACK)

	@echo ""
	@echo "=================================================="
	@echo " Deployment complete: $(STACK)"
	@echo "=================================================="
	@echo ""

# -------------------------------------------------------------------
# Destroy
# -------------------------------------------------------------------

destroy:
	@if [ -z "$(STACK)" ]; then \
		echo "ERROR: STACK required"; \
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