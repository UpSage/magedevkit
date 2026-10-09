# @@HEADER@@
# The EC2 box: Ubuntu 24.04 with Docker, the CodeDeploy agent and the AWS
# CLI (installed by user-data.sh.tftpl), an Elastic IP, and an instance role
# that may pull from ECR, read this environment's SSM secrets, fetch
# CodeDeploy revisions and use the media bucket. No SSH key: shell access is
# through SSM Session Manager.

data "aws_ami" "ubuntu" {
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
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_iam_role" "box" {
  name = "${var.app}-${var.env}-box"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr" {
  role       = aws_iam_role.box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "box" {
  name = "${var.app}-${var.env}-box"
  role = aws_iam_role.box.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "Secrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"]
        Resource = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${var.params_prefix}*"
      },
      {
        Sid       = "DecryptSecrets"
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${data.aws_region.current.region}.amazonaws.com" } }
      },
      {
        Sid      = "Revisions"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"]
        Resource = [var.artifact_bucket_arn, "${var.artifact_bucket_arn}/*"]
      },
      ], var.media_bucket == "" ? [] : [
      {
        Sid      = "Media"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.media_bucket}", "arn:aws:s3:::${var.media_bucket}/*"]
      },
    ])
  })
}

resource "aws_iam_instance_profile" "box" {
  name = "${var.app}-${var.env}-box"
  role = aws_iam_role.box.name
}

resource "aws_instance" "box" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.box.name

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    app    = var.app
    env    = var.env
    region = data.aws_region.current.region
  })
  user_data_replace_on_change = false

  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name   = "${var.app}-${var.env}"
    Deploy = "${var.app}-${var.env}"
  }

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "box" {
  instance = aws_instance.box.id
  domain   = "vpc"
  tags     = { Name = "${var.app}-${var.env}" }
}
