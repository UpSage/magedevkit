# @@HEADER@@
terraform {
  backend "s3" {
    bucket       = "@@TF_STATE_BUCKET@@"
    key          = "@@APP@@/@@ENV_NAME@@/terraform.tfstate"
    region       = "@@AWS_REGION@@"
    encrypt      = true
    use_lockfile = true
  }
}
