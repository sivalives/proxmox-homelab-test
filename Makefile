# ============================================================
# Homelab Makefile
# ============================================================

ANSIBLE_CONFIG := ansible/ansible.cfg

TERRAFORM_DIR := terraform
STACKS_DIR    := $(TERRAFORM_DIR)/stacks

# Example:
#   make terraform-plan GROUP=ai
#   make deploy GROUP=pihole
#
# Available stacks:
#   ai pihole splunk dockership
#
STACKS := ai pihole splunk dockership


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


# ============================================================
# Terraform
# ============================================================

.PHONY: terraform-init terraform-fmt terraform-validate \
        terraform-plan terraform-apply terraform-output \
        terraform-destroy

terraform-init: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform init

terraform-fmt:
	terraform fmt -recursive $(TERRAFORM_DIR)

terraform-validate: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform validate

terraform-plan: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform plan

terraform-apply: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform apply

terraform-output: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform output

terraform-destroy: check-group
	cd $(STACKS_DIR)/$(GROUP) && terraform destroy


# ============================================================
# Ansible
# ============================================================

.PHONY: ansible-bootstrap ansible-docker ansible-dockge

ansible-bootstrap:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml \
	--limit $(GROUP)_hosts

ansible-docker:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml \
	--limit $(GROUP)_hosts

ansible-dockge:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml \
	--limit $(GROUP)_hosts


# ============================================================
# Application Stack
# ============================================================

.PHONY: stack

stack:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required"; \
		echo "Usage: make stack GROUP=ai"; \
		exit 1; \
	fi
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/stack.yml \
	-e stack_group=$(GROUP)


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
	$(MAKE) terraform-init GROUP=$(GROUP)

	@echo ""
	@echo "=== Terraform format ==="
	$(MAKE) terraform-fmt

	@echo ""
	@echo "=== Terraform validate ==="
	$(MAKE) terraform-validate GROUP=$(GROUP)

	@echo ""
	@echo "=== Terraform plan ==="
	$(MAKE) terraform-plan GROUP=$(GROUP)

	@echo ""
	@echo "=== Terraform apply ==="
	$(MAKE) terraform-apply GROUP=$(GROUP)

	@echo ""
	@echo "=== Ansible bootstrap ==="
	$(MAKE) ansible-bootstrap GROUP=$(GROUP)

	@echo ""
	@echo "=== Ansible Docker ==="
	$(MAKE) ansible-docker GROUP=$(GROUP)

	@echo ""
	@echo "=== Ansible Dockge ==="
	$(MAKE) ansible-dockge GROUP=$(GROUP)

	@echo ""
	@echo "=== Application stack ==="
	$(MAKE) stack GROUP=$(GROUP)

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
		$(MAKE) deploy GROUP=$$stack || exit 1; \
	done

	@echo ""
	@echo "============================================================"
	@echo " All stacks deployed"
	@echo "============================================================"


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
	@echo ""
	@echo "Full deployment:"
	@echo "  make deploy GROUP=ai"
	@echo "  make deploy-all"
	@echo ""
	@echo "Available stacks:"
	@echo "  $(STACKS)"
	@echo ""