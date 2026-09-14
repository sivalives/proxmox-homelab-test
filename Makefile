# ============================================================
# Homelab Terraform / Ansible Makefile
# ============================================================

SHELL := /bin/bash

ANSIBLE_CONFIG := ansible/ansible.cfg

TERRAFORM_DIR := terraform
STACKS_DIR    := $(TERRAFORM_DIR)/stacks
STACKS_ROOT   := stacks

# Available Terraform / application stacks
STACKS := ai pihole splunk dockership openbao

# OpenBao credentials for Splunk
OPENBAO_BOOTSTRAP_DIR  := $(STACKS_ROOT)/openbao/bootstrap
SPLUNK_BOOTSTRAP_DIR   := $(OPENBAO_BOOTSTRAP_DIR)/lxc102-splunk
OPENBAO_ROLE_ID_FILE   := $(SPLUNK_BOOTSTRAP_DIR)/role-id
OPENBAO_SECRET_ID_FILE := $(SPLUNK_BOOTSTRAP_DIR)/secret-id


# ============================================================
# Help
# ============================================================

.PHONY: help

help:
	@echo ""
	@echo "Homelab Terraform / Ansible"
	@echo ""
	@echo "Terraform:"
	@echo "  make terraform-init GROUP=ai"
	@echo "  make terraform-fmt"
	@echo "  make terraform-validate GROUP=ai"
	@echo "  make terraform-plan GROUP=ai"
	@echo "  make terraform-apply GROUP=ai"
	@echo "  make terraform-output GROUP=ai"
	@echo "  make terraform-destroy GROUP=ai"
	@echo ""
	@echo "Ansible:"
	@echo "  make ansible-bootstrap GROUP=ai"
	@echo "  make ansible-docker GROUP=ai"
	@echo "  make ansible-dockge GROUP=ai"
	@echo ""
	@echo "Application:"
	@echo "  make stack GROUP=ai"
	@echo "  make stack-openbao GROUP=splunk"
	@echo ""
	@echo "OpenBao:"
	@echo "  make check-openbao-credentials"
	@echo ""
	@echo "Full deployment:"
	@echo "  make deploy GROUP=ai"
	@echo "  make deploy GROUP=splunk"
	@echo "  make deploy-all"
	@echo ""
	@echo "Available stacks:"
	@echo "  $(STACKS)"
	@echo ""


# ============================================================
# Validation
# ============================================================

.PHONY: check-group

check-group:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required"; \
		echo "Usage: make <target> GROUP=ai"; \
		exit 1; \
	fi
	@if ! echo "$(STACKS)" | grep -qw "$(GROUP)"; then \
		echo "ERROR: Unknown GROUP '$(GROUP)'"; \
		echo "Available stacks: $(STACKS)"; \
		exit 1; \
	fi


.PHONY: check-openbao-credentials

check-openbao-credentials:
	@if [ ! -s "$(OPENBAO_ROLE_ID_FILE)" ]; then \
		echo "ERROR: Missing OpenBao Role ID file:"; \
		echo "  $(OPENBAO_ROLE_ID_FILE)"; \
		exit 1; \
	fi
	@if [ ! -s "$(OPENBAO_SECRET_ID_FILE)" ]; then \
		echo "ERROR: Missing OpenBao Secret ID file:"; \
		echo "  $(OPENBAO_SECRET_ID_FILE)"; \
		exit 1; \
	fi
	@chmod 600 \
		"$(OPENBAO_ROLE_ID_FILE)" \
		"$(OPENBAO_SECRET_ID_FILE)"
	@echo "[ OK ] OpenBao credentials found"


# ============================================================
# Terraform
# ============================================================

.PHONY: terraform-init terraform-fmt terraform-validate \
        terraform-plan terraform-apply terraform-output \
        terraform-destroy

terraform-init: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform init

terraform-fmt:
	terraform fmt -recursive "$(TERRAFORM_DIR)"

terraform-validate: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform validate

terraform-plan: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform plan

terraform-apply: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform apply

terraform-output: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform output

