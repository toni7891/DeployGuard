output "cluster_endpoint" {
  description = "k3s API server endpoint"
  value       = "https://${aws_eip.k3s.public_ip}:6443"
}

output "eip_address" {
  description = "Elastic IP address of the k3s node"
  value       = aws_eip.k3s.public_ip
}
