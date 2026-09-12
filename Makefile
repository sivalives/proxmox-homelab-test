#Sets the ansible.cfg, without this, the ansible-playbook command will not work as expected. Or move the ansible.cfg to the home directory of the user running the playbooks.
ANSIBLE_CONFIG := ansible/ansible.cfg

ansible-bootstrap:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/bootstrap.yml

ansible-docker:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/docker.yml

ansible-dockge:
	ANSIBLE_CONFIG=$(ANSIBLE_CONFIG) ansible-playbook ansible/playbooks/dockge.yml