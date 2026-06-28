variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Fully-qualified domain name for the cluster endpoint and TLS cert (e.g. api.example.com)"
  type        = string
}

variable "zone_id" {
  description = "Route 53 hosted zone ID that owns domain_name"
  type        = string
}

variable "key_name" {
  description = "EC2 key pair name used for SSH access to retrieve kubeconfig"
  type        = string
}

variable "spot_price" {
  description = "Maximum Spot price in USD/hr. Leave empty to use on-demand price cap."
  type        = string
  default     = "0.05"
}

variable "instance_count" {
  description = "Set to 0 to pause (destroy instance, keep EIP + DNS), 1 to run"
  type        = number
  default     = 1
}
