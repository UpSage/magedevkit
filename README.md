# MageDevKit — Magento Developer Kit

A Magento 2 developer kit: Docker stacks for every local store, browser wizards (setup, refresh,
version, build, pipeline), a dashboard with environments, builds and Git, and the EC2 deploy kit.
The command is `mdk`.

Docker Compose configuration for a Magento 2 runtime, for **local development**. This
repo controls the **infrastructure** the application runs on (Nginx, Varnish, PHP-FPM, MariaDB,
Valkey, RabbitMQ, OpenSearch, Mailpit) — it does not contain the Magento application itself.

**One repo, many stores.** This repo is the shared platform code. Each Magento store you run is
an *instance*: its own folder next to the repo, created with `mdk new NAME` (or
**New instance** on the dashboard). The Magento code is either downloaded fresh or cloned from your
project's repository. Editing a script here changes every instance at once. See "Instances" below.

> **Deploying:** the pipeline wizard writes an EC2/CodePipeline deploy kit into a store's repo — see
> "Deploying to EC2 (pipeline kit)".

**New to this project?** Start with [`LOCAL_SETUP.md`](LOCAL_SETUP.md) — a complete, from-scratch
walkthrough (WSL2/Docker install through a working local site). This file covers the permission
model and the full command reference.

## Instances

```
~/Sites/
├── magedevkit/                  ← this repo (MageDevKit): the wizard + all platform code (bin/, compose/, docker/, m2/, …)
├── m248p5/                      ← an instance (folder name = its APP_NAME) — local data only, no scripts
│   ├── .platform-instance       marker: which platform repo runs it
│   ├── .env                     its settings: APP_NAME, domain, ports, versions, mode, …
│   ├── magento/                 the store's code — its own git repo, the only thing ever pushed
│   ├── nginx/  certs/           generated vhost + mkcert certificate
│   └── logs/   db/              run logs, operation records, backups, version snapshots
└── m249/                        ← another instance, same shape
```

**One command for every instance: `mdk`.** Install it once (a symlink in `~/.local/bin`):
```bash
bin/local/install-mdk
```
Then:
```bash
mdk new m249                      # create ~/Sites/m249 and open its setup wizard (--terminal, --no-setup)
cd ~/Sites/m249
mdk magento cache:flush           # bin/magento in this instance's php container
mdk composer install              # composer, likewise
mdk platform up -d                # docker compose for this stack (ps, logs -f php, stop, …)
mdk refresh-wizard                # or setup-wizard / version-wizard; terminal: setup / refresh / version
mdk mode production               # also: ownership single|two-owner, permissions apply|lock|unlock
mdk pipeline-wizard               # make magento/ pipeline-ready for an EC2 environment (see "Deploying to EC2")
mdk build                         # Magento-only build: setup:upgrade, di:compile, static -f, cache:flush (maintenance on; containers untouched)
mdk build --composer update vendor/pkg   # composer first, then the same build
mdk module disable --build Vendor_Module   # turn modules on/off, then the same build
mdk module check-updates          # composer outdated for composer.json's packages (the Modules tab shows it)
mdk module update --dry-run vendor/pkg:^2.0   # check an update without changing anything; drop --dry-run to apply + build
mdk rebuild-wizard                # full rebuild: also resets compiled code, restarts php/nginx/varnish, config, reindex
mdk --version                     # the platform version (VERSION file, shown in every page's footer)
mdk rebuild --composer update vendor/pkg   # composer first, then the full rebuild (terminal)
mdk destroy --yes                 # delete the instance: containers, data volumes and its folder (no undo)
mdk -i m249 magento indexer:status   # any command, from any folder, by instance name
mdk list                          # instances
mdk dashboard                     # all instances in the browser
```
`mdk` picks the instance from `-i NAME`, otherwise from the folder you're in. It sets
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
  to `mdk new`.
- **Removing an instance:** `mdk -i <name> destroy --yes`, or **Danger zone → Destroy instance** on
  the dashboard's Overview tab. It removes the containers, the data volumes and the whole folder.
  The dashboard first shows uncommitted or unpushed work in `magento/` and refuses to go on until
  you confirm it may be lost.

## Layout

This repo — the shared platform code. It holds no store data; that lives in each instance folder
(see "Instances" above).

```
magedevkit/
├── bin/
│   ├── mdk                 The one command for every instance (install: bin/local/install-mdk)
│   ├── platform            docker compose for an instance's stack (mdk platform …)
│   ├── magento, composer   bin/magento and composer inside its php container
│   ├── refresh, version, sync-db     Dispatchers to the matching bin/local/ flow
│   ├── sync-media-to-s3    Copies local pub/media to the S3 bucket (mdk sync-media)
│   ├── remote-storage.sh   S3 defaults for the media sync
│   ├── local/              Entry points
│   │     terminal flows:   setup, refresh, version, sync-db, pipeline, rebuild, magento-build (mdk build)
│   │     browser wizards:  setup-wizard, refresh-wizard, version-wizard, pipeline-wizard, rebuild-wizard
│   │     instances:        new-instance (mdk new), destroy, dashboard, install-mdk
│   ├── mode/               default | developer | production | show
│   ├── ownership/          single | two-owner
│   ├── permissions/        apply | lock | unlock
│   ├── m2/                 check-requirements: compare the m2/ catalog with Adobe's requirements
│   └── lib/                Everything the entry points source (see "Architecture & conventions"):
│       ├── bootstrap.sh        Start of every bin/local/* script: ui, env, util, paths
│       ├── ui.sh               Prompts, progress, log/fail/cancel, the lock — terminal and browser
│       ├── env.sh util.sh      Env-file reads/writes, validators, version compare, composer rules
│       ├── paths.sh            Where an instance's files live
│       ├── generated.sh        Out-of-date compiled code: generated_stale, clear_generated
│       ├── *-engine.sh         The flows: setup, refresh, version, rebuild, pipeline, mode, …
│       ├── setup/              Setup's steps, 01-preflight … 11-finish
│       ├── db-backup.sh        Every database call (db_client), dumps, restore
│       ├── prod-urls.sh        The production URLs a dump is rewritten from, read from the dump
│       │                       itself by dump-config.py
│       ├── git-repo.sh         Per-repo git identity and saved login for magento/ (the identity
│       │                       from the GitHub account: github-identity.py)
│       ├── aws-storage.sh tools.sh   S3 setup; verified installs of mkcert, direnv and the
│       │                       AWS CLI; keys/ (AWS CLI signing key)
│       ├── aws.sh aws-credentials.py aws-profiles.py   MageDevKit's own AWS connections (mdk aws)
│       ├── cfn-check.py secret-scan.py   the pipeline kit's template check; no credentials in a commit
│       └── web-wizard/         The browser wizard (server.py) and the dashboard (dashboard.py,
│                               envs, ops, locks, oplogs, destroy, gitrepo, pipeline, webcore),
│                               ui/ = the pages (kit.js, app.js, dash-*.js, wizard.css)
├── compose/            docker-compose.yml (base) + docker-compose.local.yml; edge/ = the shared
│                       Traefik proxy every stack sits behind
├── config/             Service config: nginx-local/default.conf.template (the vhost template),
│                       php/99-app.ini, varnish/default.vcl
├── docker/             Dockerfiles for the custom nginx and php images
├── env/                .env.local.example — the template every instance's .env starts from
├── m2/                 Magento version catalog for fresh installs and upgrades (see m2/README.md)
├── pipeline/           The deploy kit the pipeline wizard writes into a store (templates/deploy/,
│                       VERSION)
├── tests/              python3 -m unittest discover -s tests -t tests (see "Tests")
├── VERSION             MageDevKit's version, shown in every page's footer
├── README.md
└── LOCAL_SETUP.md      From-scratch walkthrough (WSL2, Docker, first instance)
```

