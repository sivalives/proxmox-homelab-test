# Homelab Makefile - Simple Version

.PHONY: help bootstrap plan apply destroy deploy clean

# Variables
STACK ?= 
TERRAFORM_DIR := terraform/stacks
BOOTSTRAP_DIR := stacks/openbao/bootstrap

# ============================================================
# Help
# ============================================================

help:
	@echo ""
	@echo "Homelab - Terraform + Ansible + OpenBao"
	@echo ""
	@echo "Usage: make <target> STACK=<stack_name>"
	@echo ""
	@echo "Targets:"
	@echo "  bootstrap              One-time: Setup OpenBao + create AppRoles"
	@echo "  plan STACK=splunk      Plan infrastructure changes"
	@echo "  apply STACK=splunk     Create/update infrastructure (Terraform)"
	@echo "  deploy STACK=splunk    Full deployment (Terraform + Ansible)"
	@echo "  destroy STACK=splunk   Destroy infrastructure"
	@echo "  clean                  Clean Terraform cache"
	@echo ""
	@echo "Examples:"
	@echo "  make bootstrap"
	@echo "  make deploy STACK=splunk"
	@echo "  make deploy STACK=openbao"
	@echo "  make plan STACK=pihole"
	@echo "  make destroy STACK=pihole"
	@echo ""

# ============================================================
# Bootstrap (One-time)
# ============================================================

bootstrap:
	@echo ""
	@echo "Starting OpenBao Bootstrap..."
	@echo ""
	cd stacks/openbao && ./00-bootstrap-openbao.sh
	@echo ""
	@echo "Bootstrap complete!"
	@echo ""

# ============================================================
# Terraform
# ============================================================

plan:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required (e.g., make plan STACK=splunk)"; exit 1; fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform plan

apply:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required (e.g., make apply STACK=splunk)"; exit 1; fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply

destroy:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required (e.g., make destroy STACK=splunk)"; exit 1; fi
	@echo ""
	@echo "WARNING: Destroying $(STACK)"
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	cd $(TERRAFORM_DIR)/$(STACK) && terraform destroy

# ============================================================
# Deploy (Terraform + Ansible)
# ============================================================

deploy:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required (e.g., make deploy STACK=splunk)"; exit 1; fi
	@echo ""
	@echo "=========================================="
	@echo "Deploying: $(STACK)"
	@echo "=========================================="
	@echo ""
	
	@# Check if bootstrap needed (for agent stacks)
	@if [ "$(STACK)" != "openbao" ]; then \
		if [ ! -d "$(BOOTSTRAP_DIR)/lxc*" ]; then \
			echo "ERROR: Bootstrap credentials not found!"; \
			echo "Run: make bootstrap"; \
			exit 1; \
		fi; \
	fi
	
	@echo "Step 1: Terraform Apply"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply
	
	@echo ""
	@echo "Waiting for container..."
	@sleep 10
	
	@echo ""
	@echo "Step 2: Ansible Deploy"
	@if [ "$(STACK)" = "openbao" ]; then \
		ansible-playbook ansible/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e enable_openbao_agent=false; \
	else \
		VMID=$$(grep "vm_id.*=" $(TERRAFORM_DIR)/$(STACK)/main.tf | grep -oE '[0-9]+' | head -1); \
		ROLE_ID=$$(cat $(BOOTSTRAP_DIR)/lxc$$VMID-$(STACK)/role-id 2>/dev/null); \
		SECRET_ID=$$(cat $(BOOTSTRAP_DIR)/lxc$$VMID-$(STACK)/secret-id 2>/dev/null); \
		ansible-playbook ansible/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e enable_openbao_agent=true \
			-e openbao_role_id="$$ROLE_ID" \
			-e openbao_secret_id="$$SECRET_ID"; \
	fi
	
	@echo ""
	@echo "=========================================="
	@echo "Deployment complete: $(STACK)"
	@echo "=========================================="
	@echo ""

# ============================================================
# Cleanup
# ============================================================

clean:
	@echo "Cleaning Terraform cache..."
	find $(TERRAFORM_DIR) -name ".terraform" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(TERRAFORM_DIR) -name ".terraform.lock.hcl" -delete 2>/dev/null || true
	find $(TERRAFORM_DIR) -name "*.tfstate*" -delete 2>/dev/null || true
	@echo "Clean complete"