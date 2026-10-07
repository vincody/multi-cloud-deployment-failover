variable "enable_https" {
  description = "Provision HTTPS after public DNS delegation is ready; requires workload automation."
  type        = bool
  default     = false
}

variable "certificate_email" {
  description = "Contact email for Let's Encrypt registration."
  type        = string
  default     = ""
  validation {
    condition     = var.certificate_email == "" || can(regex("^[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.certificate_email))
    error_message = "Set a valid certificate contact email."
  }
}

variable "azure_dns_credentials_file" {
  description = "Local INI [default] credential file for the Azure DNS-only IAM user. Only the path enters state. Empty reuses an existing VM file."
  type        = string
  default     = ""
}

locals {
  https_config = merge(local.automation_config, {
    domain           = var.domain_name
    email            = var.certificate_email
    nameservers      = aws_route53_zone.main.name_servers
    credentials_file = var.azure_dns_credentials_file
  })
}

resource "terraform_data" "https" {
  count = var.enable_https ? 1 : 0
  triggers_replace = {
    workloads   = try(terraform_data.workloads[0].id, null)
    config      = local.https_config
    bundle_hash = sha256(join("", [for name in sort(tolist(fileset("${path.module}/https", "**"))) : filesha256("${path.module}/https/${name}") if !strcontains(name, "__pycache__")]))
  }
  lifecycle {
    precondition {
      condition     = var.enable_workload_automation && var.certificate_email != ""
      error_message = "HTTPS requires enable_workload_automation=true and certificate_email; delegate public DNS before enabling."
    }
  }
  provisioner "local-exec" {
    command     = "\"${var.automation_python}\" \"${abspath(path.module)}/https/deploy.py\""
    environment = { DCS29_HTTPS_CONFIG = jsonencode(local.https_config) }
  }
  depends_on = [terraform_data.workloads, aws_route53_record.aws_origin, aws_route53_record.azure_origin, aws_iam_role_policy_attachment.ec2_certbot, aws_iam_user_policy_attachment.azure_certbot]
}

output "https_automation" {
  description = "HTTPS configuration; current certificate and timer state must be checked on the VM."
  value = {
    enabled = var.enable_https
    aws     = "https://${local.aws_origin_host}"
    azure   = "https://${local.azure_origin_host}"
    shared  = "https://${local.shared_host}"
  }
}
