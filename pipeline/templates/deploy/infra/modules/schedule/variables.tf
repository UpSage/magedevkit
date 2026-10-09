variable "app" {
  type = string
}

variable "env" {
  type = string
}

variable "instance_id" {
  type = string
}

variable "instance_arn" {
  type = string
}

variable "timezone" {
  type = string
}

variable "stop_cron" {
  type    = string
  default = "cron(0 20 ? * MON-FRI *)"
}

variable "start_cron" {
  type    = string
  default = "cron(0 7 ? * MON-FRI *)"
}
