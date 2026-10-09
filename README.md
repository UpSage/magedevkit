# MageRunner

A Magento 2 workbench: Docker stacks for every local store, browser wizards (setup, refresh,
version, build, pipeline), a dashboard with environments, builds and Git, and the EC2 deploy kit.
Formerly *magento2-docker-wizard*. The command is `mrn`.

Docker Compose configuration for a Magento 2 runtime, for **local development**. This
repo controls the **infrastructure** the application runs on (Nginx, Varnish, PHP-FPM, MariaDB,
Valkey, RabbitMQ, OpenSearch, Mailpit) — it does not contain the Magento application itself.

**One repo, many stores.** This repo is the shared platform code. Each Magento store you run is
an *instance*: its own folder next to the repo, created with `mrn new NAME` (or
**New instance** on the dashboard). The Magento code is either downloaded fresh or cloned from your
project's repository. Editing a script here changes every instance at once. See "Instances" below.

> **CI/CD / dev / staging:** removed for now while the CodePipeline setup is rebuilt. The previous
> dev/staging scripts, compose overlay, nginx vhosts, and CodeDeploy hooks are in git history
> (branch `fixed/dev-local-infra`).

**New to this project?** Start with [`LOCAL_SETUP.md`](LOCAL_SETUP.md) — a complete, from-scratch
walkthrough (WSL2/Docker install through a working local site). This file covers the permission
model and the full command reference.

## Instances

```
~/Sites/
├── magerunner/                  ← this repo (MageRunner): the wizard + all platform code (bin/, compose/, docker/, m2/, …)
├── m248p5/                      ← an instance (folder name = its APP_NAME) — local data only, no scripts
│   ├── .platform-instance       marker: which platform repo runs it
│   ├── .env                     its settings: APP_NAME, domain, ports, versions, mode, …
│   ├── magento/                 the store's code — its own git repo, the only thing ever pushed
│   ├── nginx/  certs/           generated vhost + mkcert certificate
│   └── logs/   db/              run logs, operation records, backups, version snapshots
└── m249/                        ← another instance, same shape
```

**One command for every instance: `mrn`.** Install it once (a symlink in `~/.local/bin`):
```bash
bin/local/install-mrn
```
Then:
```bash
mrn new m249                      # create ~/Sites/m249 and open its setup wizard (--terminal, --no-setup)
cd ~/Sites/m249
mrn magento cache:flush           # bin/magento for this instance
mrn composer install              # bin/composer
mrn platform up -d                # docker compose for this stack (ps, logs -f php, stop, …)
mrn refresh-wizard                # or setup-wizard / version-wizard; terminal: setup / refresh / version
mrn mode production               # also: ownership single|two-owner, permissions apply|lock|unlock
mrn pipeline-wizard               # make magento/ pipeline-ready for an EC2 environment (see "Deploying to EC2")
mrn build                         # Magento-only build: setup:upgrade, di:compile, static -f, cache:flush (maintenance on; containers untouched)
mrn build --composer update vendor/pkg   # composer first, then the same build
mrn rebuild-wizard                # full rebuild: also resets compiled code, restarts php/nginx/varnish, config, reindex
mrn --version                     # the platform version (VERSION file, shown in every page's footer)
mrn rebuild --composer update vendor/pkg   # composer first, then the full rebuild (terminal)
mrn destroy --yes                 # delete the instance: containers, data volumes and its folder (no undo)
mrn -i m249 magento indexer:status   # any command, from any folder, by instance name
mrn list                          # instances
mrn dashboard                     # all instances in the browser
```
`mrn` picks the instance from `-i NAME`, otherwise from the folder you're in. It sets
`PLATFORM_INSTANCE_DIR` and runs this repo's script, and `bin/lib/paths.sh` turns that into every
path the scripts use.

- **The name** becomes the folder and `APP_NAME`: the Docker project `<name>-platform`, volumes
  `<name>-*`. Use lowercase letters, digits, `_` and `-`.
- **Setup** asks for everything else: domain (default `<name>.test`), Magento source (fresh download
  from the `m2/` catalog, or clone from GitHub), `auth.json` and database dump (upload them on the
  page, or give a path or URL), database login, mode, and so on.
- **Edit once.** Instance folders hold no scripts, so updating this repo (`git pull`) updates every
  instance. Only `magento/` is a git repository; nothing else in an instance folder is pushed or
  deployed.
- **Instances live next to the repo** by default; set `PLATFORM_INSTANCES_ROOT` to use another
  folder. Running a command in the repo itself, with no instance chosen, is refused with a pointer
  to `mrn new`.
- **Removing an instance:** `mrn -i <name> platform down -v` (containers and data volumes), then
  delete the folder.
- **Older instances** had a `bin/` of launcher scripts; `mrn new <name>` on such an instance removes
  them (only the generated ones) and leaves `.env` and data alone.
- **The older layout still works:** a checkout called `platform/` next to `magento/`, with its own
  `env/.env.local`; there you keep using its `bin/` directly.

## Layout

The repo itself:

```
<repo root>/
├── magento/     ← the Magento application (separate repo)
└── platform/    ← this repo
    ├── bin/            Wrapper scripts — always use these (or mrn), not raw `docker compose`
    │   ├── mrn             The one command for every instance (install: bin/local/install-mrn)
    │   ├── local/          Local entry points (new-instance, setup, refresh, sync-db, version,
    │   │                   the browser versions setup-wizard / refresh-wizard / version-wizard,
    │   │                   and dashboard)
    │   ├── mode/           default | developer | production | show
    │   ├── ownership/      single | two-owner
    │   ├── permissions/    apply | lock | unlock
    │   └── lib/            Engines sourced by the entry points; paths.sh = where an instance's
    │                       files live; ui.sh = every prompt/progress
    │                       helper (terminal + web); web-wizard/ = the browser wizard and
    │                       the dashboard (bin/local/dashboard)
    ├── m2/             Magento version catalog for fresh installs and upgrades (see m2/README.md)
    ├── compose/        docker-compose.yml (base) + docker-compose.local.yml (local override)
    ├── config/         Per-service runtime config
    │   ├── nginx-local/    Local vhost (HTTPS via mkcert, local.magento.test) + certs/
    │   ├── php/
    │   └── varnish/
    ├── docker/         Dockerfiles for the custom-built nginx and php images
    └── env/            Environment files — see "Environment files" below
```

## Day-to-day commands

Use the wrappers in `bin/` (inside an instance: its `bin/`) — they always use the env file plus both compose files, and refuse to
run (pointing you at `bin/local/setup`) if `env/.env.local` doesn't exist yet:

