# @@HEADER@@
# Stops the box in the evening and starts it in the morning on weekdays
# (EventBridge Scheduler), to save cost on a test environment. The Elastic IP
# stays attached, so the address doesn't change.

resource "aws_iam_role" "scheduler" {
  name = "${var.app}-${var.env}-scheduler"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.app}-${var.env}-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["ec2:StartInstances", "ec2:StopInstances"], Resource = var.instance_arn }]
  })
}

resource "aws_scheduler_schedule" "stop" {
  name                         = "${var.app}-${var.env}-stop"
  schedule_expression          = var.stop_cron
  schedule_expression_timezone = var.timezone
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ InstanceIds = [var.instance_id] })
  }
}

resource "aws_scheduler_schedule" "start" {
  name                         = "${var.app}-${var.env}-start"
  schedule_expression          = var.start_cron
  schedule_expression_timezone = var.timezone
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ InstanceIds = [var.instance_id] })
  }
}
