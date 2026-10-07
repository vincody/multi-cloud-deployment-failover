mock_provider "aws" {}
mock_provider "azurerm" {}
mock_provider "archive" {}

# Route53 metrics and the controller live in us-east-1.
mock_provider "aws" {
  alias = "recovery"
}

variables {
  ssh_private_key_path  = "fixtures/operator-key"
  postgres_image        = "postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  k3s_version           = "v1.35.1+k3s1" # Fixture, not a production release recommendation.
  aws_region            = "ap-southeast-1"
  azure_region          = "indiasouthcentral"
  azure_subscription_id = "00000000-0000-0000-0000-000000000000"
  domain_name           = "lab.terraform.local"
  ssh_public_key        = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINgrR4Kat7vlx5GTg7Wy1fgZB7ZiGuPBSfXx54z6CpAr"
  ssh_cidr              = "198.51.100.10/32"
  ghcr_image            = "ghcr.io/example/app"
  image_digest          = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "http_origins_first" {
  command = plan

  assert {
    condition     = length(terraform_data.workloads) == 1 && aws_vpc_security_group_ingress_rule.wireguard[0].ip_protocol == "udp" && aws_vpc_security_group_ingress_rule.wireguard[0].from_port == 51820 && azurerm_network_security_rule.wireguard[0].protocol == "Udp" && azurerm_network_security_rule.wireguard[0].destination_port_range == "51820"
    error_message = "Initial automation must deploy workloads with peer-only WireGuard UDP rules."
  }

  assert {
    condition     = aws_instance.primary.instance_type == "t3.medium" && azurerm_linux_virtual_machine.standby.size == "Standard_B2s" && one(aws_instance.primary.root_block_device).volume_size == 30 && one(aws_instance.primary.credit_specification).cpu_credits == "standard" && one(azurerm_linux_virtual_machine.standby.os_disk).disk_size_gb == 30
    error_message = "Default VM sizes, root disk and CPU credit policy must match the current lab."
  }

  assert {
    condition     = strcontains(aws_instance.primary.user_data, "INSTALL_K3S_VERSION='v1.35.1+k3s1'") && strcontains(base64decode(azurerm_linux_virtual_machine.standby.custom_data), "INSTALL_K3S_VERSION='v1.35.1+k3s1'") && !strcontains(aws_instance.primary.user_data, "docker compose") && strcontains(aws_instance.primary.user_data, "secretKeyRef: {name: issues-db, key: DATABASE_URL}")
    error_message = "Both independent clusters must pin K3s and require a database Secret instead of Compose."
  }

  assert {
    condition     = length(aws_route53_health_check.aws_origin) == 0 && length(aws_route53_health_check.azure_origin) == 0
    error_message = "The initial deployment must not create HTTPS health checks before certificates exist."
  }

  assert {
    condition     = length(aws_route53_record.app_primary) == 0 && length(aws_route53_record.app_secondary) == 0
    error_message = "The initial deployment must not publish app failover records."
  }

  assert {
    condition     = strcontains(aws_instance.primary.user_data, "image: ghcr.io/example/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") && strcontains(base64decode(azurerm_linux_virtual_machine.standby.custom_data), "image: ghcr.io/example/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    error_message = "Both VM bootstrap scripts must use the same pinned GHCR image."
  }
}

run "infrastructure_only" {
  command = plan
  variables {
    enable_workload_automation = false
    ssh_private_key_path      = ""
    postgres_image            = ""
  }
  assert {
    condition     = length(terraform_data.workloads) == 0 && length(aws_vpc_security_group_ingress_rule.wireguard) == 0 && length(azurerm_network_security_rule.wireguard) == 0
    error_message = "Infrastructure-only mode must not provision workloads or WireGuard ingress."
  }
}

run "https_after_delegation" {
  command = plan
  variables {
    enable_https      = true
    certificate_email = "operator@example.com"
  }
  assert {
    condition     = length(terraform_data.https) == 1 && local.https_config.email == "operator@example.com" && !contains(keys(local.https_config), "aws_secret_access_key")
    error_message = "HTTPS must be opt-in and must not put DNS credentials into its Terraform configuration."
  }
}

run "reject_https_without_email" {
  command = plan
  variables {
    enable_https = true
  }
  expect_failures = [terraform_data.https]
}

run "reject_https_without_workloads" {
  command = plan
  variables {
    enable_https               = true
    certificate_email          = "operator@example.com"
    enable_workload_automation = false
  }
  expect_failures = [terraform_data.https]
}

run "reject_incomplete_automation_inputs" {
  command = plan
  variables {
    ssh_private_key_path = ""
    postgres_image       = ""
  }
  expect_failures = [terraform_data.workloads]
}

run "automatic_failover_opt_in" {
  command = plan
  variables {
    enable_auto_failover = true
    enable_https        = true
    certificate_email   = "operator@example.com"
    enable_health_checks = true
    enable_failover     = true
  }
  assert {
    condition     = aws_sfn_state_machine.dr[0].type == "STANDARD" && aws_cloudwatch_metric_alarm.dr[0].period == 60 && aws_cloudwatch_metric_alarm.dr[0].treat_missing_data == "notBreaching"
    error_message = "The controller must use a Standard workflow and not promote on missing metrics."
  }
  assert {
    condition     = jsondecode(templatefile("${path.module}/failover/workflow.json.tmpl", { lambda_arn = "arn:aws:lambda:us-east-1:000000000000:function:fixture" })).States.PromoteAzure.Parameters.Payload.action == "promote" && jsondecode(templatefile("${path.module}/failover/workflow.json.tmpl", { lambda_arn = "fixture" })).States.Fenced.Default == "FenceDelay"
    error_message = "Promotion must follow confirmed fencing with bounded status polling."
  }
  assert {
    condition     = aws_dynamodb_table.dr_latch[0].billing_mode == "PAY_PER_REQUEST" && !can(aws_dynamodb_table.dr_latch[0].ttl[0])
    error_message = "The incident latch must not be automatically removed by Lambda."
  }
  assert {
    condition     = azurerm_network_security_rule.wireguard[0].priority != azurerm_network_security_rule.https.priority
    error_message = "WireGuard and HTTPS NSG priorities must not conflict."
  }
}

run "reject_auto_failover_before_baseline" {
  command = plan
  variables {
    enable_auto_failover = true
  }
  expect_failures = [terraform_data.azure_ssm]
}

run "failover_after_https" {
  command = plan

  variables {
    enable_health_checks = true
  }

  assert {
    condition     = aws_route53_health_check.aws_origin[0].resource_path == "/health/database" && aws_route53_health_check.azure_origin[0].resource_path == "/health/database"
    error_message = "DNS must check writer readiness, not static dashboard data."
  }

  assert {
    condition     = length(aws_route53_health_check.aws_origin) == 1 && length(aws_route53_health_check.azure_origin) == 1
    error_message = "The health-check phase must create both origin checks."
  }

  assert {
    condition     = length(aws_route53_record.app_primary) == 0 && length(aws_route53_record.app_secondary) == 0
    error_message = "The health-check phase must not publish app CNAMEs yet."
  }
}

run "reject_dns_without_health_checks" {
  command = plan
  variables {
    enable_failover = true
  }
  expect_failures = [aws_route53_record.app_primary, aws_route53_record.app_secondary]
}

run "dns_after_healthy_checks" {
  command = plan

  variables {
    enable_health_checks = true
    enable_failover      = true
  }

  assert {
    condition     = length(aws_route53_health_check.aws_origin) == 1 && length(aws_route53_health_check.azure_origin) == 1
    error_message = "Enabling failover must create both origin health checks."
  }

  assert {
    condition     = length(aws_route53_record.app_primary) == 1 && length(aws_route53_record.app_secondary) == 1
    error_message = "Enabling failover must create both app CNAME records."
  }
}
