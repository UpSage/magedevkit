output "arns" {
  value = [for r in aws_ecr_repository.this : r.arn]
}