Generated per instance, never in this repo: the rendered vhost (`<instance>/nginx/default.conf`),
the mkcert certificate (`<instance>/certs/`), logs and operation records (`<instance>/logs/`),
database backups and version snapshots (`<instance>/db/`), and the settings (`<instance>/.env`).

## Day-to-day commands

Inside an instance folder (or anywhere, with `-i NAME`):

```bash
mdk platform ps                    # container status
mdk platform up -d                 # start the stack
mdk platform stop                  # stop it (keeps containers and data)
mdk platform logs -f php           # tail logs for one service
mdk platform exec php bash         # shell into a container

mdk magento cache:flush            # any bin/magento command, in the php container
mdk magento setup:upgrade
mdk composer require vendor/package

mdk build                          # code-only build: setup:upgrade, di:compile, static -f, cache:flush
mdk rebuild-wizard                 # full rebuild (also resets compiled code, restarts php/nginx/varnish)

mdk setup-wizard                   # first-time setup, as a page in your browser
mdk refresh-wizard                 # replace the database from a dump
mdk version-wizard                 # upgrade Magento / roll back to a snapshot
mdk pipeline-wizard                # write the deploy kit into magento/deploy/ (see "Deploying to EC2")
mdk setup | refresh | version | pipeline | rebuild   # the same flows in the terminal

mdk dashboard                      # every instance on this machine (see "Dashboard")
```

The wrappers always use the instance's `.env` and both compose files, and refuse to run (pointing at
`mdk setup-wizard`) until that `.env` exists.

With `FILE_OWNERSHIP=single`, a bare `mdk platform exec php ...` (no `--user`) already runs as you —
the same identity `mdk magento`/`mdk composer` use. With `two-owner` it runs as php-fpm's
`www-data`; use `mdk magento`/`mdk composer` (or `--user "$(id -u):33"`) to act as yourself.
See "Magento mode & file permissions" below.

## Local development setup

**Before you start:** Docker Desktop with WSL2 integration (on Windows), `git`, `curl` and
`python3`. Anything else a step needs — mkcert, and for S3 media the AWS CLI and direnv — setup
installs into `~/.local/bin` when it's missing, after checking it against a pinned checksum (the AWS
CLI against AWS's signature). [`LOCAL_SETUP.md`](LOCAL_SETUP.md) walks through installing WSL2 and
Docker.

**Create a store:**
```bash
bin/local/install-mdk       # once: puts `mdk` on your PATH
mdk new m249                # creates ~/Sites/m249 and opens its setup wizard in the browser
                            # (--terminal: the same questions in the terminal)
```
Setup is safe to re-run: every step skips itself when it's already done.

**What setup does**, step by step (the same steps appear in the wizard's sidebar):

1. **Welcome & checks** — Docker, mkcert, the instance folders.
2. **Magento source** — a fresh download from the `m2/` catalog (see "Fresh Magento install"), or a
   clone of your store's repository (see "Git identity and login" below), or the code already in
   `magento/`.
3. **Store data** — the database dump to import (upload it on the page, or give a path or URL), and
   whether it's a copy of production (its URLs get rewritten) or already prepared for local use.
4. **Project** — the local domain (default `<name>.test`, a real hostname with a dot), the admin path,
   the search-index prefix, and optional URL overrides.
5. **Database login** — the MariaDB names and passwords, asked only while the database volume doesn't
   exist yet.
6. **Mode & versions** — Magento mode, file ownership, and the stack versions the `m2/` catalog
   recommends for this Magento version.
7. **Review & start** — everything above on one screen; nothing heavy has run yet (**Change answers**
   goes back with your answers kept).
8. **Containers** — the mkcert certificate for the domain, the images, the stack (behind the shared
   edge proxy), and a wait until every service is healthy.
9. **Dependencies** — `composer install`, or `composer create-project` for a fresh download.
10. **Magento install** — `setup:install` for a new store (admin account asked on the page), or a
    check that an existing install is complete.
11. **Media & data** — the media storage (local disk or S3) and the database import, if any (see
    "Refreshing local data").
12. **Finish** — mode, caches, Varnish and search configured; a summary with links and the commands
    you'll use day to day.

**Git identity and login.** A clone over HTTPS asks for a GitHub username and a personal access
token, used for that clone only — or, if you tick **Remember**, saved for **this repo only** in
`magento/.git/credentials` (0600) through a repo-local credential helper. The **name and email for
commits in this repo** come from that same GitHub account (its display name, and its primary email
if the token may read it — otherwise its public email, or GitHub's `…@users.noreply.github.com`
address for it, which still credits the commits to the account) and are saved in
`magento/.git/config`. They're only asked for when there's no token to ask GitHub with (an SSH
clone, a login your system already had, or an existing `magento/`). Nothing goes into your global
git config, so a company store and a personal one on the same machine never share an identity or a
token; change either on the dashboard's Git tab. Composer credentials go in `magento/auth.json`
(repo.magento.com keys, plus any private repos composer.json declares).

**Production URLs.** Importing a production dump needs no production facts from you: the refresh
reads the live site's URLs and cookie domain from the dump itself (see "Refreshing local data") and
saves them as `PROD_BASE_URL` / `PROD_MEDIA_URL` / `PROD_COOKIE_DOMAIN` in `.env` for reference.

**URL overrides** (optional, in `.env`) — leave them blank to keep Magento's own defaults (media and
static URLs derived from `base_url`; cookie domain left to the current host). The refresh reapplies
them on every run, since a reimport would reset them:
```
BASE_MEDIA_URL=
BASE_STATIC_URL=
COOKIE_DOMAIN=
```

**S3 media** (optional). Choosing Amazon S3 in step 11 asks for an AWS profile (from `~/.aws`, or a
new one) and a bucket, checks both work, and writes `<instance>/.envrc` with the profile **name**
only — the keys stay in `~/.aws/credentials`. See "S3-backed media" below.

**After setup — the only steps you do yourself:**

1. **Hosts file** (Windows — the browser resolves names there, not in WSL): add one line per
   instance to `C:\Windows\System32\drivers\etc\hosts` as Administrator; they all point at the
   shared edge proxy (see "Running several stacks at once"). The finish screen shows the exact line:
   ```
   127.0.0.1 m249.test
   ```
2. **Trust the local certificate authority** in a browser running outside WSL (e.g. Chrome on
   Windows): open `\\wsl$\<distro>\home\<user>\.local\share\mkcert\rootCA.pem` in Explorer →
   **Install Certificate** → **Local Machine** → **Trusted Root Certification Authorities**, then
   restart the browser. Once per machine: the authority is one per user (its key readable only by
   you), and every instance's certificate is signed by it.
3. **Check it:**
   - `https://<APP_DOMAIN>/` — the storefront;
   - `https://<APP_DOMAIN>/<admin path>/` — the admin (for an imported database, its own admin
     accounts);
   - `http://localhost:<MAILPIT_HTTP_PORT>/` — Mailpit (setup's summary prints the port).

### Fresh Magento install (vanilla, no data import)

Choosing **Download a new, empty Magento store** in setup's "Magento source" step downloads a
brand-new Magento with `composer create-project` instead of cloning a repository or importing a
database:

1. Pick the **version** and **security patch** — menus are built from the folders in
   [`m2/`](m2/README.md) (`m2/2.4.8/`, `m2/2.4.8/p1` … `p5`, `m2/2.4.9/`, …). Adding a folder adds a
   menu entry; each holds a `version.env` (`MAGENTO_VERSION`, optional `MAGENTO_PACKAGE` and stack
   pins) and optionally an executable `post-install` hook for version-specific commands. A newer
   patch Adobe has published but `m2/` doesn't have yet is offered too — and pre-selected as the
   latest — when the stack versions of the newest catalogued patch on its line pass Adobe's
   requirements for it (see "New releases" in [`m2/README.md`](m2/README.md)).
2. Give your **repo.magento.com credentials** — an existing `auth.json`, or the public/private keys.
   They're kept outside `magento/` until the download finishes (create-project needs an empty
   directory), then saved to `magento/auth.json`.