```bash
bin/platform ps                    # container status
bin/platform up -d                 # start the stack
bin/platform down                  # stop the stack
bin/platform logs -f php           # tail logs for one service
bin/platform exec php bash         # shell into a container

bin/magento cache:flush            # run any bin/magento command against the running php container
bin/magento setup:upgrade
bin/magento --version

bin/composer install               # run any composer command in the php container, in magento/
bin/composer require vendor/package

bin/local/setup-wizard             # first-time setup, as a step-by-step page in your browser
bin/local/refresh-wizard           # upload/replace the database, as a page in your browser
bin/local/version-wizard           # upgrade Magento / roll back to a snapshot, in your browser
bin/version                        # the same, in the terminal
bin/local/pipeline-wizard          # write the pipeline kit into magento/deploy/ (see "Deploying to EC2")

bin/local/dashboard                # every checkout on this machine: status, actions, wizards (see "Dashboard")
```

With `FILE_OWNERSHIP=single`, a bare `bin/platform exec php ...` (no `--user`) already runs as you —
the same identity `bin/magento`/`bin/composer`/`bin/refresh` use. With `two-owner` it runs as
php-fpm's `www-data`; use `bin/magento`/`bin/composer` (or `--user "$(id -u):33"`) to act as yourself.
See "Magento mode & file permissions" below.

## Local development setup

**Automated**: once prerequisites (below) are installed and both repos are cloned as siblings, run
```bash
bin/local/setup            # in the terminal
bin/local/setup-wizard     # or: the same setup as a step-by-step page in your browser
```
It walks through steps 2–14 below itself — composer/AWS/S3 credentials prompted for inline wherever
a real value is needed, everything else (env file, TLS cert, Docker build/up, Magento install,
data refresh) fully automated — and prints the hosts-file line (step 8) as the very last thing, since
that's the last manual action standing between you and a working browser. Safe to re-run any time;
every step skips itself if already done. The manual steps below are what it's doing under the hood,
useful for understanding or debugging any one step on its own.

