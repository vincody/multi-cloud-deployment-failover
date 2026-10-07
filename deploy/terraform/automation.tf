resource "aws_vpc_security_group_ingress_rule" "wireguard" {
  count             = var.enable_workload_automation ? 1 : 0
  security_group_id = aws_security_group.primary.id
  description       = "WireGuard only from Azure peer"
  cidr_ipv4         = "${azurerm_public_ip.standby.ip_address}/32"
  from_port         = 51820
  to_port           = 51820
  ip_protocol       = "udp"
}

resource "azurerm_network_security_rule" "wireguard" {
  count                       = var.enable_workload_automation ? 1 : 0
  name                        = "wireguard-from-aws"
  priority                    = 330
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Udp"
  source_port_range           = "*"
  destination_port_range      = "51820"
  source_address_prefix       = "${aws_eip.primary.public_ip}/32"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.standby.name
  network_security_group_name = azurerm_network_security_group.standby.name
}

locals {
  automation_config = {
    aws_host           = aws_eip.primary.public_ip
    azure_host         = azurerm_public_ip.standby.ip_address
    azure_user         = var.azure_admin_username
    private_key        = var.ssh_private_key_path
    image_ref          = local.image_ref
    postgres_ref       = var.postgres_image
    aws_region         = var.aws_region
    azure_region       = var.azure_region
    standby_generation = var.standby_storage_generation
  }
}

resource "terraform_data" "workloads" {
  count = var.enable_workload_automation ? 1 : 0
  triggers_replace = {
    aws_vm      = aws_instance.primary.id
    azure_vm    = azurerm_linux_virtual_machine.standby.id
    config      = local.automation_config
    bundle_hash = sha256(join("", [for name in sort(tolist(fileset("${path.module}/automation", "**"))) : filesha256("${path.module}/automation/${name}") if !strcontains(name, "__pycache__")]))
  }

  lifecycle {
    precondition {
      condition     = trimspace(var.ssh_private_key_path) != "" && var.postgres_image != ""
      error_message = "Automation requires ssh_private_key_path and a pinned postgres_image. Set enable_workload_automation=false for infrastructure only."
    }
  }

  provisioner "local-exec" {
    command = "\"${var.automation_python}\" \"${abspath(path.module)}/automation/deploy.py\""
    environment = {
      DCS29_AUTOMATION_CONFIG = jsonencode(local.automation_config)
    }
  }

  depends_on = [aws_vpc_security_group_ingress_rule.wireguard, azurerm_network_security_rule.wireguard]
}
