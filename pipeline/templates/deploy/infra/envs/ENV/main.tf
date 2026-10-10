# @@HEADER@@
# Environment "@@ENV_NAME@@" of @@APP@@. Applied by the provision wizard
# (mdk provision-wizard), never by the pipeline. Settings: terraform.tfvars.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      App         = var.app
      Environment = var.env
      ManagedBy   = "terraform (deploy/infra)"
    }
  }
}

module "network" {
  source = "../../modules/network"
  name   = "${var.app}-${var.env}"
}

module "ecr" {
  source = "../../modules/ecr"
  app    = var.app
}

module "params" {
  source = "../../modules/params"
  app    = var.app
  env    = var.env
}

module "pipeline" {
  source                = "../../modules/pipeline"
  app                   = var.app
  env                   = var.env
  github_repo           = var.github_repo
  github_connection_arn = var.github_connection_arn
  branch                = var.branch
  deploy_tag            = "${var.app}-${var.env}"
  ecr_arns              = module.ecr.arns
  composer_auth_param   = module.params.composer_auth_name
  static_locales        = var.static_locales
}

module "compute" {
  source              = "../../modules/compute"
  app                 = var.app
  env                 = var.env
  instance_type       = var.instance_type
  volume_gb           = var.volume_gb
  subnet_id           = module.network.subnet_id
  security_group_id   = module.network.security_group_id
  params_prefix       = module.params.prefix
  artifact_bucket_arn = module.pipeline.artifact_bucket_arn
  media_bucket        = var.media_bucket
}

module "dns" {
  source = "../../modules/dns"
  zone   = var.hosted_zone
  domain = var.domain
  ip     = module.compute.public_ip
}

module "schedule" {
  source       = "../../modules/schedule"
  count        = var.off_hours ? 1 : 0
  app          = var.app
  env          = var.env
  instance_id  = module.compute.instance_id
  instance_arn = module.compute.instance_arn
  timezone     = var.timezone
}
