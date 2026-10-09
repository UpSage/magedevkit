# Magento version catalog

Used by `bin/local/setup` → "2) Download a new, empty Magento store". The version and
security-patch menus are built from these folders, so adding a folder adds a menu entry —
no script changes needed.

```
m2/
  2.4.8/version.env            base release (menu: "1) 2.4.8 — original release, no patches")
  2.4.8/p1/version.env         security patch, loaded on top of ../version.env
  ...
  2.4.9/version.env
```

## version.env

Plain `KEY=value` lines:

| Key | Required | Meaning |
|---|---|---|
| `MAGENTO_VERSION` | yes | Composer version constraint, e.g. `2.4.8-p3` |
| `MAGENTO_PACKAGE` | no | Composer project, default `magento/project-community-edition` (`magento/project-enterprise-edition` for Adobe Commerce — needs Commerce keys) |
| `PHP_VERSION`, `MARIADB_VERSION`, `OPENSEARCH_VERSION`, `VALKEY_VERSION`, `RABBITMQ_VERSION`, `VARNISH_VERSION`, `NGINX_VERSION`, `COMPOSER_VERSION` | no | Docker image versions for that release, written into `env/.env.local` before the images are built |
| `DEBIAN_RELEASE` | no | `bookworm` or `trixie` for the custom php/nginx images — must have a tag for both (e.g. nginx 1.26 is bookworm-only, 1.30 trixie-only) |

A patch folder's `version.env` only needs what differs from its base folder (usually just
`MAGENTO_VERSION`).

## Where the stack versions come from

Adobe's own data file, which the
[system requirements page](https://experienceleague.adobe.com/en/docs/commerce-operations/installation-guide/system-requirements)
is generated from — it has one entry per patch (2.4.8, 2.4.8-p1 … p5, 2.4.9), unlike the rendered
page, which only shows the latest releases in full:
<https://github.com/AdobeDocs/commerce-operations.en/blob/main/_jekyll/_data/system-requirements.yml>

Where Adobe lists several versions (e.g. PHP `8.4, 8.3`), the highest supported is pinned. For an
existing project, `config.platform.php` in its `composer.json` overrides the catalog's `PHP_VERSION`
(its `composer.lock` was resolved for that PHP). Image tags
must exist on Docker Hub — e.g. OpenSearch has no bare `2.19` tag, so `2.19.6` is pinned.

Compare the catalog with Adobe's current data any time (read-only):

```bash
bin/m2/check-requirements          # all 2.4.8+ releases; also lists Adobe releases with no folder yet
bin/m2/check-requirements 2.4.9    # one version line
```

`bin/local/setup` applies these pins on a fresh install, and offers them (`[Y/n]`) when it detects an
existing checkout's version from `magento/composer.lock`. It never downgrades an existing MariaDB
data volume, and offers to drop the OpenSearch volume on a major change (search is rebuilt by
reindexing).

## post-install hooks (optional)

An executable `post-install` file in a version folder runs right after `setup:install` (and sample
data), with `PLATFORM_DIR` and `MAGENTO_VERSION` in its environment — for version-specific
commands such as applying a patch or toggling a module. The base folder's hook runs first, then
the patch folder's. Example:

```bash
#!/bin/bash
set -e
"$PLATFORM_DIR/bin/magento" module:disable Magento_TwoFactorAuth
```

## post-upgrade hooks (optional)

Same idea for `bin/version` upgrades: an executable `post-upgrade` file runs after the upgrade's
`setup:upgrade`/compile/deploy, with `PLATFORM_DIR`, `MAGENTO_VERSION` (the new version) and
`PREVIOUS_VERSION` in its environment. Base folder first, then the patch folder. Not run on a
rollback (a snapshot restores a state that already had them).

## Adding a version

```bash
mkdir -p m2/2.4.8/p6
printf 'MAGENTO_VERSION=2.4.8-p6\n' > m2/2.4.8/p6/version.env

mkdir -p m2/2.4.10
printf 'MAGENTO_VERSION=2.4.10\nMAGENTO_PACKAGE=magento/project-community-edition\n' > m2/2.4.10/version.env
```

Composer fails clearly at install time if a version doesn't exist on repo.magento.com (or your
keys can't access it).
