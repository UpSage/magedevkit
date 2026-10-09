variable "app" {
  type = string
}

variable "env" {
  type = string
}

variable "region" {
  type = string
}

variable "domain" {
  type = string
}

variable "hosted_zone" {
  type = string
}

variable "instance_type" {
  type = string
}

variable "volume_gb" {
  type = number
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

variable "static_locales" {
  type = string
}

variable "media_bucket" {
  type    = string
  default = ""
}

variable "off_hours" {
  type = bool
}

variable "timezone" {
  type = string
}
