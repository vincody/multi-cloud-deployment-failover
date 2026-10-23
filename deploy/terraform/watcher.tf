variable "enable_auto_failover" {
  description = "Install the one-way AWS->Azure failover watcher on the Azure VM after a successful manual drill. No automatic failback."
  type        = bool
  default     = false
}

variable "azure_watcher_credentials_file" {
  description = "Local INI [default] file with the access key of the watcher IAM user (see output azure_watcher_iam_user). Only the path enters state. Empty reuses the file already on the VM."
  type        = string
  default     = ""
}

variable "watcher_threshold" {
  description = "Consecutive checks on which both views must agree that AWS is down before failing over."
  type        = number
  default     = 3
}

variable "watcher_interval_seconds" {
  description = "Seconds between watcher checks."
  type        = number
  default     = 5
}

# The access key is created outside Terraform so the secret never enters state.
resource "aws_iam_user" "azure_watcher" {
  count         = var.enable_auto_failover ? 1 : 0
  name          = "dcs29-azure-watcher"
  force_destroy = true # deletes the externally created access key on destroy
}

locals {
  # Everything the watcher may do in AWS: read instance and health state, and stop the old primary.
  watcher_statements = [
    { Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*" },
    { Effect = "Allow", Action = ["ec2:StopInstances"], Resource = aws_instance.primary.arn },
    { Effect = "Allow", Action = ["route53:GetHealthCheckStatus"], Resource = "arn:aws:route53:::healthcheck/${try(aws_route53_health_check.aws_origin[0].id, "unset")}" }
  ]
}

resource "aws_iam_user_policy" "azure_watcher" {
  count  = var.enable_auto_failover ? 1 : 0
  name   = "dcs29-azure-watcher"
  user   = aws_iam_user.azure_watcher[0].name
  policy = jsonencode({ Version = "2012-10-17", Statement = local.watcher_statements })
}

locals {
  watcher_config = merge(local.automation_config, {
    aws_instance_id   = aws_instance.primary.id
    health_check_id   = try(aws_route53_health_check.aws_origin[0].id, "")
    aws_origin_host   = local.aws_origin_host
    azure_origin_host = local.azure_origin_host
    threshold         = var.watcher_threshold
    interval          = var.watcher_interval_seconds
    credentials_file  = var.azure_watcher_credentials_file
  })
}

resource "terraform_data" "watcher" {
  count = var.enable_auto_failover ? 1 : 0
  triggers_replace = {
    azure  = azurerm_linux_virtual_machine.standby.id
    config = local.watcher_config
    hash   = sha256(join("", [for name in sort(tolist(fileset("${path.module}/watcher", "*.py"))) : filesha256("${path.module}/watcher/${name}")], [filesha256("${path.module}/../../scripts/watcher_core.py")]))
  }
  lifecycle {
    precondition {
      condition     = var.enable_workload_automation && var.enable_https && var.enable_health_checks && var.enable_failover
      error_message = "The watcher requires verified workload/HTTPS automation, health checks and failover DNS. Complete a manual fencing/promote/rejoin drill before enabling it."
    }
    precondition {
      condition     = var.watcher_threshold >= 2 && var.watcher_threshold <= 20 && var.watcher_interval_seconds >= 2 && var.watcher_interval_seconds <= 60
      error_message = "watcher_threshold must be 2-20 and watcher_interval_seconds 2-60."
    }
  }
  provisioner "local-exec" {
    command     = "\"${var.automation_python}\" \"${abspath(path.module)}/watcher/deploy.py\""
    environment = { DCS29_WATCHER_CONFIG = jsonencode(local.watcher_config) }
  }
  depends_on = [terraform_data.https, aws_iam_user_policy.azure_watcher]
}

output "auto_failover" {
  description = "Watcher runs on the Azure VM; check it with: systemctl status dcs29-watcher"
  value = {
    enabled   = var.enable_auto_failover
    host      = "azure"
    threshold = var.watcher_threshold
    interval  = var.watcher_interval_seconds
  }
}

output "azure_watcher_iam_user" {
  description = "Create an access key for this user outside Terraform and put it in azure_watcher_credentials_file."
  value       = try(aws_iam_user.azure_watcher[0].name, null)
}

output "azure_watcher_permissions" {
  description = "AWS actions granted to the watcher IAM user."
  value       = flatten([for statement in local.watcher_statements : statement.Action])
}
