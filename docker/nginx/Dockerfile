# ============================================================
# MageRunner - Nginx
# ============================================================

ARG NGINX_VERSION=1.29
# bookworm | trixie — set per Magento version by the m2/ catalog (e.g. nginx
# 1.30 is only published for trixie, 1.26 only for bookworm).
ARG DEBIAN_RELEASE=bookworm

# public.ecr.aws mirrors Docker Hub's official images 1:1 — pulling from
# there instead avoids Docker Hub's anonymous rate limit (429 Too Many
# Requests), which has already failed CodeBuild runs outright since this
# pipeline authenticates to neither registry and has no build cache.
FROM public.ecr.aws/docker/library/nginx:${NGINX_VERSION}-${DEBIAN_RELEASE}

# ------------------------------------------------------------
# Environment
# ------------------------------------------------------------

ENV TZ=America/New_York

# ------------------------------------------------------------
# Create Magento document root
# ------------------------------------------------------------

RUN mkdir -p /var/www/html/pub

WORKDIR /var/www/html

# ------------------------------------------------------------
# Nginx
# ------------------------------------------------------------

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