**1. Prerequisites**
- Docker Desktop with WSL2 integration enabled (if on Windows)
- This repo and the `magento` repo checked out as sibling directories
- [`mkcert`](https://github.com/FiloSottile/mkcert), AWS CLI v2, and [`direnv`](https://direnv.net/)
  — `bin/local/setup` installs all three automatically if missing

**2. Composer credentials**
```bash
cd ../magento
cp auth.json.sample auth.json
# Edit auth.json: fill in real repo.magento.com credentials, plus any private repos
# (Amasty, Meetanshi, bird.eu, etc.) declared in composer.json
```

**3. Local environment file**
```bash
cp env/.env.local.example env/.env.local
# Leave REMOTE_STORAGE_*/AWS profile at CHANGE_ME for now — filled in step 5
```
`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_ROOT_PASSWORD` initialize MariaDB on its very first start
(defaults: `magento`). `bin/local/setup` asks for them once, only while the
`${APP_NAME}-db-data` volume doesn't exist yet; `bin/sync-db` and `setup:install` read the same values.

**4. Local domain & Magento URLs** — by default the site runs at `local.magento.test`, but the
domain (and a few related URLs) can be customized per machine. Edit `env/.env.local`:
```
APP_DOMAIN=local.magento.test   # or your own choice, e.g. dev.local.test
```
`APP_DOMAIN` drives `base_url`, the generated TLS cert (step 6), and the generated nginx config
(`config/nginx-local/default.conf`, rendered from `default.conf.template` — never edit the
generated file directly, it's gitignored and gets overwritten):
```bash
sed "s/local\.magento\.test/$APP_DOMAIN/g" \
    config/nginx-local/default.conf.template > config/nginx-local/default.conf
```
Three more fields are optional overrides — leave blank to keep Magento's own sane defaults
(media/static URLs derived from `base_url`; cookie domain left to the current host):
```
BASE_MEDIA_URL=
BASE_STATIC_URL=
COOKIE_DOMAIN=
```
`bin/refresh` reapplies whichever of these three you set on every run — `bin/sync-db`'s DB reimport
would otherwise reset them. (`bin/local/setup` automates this whole step interactively — this
manual version is only for hand-following.)

Also required — the real production site's own facts, so `bin/sync-db` knows what to rewrite an
imported production dump's URLs/cookie domain *from*:
```
PROD_BASE_URL=<production's base URL, e.g. https://example.com/>
PROD_MEDIA_URL=<production's media/CDN URL>
PROD_COOKIE_DOMAIN=<production's cookie domain>
```

**5. AWS CLI profile & Magento remote storage** (profile needed for `bin/sync-db`,
part of step 12's refresh; remote storage controls where Magento itself serves `pub/media` from —
the same IAM keys are entered once and reused for both)
```bash
cat > .envrc <<'EOF'
export AWS_PROFILE=<pick-a-profile-name>       # e.g. MagentoS3Dev
export AWS_S3_PROFILE=<pick-a-profile-name>
EOF
direnv allow .
aws configure --profile <pick-a-profile-name>   # paste in keys from a teammate — scoped for S3 db/media read access
```
`.envrc` is gitignored and holds only the profile *name*, never real keys — those live in the
standard `~/.aws/credentials` file, entirely outside this repo, written by `aws configure` above.

Then choose how Magento serves `pub/media` — edit `env/.env.local`:
```
REMOTE_STORAGE_DRIVER=aws-s3      # or "file" to serve pub/media from local disk instead
REMOTE_STORAGE_BUCKET=<your-bucket>
REMOTE_STORAGE_REGION=us-east-2
REMOTE_STORAGE_KEY=<same access key as above>
REMOTE_STORAGE_SECRET=<same secret as above>
```
If `REMOTE_STORAGE_DRIVER=file`, leave `BUCKET`/`REGION`/`KEY`/`SECRET` blank — `bin/refresh` skips
S3 configuration entirely. (`bin/local/setup` automates this whole step interactively — this manual
version is only for hand-following.)

**6. Generate an HTTPS certificate** (one-time, via [mkcert](https://github.com/FiloSottile/mkcert))
```bash
cd config/nginx-local/certs
mkcert -install          # creates a local CA (may warn about system trust store without sudo — fine)
mkcert "$APP_DOMAIN"     # local.magento.test unless you changed it in step 4
```
The generated `.pem`/`-key.pem` are gitignored; each developer generates their own.

**7. Trust the CA in your browser**: if your browser runs outside this environment (e.g. Windows
host with Docker in WSL2), open `\\wsl$\<distro>\home\<user>\.local\share\mkcert\rootCA.pem` in
Windows Explorer, double-click → **Install Certificate** → **Local Machine** → **Trusted Root
Certification Authorities**, then restart your browser. Otherwise `mkcert -install` handles it
directly.

**8. Hosts file** (Windows, not WSL2 — the browser resolves DNS there): edit
`C:\Windows\System32\drivers\etc\hosts` as Administrator (one line per stack — they all point at
the shared edge proxy on 127.0.0.1, see "Running several stacks at once"):
```
127.0.0.1 local.magento.test
```

**9. Build and start the stack**
```bash
bin/platform build
bin/platform up -d
bin/platform ps   # wait until every service shows healthy
```

**10. Install Composer dependencies**
```bash
bin/composer install
```

**11. First-time Magento install** — bootstraps `app/etc/env.php`; real data comes in step 12
```bash
bin/magento setup:install \
  --base-url=https://local.magento.test/ \
  --db-host=mariadb --db-name=magento --db-user=magento --db-password=magento \
  --backend-frontname=admin \
  --admin-firstname=Local --admin-lastname=Admin --admin-email=you@example.com \
  --admin-user=admin --admin-password='Admin123!' \
  --language=en_US --currency=USD --timezone=America/New_York \
  --use-rewrites=1 --use-secure=0 --use-secure-admin=0 \
  --search-engine=opensearch --opensearch-host=opensearch --opensearch-port=9200 \
  --opensearch-index-prefix=magento2 --opensearch-enable-auth=0 \
  --amqp-host=rabbitmq --amqp-port=5672 --amqp-user=magento --amqp-password=magento --amqp-virtualhost=/ \
  --session-save=redis --session-save-redis-host=valkey --session-save-redis-port=6379 \
  --cache-backend=redis --cache-backend-redis-server=valkey --cache-backend-redis-port=6379 --cache-backend-redis-db=0 \
  --page-cache=redis --page-cache-redis-server=valkey --page-cache-redis-port=6379 --page-cache-redis-db=1
```
> If this fails on a `pub/media` permissions check, run
> `bin/platform exec --user "$(id -u):$(id -g)" php mkdir -p /var/www/html/pub/media` first and
> retry — the installer checks for the directory but won't create it itself.

**12. Run the full refresh** — pulls real DB/media data and finishes configuration
```bash
bin/refresh
```
See "Refreshing local data" below for exactly what this does. Takes 20–40 minutes the first time.

**13. Customize the admin URI** (optional)
```bash
bin/magento setup:config:set --backend-frontname=admin -n
bin/magento cache:flush
```

**14. Verify**
- `https://local.magento.test/` — storefront
- `https://local.magento.test/admin/` (or whatever frontname you chose) — admin;
  credentials come from the imported database, not the ones set in step 11
- `http://localhost:8025/` — Mailpit

### Fresh Magento install (vanilla, no data import)

`bin/local/setup` → **"2) Download a new, empty Magento store"** downloads a brand-new Magento
with `composer create-project` instead of using an existing checkout or importing a database:

1. Pick the **version** and **security patch** — menus are built from the folders in
   [`m2/`](m2/README.md) (`m2/2.4.8/`, `m2/2.4.8/p1` … `p5`, `m2/2.4.9/`, …). Adding a folder adds a
   menu entry; each holds a `version.env` (`MAGENTO_VERSION`, optional `MAGENTO_PACKAGE` and stack
   pins) and optionally an executable `post-install` hook for version-specific commands.
2. Give your **repo.magento.com credentials** — an existing `auth.json`, or the public/private keys.
   They're kept outside `magento/` until the download finishes (create-project needs an empty
   directory), then saved to `magento/auth.json`.
3. The rest of the wizard runs as usual (project config, DB credentials, mode/ownership, TLS,
   Docker). Skipped for a fresh install: the database-import and production-facts prompts, and the
   S3 data sync — `bin/refresh` isn't run.
4. After the stack is up: `create-project` → `composer install` → **admin account prompts**
   (name, email, username, password — 7+ chars with letters and digits; the password is never
   saved) and a **Luma sample data** y/N question → `setup:install` → sample data (if chosen) →
   `m2/<version>/post-install` hooks →
   the same Magento configuration `bin/refresh` applies (OpenSearch, Varnish FPC, storage, URL
   overrides, admin path; shared via `bin/lib/magento-config-engine.sh`) → `bin/mode/<mode>`.

`magento/` must be empty (setup offers to clear it). The chosen package/version are recorded in
`env/.env.local` (`MAGENTO_PACKAGE`, `MAGENTO_VERSION`).

### Refreshing local data

**In the browser:** `bin/local/refresh-wizard` runs exactly what `bin/refresh` runs, as a
step-by-step page: start the stack if it's stopped (the usual state after a reboot) → choose the dump
(**upload** it, pick a file on this computer — Windows paths like `C:\Users\…\dump.sql.gz` work — or
paste a URL) → the same questions as below → **type the project name** to confirm → a live checklist
of the import and rebuild → links to the site. See "Web wizards" below.

**Backups:** before `DROP DATABASE`, the current database is saved to
`db/backups/<APP_NAME>-<timestamp>.sql.gz` (newest 3 kept; asked as "Back up the current database
before replacing it?", default yes — terminal and browser). To restore one, refresh from that file
and answer **No** to "still contains production configuration".

`bin/sync-db` and `bin/refresh` are thin dispatchers that hand off (via `exec`) to
`bin/local/sync-db` and `bin/local/refresh`. The mechanical work —
sanitize/strip/import/verify and the full Magento config/compile/deploy pipeline —
lives under `bin/lib/` (`sync-db-config-engine.sh`, `db-refresh-engine.sh`,
`refresh-engine.sh`). Always run `bin/sync-db` or `bin/refresh`, never the
`local/`/`lib/` files directly.

- **`bin/sync-db`** — asks each run for a **database source**: a local file path, or an HTTP(S)
  URL (fetched with `curl`, not tied to S3/the `aws` CLI) — a teammate's link, an RDS export, a
  completely different project's dump. This is required — blank is rejected and re-prompted, there's
  no default and no silent fall-back to pulling from S3. No presigned link is ever generated or
  displayed either, since a presigned URL is itself a temporary bearer credential for the dump. It
  asks whether the dump still contains production configuration (if so, its URLs are rewritten
  using `PROD_BASE_URL`/`PROD_MEDIA_URL`/`PROD_COOKIE_DOMAIN` from `env/.env.local`), and asks for
  *this local site's own* domain/media/static URLs (defaulted to whatever's already in
  `env/.env.local`), writing any change straight back to that file. Changing the domain here does
  **not** regenerate the TLS cert or `config/nginx-local/default.conf` — re-run `bin/local/setup`
  for that. It also asks where Magento should store media files (local filesystem or S3, defaulted
  to the current `REMOTE_STORAGE_DRIVER`) — choosing S3 walks through the same AWS CLI
  profile/bucket verification `bin/local/setup` does (deriving region/key/secret from the profile,
  never asking for a raw secret key directly), and any change is written back to `env/.env.local`
  too, so `bin/refresh` (which re-reads the file right after `sync-db` returns) applies it to
  Magento in the same run. All of this is asked fresh on every standalone run; it's skipped only
  when `bin/local/setup` is the one driving the call, since by then it has already resolved these
  into `env/.env.local` itself. It then sanitizes the dump (strips `DEFINER` clauses, rewrites the
  source base URL to the local domain and the source media URL to `BASE_MEDIA_URL`), prompts for an
  optional, comma-separated list of tables whose data to strip (nothing by default), drops and
  reimports the database, and repoints OpenSearch at the local container (a production import
  otherwise leaves OpenSearch pointed at a private AWS IP unreachable from Docker).
- **`bin/refresh`** — the full local refresh pipeline: `composer install` → `sync-db` →
  repoint OpenSearch → reapply Varnish full-page-cache config (see below) → disable
  DB media storage → configure remote storage (see below) → `deploy:mode:set $MAGENTO_MODE` →
  `setup:upgrade` → `setup:di:compile` → `setup:static-content:deploy -f` → `indexer:reindex` →
  `cache:flush` → `maintenance:disable` (safety net in case an interrupted step left the site in
  maintenance mode).

### Switching Magento versions (upgrade / roll back)

`bin/version` (terminal) or `bin/local/version-wizard` (browser) changes the installed Magento
version — for a fresh install and an imported store alike. The current version is read from
`magento/composer.lock` and recorded as `MAGENTO_VERSION` in `env/.env.local`.

- **Upgrade** — to any newer version or patch in the `m2/` catalog. Review screen (stack changes
  included) → typed confirmation (browser) → **snapshot** → maintenance on → the target's stack
  pins (PHP, MariaDB, OpenSearch, … — containers rebuilt when they change; a `config.platform.php`
  pin in composer.json moves along with PHP) → `composer require-commerce` (or `composer require`
  without the root-update plugin) `magento/product-<edition>-edition <version> --no-update` →
  `composer update` → the same rebuild `bin/refresh` runs (`setup:upgrade`, compile, static deploy,
  reindex, maintenance off) → `m2/<version>/post-upgrade` hooks. If composer can't resolve the new
  version (usually a third-party extension that doesn't support it yet), composer.json/lock are put
  back and the database is untouched.
- **Roll back (downgrade)** — Magento can't undo schema changes or data patches in place, so going
  back means restoring a snapshot: the stack pins, `composer.json`/`composer.lock`,
  `app/etc/config.php` and the database, then the same rebuild. The current state is snapshotted
  first, so a rollback can be undone too. A MariaDB downgrade or an OpenSearch major change
  recreates that service's data volume (the database comes from the snapshot; search is
  reindexed). You can only roll back to versions you have a snapshot of.

**Snapshots** live in `db/snapshots/<APP_NAME>/<timestamp>_<version>/` (gitignored); the newest 5
are kept (`VERSION_SNAPSHOTS_KEEP` in `env/.env.local` to change that). Any database changes made
after a snapshot are lost when you roll back to it.

Without a terminal, preset the answers: `PRESET_VERSION_ACTION=upgrade PRESET_TARGET_VERSION=2.4.9
bin/version`, or `PRESET_VERSION_ACTION=rollback PRESET_SNAPSHOT=<snapshot folder name> bin/version`.

### Deploying to EC2 (pipeline kit)

The **pipeline wizard** (`mrn pipeline-wizard`, or **Make pipeline-ready** on the dashboard's
Pipeline tab) makes an instance's Magento repo deployable to an EC2 box. It asks for a target and
writes the kit into the repo; you push it with the store.

**What it asks for** (saved as `PIPELINE_*` in the instance `.env` and offered as the defaults
next time):

- the environment name (`dev`, `staging`, …) and the store hostname
- the AWS profile, region and hosted zone (picked from your AWS account when the profile works)
- the instance size, the branch that deploys, and the GitHub connection
- the Magento mode, and whether to stop the box outside working hours

**What it writes:**

| Path | What |
|---|---|
| `magento/.platform/stack.env` | the version pins: the same values as the instance's `.env` |
| `magento/deploy/` | `buildspec.yml`, `appspec.yml`, the deploy scripts, the box's compose files, the php/nginx images and service config (copied from this repo, so local and deployed match), and `infra/` (Terraform for the AWS side) |
| `magento/deploy/pipeline.json` | the kit version, the targets and a checksum of every file it wrote |

It also removes the old hand-made root `buildspec.yml`, `appspec.yml` and `scripts/*.sh` if they
exist.

**Before anything is committed**, the wizard checks the kit:

- `bash -n` on every script
- `docker compose config` on the box's stack
- `terraform fmt` and `terraform validate`, using a pinned Terraform it downloads to
  `~/.cache/platform-terraform` if needed
- optionally, a release build exactly like CodeBuild's: the php image, `composer --no-dev`, DI
  compile and static content

It then commits on a new local branch, `pipeline/<env>-<date>`, and never pushes.

**Running it again:**

- It updates files it wrote earlier.
- A file someone changed by hand is reported and kept, unless you choose to replace it.
- Another environment name adds a target, and the other targets' files stay.

The dashboard's **Pipeline** tab shows, for each instance:

- whether the kit is set up, current, outdated or edited by hand
- whether the pins still match the instance
- the targets, and whether the kit is on each target's branch (from the last fetch; it never
  fetches or pushes)

How a deploy runs on AWS is described in the generated `magento/deploy/README.md`.

### S3-backed media (Remote Storage)

`bin/refresh` configures Magento's `Magento_RemoteStorage` driver from `env/.env.local`'s
`REMOTE_STORAGE_DRIVER` — `aws-s3` serves `pub/media` directly from the configured
S3 bucket (`REMOTE_STORAGE_BUCKET`), via `setup:config:set --remote-storage-driver=aws-s3 ...`; `file`
disables it and serves `pub/media` from local disk instead (the default when no bucket is set).
There is no default bucket; region defaults to `us-east-2` (`bin/remote-storage.sh`);
`REMOTE_STORAGE_KEY`/`REMOTE_STORAGE_SECRET` are always per-machine, from `env/.env.local`
(gitignored). `bin/local/setup` prompts for all of this once and remembers the answer on future
runs.

### Varnish full-page cache

Magento is fronted by Varnish. Because Varnish only speaks plain HTTP, nginx's role is split into
two server blocks (same container/image):

```
nginx (443, TLS termination) → varnish (80, cache) → nginx (8080, internal-only, unchanged
  static/media/fastcgi logic) → php-fpm
```

The 8080 block is never published to the host — it's only reachable from Varnish, at
`nginx:8080`. `config/varnish/default.vcl` is Magento's own `varnish7.vcl` template
(`vendor/magento/module-page-cache/etc/varnish7.vcl`) with the placeholders filled in for this
stack (backend `nginx:8080`, purge ACL = the RFC1918 private ranges, which cover whatever subnet
Docker auto-assigns `up-network`; `ssl_offloaded_header` →
`X-Forwarded-Proto`, `grace_period` → `300`).

**Two one-time setup steps** — neither is part of `bin/refresh`, because both
live in `app/etc/env.php`, which a DB reimport never touches:

```bash
# Tells Magento where to send live PURGE requests when cache is invalidated (a product save,
# cache:flush, etc). The admin-UI "System > Full Page Cache > Varnish" fields do NOT control
# this — they only feed the "Export VCL" button. Without this, Magento falls back to PURGE-ing
# whatever Host header the current request happened to have, which silently never reaches Varnish.
bin/magento setup:config:set --http-cache-hosts="varnish:80" -n

# Turns on Magento's own full-page cache type. If this is off, every response comes back
# Cache-Control: no-store and Varnish has nothing cacheable to work with, regardless of VCL.
bin/magento cache:enable full_page
```

`bin/refresh` *does* reapply these every run (safe to repeat, and `sync-db`'s DB reimport wipes
`core_config_data`, which is where they live) — only the two commands above need running by hand,
once:
```bash
bin/magento config:set system/full_page_cache/caching_application 2 -n
bin/magento config:set system/full_page_cache/varnish/access_list "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" -n
bin/magento config:set system/full_page_cache/varnish/backend_host nginx -n
bin/magento config:set system/full_page_cache/varnish/backend_port 8080 -n
bin/magento config:set system/full_page_cache/varnish/grace_period 300 -n
```

**Verify it's working:**
```bash
curl -Ik https://local.magento.test/
```
- First request: `X-Magento-Cache-Debug: MISS`. Second request to the same URL: `HIT`.
- After `bin/magento cache:flush` (or saving a product/CMS page in admin), the next request
  should show `MISS` again — this confirms the live PHP → Varnish PURGE is actually reaching
  Varnish, not just clearing Magento's own cache pool.
- `/static/` and `/media/` requests will show `X-Magento-Cache-Debug: UNCACHEABLE` — expected,
  Varnish deliberately passes these straight through (nginx already serves/caches them via its
  own `Cache-Control`/`expires` headers; no double-caching).

**Debugging:**
```bash
bin/platform exec varnish varnishlog    # live request/cache-decision trace
bin/platform exec varnish varnishstat   # hit/miss counters
bin/platform exec varnish varnishadm backend.list   # confirm backend shows "healthy"
```
If `backend.list` shows the backend as sick, check `bin/platform logs nginx` for
`/health_check.php` requests returning non-200 — Varnish's backend probe hits that path on
`nginx:8080` every 5s and marks the backend down after repeated failures.


### Magento mode & file permissions

`bin/local/setup` asks two things on every run (current values as defaults), stored in
`env/.env.local`:

1. **`MAGENTO_MODE`** — `default`, `developer`, or `production`. `bin/refresh` applies it
   (`deploy:mode:set`; production is set after compiling, via `--skip-compilation`). When setup runs
   without a data refresh it switches the mode itself.
2. **`FILE_OWNERSHIP`** — who php-fpm runs as, per Adobe's
   [file-system-permissions guide](https://experienceleague.adobe.com/en/docs/commerce-operations/configuration-guide/deployment/file-system-permissions):

| | php-fpm runs as | CLI (`bin/magento`, `bin/composer`, refresh) | Writable dirs (`var generated pub/static pub/media app/etc`) |
|---|---|---|---|
| `single` (default) | you (`$(id -u):$(id -g)`) | you | owned by you, `u+w` — Adobe's "one owner" |
| `two-owner` | `www-data` (33:33) | you, group `www-data` (`$(id -u):33`) | `you:www-data`, dirs setgid `2770`/`2775`, files `660`/`664` — Adobe's "two owners" |

`bin/lib/identity.sh` resolves this for every wrapper (it exports `PHP_UID`/`PHP_GID` for
`docker-compose.local.yml`'s php `user:` and sets `EXEC_USER`), so switching models just needs
`bin/platform up -d` to recreate php — setup does that. `two-owner` needs `sudo` once: setup adds you
to the host's `www-data` group (open a new shell, or `wsl --shutdown` from Windows, for your
editor/shell to pick it up) and chowns the writable dirs. Magento's own bootstrap uses umask `002`,
so files either side creates stay group-writable.

**Production lock (both models).** In production mode, `bin/refresh` (and setup) end with
`bin/permissions/lock`: write access (`u-w` for single, `g-w` for two-owner) is removed from
everything except `var/`, `generated/`, `pub/media/` and `.git/`, then `var/view_preprocessed`,
`generated/code` and `generated/metadata` are re-locked. `app/etc/env.php` always loses all
"other" access. `bin/refresh` always runs `unlock` + `apply` first, so a locked tree never blocks it.

#### Changing your mind later

Each choice has its own command — no need to re-run the whole setup, and every one is reversible
(it unlocks and restores ownership first, then re-locks only if the result is production):

```bash
bin/mode/show                 # configured vs. actual mode, and the ownership model
bin/mode/developer            # switch mode (also: bin/mode/default, bin/mode/production)
bin/ownership/two-owner       # switch ownership (also: bin/ownership/single) — recreates php
bin/permissions/unlock        # make code writable (e.g. composer by hand in production mode)
bin/permissions/lock          # lock it again
bin/permissions/apply         # re-apply ownership/permissions for the current model
```

The logic lives in `bin/lib/mode-engine.sh` and `bin/lib/permissions-engine.sh`; the scripts in
`bin/mode/`, `bin/ownership/` and `bin/permissions/` are one-line entry points. Switching mode by
hand doesn't re-run a data refresh — `bin/mode/production` uses Magento's own
`deploy:mode:set production` (which compiles and deploys static content), then locks.

## Web wizards

`bin/local/setup-wizard`, `bin/local/refresh-wizard` and `bin/local/version-wizard` open a local
page that walks through `bin/local/setup` / `bin/refresh` / `bin/version` step by step — numbered steps with a progress bar, one question at
a time, a live checklist of what's running, a review screen before anything heavy (setup) or a typed
confirmation before the database is replaced (refresh) or the version changes, and a finish screen with links, the Windows
hosts-file line and everyday commands. Options: `--no-browser` (print the link only), `--port N`.

**Same wizard, two front ends.** Every question and progress line comes from `bin/lib/ui.sh`
(`prompt_value`, `prompt_menu`, `prompt_yes_no`, `prompt`, `ui_step`, `ui_task`, …). In the terminal
they behave as they always did; under the wizard (`SETUP_UI=web`) they send the question to the page
instead. So the page asks exactly what the terminal asks — to add a question, add it to the bash
script; to add a new *kind* of question, add it to `bin/lib/ui.sh` (and render it in
`bin/lib/web-wizard/ui/app.js`). Steps are listed once, in `UI_STEPS_setup` / `UI_STEPS_refresh` / `UI_STEPS_version`.

**How it runs.** `bin/lib/web-wizard/launch.sh` starts `server.py` (Python standard library only),
which runs the bash script as a child and relays questions/answers through two FIFOs. The page is
plain HTML + vanilla JS styled with Tailwind v4 using shadcn/ui's tokens and component recipes;
`ui/wizard.css` is precompiled and committed (`bin/lib/web-wizard/build-css` rebuilds it with the
Tailwind standalone CLI — no Node — after changing classes). No CDN, so it works offline.

**Safe to use:**
- listens on `127.0.0.1` only; the link carries a token (reusable while the wizard runs) that becomes an HttpOnly,
  SameSite=Strict cookie; wrong `Host` headers (DNS rebinding) and requests without the
  `X-Wizard` header (CSRF) are refused;
- passwords and keys are never stored by the page or written to the log (the server also redacts
  any submitted password from the log as a backstop); the review screen masks them;
- refreshing, closing or going "back" in the browser never stops or rewinds anything — the script
  runs in the terminal; reopen the link to continue. Closing the **terminal** stops it, and Ctrl+C
  needs to be pressed twice;
- **Cancel** stops at a question immediately, or after the current task ("stop now" is not offered
  while the database is being replaced);
- one setup/refresh/version switch at a time per checkout (`.wizard.lock`, terminal runs included);
- each run's log is saved to `logs/<setup|refresh|version>-<timestamp>.log`, and its outcome to
  `logs/operations/<id>.json` (no secrets) for the dashboard;
- the login cookie is named after the port (`wiz_<port>`) and lasts for the browser session, so
  several wizards (and the dashboard) open at once don't log each other out.

The page's DOM helpers, icons and component recipes live in `ui/kit.js`, shared with the dashboard;
`ui/app.js` is the wizard itself.

## Dashboard

```bash
bin/local/dashboard                 # opens http://127.0.0.1:<port>/?token=… in your browser
bin/local/dashboard --no-browser    # print the link only
bin/local/dashboard --port 8800     # fixed port (default: the one it used last time)
bin/local/dashboard --scan ~/Sites --scan /srv/stores   # where to look for checkouts
```

One persistent page for **every instance (and older-layout checkout) on this machine**, laid out like
the Magento admin: a dark sidebar lists the environments and, for the open one, its sections (on a
narrow screen the sections become tabs). The wizard pages use the same layout, with their steps in
the sidebar. The platform version (the repo's `VERSION` file) is at the foot of every page. It shows:

- **New instance**: name it, and it creates `~/Sites/<name>/` and opens the setup wizard;
- a card per environment, with domain, Magento and PHP versions, mode, git branch and commit, and
  the health of Docker, Nginx, PHP-FPM, MariaDB, OpenSearch, RabbitMQ, Valkey and Magento;
- per environment:
  - **Overview**: paths, compose project, stack versions, both repos' branch, commit and
    clean/modified state, and disk usage on request;
  - **Containers**: state, health, uptime, ports, Start/Stop/Restart;
  - **Magento**:
    - cache flush/clean, indexer status, reindex, mode;
    - **Build Magento (code only)**: `setup:upgrade` → `setup:di:compile` →
      `setup:static-content:deploy -f` → `cache:flush`, with maintenance mode on. It never touches
      the containers, so it's the one to use after CSS/JS or code changes;
    - **composer install / update**, which always continue with that build, since composer alone
      doesn't make a change live;
    - **Full rebuild**: what the refresh and version wizards run, in the wizard. On top of the build
      it resets compiled code with php/nginx/varnish restarts, applies the platform config and reindexes;
  - **Workflows**: the setup, refresh and version wizards;
  - **Pipeline**: whether `magento/` is pipeline-ready (see "Deploying to EC2");
  - **Git** (`magento/`):
    - branches: switch, create a new one, or delete a local one. Delete never touches origin; a branch
      with commits that exist nowhere else is only deleted after you confirm how many would be lost;
    - changed files in two columns, Unstaged and Staged. Drag files between them, or use the arrows,
      to stage and unstage (`git add` / `git restore --staged`). A file with both kinds of changes
      shows in both columns as "partly staged", and what `.gitignore` keeps out is listed under
      Ignored. You can also view a file's diff or discard its changes (asks first);
    - commit: **Commit (N)** appears once files are staged and opens a dialog for the message;
    - fetch, pull (fast-forward only) and push, with ahead/behind origin;
    - recent commits, with the ones not pushed yet marked.

    Fetch, pull and push use git's own saved login. When there isn't one, the dialog takes a GitHub
    username and personal access token. With **Remember my credentials** ticked, git saves them in
    `~/.git-credentials` (readable only by you, the standard git credential store); otherwise they're
    used for that one command and never saved or logged. **Forget** removes a saved login. The setup
    wizard's GitHub clone offers the same choice. Nothing here force-pushes, rebases or merges;
  - **Logs**: container logs, Magento `var/log`, platform `logs/`;
  - **Overview → Danger zone → Destroy instance**:
    - removes the containers, all data volumes and the whole instance folder;
    - first shows what would be lost, including uncommitted or unpushed work in `magento/`, which
      has to be confirmed;
    - asks you to type the instance name;
    - the hosts-file line on Windows is left for you to remove;
  - **Operations**: history, running state, and Reopen/Cancel.

It runs until Ctrl+C, and only one runs per user: running `bin/local/dashboard` again just prints
the existing link. Operations it started keep running after it stops.

The existing commands (`bin/local/*-wizard`, `bin/refresh`, `bin/version`, …) work exactly as
before; the dashboard only calls them.

### How it works

```
browser ──> dashboard.py   (127.0.0.1 only; status + JSON API)
              ├─ envs.py    discovery + status: docker inspect, env files, git, /health_check.php
              ├─ ops.py     whitelisted actions and workflows, operation history
              │    ├─ locks.py    the checkout lock (.wizard.lock)
              │    ├─ oplogs.py   log tails + redaction
              │    ├─ destroy.py  deleting an instance
              │    └─ oprun.py    detached runner: one operation, its log and record
              ├─ gitrepo.py the Git tab        pipeline.py  the Pipeline tab
              └─ webcore.py shared with the wizard server: auth, login/static files, headers,
                            redaction, records, child-process environment (no tokens)
                    │
                    ▼
<repo>/bin/platform | bin/magento | bin/composer | bin/mode/* | bin/lib/web-wizard/launch.sh
   (with PLATFORM_INSTANCE_DIR=<instance>; older-layout checkouts use their own bin/)
```

- **Discovery.** There is no registry and nothing to keep up to date. The dashboard combines two
  sources:
  - **Docker labels** on every container, running or stopped. The compose project
    (`<APP_NAME>-platform`) says which stacks exist, and `platform.instance_dir` says which folder
    each belongs to. Stacks started before that label existed fall back to compose's
    `working_dir` (`<checkout>/compose`).
  - **A folder scan**, two levels deep, for environments with no containers yet. The default is
    the folder holding the repo (e.g. `~/Sites`); change it with `--scan`.

  An environment is either an **instance folder** (`.platform-instance` + `.env`) or an
  **older-layout checkout** (`bin/platform`, `compose/docker-compose.yml`, `env/.env.local`). Its
  `APP_NAME` is its id: setup keeps that unique. An instance whose `APP_NAME` doesn't match its
  folder name, or whose platform repo has gone, is listed read-only. Values come from its env
  file; for older checkouts `env/stack.env` takes precedence where it exists. Checkouts set up as
  a dev server (`env/.env.dev`) are listed read-only. Anything that can't be determined is shown
  as **Unknown**.
- **Health.**
  - Container state and Docker healthchecks. Nginx, PHP-FPM, Varnish and Mailpit have no
    healthcheck and show "Running (no healthcheck)".
  - OpenSearch's cluster colour comes from its healthcheck output.
  - **Magento** is checked separately with a real request to `https://<APP_DOMAIN>/health_check.php`
    through the edge proxy. A running container doesn't mean a working store.
- **Actions run the checkout's own scripts.** Each action is a fixed command line in
  `ops.ACTIONS`:
  - `bin/platform up -d`, `stop`, `restart [service]`;
  - `bin/magento cache:flush`, `cache:clean`, `indexer:status`, `indexer:reindex`;
  - `bin/mode/<mode>` and `bin/mode/show`;
  - `bin/composer validate`, `install`.

  There is no shell, and no free-form input reaches a command line. Each action runs detached
  (`oprun.py`), so closing the page or restarting the dashboard doesn't stop it. The dashboard
  first removes from the environment anything that could point a script at another stack: every
  env-file key, `COMPOSE_*`, `WIZARD_*`, `PRESET_*`.
  - **Stop** keeps containers; it never runs `down`.
  - **Start** is always `up -d`, so ports and the edge proxy are re-checked.
  - **Cancel** is offered only for start/stop/restart. Commands that run inside the php container
    keep running there even if the client is stopped, so they always run to the end.
- **Workflows run the checkout's own wizard.** Setup, Refresh and Version start
  `bin/lib/web-wizard/launch.sh <flow> --no-browser` in that checkout and open its page in a new
  tab. Its questions, uploads, progress, typed confirmations and Cancel are unchanged. Reopen it
  from **Operations** while it runs.
- **Locks.** Each checkout's `.wizard.lock` is the same lock setup/refresh/version take. Dashboard
  actions that change something take it too, and are refused (with the holder's PID) while a
  wizard or another action runs in that checkout. Read-only views and other checkouts are never
  blocked.
- **History.** Each wizard run and dashboard action writes `logs/operations/<id>.json` with its
  status, step, exit code and log file. On every view the dashboard checks the recorded process
  against `/proc`, so a run whose process vanished shows **Interrupted** instead of "running"
  forever. Terminal runs and wizards from older copies of the platform scripts don't write a
  record; their `logs/*.log` files are listed as "Earlier runs".
- **Logs** are always redacted before they reach the page:
  - the checkout's own secrets: `.env.local` passwords and keys, the `env.php` crypt key and DB
    password, every `auth.json` credential;
  - generic patterns: `password=…`, AWS access keys, bearer tokens, credentials in URLs, signed
    URLs, tokens.

  Literal values shorter than 8 characters, such as the stock `magento` passwords, are only
  caught by those patterns.

### Adding an action

1. Add an entry to `ACTIONS` in `bin/lib/web-wizard/ops.py`:
   - `label` and `argv`: a fixed command, relative to the checkout, using its wrappers;
   - `mutating: True` if it changes anything, so it takes the checkout lock;
   - `confirm` for anything disruptive;
   - `cancellable` only if it runs on the host;
   - `arg` if it takes a whitelisted parameter (see `service`/`mode` in `run_action`).
2. Add a button in `ui/dash-env.js`: `runAction(env, '<name>', body)` in the relevant tab.
3. Add the case to `tests/test_dashboard_api.py`. If you used new Tailwind classes, run
   `bin/lib/web-wizard/build-css`.

Business logic belongs in the bash engines; the dashboard only calls them.

### Security

**Local (supported).** Same model as the wizard: listens on `127.0.0.1` only (no option for
anything else).
- A token in the printed link becomes an HttpOnly, SameSite=Strict cookie (`dash_<port>`).
- `Host` is checked (DNS rebinding) and every POST needs the `X-Wizard` header (CSRF).
- Strict CSP, `X-Frame-Options: DENY`, no CDN.
- Environment ids and log names are only looked up in what discovery found, never used to build a
  path.
- The link and token live in `~/.cache/platform-dashboard/state.json` (0600). Anyone who can read
  your files there can use the dashboard as you.

**Remote / EC2: not implemented. Don't expose it.** The dashboard has no user accounts, roles or
audit trail. Never publish its port, open it in a security group, or put it behind a plain
reverse proxy. A remote setup would need, in front of the localhost service:
- HTTPS (Cloudflare or a reverse proxy);
- real authentication (e.g. Cloudflare Access or oauth2-proxy with your identity provider);
- authorization (who may stop or refresh what);
- audit logging of every action.

On a dev server, use SSH port forwarding (`ssh -L 8800:127.0.0.1:8800 …`) until then. The current
token cookie is a local convenience, not a substitute for any of these.

### Tests

```bash
python3 -m unittest discover -s tests -t tests
```
Standard library only. The suite runs the real wizard server (`server.py`) and the dashboard
against fake checkouts and a fake `docker`, covering:
- auth, Host and CSRF checks;
- unknown ids, path traversal and injection attempts;
- environment scrubbing and lock handling;
- redaction;
- operation records;
- the bash helpers that guard input (`tests/test_bash_helpers.py`: `set_env_var`, `env_get`,
  sed literals, hostnames, composer arguments, backup pruning, the S3 storage flow).

## Architecture & conventions

Three layers, each with one job:

| Layer | Where | Does |
|---|---|---|
| **Bash** | `bin/` | Everything that changes an instance: Docker, Magento, the database, files |
| **Python** | `bin/lib/web-wizard/*.py` | The local web servers: the wizard (`server.py`) and the dashboard (`dashboard.py`). They only run bash entry points, with fixed argv |
| **Browser** | `bin/lib/web-wizard/ui/` | The pages. `kit.js` holds every UI recipe (shadcn-style, Magento admin colours); `app.js` is the wizard; `dash-core.js`, `dash-env.js`, `dash-git.js` and `dashboard.js` (boot) are the dashboard |

**Bash**
- Every `bin/local/*` entry point starts with `set -e` (plus `pipefail` where the script was
  written for it) and `source …/lib/bootstrap.sh`. That gives `PLATFORM_DIR`, `MAGENTO` and the
  helpers: `ui.sh` (log, fail, cancel, prompts, progress, lock), `env.sh`, `util.sh` and `paths.sh`.
- Setup's steps live in `bin/lib/setup/NN-*.sh`, sourced in order by `setup-engine.sh`.
- The env file is read with `env_get FILE KEY [DEFAULT]` and written with
  `set_env_var FILE KEY VALUE`. Values are copied literally and multi-line values are refused.
  Nothing else greps or seds it.
- The database is reached through `db_client root|app …` and the `db_*` helpers in
  `db-backup.sh`. The password travels in the container's environment, never on a command line.
- A check that errors is a failure, never a "no". Nothing is dropped or deleted on a default
  answer without someone there to see the question (`ui_interactive`).
- `fail` (an error, exit 1) and `cancel` (the user said no, exit 130, shown as *Cancelled*) are the
  only ways a flow stops. Errors go to stderr.
- A function relied on to stop at its first error must not be run in an `if`/`&&`/`||` context,
  because bash then ignores `set -e`. `ui_task` runs its command outside one.
- Names users type are validated before they reach a file, sed, SQL or Docker:
  - instance names: `valid_instance_name`;
  - domains: `valid_hostname`;
  - packages: `composer_check`;
  - table names: in the DB refresh.

**Python**
- Standard library only.
- Every request input is validated and gives 400, never a traceback.
- `webcore.OpError(code, message, detail)` is the one error type.
- Child processes get `webcore.base_env()` / `git_env()`, built when they start: never a server
  token.
- Logs, records and one-time credentials are 0600 in 0700 folders.
- Everything shown on a page goes through redaction first.

**Browser**
- No libraries and no build step besides Tailwind (`build-css`).
- UI comes only from `kit.js` recipes. Variants are props (`size: 'icon-sm'`,
  `Card({ gap: 'sm' })`), never a `cls` that tries to override a recipe's own size or spacing.
- Requests go through `api()` / `getJSON()`. They never throw, and a dropped connection comes back
  as `{ ok: false, status: 0 }`.
- Dialogs that need a valid answer use `formDialog({ …, validate })`; yes/no questions use
  `confirmDialog`.
- Log panes use `setLogText` so a reader who scrolled up stays put.
- Background re-renders wait while someone types or a dialog is open (`userBusy`).
- New tabs open with `openPendingTab` and then `navigateTab`, which only accepts http(s) URLs.

## Services

| Service    | Image                                | Version (from env file) |
|------------|---------------------------------------|--------------------------|
| nginx      | custom build (`docker/nginx`)         | `NGINX_VERSION=1.29`     |
| php        | custom build (`docker/php`)           | `PHP_VERSION=8.4`        |
| mariadb    | `mariadb`                             | `MARIADB_VERSION=11.4`   |
| valkey     | `valkey/valkey`                       | `VALKEY_VERSION=8`       |
| rabbitmq   | `rabbitmq` (management)               | `RABBITMQ_VERSION=4`     |
| opensearch | `opensearchproject/opensearch`        | `OPENSEARCH_VERSION=3`   |
| varnish    | `varnish`                             | `VARNISH_VERSION=7.5`    |
| mailpit    | `axllent/mailpit`                     | pinned in compose file   |

Nginx terminates TLS for `local.magento.test` (or your `APP_DOMAIN`) using a locally-generated
mkcert certificate (see "Local development setup" above). Nginx sits in front of Varnish (TLS
termination on 443, proxies to Varnish on plain HTTP), and behind it (internal-only port 8080, the
actual app-serving logic) — see "Varnish full-page cache" above.

### Running several stacks at once

Every checkout of this platform (2.4.8, 2.4.9, …) can run at the same time; nothing is hardcoded
per machine:

- **80/443** belong to a shared edge proxy, not to any stack — `compose/edge/docker-compose.yml`
  (Traefik, compose project `platform-edge`), started automatically by `bin/platform up` and never
  stopped by `bin/platform down`. It routes by hostname over the external `platform-edge` network to
  each stack's nginx: 443 is TLS passthrough on SNI (nginx still serves its own mkcert cert), 80 is
  plain HTTP on `Host`. So every site keeps a port-less `https://<APP_DOMAIN>/`, and each just needs
  its own `127.0.0.1 <APP_DOMAIN>` hosts-file line. Stop it with
  `docker compose -p platform-edge down` if you ever need 80/443 back.
- **Network** — each stack's `up-network` gets a Docker-assigned subnet (no fixed CIDR).
- **Debug ports** (`DB_PORT`, `VALKEY_PORT`, `RABBITMQ_*_PORT`, `OPENSEARCH_PORT`, `MAILPIT_*_PORT`)
  — `bin/platform up` (`bin/lib/ports.sh`) keeps each value in `env/.env.local` unless another
  stack's container (running *or* stopped) or a host process holds it, in which case it moves to the
  next free port, saves it, and says so. Ports stay stable across restarts.
- **Names** — compose project, volumes and images derive from `APP_NAME`; `bin/local/setup` refuses
  an `APP_NAME` or `APP_DOMAIN` another checkout already uses.

Stacks created before this (fixed `af-network` on `172.28.0.0/16`) are migrated automatically on
their next `bin/platform up`: containers are recreated on `up-network`, data volumes are kept.

If you ever run so many stacks that Docker runs out of networks ("could not find an available,
non-overlapping IPv4 address pool"), widen its pools in Docker Desktop → Settings → Docker Engine:
`"default-address-pools": [{"base": "10.200.0.0/16", "size": 24}]`.

## Environment files

| File | Committed? | Used by |
|---|---|---|
| `env/.env.local.example` | Yes (placeholders for secrets) | Template — `bin/local/setup` copies it to `env/.env.local` |
| `env/.env.local` | **No** (gitignored) | Every `bin/` script — required; they refuse to run without it |

**Changing DB credentials later:** editing `DB_*` in `env/.env.local` after the `${APP_NAME}-db-data`
volume exists does **not** change the live MariaDB users — MariaDB only applies them when it first
initializes the volume, and `app/etc/env.php` keeps its own copy. Either run the matching
`ALTER USER ...` and update `env.php`'s `db.connection.default`, or start over with
`bin/platform down -v` (deletes the database) and re-run `bin/local/setup`.
