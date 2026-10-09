output "instance_id" {
  value = module.compute.instance_id
}

output "public_ip" {
  value = module.compute.public_ip
}

output "url" {
  value = "https://${var.domain}/"
}

output "pipeline_name" {
  value = module.pipeline.pipeline_name
}

output "codedeploy_app" {
  value = module.pipeline.codedeploy_app
}

output "secrets_prefix" {
  value = module.params.prefix
}
