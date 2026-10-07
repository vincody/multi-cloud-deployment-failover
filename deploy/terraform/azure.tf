resource "azurerm_resource_group" "standby" {
  name     = "dcs29-rg"
  location = var.azure_region
  tags     = local.azure_tags
}

resource "azurerm_virtual_network" "standby" {
  name                = "dcs29-azure-vnet"
  address_space       = ["10.30.0.0/16"]
  location            = var.azure_region
  resource_group_name = azurerm_resource_group.standby.name
  tags                = local.azure_tags
}

resource "azurerm_subnet" "standby" {
  name                 = "dcs29-azure-public-subnet"
  resource_group_name  = azurerm_resource_group.standby.name
  virtual_network_name = azurerm_virtual_network.standby.name
  address_prefixes     = ["10.30.1.0/24"]
}

resource "azurerm_network_security_group" "standby" {
  name                = "dcs29-azure-vm-nsg"
  location            = var.azure_region
  resource_group_name = azurerm_resource_group.standby.name
  tags                = local.azure_tags
}

resource "azurerm_network_security_rule" "ssh" {
  name                        = "ssh-from-operator"
  priority                    = 300
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = var.ssh_cidr
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.standby.name
  network_security_group_name = azurerm_network_security_group.standby.name
}

resource "azurerm_network_security_rule" "http" {
  name                        = "http-origin"
  priority                    = 310
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.standby.name
  network_security_group_name = azurerm_network_security_group.standby.name
}

resource "azurerm_network_security_rule" "https" {
  name                        = "https-origin"
  priority                    = 320
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.standby.name
  network_security_group_name = azurerm_network_security_group.standby.name
}

resource "azurerm_public_ip" "standby" {
  name                = "dcs29-azure-vm-pip"
  location            = var.azure_region
  resource_group_name = azurerm_resource_group.standby.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.azure_tags
}

resource "azurerm_network_interface" "standby" {
  name                = "dcs29-azure-vm-nic"
  location            = var.azure_region
  resource_group_name = azurerm_resource_group.standby.name
  tags                = local.azure_tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.standby.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.standby.id
  }
}

resource "azurerm_network_interface_security_group_association" "standby" {
  network_interface_id      = azurerm_network_interface.standby.id
  network_security_group_id = azurerm_network_security_group.standby.id
}

resource "azurerm_linux_virtual_machine" "standby" {
  name                            = "dcs29-azure-vm"
  resource_group_name             = azurerm_resource_group.standby.name
  location                        = var.azure_region
  size                            = var.azure_vm_size
  admin_username                  = var.azure_admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.standby.id]
  custom_data = base64encode(templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    image_ref   = local.image_ref
    app_env     = "azure"
    k3s_version = var.k3s_version
    app_region  = var.azure_region
  }))
  tags = local.azure_tags

  admin_ssh_key {
    username   = var.azure_admin_username
    public_key = trimspace(var.ssh_public_key)
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.azure_os_disk_size
    # Increase this value if the selected image requires more than 30 GiB.
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  lifecycle {
    # Azure replaces the VM when custom_data changes; keep HTTPS and data on later applies.
    ignore_changes = [custom_data]
  }

  depends_on = [azurerm_network_interface_security_group_association.standby]
}
