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