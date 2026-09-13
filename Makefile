# Ansible configuration
ANSIBLE_CONFIG := ansible/ansible.cfg

# -------------------------
# Ansible
# -------------------------

ansible-bootstrap:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml

ansible-docker:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml

ansible-dockge:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml

# Deploy one application stack
# Usage: make stack GROUP=ai
stack:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required."; \
		echo "Usage: make stack GROUP=ai"; \
		exit 1; \
	fi
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/stack.yml \
	-e stack_group=$(GROUP)


# -------------------------
# Terraform
# -------------------------

terraform-init:
	cd terraform && terraform init

terraform-fmt:
	cd terraform && terraform fmt -recursive

terraform-validate:
	cd terraform && terraform validate

# Plan one LXC
# Usage: make terraform-plan GROUP=ai
terraform-plan:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required."; \
		echo "Usage: make terraform-plan GROUP=ai"; \
		exit 1; \
	fi
	cd terraform && terraform plan \
		-var-file=terraform.tfvars \
		-target=module.$(GROUP)

# Apply one LXC
# Usage: make terraform-apply GROUP=ai
terraform-apply:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required."; \
		echo "Usage: make terraform-apply GROUP=ai"; \
		exit 1; \
	fi
	cd terraform && terraform apply \
		-var-file=terraform.tfvars \
		-target=module.$(GROUP)

terraform-output:
	cd terraform && terraform output

terraform-destroy:
	cd terraform && terraform destroy \
		-var-file=terraform.tfvars


# -------------------------
# Deploy one stack
# -------------------------

# Usage:
#   make deploy GROUP=ai
#   make deploy GROUP=pihole
#   make deploy GROUP=splunk
deploy:
	@if [ -z "$(GROUP)" ]; then \
		echo "ERROR: GROUP is required."; \
		echo "Usage: make deploy GROUP=ai"; \
		echo "       make deploy GROUP=pihole"; \
		echo "       make deploy GROUP=splunk"; \
		exit 1; \
	fi

	@echo "========================================"
	@echo " Deploying stack: $(GROUP)"
	@echo "========================================"

	@echo "=== Terraform init ==="
	cd terraform && terraform init

	@echo "=== Terraform format ==="
	cd terraform && terraform fmt -recursive

	@echo "=== Terraform validate ==="
	cd terraform && terraform validate

	@echo "=== Terraform plan: $(GROUP) ==="
	cd terraform && terraform plan \
		-var-file=terraform.tfvars \
		-target=module.$(GROUP)

	@echo "=== Terraform apply: $(GROUP) ==="
	cd terraform && terraform apply \
		-var-file=terraform.tfvars \
		-target=module.$(GROUP)

	@echo "=== Ansible bootstrap: $(GROUP) ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml \
	--limit $(GROUP)

	@echo "=== Ansible Docker: $(GROUP) ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml \
	--limit $(GROUP)

	@echo "=== Ansible Dockge: $(GROUP) ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml \
	--limit $(GROUP)

	@echo "=== Deploy application stack: $(GROUP) ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/stack.yml \
	-e stack_group=$(GROUP)

	@echo "========================================"
	@echo " Deployment complete: $(GROUP)"
	@echo "========================================"


# -------------------------
# Full homelab deployment
# -------------------------

# Usage: make deploy-all
deploy-all:
	@echo "========================================"
	@echo " Deploying FULL homelab"
	@echo "========================================"

	@echo "=== Terraform init ==="
	cd terraform && terraform init

	@echo "=== Terraform format ==="
	cd terraform && terraform fmt -recursive

	@echo "=== Terraform validate ==="
	cd terraform && terraform validate

	@echo "=== Terraform plan ==="
	cd terraform && terraform plan \
		-var-file=terraform.tfvars

	@echo "=== Terraform apply ==="
	cd terraform && terraform apply \
		-var-file=terraform.tfvars

	@echo "=== Ansible bootstrap ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml

	@echo "=== Ansible Docker ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml

	@echo "=== Ansible Dockge ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml

	@echo "=== Deploy all application stacks ==="
	@echo "Use 'make stack GROUP=<name>' to deploy individual stacks."

	@echo "========================================"
	@echo " Full homelab deployment complete"
	@echo "========================================"


# -------------------------
# Make targets
# -------------------------

.PHONY: \
	ansible-bootstrap \
	ansible-docker \
	ansible-dockge \
	stack \
	terraform-init \
	terraform-fmt \
	terraform-validate \
	terraform-plan \
	terraform-apply \
	terraform-output \
	terraform-destroy \
	deploy \
	deploy-all