3. The rest of the wizard runs as usual (project config, DB credentials, mode/ownership, TLS,
   Docker). Skipped for a fresh install: the database import and the S3 data sync — `mdk refresh`
   isn't run.
4. After the stack is up: `create-project` → `composer install` → **admin account prompts**
   (name, email, username, password — 7+ chars with letters and digits; the password is never
   saved) and a **Luma sample data** y/N question → `setup:install` → sample data (if chosen) →
   `m2/<version>/post-install` hooks →
   the same Magento configuration `mdk refresh` applies (OpenSearch, Varnish FPC, storage, URL
   overrides, admin path; shared via `bin/lib/magento-config-engine.sh`) → `mdk mode <mode>`.

`magento/` must be empty (setup offers to clear it). The chosen package/version are recorded in
`.env` (`MAGENTO_PACKAGE`, `MAGENTO_VERSION`).

### Refreshing local data

**In the browser:** `mdk refresh-wizard` runs exactly what `mdk refresh` runs, as a
step-by-step page: start the stack if it's stopped (the usual state after a reboot) → choose the dump
(**upload** it, pick a file on this computer — Windows paths like `C:\Users\…\dump.sql.gz` work — or
paste a URL) → the same questions as below → **type the project name** to confirm → a live checklist
of the import and rebuild → links to the site. See "Web wizards" below.

**Backups:** before `DROP DATABASE`, the current database is saved to
`db/backups/<APP_NAME>-<timestamp>.sql.gz` (newest 3 kept; asked as "Back up the current database
before replacing it?", default yes — terminal and browser). To restore one, refresh from that file
and answer **No** to "still contains production configuration".

`mdk sync-db` runs only the database import; `mdk refresh` runs the import and then the full
Magento rebuild. Both take the instance's lock, so they never overlap a setup, another refresh or
a dashboard action. The work itself lives under `bin/lib/` (`sync-db-config-engine.sh`,
`db-refresh-engine.sh`, `refresh-engine.sh`).

- **`mdk sync-db`** asks, on every standalone run (setup answers these itself when it drives the
  import):
  - the **database source** — a local file path or an HTTP(S) URL (fetched with `curl`): a
    teammate's link, an RDS export, another project's dump. It's required: there's no default and no
    silent fall-back to S3, and no presigned link is ever generated or shown (one is a temporary
    credential for the dump). Without a terminal, a blank answer stops the run;
  - whether the dump is a **copy of production** (its URLs are then rewritten, see below);
  - **this site's** domain and optional media/static URL overrides, written back to `.env`.
    Changing the domain here doesn't regenerate the certificate or `nginx/default.conf` — re-run
    `mdk setup-wizard` for that;
  - **media storage** (local disk or S3, defaulting to the current `REMOTE_STORAGE_DRIVER`) — S3
    goes through the same profile and bucket checks as setup, and the change reaches Magento in the
    same `mdk refresh`;
  - optional **tables whose data to strip** (comma-separated; table names only — letters, digits,
    `_`).

  Then it strips `DEFINER` clauses, rewrites the URLs, drops and reimports the database, and points
  OpenSearch at the local container (a production import would otherwise leave it pointing at the
  production search host).
- **Where the dump came from is read from the dump.** Right after the dump is checked, the refresh
  reads its `core_config_data` (`bin/lib/dump-config.py`, a few seconds even for a multi-GB dump —
  it stops once that table is past) and logs it: "The dump is from https://www.example.com/ (media:
  https://cdn.example.com/media/; cookie domain: .example.com)". It then rewrites **every** one of
  those to this site: the unsecure and secure base URLs (they can differ), the link URLs, the media
  URLs (a CDN included), and the cookie domain when there is one. Nothing is typed in, so nothing
  can be mistyped.
  - **You check the rewrite before it runs.** The refresh lists every change as "from → to" (store
    URLs, media URLs, cookie domain) plus the website/store-view URLs it leaves alone, and asks
    "Rewrite the dump's URLs like this?". No stops the run with nothing backed up, dropped or
    saved. A run with nobody to answer only logs the list (it got that far because the replace
    was confirmed in advance with `PRESET_REPLACE_CHOICE=Y`). The setup review already shows the
    site a local dump file is from; a dump given as a URL is read after its download.
  - Once confirmed, the values are saved to `.env` as `PROD_*`. A later dump that doesn't carry them (no
    `core_config_data`) falls back to those; with none saved either, the refresh asks — and stops if
    nobody can answer.
  - A dump that already uses this site's domain is imported without rewriting.
  - Websites or store views with **their own URLs** (multi-site stores) are listed with a warning:
    they keep their production values, and the log shows the `mdk magento config:set --scope=…`
    command to point them at this site.
- **Replacing the database always needs a yes.** On the refresh page you type the project name; in
  a terminal or on the setup page you answer the question. With nobody to answer (no terminal, no
  page) the run stops before anything is dropped — a script can confirm in advance with
  `PRESET_REPLACE_CHOICE=Y`. If the current database can't even be checked (DB down, wrong
  password), the run stops too: an error is never read as "the database is empty".
- **`mdk refresh`** — the full local refresh pipeline: `composer install` → `sync-db` →
  repoint OpenSearch → reapply Varnish full-page-cache config (see below) → disable
  DB media storage → configure remote storage (see below) → `deploy:mode:set $MAGENTO_MODE` →
  `setup:upgrade` → `setup:di:compile` → `setup:static-content:deploy -f` → `indexer:reindex` →
  `cache:flush` → `maintenance:disable` (safety net in case an interrupted step left the site in
  maintenance mode).

### Switching Magento versions (upgrade / roll back)

`mdk version` (terminal) or `mdk version-wizard` (browser) changes the installed Magento
version — for a fresh install and an imported store alike. The current version is read from
`magento/composer.lock` and recorded as `MAGENTO_VERSION` in `.env`.

