# ---- Core inputs ----

variable "aws_region" {
  description = "AWS region for the primary EC2 instance."
  type        = string
}

variable "azure_region" {
  description = "Azure region for the standby VM and its network."
  type        = string
}

variable "azure_subscription_id" {
  description = "Subscription selected with az account set; authentication comes from Azure CLI."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.azure_subscription_id))
    error_message = "azure_subscription_id must be a subscription UUID."
  }
}

variable "domain_name" {
  description = "Domain you own and can delegate to the new Route 53 hosted zone, without a trailing dot."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]+(\\.[a-zA-Z0-9-]+)+$", var.domain_name)) && !can(regex("\\.(example|test|invalid)$", var.domain_name)) && var.domain_name != "example.com"
    error_message = "Use your real domain without https:// or a trailing dot; reserved example domains are placeholders."
  }
}

variable "ssh_public_key" {
  description = "Contents of an existing OpenSSH public key (.pub); never supply the private key."
  type        = string

  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) [A-Za-z0-9+/=]{40,}( .*)?$", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must contain a real ssh-ed25519 or ssh-rsa public key, not the example placeholder."
  }
}

variable "ssh_cidr" {
  description = "Your current public IPv4 address with /32, allowed to reach SSH on both VMs."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.ssh_cidr)) && can(regex("/32$", var.ssh_cidr)) && var.ssh_cidr != "203.0.113.10/32"
    error_message = "ssh_cidr must be a public IPv4 /32 such as 198.51.100.10/32."
  }
}

variable "ghcr_image" {
  description = "Public GHCR image path without a tag or digest."
  type        = string

  validation {
    condition     = can(regex("^ghcr\\.io/[a-z0-9._/-]+$", var.ghcr_image))
    error_message = "ghcr_image must look like ghcr.io/owner/package, with no tag or digest."
  }
}

variable "image_digest" {
  description = "Only the 64 hexadecimal characters after sha256: from the selected GHCR version."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.image_digest))
    error_message = "image_digest must be exactly 64 lowercase hex characters, without sha256:."
  }
}

variable "aws_instance_type" {
  description = "x86-64 EC2 instance type; the published application image is linux/amd64."
  type        = string
  default     = "t3.medium"
}

variable "azure_vm_size" {
  description = "Azure x86-64 VM size permitted by your subscription quota and policy."
  type        = string
  default     = "Standard_B2s"
}

variable "azure_admin_username" {
  description = "SSH username on the Azure VM."
  type        = string
  default     = "azureuser"
}

variable "k3s_version" {
  description = "Exact reviewed K3s release used on both independent clusters, e.g. v1.35.1+k3s1. No moving channel."
  type        = string
  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+\\+k3s[0-9]+$", var.k3s_version))
    error_message = "Set k3s_version to an exact published release, vX.Y.Z+k3sN."
  }
}

variable "aws_root_volume_size" {
  description = "Encrypted gp3 root disk in GiB; also holds K3s local-path data in this lab. Never shrink an existing disk."
  type        = number
  default     = 30
  validation {
    condition     = var.aws_root_volume_size >= 30 && floor(var.aws_root_volume_size) == var.aws_root_volume_size
    error_message = "AWS root disk must be an integer of at least 30 GiB."
  }
}

variable "aws_cpu_credits" {
  description = "T3 CPU credit policy. Standard avoids surplus credit charges but throttles when credits run out."
  type        = string
  default     = "standard"
  validation {
    condition     = contains(["standard", "unlimited"], var.aws_cpu_credits)
    error_message = "Choose standard or unlimited."
  }
}

variable "azure_os_disk_size" {
  description = "Azure OS disk size in GiB. Use at least 30 and the source image minimum; never shrink an existing disk."
  type        = number
  default     = 30
  validation {
    condition     = var.azure_os_disk_size >= 30 && floor(var.azure_os_disk_size) == var.azure_os_disk_size
    error_message = "Azure OS disk must be an integer of at least 30 GiB and no smaller than the source image."
  }
}

# ---- Workload automation (WireGuard, PostgreSQL, app over SSH) ----

variable "enable_workload_automation" {
  description = "Deploy WireGuard, PostgreSQL and Issues via operator SSH after VM bootstrap. Never performs failover."
  type        = bool
  default     = true
}

variable "ssh_private_key_path" {
  description = "Local path to the existing private key matching ssh_public_key. Only the path enters state, never key contents."
  type        = string
  default     = ""
}

variable "postgres_image" {
  description = "Pinned public postgres:17-bookworm image reference, postgres@sha256:DIGEST. Runtime verifies PostgreSQL 17/UID 999."
  type        = string
  default     = ""
  validation {
    condition     = var.postgres_image == "" || can(regex("^(docker.io/library/)?postgres@sha256:[0-9a-f]{64}$", var.postgres_image))
    error_message = "Use the digest of postgres:17-bookworm, not a moving tag."
  }
}

variable "automation_python" {
  description = "Local Python 3.10+ executable used by local-exec; use an absolute path if needed."
  type        = string
  default     = "python"
  validation {
    condition     = !can(regex("[\"\r\n]", var.automation_python))
    error_message = "Use an executable path without quotes or newlines."
  }
}

variable "standby_storage_generation" {
  description = "Azure PVC generation. Change only to retry a failed/incomplete initial clone on NEW storage; old PVCs are retained."
  type        = string
  default     = "initial"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,24}$", var.standby_storage_generation))
    error_message = "Use a short lowercase DNS label such as initial or retry1."
  }
}

# ---- HTTPS (opt-in, after DNS delegation) ----

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

# ---- DNS failover (opt-in, after TLS and replication are verified) ----

variable "enable_health_checks" {
  description = "Create two HTTPS origin checks only after DNS delegation and GĐ5 TLS verification."
  type        = bool
  default     = false
}

variable "enable_failover" {
  description = "Publish app CNAME failover after TLS, replication and fencing have been verified; only the active writer is healthy."
  type        = bool
  default     = false
}

variable "dns_ttl_seconds" {
  description = "TTL of the shared app CNAME records. Lower values shorten how long clients keep the old origin after a failover."
  type        = number
  default     = 30
  validation {
    condition     = var.dns_ttl_seconds >= 10 && var.dns_ttl_seconds <= 300
    error_message = "dns_ttl_seconds must be between 10 and 300."
  }
}

variable "health_check_interval_seconds" {
  description = "Route 53 health check interval. 10 is the fast interval and costs extra per check."
  type        = number
  default     = 30
  validation {
    condition     = contains([10, 30], var.health_check_interval_seconds)
    error_message = "Route 53 supports an interval of 10 or 30 seconds."
  }
}

variable "health_check_failure_threshold" {
  description = "Consecutive failed checks before Route 53 marks an origin unhealthy. Detection time is interval x threshold."
  type        = number
  default     = 3
  validation {
    condition     = var.health_check_failure_threshold >= 1 && var.health_check_failure_threshold <= 10 && floor(var.health_check_failure_threshold) == var.health_check_failure_threshold
    error_message = "health_check_failure_threshold must be an integer between 1 and 10."
  }
}

# ---- Automatic failover watcher (opt-in, after a manual drill) ----

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
