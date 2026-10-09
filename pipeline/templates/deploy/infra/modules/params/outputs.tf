output "prefix" {
  value = local.prefix
}

output "composer_auth_name" {
  value = aws_ssm_parameter.composer_auth.name
}
