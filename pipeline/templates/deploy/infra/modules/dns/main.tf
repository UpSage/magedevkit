# @@HEADER@@
# The store's hostname → the box's Elastic IP, in an existing hosted zone.

data "aws_route53_zone" "this" {
  name         = var.zone
  private_zone = false
}

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [var.ip]
}
