variable "enable_auto_failover" {
  description = "Arm one-way automatic AWS->Azure failover only after a successful manual drill. No automatic failback."
  type        = bool
  default     = false
}

provider "archive" {}
data "aws_caller_identity" "current" {}
locals {
  dr_name           = "dcs29-auto-failover"
  dr_node_parameter = "/dcs29/dr/azure-node"
}

resource "aws_iam_role" "hybrid" {
  count              = var.enable_auto_failover ? 1 : 0
  name               = "dcs29-azure-ssm"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ssm.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "hybrid" {
  count      = var.enable_auto_failover ? 1 : 0
  role       = aws_iam_role.hybrid[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "terraform_data" "azure_ssm" {
  count = var.enable_auto_failover ? 1 : 0
  triggers_replace = {
    azure = azurerm_linux_virtual_machine.standby.id
    role  = aws_iam_role.hybrid[0].id
    ssh   = var.ssh_private_key_path
    hash  = sha256("${filesha256("${path.module}/failover/bootstrap.py")}${filesha256("${path.module}/failover/bootstrap_remote.py")}")
  }
  lifecycle {
    precondition {
      condition     = var.enable_workload_automation && var.enable_https && var.enable_health_checks && var.enable_failover
      error_message = "Auto failover requires verified workload/HTTPS automation, health checks and failover DNS. Complete a manual fencing/promote/rejoin drill before arming."
    }
  }
  provisioner "local-exec" {
    command     = "\"${var.automation_python}\" \"${abspath(path.module)}/failover/bootstrap.py\""
    environment = { DCS29_FAILOVER_CONFIG = jsonencode(merge(local.automation_config, { hybrid_role = aws_iam_role.hybrid[0].name, node_parameter = local.dr_node_parameter, account_id = data.aws_caller_identity.current.account_id })) }
  }
  depends_on = [terraform_data.https, aws_iam_role_policy_attachment.hybrid]
}

resource "aws_dynamodb_table" "dr_latch" {
  provider     = aws.recovery
  count        = var.enable_auto_failover ? 1 : 0
  name         = "dcs29-dr-latch"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
}
resource "aws_cloudwatch_log_group" "dr" {
  provider          = aws.recovery
  count             = var.enable_auto_failover ? 1 : 0
  name              = "/aws/lambda/${local.dr_name}"
  retention_in_days = 7
}
data "archive_file" "dr" {
  count       = var.enable_auto_failover ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/failover/lambda"
  output_path = "${path.module}/.terraform/dr-lambda.zip"
  excludes    = ["__pycache__", "__pycache__/*"]
}
resource "aws_iam_role" "dr_lambda" {
  count              = var.enable_auto_failover ? 1 : 0
  name               = "dcs29-dr-lambda"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "dr_lambda" {
  count = var.enable_auto_failover ? 1 : 0
  role  = aws_iam_role.dr_lambda[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.dr[0].arn}:*" },
    { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.dr_latch[0].arn },
    { Effect = "Allow", Action = "ec2:DescribeInstances", Resource = "*" },
    { Effect = "Allow", Action = "ec2:StopInstances", Resource = aws_instance.primary.arn },
    { Effect = "Allow", Action = "cloudwatch:DescribeAlarms", Resource = "arn:aws:cloudwatch:us-east-1:${data.aws_caller_identity.current.account_id}:alarm:${local.dr_name}" },
    { Effect = "Allow", Action = "ssm:GetParameter", Resource = "arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:parameter${local.dr_node_parameter}" },
    { Effect = "Allow", Action = ["ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"], Resource = "*" },
    { Effect = "Allow", Action = "ssm:SendCommand", Resource = "arn:aws:ssm:us-east-1::document/AWS-RunShellScript" },
    { Effect = "Allow", Action = "ssm:SendCommand", Resource = "arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:managed-instance/*", Condition = { StringEquals = { "ssm:resourceTag/Purpose" = "azure-dr", "ssm:resourceTag/Project" = "dcs29" } } }
  ] })
}
resource "aws_lambda_function" "dr" {
  provider         = aws.recovery
  count            = var.enable_auto_failover ? 1 : 0
  function_name    = local.dr_name
  role             = aws_iam_role.dr_lambda[0].arn
  filename         = data.archive_file.dr[0].output_path
  source_code_hash = data.archive_file.dr[0].output_base64sha256
  runtime          = "python3.12"
  handler          = "handler.handler"
  timeout          = 60
  memory_size      = 128
  environment {
    variables = {
      PRIMARY_REGION   = var.aws_region
      PRIMARY_INSTANCE = aws_instance.primary.id
      AWS_ORIGIN       = "https://${local.aws_origin_host}"
      AZURE_ORIGIN     = "https://${local.azure_origin_host}"
      ALARM_NAME       = local.dr_name
      LOCK_TABLE       = aws_dynamodb_table.dr_latch[0].name
      NODE_PARAMETER   = local.dr_node_parameter
      EXECUTION_PREFIX = "arn:aws:states:us-east-1:${data.aws_caller_identity.current.account_id}:execution:${local.dr_name}:"
    }
  }
  depends_on = [aws_iam_role_policy.dr_lambda, terraform_data.azure_ssm]
}
resource "aws_iam_role" "dr_states" {
  count              = var.enable_auto_failover ? 1 : 0
  name               = "dcs29-dr-states"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "dr_states" {
  count  = var.enable_auto_failover ? 1 : 0
  role   = aws_iam_role.dr_states[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.dr[0].arn }] })
}
resource "aws_sfn_state_machine" "dr" {
  provider   = aws.recovery
  count      = var.enable_auto_failover ? 1 : 0
  name       = local.dr_name
  role_arn   = aws_iam_role.dr_states[0].arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/failover/workflow.json.tmpl", { lambda_arn = aws_lambda_function.dr[0].arn })
  depends_on = [aws_iam_role_policy.dr_states]
}
resource "aws_cloudwatch_metric_alarm" "dr" {
  provider            = aws.recovery
  count               = var.enable_auto_failover ? 1 : 0
  alarm_name          = local.dr_name
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = try(aws_route53_health_check.aws_origin[0].id, "") }
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  statistic           = "Minimum"
  treat_missing_data  = "notBreaching"
  depends_on          = [aws_sfn_state_machine.dr]
}
resource "aws_iam_role" "dr_events" {
  count              = var.enable_auto_failover ? 1 : 0
  name               = "dcs29-dr-events"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "dr_events" {
  count  = var.enable_auto_failover ? 1 : 0
  role   = aws_iam_role.dr_events[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.dr[0].arn }] })
}
resource "aws_cloudwatch_event_rule" "dr" {
  provider      = aws.recovery
  count         = var.enable_auto_failover ? 1 : 0
  name          = local.dr_name
  event_pattern = jsonencode({ source = ["aws.cloudwatch"], "detail-type" = ["CloudWatch Alarm State Change"], resources = [aws_cloudwatch_metric_alarm.dr[0].arn], detail = { alarmName = [local.dr_name], state = { value = ["ALARM"] } } })
}
resource "aws_cloudwatch_event_target" "dr" {
  provider   = aws.recovery
  count      = var.enable_auto_failover ? 1 : 0
  rule       = aws_cloudwatch_event_rule.dr[0].name
  arn        = aws_sfn_state_machine.dr[0].arn
  role_arn   = aws_iam_role.dr_events[0].arn
  depends_on = [aws_iam_role_policy.dr_events]
}
output "auto_failover" {
  value = {
    enabled        = var.enable_auto_failover
    control_region = "us-east-1"
    workflow       = try(aws_sfn_state_machine.dr[0].arn, null)
    node_parameter = local.dr_node_parameter
    latch_table    = try(aws_dynamodb_table.dr_latch[0].name, null)
  }
}
