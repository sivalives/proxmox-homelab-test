# Ansible configuration
ANSIBLE_CONFIG := ansible/ansible.cfg

# -------------------------
# Ansible
# -------------------------

ansible-bootstrap:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/lxc-bootstrap.yml

ansible-docker:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/docker.yml

ansible-dockge:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/dockge.yml

# Deploy stack
# Usage: make stack GROUP=ai
stack:
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

terraform-plan:
	cd terraform && terraform plan \
		-var-file=environments/homelab.tfvars

terraform-apply:
	cd terraform && terraform apply \
		-var-file=environments/homelab.tfvars

terraform-output:
	cd terraform && terraform output

terraform-destroy:
	cd terraform && terraform destroy \
		-var-file=environments/homelab.tfvars

.PHONY: deploy

deploy:
	@echo "=== Terraform init ==="
	cd terraform && terraform init

	@echo "=== Terraform format ==="
	cd terraform && terraform fmt -recursive

	@echo "=== Terraform validate ==="
	cd terraform && terraform validate

	@echo "=== Terraform plan ==="
	cd terraform && terraform plan \
		-var-file=environments/homelab.tfvars

	@echo "=== Terraform apply ==="
	cd terraform && terraform apply \
		-var-file=environments/homelab.tfvars

	@echo "=== Ansible bootstrap ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml

	@echo "=== Ansible Docker ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml

	@echo "=== Ansible Dockge ==="
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml

	@if [ -n "$(GROUP)" ]; then \
		echo "=== Deploying stack: $(GROUP) ==="; \
		ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
		ansible-playbook ansible/playbooks/stack.yml \
		-e stack_group=$(GROUP); \
	else \
		echo "No stack group specified. Skipping stack deployment."; \
	fi