terraform-destroy: check-group
	cd "$(STACKS_DIR)/$(GROUP)" && terraform destroy


# ============================================================
# Ansible
# ============================================================

.PHONY: ansible-bootstrap ansible-docker ansible-dockge

ansible-bootstrap: check-group
	ANSIBLE_CONFIG="$(ANSIBLE_CONFIG)" \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml \
		--limit "$(GROUP)_hosts"

ansible-docker: check-group
	ANSIBLE_CONFIG="$(ANSIBLE_CONFIG)" \
	ansible-playbook ansible/playbooks/docker.yml \
		--limit "$(GROUP)_hosts"

ansible-dockge: check-group
	ANSIBLE_CONFIG="$(ANSIBLE_CONFIG)" \
	ansible-playbook ansible/playbooks/dockge.yml \
		--limit "$(GROUP)_hosts"


# ============================================================
# Application Stack
# ============================================================

.PHONY: stack

stack: check-group
	ANSIBLE_CONFIG="$(ANSIBLE_CONFIG)" \
	ansible-playbook ansible/playbooks/deploy-stack.yml \
		-e "stack_name=$(GROUP)" \
		-e "enable_openbao_agent=false"


# ============================================================
# Application Stack with OpenBao Agent
# ============================================================

.PHONY: stack-openbao

stack-openbao: check-group check-openbao-credentials
	@ROLE_ID="$$(cat "$(OPENBAO_ROLE_ID_FILE)")"; \
	SECRET_ID="$$(cat "$(OPENBAO_SECRET_ID_FILE)")"; \
	ANSIBLE_CONFIG="$(ANSIBLE_CONFIG)" \
	ansible-playbook ansible/playbooks/deploy-stack.yml \
		-e "stack_name=$(GROUP)" \
		-e "enable_openbao_agent=true" \
		-e "openbao_role_id=$$ROLE_ID" \
		-e "openbao_secret_id=$$SECRET_ID"


# ============================================================
# Full Deployment
# ============================================================

.PHONY: deploy

deploy: check-group
	@echo ""
	@echo "============================================================"
	@echo " Deploying stack: $(GROUP)"
	@echo "============================================================"

	@echo ""
	@echo "=== Terraform init ==="
	$(MAKE) terraform-init GROUP="$(GROUP)"

	@echo ""
	@echo "=== Terraform format ==="
	$(MAKE) terraform-fmt

	@echo ""
	@echo "=== Terraform validate ==="
	$(MAKE) terraform-validate GROUP="$(GROUP)"

	@echo ""
	@echo "=== Terraform plan ==="
	$(MAKE) terraform-plan GROUP="$(GROUP)"

	@echo ""
	@echo "=== Terraform apply ==="
	$(MAKE) terraform-apply GROUP="$(GROUP)"

	@echo ""
	@echo "=== Ansible bootstrap ==="
	$(MAKE) ansible-bootstrap GROUP="$(GROUP)"

	@echo ""
	@echo "=== Ansible Docker ==="
	$(MAKE) ansible-docker GROUP="$(GROUP)"

	@echo ""
	@echo "=== Ansible Dockge ==="
	$(MAKE) ansible-dockge GROUP="$(GROUP)"

	@echo ""
	@echo "=== Application stack ==="
	@if [ "$(GROUP)" = "splunk" ]; then \
		$(MAKE) stack-openbao GROUP="$(GROUP)"; \
	else \
		$(MAKE) stack GROUP="$(GROUP)"; \
	fi

	@echo ""
	@echo "============================================================"
	@echo " Deployment complete: $(GROUP)"
	@echo "============================================================"


# ============================================================
# Deploy All Stacks
# ============================================================

.PHONY: deploy-all

deploy-all:
	@for stack in $(STACKS); do \
		echo ""; \
		echo "============================================================"; \
		echo " Deploying $$stack"; \
		echo "============================================================"; \
		$(MAKE) deploy GROUP="$$stack" || exit 1; \
	done

	@echo ""
	@echo "============================================================"
	@echo " All stacks deployed"
	@echo "============================================================"