output "instance_id" {
  value = aws_instance.box.id
}

output "public_ip" {
  value = aws_eip.box.public_ip
}

output "deploy_tag" {
  value = "${var.app}-${var.env}"
}

output "instance_arn" {
  value = aws_instance.box.arn
}
