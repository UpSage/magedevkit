variable "app" {
  type = string
}

variable "images" {
  type    = list(string)
  default = ["php", "nginx"]
}
