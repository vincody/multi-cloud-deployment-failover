# Names and references shared by several files. Resource-specific locals stay next to their resources.
locals {
  image_ref         = "${var.ghcr_image}@sha256:${var.image_digest}"
  aws_origin_host   = "aws-origin.${var.domain_name}"
  azure_origin_host = "azure-origin.${var.domain_name}"
  shared_host       = "app.${var.domain_name}"
  azure_tags = {
    Project   = "dcs29"
    ManagedBy = "terraform"
  }
}
