output "bootstrap_image_ref" {
  description = "Pinned GHCR reference; workload automation deploys changes without recreating the VM."
  value       = local.image_ref
}

output "workload_automation" {
  description = "Initial automation configuration; validate VM result files and APIs for current runtime state."
  value = {
    enabled     = var.enable_workload_automation
    aws_http    = "http://${aws_eip.primary.public_ip}"
    azure_http  = "http://${azurerm_public_ip.standby.ip_address}"
    azure_pvc   = "postgres-data-standby-${var.standby_storage_generation}"
    result_file = "/opt/dcs29/automation-result.json"
  }
}

output "aws_public_ip" {
  description = "Elastic IP of the AWS origin."
  value       = aws_eip.primary.public_ip
}

output "azure_public_ip" {
  description = "Static Public IP of the Azure origin."
  value       = azurerm_public_ip.standby.ip_address
}

output "hosted_zone_id" {
  description = "Route 53 public hosted zone ID for Certbot and DNS checks."
  value       = aws_route53_zone.main.zone_id
}

output "route53_nameservers" {
  description = "Replace nameservers at your domain registrar with this exact set."
  value       = aws_route53_zone.main.name_servers
}

output "origin_hosts" {
  value = {
    aws    = local.aws_origin_host
    azure  = local.azure_origin_host
    shared = local.shared_host
  }
}

output "health_check_ids" {
  description = "Populated after enable_health_checks is true."
  value = {
    aws   = try(aws_route53_health_check.aws_origin[0].id, null)
    azure = try(aws_route53_health_check.azure_origin[0].id, null)
  }
}

output "azure_certbot_iam_user" {
  description = "Create this user's access key manually for the Azure host; no secret is stored in Terraform state."
  value       = aws_iam_user.azure_certbot.name
}
