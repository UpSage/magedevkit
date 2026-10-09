variable "app" {
  type = string
}

variable "env" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "github_connection_arn" {
  type = string
}

variable "branch" {
  type = string
}

variable "deploy_tag" {
  type = string
}

variable "ecr_arns" {
  type = list(string)
}

variable "composer_auth_param" {
  type = string
}

variable "static_locales" {
  type = string
}

variable "build_compute_type" {
  type    = string
  default = "BUILD_GENERAL1_MEDIUM"
}
