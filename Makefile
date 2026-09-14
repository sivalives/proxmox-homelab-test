# Homelab Makefile - Simple Version

.PHONY: help bootstrap plan apply destroy deploy clean

# Variables
STACK ?=
TERRAFORM_DIR := terraform/stacks
BOOTSTRAP_DIR := stacks/openbao/bootstrap

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
	@echo "  deploy STACK=splunk    Full deployment (Terraform + Ansible)"
	@echo "  destroy STACK=splunk   Destroy infrastructure"
	@echo "  clean                  Clean Terraform cache"
	@echo ""

bootstrap:
	@echo "Starting OpenBao Bootstrap..."
	cd stacks/openbao && ./00-bootstrap-openbao.sh

plan:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required"; exit 1; fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform plan

apply:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required"; exit 1; fi
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply

destroy:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required"; exit 1; fi
	@echo "WARNING: Destroying $(STACK) in 5 seconds..."
	@sleep 5
	cd $(TERRAFORM_DIR)/$(STACK) && terraform destroy

deploy:
	@if [ -z "$(STACK)" ]; then echo "ERROR: STACK required"; exit 1; fi
	@echo "Deploying: $(STACK)"
	cd $(TERRAFORM_DIR)/$(STACK) && terraform apply
	@sleep 10
	@if [ "$(STACK)" = "openbao" ]; then \
		ansible-playbook ansible/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e enable_openbao_agent=false; \
	else \
		CRED_DIR=$$(find $(BOOTSTRAP_DIR) -maxdepth 1 -type d -name "*-$(STACK)" | head -1); \
		if [ -z "$$CRED_DIR" ] || [ ! -f "$$CRED_DIR/role-id" ]; then \
			echo "ERROR: Bootstrap credentials not found for $(STACK)"; \
			echo "Expected: $(BOOTSTRAP_DIR)/lxc*-$(STACK)/"; \
			echo "Run: make bootstrap"; \
			exit 1; \
		fi; \
		ROLE_ID=$$(cat $$CRED_DIR/role-id); \
		SECRET_ID=$$(cat $$CRED_DIR/secret-id); \
		echo "Using credentials from $$CRED_DIR"; \
		ansible-playbook ansible/playbooks/deploy-stack.yml \
			-e stack_name=$(STACK) \
			-e enable_openbao_agent=true \
			-e openbao_role_id="$$ROLE_ID" \
			-e openbao_secret_id="$$SECRET_ID"; \
	fi

clean:
	find $(TERRAFORM_DIR) -name ".terraform" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(TERRAFORM_DIR) -name ".terraform.lock.hcl" -delete 2>/dev/null || true
	find $(TERRAFORM_DIR) -name "*.tfstate*" -delete 2>/dev/null || true