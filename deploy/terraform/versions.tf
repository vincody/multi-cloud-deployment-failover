terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  alias  = "recovery"
  region = "us-east-1"
  default_tags {
    tags = { Project = "dcs29", ManagedBy = "terraform" }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "dcs29"
      ManagedBy = "terraform"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = var.azure_subscription_id
}
