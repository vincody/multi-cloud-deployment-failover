resource "aws_route53_zone" "main" {
  name = var.domain_name

  tags = { Name = "dcs29-public-zone" }
}

resource "aws_route53_record" "aws_origin" {
  zone_id = aws_route53_zone.main.zone_id
  name    = local.aws_origin_host
  type    = "A"
  ttl     = 60
  records = [aws_eip.primary.public_ip]
}

resource "aws_route53_record" "azure_origin" {
  zone_id = aws_route53_zone.main.zone_id
  name    = local.azure_origin_host
  type    = "A"
  ttl     = 60
  records = [azurerm_public_ip.standby.ip_address]
}

# Enable after DNS/TLS and database setup. Standby returns 503 until promoted.
# DNS never fences or promotes PostgreSQL; follow GĐ6 before switching writers.
resource "aws_route53_health_check" "aws_origin" {
  depends_on = [terraform_data.https]
  count      = var.enable_health_checks ? 1 : 0

  fqdn              = local.aws_origin_host
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health/database"
  enable_sni        = true
  request_interval  = var.health_check_interval_seconds
  failure_threshold = var.health_check_failure_threshold

  tags = { Name = "dcs29-aws-https-ready" }
}

resource "aws_route53_health_check" "azure_origin" {
  depends_on = [terraform_data.https]
  count      = var.enable_health_checks ? 1 : 0

  fqdn              = local.azure_origin_host
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health/database"
  enable_sni        = true
  request_interval  = var.health_check_interval_seconds
  failure_threshold = var.health_check_failure_threshold

  tags = { Name = "dcs29-azure-https-ready" }
}

resource "aws_route53_record" "app_primary" {
  count = var.enable_failover ? 1 : 0

  zone_id         = aws_route53_zone.main.zone_id
  name            = local.shared_host
  type            = "CNAME"
  ttl             = var.dns_ttl_seconds
  records         = [local.aws_origin_host]
  set_identifier  = "aws-primary"
  health_check_id = try(aws_route53_health_check.aws_origin[0].id, null)

  failover_routing_policy {
    type = "PRIMARY"
  }

  lifecycle {
    precondition {
      condition     = var.enable_health_checks
      error_message = "Enable writer health checks and verify replication/fencing before publishing failover records. Only the current primary should be healthy."
    }
  }
}

resource "aws_route53_record" "app_secondary" {
  count = var.enable_failover ? 1 : 0

  zone_id         = aws_route53_zone.main.zone_id
  name            = local.shared_host
  type            = "CNAME"
  ttl             = var.dns_ttl_seconds
  records         = [local.azure_origin_host]
  set_identifier  = "azure-secondary"
  health_check_id = try(aws_route53_health_check.azure_origin[0].id, null)

  failover_routing_policy {
    type = "SECONDARY"
  }

  lifecycle {
    precondition {
      condition     = var.enable_health_checks
      error_message = "Enable writer health checks and verify replication/fencing before publishing failover records. Only the current primary should be healthy."
    }
  }
}

output "dns_failover_timing" {
  description = "Worst-case time for Route 53 to mark an origin unhealthy, and the TTL clients may still cache. Real failover time adds the database promotion."
  value = {
    health_check_detection_seconds = var.health_check_interval_seconds * var.health_check_failure_threshold
    app_cname_ttl_seconds          = var.dns_ttl_seconds
  }
}
