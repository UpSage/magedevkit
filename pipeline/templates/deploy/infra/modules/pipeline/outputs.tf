output "pipeline_name" {
  value = aws_codepipeline.this.name
}

output "artifact_bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "codedeploy_app" {
  value = aws_codedeploy_app.this.name
}
