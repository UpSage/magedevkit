ARG PHP_VERSION=8.4
# Debian release of the base image (bookworm | trixie) and Composer version —
# set per Magento version by the m2/ catalog via env/.env.local.
ARG DEBIAN_RELEASE=bookworm
ARG COMPOSER_VERSION=2

# COPY --from can't take a build arg directly, so Composer comes in through
# a named stage.
FROM public.ecr.aws/docker/library/composer:${COMPOSER_VERSION} AS composer

# public.ecr.aws mirrors Docker Hub's official images 1:1 — pulling from
# there instead avoids Docker Hub's anonymous rate limit (429 Too Many
# Requests), which has already failed CodeBuild runs outright since this
# pipeline authenticates to neither registry and has no build cache.
FROM public.ecr.aws/docker/library/php:${PHP_VERSION}-fpm-${DEBIAN_RELEASE}

ENV DEBIAN_FRONTEND=noninteractive

# The base image's apt sources point at plain http://deb.debian.org, whose
# Fastly-fronted mirror intermittently 403s specific packages over HTTP while
# serving the same files fine over HTTPS. apt >=1.5 (this image has 2.6)
# speaks HTTPS natively, no apt-transport-https package needed.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/*.sources

# rsync and patch: used by a pipeline kit's deploy/scripts/build-release.sh,
# which builds releases inside this same image.
RUN apt-get update && apt-get install -y \
    git \
    rsync \
    patch \
    unzip \
    zip \
    curl \
    ca-certificates \
    libicu-dev \
    libfreetype6-dev \
    libjpeg62-turbo-dev \
    libpng-dev \
    libwebp-dev \
    libzip-dev \
    libxslt1-dev \
    libonig-dev \
    libxml2-dev \
    libsodium-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp \
    # PHP 8.5+ compiles OPcache in (it's no longer a separate extension), so
    # it's only installed on older versions.
    && EXTRA_EXTS="$(php -r 'echo PHP_VERSION_ID < 80500 ? "opcache" : "";')" \
    && docker-php-ext-install -j"$(nproc)" \
        bcmath \
        ftp \
        gd \
        intl \
        mbstring \
        pdo_mysql \
        soap \
        sockets \
        sodium \
        xsl \
        zip \
        $EXTRA_EXTS \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer /usr/bin/composer /usr/bin/composer

# Baseline ownership before compose's `user:` override takes effect.
# docker-compose.local.yml runs php-fpm as the host user instead (single
# owner — see platform/README.md's file-permissions section).
RUN mkdir -p /var/www/html \
    && chown -R www-data:www-data /var/www/html

# CLI commands (composer, bin/magento) exec in as a different uid that has no
# /etc/passwd entry inside this image, so $HOME wouldn't otherwise resolve.
ENV HOME=/tmp
ENV COMPOSER_HOME=/tmp/composer

WORKDIR /var/www/html

EXPOSE 9000

CMD ["php-fpm"]
