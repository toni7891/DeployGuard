terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.6"
}

provider "aws" {
  region = var.aws_region
}

# ── VPC ──────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true

  tags = { Name = "deployguard", deployguard = "true" }
}

# Internet Gateway — public subnet routing; intentionally no NAT Gateway.
# A NAT Gateway costs ~$32/mo in base fees plus data charges. The golden path
# uses a public subnet with an Internet Gateway for direct internet access.
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "deployguard-igw", deployguard = "true" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = { Name = "deployguard-public", deployguard = "true" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "deployguard-public-rt", deployguard = "true" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Security group ─────────────────────────────────────────────────────────────

resource "aws_security_group" "k3s" {
  name   = "deployguard-k3s"
  vpc_id = aws_vpc.main.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "k3s API server"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "deployguard-k3s-sg", deployguard = "true" }
}

# ── AMI ────────────────────────────────────────────────────────────────────────

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── EC2 Spot instance ─────────────────────────────────────────────────────────

resource "aws_instance" "k3s" {
  count = var.instance_count

  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.small"
  key_name               = var.key_name
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.k3s.id]

  instance_market_options {
    market_type = "spot"
    spot_options {
      max_price                      = var.spot_price
      instance_interruption_behavior = "terminate"
    }
  }

  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail

    # Install k3s — disable traefik (nginx-ingress is used instead)
    curl -sfL https://get.k3s.io | sh -s - --disable traefik

    # Wait until the node reports Ready before we proceed
    until /usr/local/bin/kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml \
        get nodes 2>/dev/null | grep -q " Ready"; do
      sleep 5
    done

    # Copy kubeconfig to ec2-user home for SSH retrieval
    install -o ec2-user -m 644 \
      /etc/rancher/k3s/k3s.yaml /home/ec2-user/k3s.yaml
  EOF

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
    tags                  = { Name = "deployguard-k3s-root", deployguard = "true" }
  }

  tags = { Name = "deployguard-k3s", deployguard = "true" }
}

# ── Elastic IP ─────────────────────────────────────────────────────────────────
# Allocated persistently so the IP survives pause cycles.
# Costs ~$3.65/mo when unattached (during pause) — this is by design.

resource "aws_eip" "k3s" {
  domain = "vpc"

  tags = { Name = "deployguard-k3s-eip", deployguard = "true" }
}

resource "aws_eip_association" "k3s" {
  count = var.instance_count

  instance_id   = aws_instance.k3s[0].id
  allocation_id = aws_eip.k3s.id
}

# ── TLS — ACM + Route 53 ──────────────────────────────────────────────────────

resource "aws_acm_certificate" "main" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle { create_before_destroy = true }

  tags = { deployguard = "true" }
}

resource "aws_route53_record" "acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.main.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = var.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for r in aws_route53_record.acm_validation : r.fqdn]
}

# A record pointing the domain to the Elastic IP
resource "aws_route53_record" "app" {
  zone_id = var.zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 60
  records = [aws_eip.k3s.public_ip]
}


