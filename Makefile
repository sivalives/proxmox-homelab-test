.PHONY: \
	terraform-init \
	terraform-fmt \
	terraform-validate \
	terraform-plan \
	terraform-apply \
	terraform-output \
	terraform-destroy \
	ansible-bootstrap \
	ansible-docker \
	ansible-dockge \
	stack \
	deploy

ANSIBLE_CONFIG := ansible/ansible.cfg
TF_DIR := terraform

terraform-init:
	cd $(TF_DIR) && terraform init

terraform-fmt:
	cd $(TF_DIR) && terraform fmt -recursive

terraform-validate:
	cd $(TF_DIR) && terraform validate

terraform-plan:
	cd $(TF_DIR) && terraform plan \
		-var="stack=$(STACK)"

terraform-apply:
	cd $(TF_DIR) && terraform apply \
		-var="stack=$(STACK)"

terraform-output:
	cd $(TF_DIR) && terraform output

terraform-destroy:
	cd $(TF_DIR) && terraform destroy \
		-var="stack=$(STACK)"

ansible-bootstrap:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/lxc-bootstrap.yml \
		--limit $(STACK)

ansible-docker:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/docker.yml \
		--limit $(STACK)

ansible-dockge:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/dockge.yml \
		--limit $(STACK)

stack:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) \
	ansible-playbook ansible/playbooks/stack.yml \
		-e stack_group=$(STACK)

deploy:
	@echo "Deploying stack: $(STACK)"
	$(MAKE) terraform-apply STACK=$(STACK)
	$(MAKE) ansible-bootstrap STACK=$(STACK)
	$(MAKE) ansible-docker STACK=$(STACK)
	$(MAKE) ansible-dockge STACK=$(STACK)
	$(MAKE) stack STACK=$(STACK)