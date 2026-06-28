.PHONY: up pause resume destroy

INFRA_DIR := infra

## up — init + apply (bring cluster to running state)
up:
	cd $(INFRA_DIR) && terraform init && terraform apply -auto-approve

## pause — destroy the EC2 instance; keep EIP + DNS (saves ~$2.70/mo)
pause:
	cd $(INFRA_DIR) && terraform apply -auto-approve -var="instance_count=0"

## resume — recreate the EC2 instance and reattach the EIP
resume:
	cd $(INFRA_DIR) && terraform apply -auto-approve -var="instance_count=1"

## destroy — tear down all AWS resources
destroy:
	cd $(INFRA_DIR) && terraform destroy -auto-approve