- **Upgrade** — to any newer version or patch in the `m2/` catalog, plus newer patches Adobe has
  published that pass the same check as in setup (labelled "new: versions from …"). Review screen (stack changes
  included) → typed confirmation (browser) → **snapshot** → maintenance on → the target's stack
  pins (PHP, MariaDB, OpenSearch, … — containers rebuilt when they change; a `config.platform.php`
  pin in composer.json moves along with PHP) → `composer require-commerce` (or `composer require`
  without the root-update plugin) `magento/product-<edition>-edition <version> --no-update` →
  `composer update` → the same rebuild `mdk refresh` runs (`setup:upgrade`, compile, static deploy,
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
are kept (`VERSION_SNAPSHOTS_KEEP` in `.env` to change that). Any database changes made
after a snapshot are lost when you roll back to it.

Without a terminal, preset the answers: `PRESET_VERSION_ACTION=upgrade PRESET_TARGET_VERSION=2.4.9
mdk version`, or `PRESET_VERSION_ACTION=rollback PRESET_SNAPSHOT=<snapshot folder name> mdk version`.

### Deploying to EC2 (pipeline kit)

The **pipeline wizard** (`mdk pipeline-wizard`, or **Make pipeline-ready** on the dashboard's
Pipeline tab) makes an instance's Magento repo deployable to an EC2 box. It asks for a target and
writes the kit into the repo; you push it with the store.

**What it asks for** (saved as `PIPELINE_*` in the instance `.env` and offered as the defaults
next time):

- the environment name (`dev`, `staging`, …) and the store hostname
- which of MageDevKit's AWS connections (`mdk aws`) reaches the account, the region and the hosted
  zone (picked from AWS when the connection works). The kit records the account number, never a
  connection name
- the instance size, the branch that deploys, and the GitHub connection
- the Magento mode, and whether to stop the box outside working hours
- whether it is **production**. Anything else runs on a copy of live data, so the box locks outgoing
  email off, asks search engines not to index it, and puts PayPal and Braintree in sandbox mode
- optionally, an S3 bucket of database dumps the box may load

The box's Ubuntu image is looked up once and then kept: a different image would replace the box,
and its database with it.

**What it writes:**

| Path | What |
|---|---|
| `magento/.platform/stack.env` | the version pins: the same values as the instance's `.env` |
| `magento/deploy/` | `buildspec.yml`, `appspec.yml`, the deploy scripts, the box's compose files, the php/nginx images and service config (copied from this repo, so local and deployed match) |
| `magento/deploy/infra/` | `stack.json`, a CloudFormation template for the AWS side (one stack per environment), and `<env>.params.json` with that environment's values. No secrets: those go to SSM, written by the provision wizard |
| `magento/deploy/targets/` | `<env>.env` (settings) and `<env>.config-overrides.php`, written once and then yours: your own `env.php` additions for that environment, such as switching off an ERP connector |
| `magento/deploy/pipeline.json` | the kit version, the targets and a checksum of every file it wrote |

It also removes the old hand-made root `buildspec.yml`, `appspec.yml` and `scripts/*.sh` if they
exist.

**Before anything is committed**, the wizard checks the kit:

- `bash -n` on every script
- `docker compose config` on the box's stack
- the CloudFormation template (`bin/lib/cfn-check.py`): every reference, parameter and resource
  type, and its size; `cfn-lint` too when it is installed, and AWS's own `validate-template` when
  a connection works (read-only)
- every file it is about to commit, for anything that looks like a credential
  (`bin/lib/secret-scan.py`)
- optionally, a release build exactly like CodeBuild's: the php image, `composer --no-dev`, DI
  compile and static content

It then commits on a new local branch, `pipeline/<env>-<date>`, and never pushes.

**Running it again:**

- It updates files it wrote earlier.
- A file someone changed by hand is reported and kept, unless you choose to replace it.
- A file an older kit wrote and this one no longer has (the Terraform of kit 2) is removed if it
  is still as generated, and reported and kept if someone edited it.
- Another environment name adds a target, and the other targets' files stay.

The dashboard's **Pipeline** tab shows, for each instance:

- whether the kit is set up, current, outdated or edited by hand
- whether the pins still match the instance
- the targets, and whether the kit is on each target's branch (from the last fetch; it never
  fetches or pushes)

How a deploy runs on AWS is described in the generated `magento/deploy/README.md`.

### Creating the AWS side (provision wizard)

