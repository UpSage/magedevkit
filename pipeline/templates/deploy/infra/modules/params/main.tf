# @@HEADER@@
# The environment's secrets, as SSM SecureStrings under /<app>/<env>/. The
# box reads them at deploy time (deploy/scripts/render-env); CodeBuild reads
# composer_auth. Passwords are generated here (they also sit in the
# encrypted Terraform state). composer_auth — and crypt_key when the
# database comes from another environment — are set by the provision wizard
# and never overwritten by Terraform.

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "db_root" {
  length  = 32
  special = false
}

resource "random_password" "rabbitmq" {
  length  = 32
  special = false
}

resource "random_id" "crypt_key" {
  byte_length = 16
}

locals {
  prefix = "/${var.app}/${var.env}"
}

resource "aws_ssm_parameter" "db_password" {
  name  = "${local.prefix}/db_password"
  type  = "SecureString"
  value = random_password.db.result
}

resource "aws_ssm_parameter" "db_root_password" {
  name  = "${local.prefix}/db_root_password"
  type  = "SecureString"
  value = random_password.db_root.result
}

resource "aws_ssm_parameter" "rabbitmq_password" {
  name  = "${local.prefix}/rabbitmq_password"
  type  = "SecureString"
  value = random_password.rabbitmq.result
}

resource "aws_ssm_parameter" "crypt_key" {
  name  = "${local.prefix}/crypt_key"
  type  = "SecureString"
  value = random_id.crypt_key.hex
  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "composer_auth" {
  name  = "${local.prefix}/composer_auth"
  type  = "SecureString"
  value = "{}"
  lifecycle {
    ignore_changes = [value]
  }
}
