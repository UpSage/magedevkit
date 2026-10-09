variable "app" {
  type = string
}

variable "env" {
  type = string
}

variable "instance_type" {
  type = string
}

variable "volume_gb" {
  type = number
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "params_prefix" {
  type = string
}

variable "artifact_bucket_arn" {
  type = string
}

variable "media_bucket" {
  type    = string
  default = ""
}