The **provision wizard** (`mdk provision-wizard`, or **Provision** next to a target on the
dashboard's Pipeline tab) creates or updates one environment of the kit in AWS: the
CloudFormation stack `mdk-<app>-<env>` and the environment's secrets. It needs a MageDevKit AWS
connection to the kit's account (`mdk aws add`, or **AWS accounts** on the dashboard), with
admin-level rights. Day-to-day work afterwards only needs the operator policy it creates.

What it does, in order:

1. **Target** — the kit must be current (not outdated, not edited by hand).
2. **Account** — the connection must reach the account the kit records; checked again right
   before anything changes.
3. **Checks**, read-only: the hosted zone, the GitHub connection (approved?), the instance type
   and image in the region, free Elastic IPs and VPCs, the dump bucket.
4. **Secrets** — the database, root and RabbitMQ passwords (generated), Magento's crypt key (new,
   or this instance's) and the repo.magento.com key (from `magento/auth.json`) go into SSM as
   SecureStrings. They are handed to the AWS CLI in a private file, never on a command line or in
   the log, and existing ones are never overwritten.
5. **Review** — a CloudFormation change set: what would be added, changed or removed, and a rough
   monthly cost. Replacing or removing the box (and the database on its disk) needs the typed
   environment name. Stopping here leaves nothing behind.
6. **Apply**, following the stack's events. A production environment gets termination protection;
   an AWS Budgets cost alert is offered.
7. **First release** — the pipeline starts by itself; the wizard follows it. A new environment's
   first release succeeds with an empty database and waits for one.
8. **Database** — pick a dump from the dump bucket; the box loads it (`box refresh`, a dump of the
   current database first).

`mdk provision --destroy ENV` removes an environment: it asks for the typed name (and once more,
`production-ENV`, for production), empties the artifact bucket, deletes the stack, and asks
before deleting the secrets. The dump bucket is never touched.

Scripted runs preset the answers: `PRESET_PROVISION_ENV`, `_CONNECTION`, `_CRYPT_KEY=new|instance`,
`_CONFIRM=<env>`, `_FOLLOW`, `_BUDGET`, `_DUMP=s3://…`, `_DELETE_SECRETS`.

### S3-backed media (Remote Storage)

`mdk refresh` configures Magento's `Magento_RemoteStorage` driver from `.env`'s
`REMOTE_STORAGE_DRIVER` — `aws-s3` serves `pub/media` directly from the configured
S3 bucket (`REMOTE_STORAGE_BUCKET`), via `setup:config:set --remote-storage-driver=aws-s3 ...`; `file`
disables it and serves `pub/media` from local disk instead (the default when no bucket is set).
There is no default bucket; region defaults to `us-east-2` (`bin/remote-storage.sh`);
`REMOTE_STORAGE_KEY`/`REMOTE_STORAGE_SECRET` are always per-machine, from `.env`
(never committed). `mdk setup` prompts for all of this once and remembers the answer on future
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

**What configures it:**
- **Database settings** (`system/full_page_cache/…`: Varnish as the cache, its access list,
  backend `nginx:8080`, grace period) — applied by setup and reapplied by every `mdk refresh`
  (`bin/lib/magento-config-engine.sh`), since a database import replaces them.
- **Two `app/etc/env.php` settings** — where Magento sends PURGE requests
  (`--http-cache-hosts=varnish:80`; the admin's Varnish fields only feed "Export VCL") and the
  `full_page` cache type being on. Setup sets both for a **fresh download**. For a **cloned
  store**, run them once (an import never touches `env.php`, so they stay set):
  ```bash
  mdk magento setup:config:set --http-cache-hosts="varnish:80" -n
  mdk magento cache:enable full_page
  ```

**Verify it's working:**
```bash
curl -Ik https://<APP_DOMAIN>/
```
- First request: `X-Magento-Cache-Debug: MISS`. Second request to the same URL: `HIT`.
- After `mdk magento cache:flush` (or saving a product/CMS page in admin), the next request
  should show `MISS` again — this confirms the live PHP → Varnish PURGE is actually reaching
  Varnish, not just clearing Magento's own cache pool.
- `/static/` and `/media/` requests will show `X-Magento-Cache-Debug: UNCACHEABLE` — expected,
  Varnish deliberately passes these straight through (nginx already serves/caches them via its
  own `Cache-Control`/`expires` headers; no double-caching).

**Debugging:**
```bash
mdk platform exec varnish varnishlog    # live request/cache-decision trace
mdk platform exec varnish varnishstat   # hit/miss counters
mdk platform exec varnish varnishadm backend.list   # confirm backend shows "healthy"
```
If `backend.list` shows the backend as sick, check `mdk platform logs nginx` for
`/health_check.php` requests returning non-200 — Varnish's backend probe hits that path on
`nginx:8080` every 5s and marks the backend down after repeated failures.


### Magento mode & file permissions

`mdk setup` asks two things on every run (current values as defaults), stored in
`.env`:

1. **`MAGENTO_MODE`** — `default`, `developer`, or `production`. `mdk refresh` applies it
   (`deploy:mode:set`; production is set after compiling, via `--skip-compilation`). When setup runs
   without a data refresh it switches the mode itself.
2. **`FILE_OWNERSHIP`** — who php-fpm runs as, per Adobe's
   [file-system-permissions guide](https://experienceleague.adobe.com/en/docs/commerce-operations/configuration-guide/deployment/file-system-permissions):

| | php-fpm runs as | CLI (`mdk magento`, `mdk composer`, refresh) | Writable dirs (`var generated pub/static pub/media app/etc`) |
|---|---|---|---|
| `single` (default) | you (`$(id -u):$(id -g)`) | you | owned by you, `u+w` — Adobe's "one owner" |
| `two-owner` | `www-data` (33:33) | you, group `www-data` (`$(id -u):33`) | `you:www-data`, dirs setgid `2770`/`2775`, files `660`/`664` — Adobe's "two owners" |

`bin/lib/identity.sh` resolves this for every wrapper (it exports `PHP_UID`/`PHP_GID` for
`docker-compose.local.yml`'s php `user:` and sets `EXEC_USER`), so switching models just needs
`mdk platform up -d` to recreate php — setup does that. `two-owner` needs `sudo` once: setup adds you
to the host's `www-data` group (open a new shell, or `wsl --shutdown` from Windows, for your
editor/shell to pick it up) and chowns the writable dirs. Magento's own bootstrap uses umask `002`,
so files either side creates stay group-writable.

**Production lock (both models).** In production mode, `mdk refresh` (and setup) end with
`mdk permissions lock`: write access (`u-w` for single, `g-w` for two-owner) is removed from
everything except `var/`, `generated/`, `pub/media/` and `.git/`, then `var/view_preprocessed`,
`generated/code` and `generated/metadata` are re-locked. `app/etc/env.php` always loses all
"other" access. `mdk refresh` always runs `unlock` + `apply` first, so a locked tree never blocks it.

#### Changing your mind later

Each choice has its own command — no need to re-run the whole setup, and every one is reversible
(it unlocks and restores ownership first, then re-locks only if the result is production):

```bash
mdk mode show                 # configured vs. actual mode, and the ownership model
mdk mode developer            # switch mode (also: mdk mode default, mdk mode production)
mdk ownership two-owner       # switch ownership (also: mdk ownership single) — recreates php
mdk permissions unlock        # make code writable (e.g. composer by hand in production mode)
mdk permissions lock          # lock it again
mdk permissions apply         # re-apply ownership/permissions for the current model
```

The logic lives in `bin/lib/mode-engine.sh` and `bin/lib/permissions-engine.sh`; the scripts in
`bin/mode/`, `bin/ownership/` and `bin/permissions/` are one-line entry points. Switching mode by
hand doesn't re-run a data refresh — `mdk mode production` uses Magento's own
`deploy:mode:set production` (which compiles and deploys static content), then locks.

## Web wizards

Every flow has a browser version — a local page that asks the same questions as the terminal and
shows the run as it happens:

| Wizard | Command | Runs |
|---|---|---|
| Setup | `mdk setup-wizard` | `bin/local/setup` |
| Refresh database | `mdk refresh-wizard` | `bin/local/refresh` |
| Switch Magento version | `mdk version-wizard` | `bin/local/version` |
| Make pipeline-ready | `mdk pipeline-wizard` | `bin/local/pipeline` |
| Rebuild Magento | `mdk rebuild-wizard` | `bin/local/rebuild` |
| Switch file ownership | dashboard → Magento → File ownership | `bin/ownership/<model>` (asks for the sudo password on the page) |

Each page has numbered steps in the sidebar with a progress bar, one question at a time, a live
checklist of what's running, and a finish screen with links, the Windows hosts-file line and
everyday commands. Options: `--no-browser` (print the link only), `--port N`.

- **Review before anything heavy.** Setup, version, pipeline and rebuild stop at a review screen;
  the refresh asks you to **type the project name** before the database is replaced.
- **Change answers** on the review (or the typed confirmation) starts the same wizard again on the
  same page, with your answers so far as the defaults — nothing heavy has run yet.
- **Cancel** stops at a question immediately, or after the current task ("stop now" is not offered
  while the database is being replaced). A run you stopped — or a "No" the flow can't go on
  without — shows as **Cancelled**, not as Failed.
- **Run again** is offered after a failure or a cancel; **Close wizard** ends the wizard and closes
  the tab.

**Same wizard, two front ends.** Every question and progress line comes from `bin/lib/ui.sh`
(`prompt_value`, `prompt_menu`, `prompt_yes_no`, `prompt`, `ui_step`, `ui_task`, …). In the terminal
they behave as they always did; under the wizard (`SETUP_UI=web`) they send the question to the page
instead. So the page asks exactly what the terminal asks — to add a question, add it to the bash
script; to add a new *kind* of question, add it to `bin/lib/ui.sh` (and render it in
`bin/lib/web-wizard/ui/app.js`). Steps are listed once per flow, in `UI_STEPS_setup`,
`UI_STEPS_refresh`, `UI_STEPS_version`, `UI_STEPS_pipeline`, `UI_STEPS_rebuild` and
`UI_STEPS_ownership`.

**How it runs.** `bin/lib/web-wizard/launch.sh` starts `server.py` (Python standard library only),
which runs the bash script as a child and relays questions/answers through two FIFOs. The page is
plain HTML + vanilla JS styled with Tailwind v4 using shadcn/ui's tokens and component recipes;
`ui/wizard.css` is precompiled and committed (`bin/lib/web-wizard/build-css` rebuilds it with the
Tailwind standalone CLI — no Node, checksum-verified on download — after changing classes). No CDN,
so it works offline.

**Safe to use:**
- listens on `127.0.0.1` only; the link carries a token (reusable while the wizard runs) that becomes an HttpOnly,
  SameSite=Strict cookie; wrong `Host` headers (DNS rebinding) and requests without the
  `X-Wizard` header (CSRF) are refused; malformed requests get a 400, never a crash;
- the token is never passed on to the script or anything it runs;
- passwords and keys are never stored by the page or written to the log (the server also redacts
  any submitted password from the log as a backstop); the review screen masks them;
- refreshing, closing or going "back" in the browser never stops or rewinds anything — the script
  runs in the terminal; reopen the link to continue. Closing the **terminal** stops it, and Ctrl+C
  needs to be pressed twice;
- one wizard at a time per instance (`.wizard.lock`, terminal runs and dashboard actions included);
- each run's log is saved to `logs/<flow>-<timestamp>.log` (`<flow>-<n>-…` for a second run in the
  same second), readable only by you, and its outcome to `logs/operations/<id>.json` (no secrets)
  for the dashboard;
- the login cookie is named after the port (`wiz_<port>`) and lasts for the browser session, so
  several wizards (and the dashboard) open at once don't log each other out.

The page's DOM helpers, icons and component recipes live in `ui/kit.js`, shared with the dashboard;
`ui/app.js` is the wizard itself.

## Dashboard

```bash
mdk dashboard                 # opens http://127.0.0.1:<port>/?token=… in your browser
mdk dashboard --no-browser    # print the link only
mdk dashboard --port 8800     # fixed port (default: the one it used last time)
mdk dashboard --scan ~/Sites --scan /srv/stores   # extra folders to look for instances in
```

To stop it, use the **Turn off** button (the power icon at the top of the page) or press Ctrl+C in
its terminal. Running operations and open wizard pages keep going either way.

One persistent page for **every instance on this machine**, laid out like
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
    - **Magento releases**: the installed version, the latest release, and the latest one this
      environment can move to, with every newer release marked **Available**, **Available · PHP x → y**
      (the version wizard moves composer.json's `config.platform.php` pin along, so extensions must
      support the new PHP) or **Not in the wizard yet** (no catalog line, or its stack check failed).
      Info boxes say how the upgrade goes and how to add a missing release (`bin/m2/check-requirements`,
      then an `m2/` folder). The list is Adobe's system requirements file,
      checked in the background at most once a day; **Check now** checks right away. Offline it
      shows the last copy, or the `m2/` catalog alone. When a newer compatible release exists, the
      environment page shows a **"Magento X is available"** banner (dismissed per version, in this
      browser), and the overview card a badge. Upgrading always goes through the version wizard —
      the dashboard never starts one by itself;
  - **Modules**: every module from `app/etc/config.php`, with its status, package, version and
    whether it's Magento core (Magento's own modules and everything the product metapackage
    requires). Search by module or package name, and filter by status and by source — each module is
    in exactly one: **Third-party** (the default), **app/code** or **Magento core**, or show **All
    modules**. 50 a page, with a pager. Read from the files, so the tab works with the stack
    stopped.
    - **Enable / Disable** runs `bin/magento module:enable|disable --clear-static-content` (never
      `--force`, so Magento's own dependency check still refuses a change that would break another
      module). The dialog first lists the enabled modules that depend on it, or the disabled ones
      it needs.
    - **Build Magento afterwards** is ticked by default: the change then runs inside the same build
      as Build Magento. Unticked, only the module list changes. In production mode it can't be
      unticked — the code is locked and compiled there.
    - **Check for updates** runs `composer outdated --direct --all` and saves the result in
      `logs/module-updates.json`. Each module then shows one update status: an update (with
      **Major** when it's a new major version), up to date, can't check (with the reason — no check
      yet, the check failed, or the package comes with another one), not updatable (`app/code`), or
      version wizard (Magento's own packages, which move with Magento). A failed check shows
      composer's message; private repositories need their credentials in `magento/auth.json`.
    - **Update** (one package) or **Update selected**: a review of installed → latest, then a
      **dry run** (`composer require --dry-run --update-with-dependencies`) that resolves everything
      and changes nothing. Only after it passes does **Apply update** run the same command for real,
      followed by the build. Only those packages and their own dependencies move — never a full
      `composer update`. Minor and patch updates keep composer.json's constraint; a major one sets
      it to `^<latest>`, and a bulk update leaves majors out unless **Include major versions** is
      ticked. If composer fails, `composer.json` and `composer.lock` are put back and
      `composer install` runs. If composer succeeds but the build fails, the new `composer.lock` is
      kept and the message says to run Build Magento again. Unticking the build (developer/default
      mode) runs only `setup:upgrade` and `cache:flush`.
    - **Select** rows (the header box selects every matching row on every page, never core
      modules) for **Update selected**, **Enable selected** and **Disable selected**: one composer
      run, or one `module:enable|disable` call, for all of them (at most 100).
    - Not covered: `app/code` modules (no package), packages composer.json doesn't require directly
      (`--direct`), and Adobe Commerce packages without a repo.magento.com entitlement;
  - **Workflows**: the setup, refresh and version wizards (the pipeline, rebuild and ownership
    wizards open from the Pipeline and Magento tabs);
  - **Pipeline**: whether `magento/` is pipeline-ready (see "Deploying to EC2");
  - **Git** (`magento/`):
    - branches: switch, create a new one, or delete a local one. Delete never touches origin; a branch
      with commits that exist nowhere else is only deleted after you confirm how many would be lost.
      To work on a branch from origin, pick it (marked "remote") and **Switch** — you get a local
      copy that tracks it. Other local branches that are behind origin are listed with **Update from
      origin**, which fast-forwards them without switching (`git fetch origin <branch>:<branch>`; no
      files change);
    - changed files in two columns, Unstaged and Staged. Drag files between them, or use the arrows,
      to stage and unstage (`git add` / `git restore --staged`). A file with both kinds of changes
      shows in both columns as "partly staged", and what `.gitignore` keeps out is listed under
      Ignored. You can also view a file's diff or discard its changes (asks first);
    - commit: **Commit (N)** appears once files are staged and opens a dialog for the message;
    - fetch, pull (always available on a branch that tracks origin; fast-forward only) and push, with
      ahead/behind origin;
    - recent commits, with the ones not pushed yet marked.

    **GitHub access and commit author are separate.** **GitHub access** shows "Signed in as
    <username>" when this repo has a saved login — then Fetch just runs, and Pull/Push only confirm
    what they do. **Sign in** / **Use a different login** asks for a GitHub username and personal
    access token and checks it with a fetch; with **Remember it for this repo** (ticked by default)
    git saves it — only once GitHub accepts it — in `magento/.git/credentials` (readable only by
    you). Unticked, it's used for that one command and never saved or logged. **Forget** removes it.
    If GitHub rejects the saved login, the tab says so and the next command asks again. **Commit
    author** is only the name and email written on your commits (**Edit** saves them in
    `magento/.git/config`, and a commit without one asks first) — not a login. Your global git config
    is never touched, so each store can use a different GitHub account. The setup wizard's GitHub
    clone works the same way. Nothing here force-pushes, rebases or merges;
  - **Logs**: container logs, Magento `var/log`, platform `logs/`;
  - **Overview → Danger zone → Destroy instance**:
    - removes the containers, all data volumes and the whole instance folder;
    - first shows what would be lost, including uncommitted or unpushed work in `magento/`, which
      has to be confirmed;
    - asks you to type the instance name;
    - the hosts-file line on Windows is left for you to remove;
  - **Operations**: history, running state, and Reopen/Cancel.

It runs until Ctrl+C, and only one runs per user: running `mdk dashboard` again just prints the
existing link. Operations it started keep running after it stops.

The terminal commands (`mdk setup`, `mdk refresh-wizard`, …) work exactly the same without it; the
dashboard only calls them.

### How it works

```
browser ──> dashboard.py   (127.0.0.1 only; status + JSON API)
              ├─ envs.py    discovery + status: docker inspect, env files, git, /health_check.php
              ├─ ops.py     whitelisted actions and workflows, operation history
              │    ├─ locks.py    the instance lock (.wizard.lock)
              │    ├─ oplogs.py   log tails + redaction
              │    ├─ destroy.py  deleting an instance
              │    └─ oprun.py    detached runner: one operation, its log and record
              ├─ gitrepo.py the Git tab        pipeline.py  the Pipeline tab
              ├─ modules.py the Modules tab (reads config.php, module.xml, composer.lock)
              ├─ releases.py Magento releases from Adobe (shared with the wizards' version menus)
              ├─ awsaccounts.py the AWS accounts page: MageDevKit's own AWS connections
              │                 (bin/lib/aws-credentials.py, aws-profiles.py; never ~/.aws)
              └─ webcore.py shared with the wizard server: auth, login/static files, headers,
                            redaction, records, child-process environment (no tokens, no AWS logins)
                    │
                    ▼
<repo>/bin/platform | bin/magento | bin/composer | bin/mode/* | bin/local/* | bin/lib/web-wizard/launch.sh
   (run with PLATFORM_INSTANCE_DIR=<instance>; the AWS connection and provision wizards included)
```

- **Discovery.** There is no registry and nothing to keep up to date. The dashboard combines two
  sources:
  - **Docker labels** on every container, running or stopped. The compose project
    (`<APP_NAME>-platform`) says which stacks exist, and `platform.instance_dir` says which folder
    each belongs to.
  - **A folder scan**, two levels deep, for environments with no containers yet. The default is
    the folder holding the repo (e.g. `~/Sites`); change it with `--scan`.

  An instance is a folder with `.platform-instance` and `.env`. Its `APP_NAME` is its id: `mdk new`
  and setup keep that unique. An instance whose `APP_NAME` doesn't match its folder name, or whose
  platform repo has gone, is listed read-only. Values come from its `.env`; anything that can't be
  determined is shown as **Unknown**.
- **Health.**
  - Container state and Docker healthchecks. Nginx, PHP-FPM, Varnish and Mailpit have no
    healthcheck and show "Running (no healthcheck)".
  - OpenSearch's cluster colour comes from its healthcheck output.
  - **Magento** is checked separately with a real request to `https://<APP_DOMAIN>/health_check.php`
    through the edge proxy. A running container doesn't mean a working store.
- **Actions run this repo's scripts for the instance.** Each action is a fixed command line in
  `ops.ACTIONS`:
  - `bin/platform up -d`, `stop`, `restart [service]`;
  - `bin/magento cache:flush`, `cache:clean`, `indexer:status`, `indexer:reindex`;
  - `bin/mode/<mode>` and `bin/mode/show`;
  - `bin/local/magento-build [--composer install|update [packages]]` (Build Magento, and the
    composer buttons);
  - `bin/local/magento-module enable|disable [--build] modules` (the Modules tab; names are checked
    against `app/etc/config.php`, at most 100);
  - `bin/local/magento-module check-updates` and `update [--dry-run] [--quick] package:constraint …`
    (the constraints are made by the server from the last check and composer.json — the page only
    names packages, and a package without an update there, Magento's own or `app/code`, is refused);
  - `bin/composer validate` (plus `install` / `update [packages]`, kept in the API but not on a
    button).

  There is no shell, and no free-form input reaches a command line. Each action runs detached
  (`oprun.py`), so closing the page or restarting the dashboard doesn't stop it. The dashboard
  first removes from the environment anything that could point a script at another stack: every
  env-file key, `COMPOSE_*`, `WIZARD_*`, `PRESET_*` — and never passes on its own access token.
  - **Stop** keeps containers; it never runs `down`.
  - **Start** is always `up -d`, so ports and the edge proxy are re-checked.
  - **Cancel** is offered only for start/stop/restart and the Git tab's fetch/pull/push. Commands
    that run inside the php container keep running there even if the client is stopped, so they
    always run to the end.
- **Workflows run the instance's wizard.** Setup, Refresh, Version, Pipeline, Rebuild and
  Ownership start `bin/lib/web-wizard/launch.sh <flow> --no-browser` for that instance and open its page in a new
  tab. Its questions, uploads, progress, typed confirmations and Cancel are unchanged. Reopen it
  from **Operations** while it runs.
- **Locks.** Each instance's `.wizard.lock` is the same lock setup/refresh/version take. Dashboard
  actions that change something take it too, and are refused (with the holder's PID) while a
  wizard or another action runs in that instance. Read-only views and other instances are never
  blocked.
- **Files it writes** (operation logs, records, one-time git logins) are readable only by you.
- **History.** Each wizard run and dashboard action writes `logs/operations/<id>.json` with its
  status, step, exit code and log file. On every view the dashboard checks the recorded process
  against `/proc`, so a run whose process vanished shows **Interrupted** instead of "running"
  forever. Terminal runs don't write a record; their `logs/*.log` files are listed as "Earlier
  runs".
- **Logs** are always redacted before they reach the page:
  - the environment's own secrets: env-file passwords and keys, the `env.php` crypt key and DB
    password, every `auth.json` credential;
  - generic patterns: `password=…`, AWS access keys, bearer tokens, credentials in URLs, signed
    URLs, tokens.

  Literal values shorter than 8 characters, such as the stock `magento` passwords, are only
  caught by those patterns.

### Adding an action

1. Add an entry to `ACTIONS` in `bin/lib/web-wizard/ops.py`:
   - `label` and `argv`: a fixed command, relative to this repo, using its wrappers;
   - `mutating: True` if it changes anything, so it takes the instance lock;
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
against fake instances and a fake `docker`, covering:
- auth, Host and CSRF checks;
- unknown ids, path traversal and injection attempts;
- environment scrubbing and lock handling;
- redaction;
- operation records;
- the bash helpers that guard input (`tests/test_bash_helpers.py`: `set_env_var`, `env_get`,
  sed literals, hostnames, live-site URLs, composer arguments, backup pruning, the S3 storage flow,
  per-repo git logins and identities, the refresh's "never replace without a yes" rule);
- the Modules tab's data (`tests/test_modules.py`: module states, update statuses, which updates
  may run) and `bin/local/magento-module` (`tests/test_magento_module.py`: the command order with
  and without the build, production, the update check, dry runs, and putting composer.json and
  composer.lock back when composer fails);
- out-of-date compiled code (`tests/test_generated_stale.py`: what counts as stale, and what
  `bin/magento` does about it in each mode) and Build Magento's order (`tests/test_magento_build.py`:
  `generated/` cleared before the first `bin/magento` call);
- Magento releases (`tests/test_releases.py`: Adobe's file, the stack check behind a derived
  entry, offline fallback, what an environment can move to);
- MageDevKit's AWS connections (`tests/test_aws_account.py`: keys only in MageDevKit's own
  files, `~/.aws` never changed, import of one profile, SSO and role connections, status checks
  that ignore AWS keys exported in the shell, the wrong account refused, the AWS accounts page);
- the pipeline kit's CloudFormation template (`tests/test_cfn_check.py`: the offline checker,
  per-environment image repositories, the box document's allow-list, an operator policy without
  IAM or a shell, no secret parameters) and its scripts on the box (`tests/test_box.py`: refused
  arguments, the published status, the non-production safety settings in `env.php`);
- the pipeline wizard (`tests/test_pipeline.py`: generating, committing, hand edits, upgrading
  from an older kit, production or not, no credentials in a commit);
- the provision wizard against a fake `aws` (`tests/test_provision.py`: creating, keeping secrets,
  secrets never on a command line or in the log, the wrong account refused, the typed name before
  the box could be replaced, removing an environment) and the dashboard's Provision workflow
  (`tests/test_ops_provision.py`). No test ever calls the real AWS CLI;
- `kit.js` and the dashboard's tabs in a real browser (`tests/test_js.py` runs
  `tests/js/kit-tests.html` and `tests/js/dash-tests.html` in headless Chrome/Chromium; skipped
  when none is installed).

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
  answer without someone there to see the question (`ui_interactive`); a scripted run confirms in
  advance with a `PRESET_*` answer (e.g. `PRESET_REPLACE_CHOICE=Y`).
- Git is per repo: commit identity in `magento/.git/config`, a remembered login in
  `magento/.git/credentials` (`bin/lib/git-repo.sh`, `gitrepo.py`). Nothing writes the global git
  config.
- Tools downloaded for you are verified first: pinned SHA-256 for mkcert, direnv and Tailwind;
  AWS's PGP signature (`bin/lib/keys/aws-cli.asc`) for the AWS CLI. Optional tools (`cfn-lint`)
  are used when present and never installed for you.
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

Nginx terminates TLS for the instance's `APP_DOMAIN` using a locally-generated
mkcert certificate (see "Local development setup" above). Nginx sits in front of Varnish (TLS
termination on 443, proxies to Varnish on plain HTTP), and behind it (internal-only port 8080, the
actual app-serving logic) — see "Varnish full-page cache" above.

### Running several stacks at once

Every instance (2.4.8, 2.4.9, …) can run at the same time; nothing is hardcoded
per machine:

- **80/443** belong to a shared edge proxy, not to any stack — `compose/edge/docker-compose.yml`
  (Traefik, compose project `platform-edge`), started automatically by `mdk platform up` and never
  stopped by `mdk platform down`. It routes by hostname over the external `platform-edge` network to
  each stack's nginx: 443 is TLS passthrough on SNI (nginx still serves its own mkcert cert), 80 is
  plain HTTP on `Host`. So every site keeps a port-less `https://<APP_DOMAIN>/`, and each just needs
  its own `127.0.0.1 <APP_DOMAIN>` hosts-file line. Stop it with
  `docker compose -p platform-edge down` if you ever need 80/443 back.
- **Network** — each stack's `up-network` gets a Docker-assigned subnet (no fixed CIDR).
- **Debug ports** (`DB_PORT`, `VALKEY_PORT`, `RABBITMQ_*_PORT`, `OPENSEARCH_PORT`, `MAILPIT_*_PORT`)
  — `mdk platform up` (`bin/lib/ports.sh`) keeps each value in the instance's `.env` unless another
  stack's container (running *or* stopped) or a host process holds it, in which case it moves to the
  next free port, saves it, and says so. Ports stay stable across restarts.
- **Names** — compose project, volumes and images derive from `APP_NAME` (the instance name:
  lowercase letters, digits, `_` and `-`, at most 40); `mdk new` and setup refuse a name or
  `APP_DOMAIN` another stack already uses.

If you ever run so many stacks that Docker runs out of networks ("could not find an available,
non-overlapping IPv4 address pool"), widen its pools in Docker Desktop → Settings → Docker Engine:
`"default-address-pools": [{"base": "10.200.0.0/16", "size": 24}]`.

## Environment files

| File | Committed? | Used by |
|---|---|---|
| `env/.env.local.example` (this repo) | Yes (no real values) | The template `mdk new` copies into each instance |
| `<instance>/.env` | Never (outside any repo; 0600) | Every `mdk` command for that instance — required; they refuse to run without it. `PROD_*` are written by the DB refresh (read from the dump) |
| `<instance>/.platform-instance` | Never | Marks the folder as an instance and names the platform repo that runs it |

The env file is read with `env_get` and written with `set_env_var` (`bin/lib/env.sh`) — one
`KEY=value` per line, values copied literally. Hand edits are fine; keep to that format.

**Changing DB credentials later:** editing `DB_*` in `.env` after the `${APP_NAME}-db-data`
volume exists does **not** change the live MariaDB users — MariaDB only applies them when it first
initializes the volume, and `app/etc/env.php` keeps its own copy. Either run the matching
`ALTER USER ...` and update `env.php`'s `db.connection.default`, or start over with
`mdk platform down -v` (deletes the database) and re-run `mdk setup-wizard`.

## Changelog

### 1.3.0

**AWS for any store and any account.**
- **AWS connections** (`mdk aws`, dashboard → AWS accounts): MageDevKit's own logins — access keys,
  SSO or a role — kept in `~/.config/magedevkit/aws`, apart from `~/.aws`, from a store's S3 keys
  and from the Magento repo. Each environment is bound to its account number.
- **Pipeline kit 3:** the AWS side is one CloudFormation template (no Terraform, no state bucket)
  with per-environment image repositories, an operator policy for teammates and a box command
  document that runs only allow-listed commands. Non-production environments lock outgoing email
  off, block search engines and use payment sandboxes.
- **Provision wizard** (`mdk provision-wizard`, `mdk provision --destroy`): creates, updates and
  removes an environment in AWS, with secrets straight into SSM, a reviewed change set, typed
  confirmations before anything destructive, and the first release and database load.
- **Safety:** no secret on a command line or in a log; AWS logins never reach a store's scripts;
  the kit is scanned for credentials before it is committed.
- **Dashboard:** **Turn off** button; `mdk dashboard` restarts a dashboard running older code.
- **Out-of-date compiled code no longer blocks a store:** after a module is removed or renamed,
  every `bin/magento` command used to fail with `Class … not found` (Build Magento included).
  `bin/magento` now checks before each command. In developer/default mode it clears the stale
  `generated/` itself; in production mode it warns. Build Magento always starts from a clean
  `generated/`.

### 1.2.0

**Dashboard Git tab: easier pulling and sign-in.**
- **Signed in as …:** with a saved GitHub login, Fetch runs straight away and Pull/Push only confirm;
  the login form appears only when there's no saved login or GitHub rejected it. **Remember** is on
  by default, and the login is saved only once GitHub accepts it.
- **GitHub access** and **Commit author** are separate rows — **Edit** (was "Change") is only the
  name and email on your commits.
- **Pull** is always available on a branch that tracks origin.
- **Update from origin** fast-forwards another local branch without switching to it.
- **Commit** is always shown in the Staged column and enabled once something is staged; **Push N
  commit(s)** also appears on the Recent commits card when commits there aren't on origin yet.

**Running operations.** The dashboard shows the newest running operation — also after a reload, or
when it was started from the terminal, a wizard or another tab (hiding it keeps it hidden). Its log
stays at the bottom while it runs, through every refresh; scroll up to read and it stays put,
scroll back down and it follows again. The Operations tab's log of a running operation updates the
same way.

### 1.1.0 (2026-10-12)

**Renamed: MageRunner is now MageDevKit (Magento Developer Kit), and the `mrn` command is now `mdk`.**
Containers, volumes, data and instance settings are unchanged. To move an existing install:

```bash
mv ~/Sites/magerunner ~/Sites/magedevkit
~/Sites/magedevkit/bin/local/install-mdk   # links mdk, removes the old mrn link, re-points your instances
mdk -i NAME platform up -d                 # per instance: recreates its containers on the new path
git -C ~/Sites/magedevkit remote set-url origin https://github.com/UpSage/magedevkit.git
```

`install-mdk` only re-points instances whose recorded repo folder no longer exists, and lists the
`platform up -d` commands instead of running them. Pipeline kits generated before the rename keep
their old header until the pipeline wizard regenerates them; nothing else reads it.

**The dump's URLs are shown before they're rewritten.** A database refresh now lists every URL and
the cookie domain it is about to rewrite ("from → to") and asks before going on; answering No
changes nothing, `.env` included. The setup review shows which site a local dump file is from.

**Modules tab.** The dashboard lists every Magento module (status, package, version, core or not)
with search and filters, and turns modules on or off — with Magento's own dependency check, and
the Magento build afterwards by default (always in production mode). In a terminal:
`mdk module enable|disable [--build] Vendor_Module …`.

**New Magento releases in the wizards.** Setup's version menu and the version wizard's upgrade
menu also offer patches Adobe has published but `m2/` doesn't have yet (read from Adobe's system
requirements file, at most once a day): with the stack versions of the newest catalogued patch on
that line, only when every one passes Adobe's requirements for the new patch. A folder in `m2/`
always wins; offline the menus are the catalog alone. A new minor line (e.g. 2.4.10) still needs
a catalog folder.

**Release notifications.** The Magento tab has a **Magento releases** card (what this environment
can move to, what a PHP change means, and how to add a release the wizard doesn't offer yet), the environment page a dismissable "Magento X is
available" banner, and the overview card a badge — all pointing at the version wizard.

**Module updates.** **Check for updates** on the Modules tab (`mdk module check-updates`) shows
which packages have a newer version. Update one, or select several: a dry run first, then the
update of just those packages and the Magento build — with composer.json and composer.lock put
back if composer fails.

---

Magento is a trademark of Adobe Inc. MageDevKit is an independent, unofficial project.
