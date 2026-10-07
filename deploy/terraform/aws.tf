resource "aws_vpc" "primary" {
  cidr_block           = "10.29.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "dcs29-aws-vpc" }
}

resource "aws_internet_gateway" "primary" {
  vpc_id = aws_vpc.primary.id

  tags = { Name = "dcs29-aws-igw" }
}

resource "aws_subnet" "primary" {
  vpc_id                  = aws_vpc.primary.id
  cidr_block              = "10.29.1.0/24"
  map_public_ip_on_launch = false

  tags = { Name = "dcs29-aws-public-subnet" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.primary.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.primary.id
  }

  tags = { Name = "dcs29-aws-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.primary.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "primary" {
  name        = "dcs29-aws-primary-sg"
  description = "SSH from operator; public HTTP and HTTPS to Traefik"
  vpc_id      = aws_vpc.primary.id

  tags = { Name = "dcs29-aws-primary-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.primary.id
  description       = "SSH from operator IP"
  cidr_ipv4         = var.ssh_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.primary.id
  description       = "Public HTTP origin"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.primary.id
  description       = "Public HTTPS origin and Route 53 health checks"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "all_ipv4" {
  security_group_id = aws_security_group.primary.id
  description       = "Package installation, GHCR and DNS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_key_pair" "operator" {
  key_name   = "dcs29-aws-primary-key"
  public_key = trimspace(var.ssh_public_key)
}

data "aws_ami" "ubuntu_2404" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_iam_policy" "certbot_dns" {
  name        = "dcs29-certbot-dns"
  description = "ACME DNS-01 TXT updates only in the DCS29 hosted zone"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "route53:ListHostedZones"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "route53:GetChange"
        Resource = "arn:aws:route53:::change/*"
      },
      {
        Effect   = "Allow"
        Action   = "route53:ChangeResourceRecordSets"
        Resource = aws_route53_zone.main.arn
        Condition = {
          "ForAllValues:StringEquals" = {
            "route53:ChangeResourceRecordSetsRecordTypes" = ["TXT"]
            "route53:ChangeResourceRecordSetsNormalizedRecordNames" = [
              "_acme-challenge.${local.shared_host}",
              "_acme-challenge.${local.aws_origin_host}",
              "_acme-challenge.${local.azure_origin_host}"
            ]
          }
        }
      }
    ]
  })
}

resource "aws_iam_role" "ec2_certbot" {
  name = "dcs29-ec2-certbot"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_certbot" {
  role       = aws_iam_role.ec2_certbot.name
  policy_arn = aws_iam_policy.certbot_dns.arn
}

resource "aws_iam_instance_profile" "ec2_certbot" {
  name = "dcs29-ec2-certbot"
  role = aws_iam_role.ec2_certbot.name
}

resource "aws_iam_user" "azure_certbot" {
  name = "dcs29-azure-certbot"
}

resource "aws_iam_user_policy_attachment" "azure_certbot" {
  user       = aws_iam_user.azure_certbot.name
  policy_arn = aws_iam_policy.certbot_dns.arn
}

resource "aws_instance" "primary" {
  ami                         = data.aws_ami.ubuntu_2404.id
  instance_type               = var.aws_instance_type
  subnet_id                   = aws_subnet.primary.id
  vpc_security_group_ids      = [aws_security_group.primary.id]
  key_name                    = aws_key_pair.operator.key_name
  associate_public_ip_address = false
  iam_instance_profile        = aws_iam_instance_profile.ec2_certbot.name
  user_data = templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    image_ref   = local.image_ref
    app_env     = "aws"
    k3s_version = var.k3s_version
    app_region  = var.aws_region
  })

  credit_specification {
    cpu_credits = var.aws_cpu_credits
  }

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.aws_root_volume_size
    encrypted   = true
  }

  tags = { Name = "dcs29-aws-primary" }

  lifecycle {
    # User data runs only at first boot. Existing VMs require an explicit K3s migration.
    ignore_changes = [ami, user_data]
  }

  depends_on = [aws_route_table_association.public]
}

resource "aws_eip" "primary" {
  domain   = "vpc"
  instance = aws_instance.primary.id

  tags = { Name = "dcs29-aws-primary-eip" }

  depends_on = [aws_internet_gateway.primary]
}
