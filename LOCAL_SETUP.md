# Local Development Setup

A complete, from-scratch walkthrough for getting a Magento 2 stack running on your
own machine. Written for a new developer who has never touched this project before — if you
already have everything installed, skip straight to [Step 5](#step-5-clone-the-repositories).

For the permission model, Varnish, S3 remote storage internals, or the full command reference,
see [`README.md`](README.md) — this doc only duplicates what
a first-time setup needs and links back to `README.md` for everything else.

**The quick way (recommended):** once Steps 1–4 are done (WSL2, Docker, git, SSH access), clone
this platform repo once (e.g. to `~/Sites/magerunner`), then create one instance per store:
```bash
cd ~/Sites/magerunner
bin/local/install-mrn              # once: installs the `mrn` command
mrn new m249                       # creates ~/Sites/m249 and opens its setup wizard
```
Each instance gets its own folder with its own `magento/`. Inside it, `mrn magento …`,
`mrn platform …` and `mrn refresh-wizard` (etc.) act on that instance; `mrn -i NAME …` works from
anywhere. See README's "Instances". The rest of this guide describes the
original single-checkout layout (`<project>/platform` + `<project>/magento`), which still works.

**Starting a brand-new (vanilla) Magento instead?** Run `bin/local/setup` and choose
"2) Download a new, empty Magento store" — it downloads the version you pick from `m2/` with
composer. See `README.md`'s "Fresh Magento install".

**Prefer a webpage?** Run `bin/local/setup-wizard` instead of `bin/local/setup` — the same setup as
a step-by-step page in your browser (progress bar, one question at a time, a review screen before
the installation starts, and a finish screen with the hosts-file line to copy). Later, to replace
the database, `bin/local/refresh-wizard` lets you upload the dump in the browser. See README's
"Web wizards".

**Changing the Magento version later?** `bin/local/version-wizard` (or `bin/version`) upgrades to a
newer version from `m2/` and can roll back to the snapshot it saves first. See README's
"Switching Magento versions".

**Automated**: once Steps 1–5 below (host tooling, SSH deploy keys, cloning both repos) are done,
run `bin/local/setup` from `platform/` to automate Steps 6–18 — it prompts inline for composer/AWS/S3
credentials wherever a real value is needed, and prints Step 12's hosts-file line as the very last
thing it does. Safe to re-run any time. The steps below are what it does under the hood, useful if
you want to run (or debug) any one of them by hand instead.

## What you're setting up

Two sibling repositories, run entirely in Docker:

```
<some folder>/
├── magento/     ← the Magento application (github-magento remote)
└── platform/    ← this repo — Docker Compose infra (github-platform remote)
```

`platform/` never contains Magento code; it only defines the containers (Nginx, PHP-FPM, MariaDB,
Valkey, RabbitMQ, OpenSearch, Varnish, Mailpit) that `magento/` runs inside. You always drive both
through the wrapper scripts in `platform/bin/` — never raw `docker compose` or `php bin/magento`
directly on your host.

## Prerequisites checklist

- [ ] Windows 10/11 with WSL2, **or** native Linux/macOS (this guide's exact commands assume
      Ubuntu-on-WSL2, matching the rest of the team; adjust package-manager commands if you're on
      something else)
- [ ] Docker Desktop, with WSL2 integration enabled for your distro
- [ ] Git
- [ ] GitHub access to both your Magento application repo and this wizard repo — ask whoever
      administers the GitHub org to add you, or to add a deploy key for you (not needed for a
      fresh Magento install, which downloads Magento with composer)
- [ ] `mkcert` (for a locally-trusted HTTPS certificate) — no manual install needed,
      `bin/local/setup` installs it automatically if it's missing (see Step 3)
- [ ] AWS CLI (for `bin/sync-db`, see [Step 9](#step-9-aws-cli-profile-and-remote-storage)) — also no
      manual install needed, `bin/local/setup` installs it automatically if it's missing
- [ ] `direnv` (for [Step 9](#step-9-aws-cli-profile-and-remote-storage)'s `.envrc`) — also auto-installed by
      `bin/local/setup` if it's missing
- [ ] ~15 GB free disk space and a decent amount of RAM (OpenSearch and MariaDB alone want several
      GB) — 16 GB total system RAM is a comfortable minimum

---

## Step 1: Install WSL2 + Ubuntu (Windows only — skip if on Linux/macOS)

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu-24.04
```

This installs WSL2 and Ubuntu 24.04 in one step. Reboot if prompted, then launch "Ubuntu 24.04"
from the Start menu and finish the first-run prompts (create a Unix username/password — this is
separate from your Windows login).

If WSL is already installed and you just need the distro:

```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default Ubuntu-24.04
```

Verify inside the Ubuntu shell:

```bash
cat /etc/os-release   # should show Ubuntu 24.04
```

## Step 2: Install Docker Desktop + enable WSL2 integration

1. Download and install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/).
2. During/after install, open Docker Desktop → **Settings → Resources → WSL Integration**.
3. Enable integration for **Ubuntu-24.04** (toggle it on), then **Apply & Restart**.
4. Back in your Ubuntu WSL shell, confirm Docker is reachable:
   ```bash
   docker --version
   docker compose version
   docker ps          # should print an empty table, not a connection error
   ```

If `docker ps` fails with a socket/connection error, Docker Desktop either isn't running on
Windows, or WSL integration for your distro isn't enabled — go back to step 2.3.

## Step 3: Install git inside WSL

```bash
sudo apt update
sudo apt install -y git curl
```

`mkcert` isn't in Ubuntu's package repos, but you don't need to install it by hand —
`bin/local/setup` (see the "Automated" note above) downloads the binary release into
`~/.local/bin` and adds it to your `PATH` (via `~/.bashrc`) the first time it's missing,
and does nothing on later runs once it's there. If you'd rather install it yourself
first (e.g. to run `mkcert` commands before setup, or without `bin/local/setup`):

```bash
curl -L https://github.com/FiloSottile/mkcert/releases/latest/download/mkcert-v1.4.4-linux-amd64 \
  -o /tmp/mkcert
mkdir -p ~/.local/bin
install -m 755 /tmp/mkcert ~/.local/bin/mkcert
rm /tmp/mkcert

echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

mkcert -version   # should print v1.4.4 (or newer — check the releases page for the current version)
```

## Step 4: Set up SSH access to both GitHub repos

This project uses **per-repo SSH keys** (not one shared personal key) so access to the two repos
can be granted/revoked independently. Generate one key per repo:

```bash
ssh-keygen -t ed25519 -C "your-name-magento" -f ~/.ssh/github_magento -N ""
ssh-keygen -t ed25519 -C "your-name-platform" -f ~/.ssh/github_platform -N ""
```

Print both public keys and send them to whoever administers the GitHub org, asking them to add
each as a **deploy key** (read access is enough to clone/pull; only request write access if you'll
be pushing directly rather than through a PR/service account):

```bash
cat ~/.ssh/github_magento.pub
cat ~/.ssh/github_platform.pub
```

> **Known gotcha:** if either repo ever gets renamed or recreated
> under a new name, its deploy keys do **not** automatically carry over unless it was a true GitHub
> rename (not a delete+recreate). If `git fetch` ever suddenly fails with `Permission denied
> (publickey)` after a repo rename, the fix is to re-add the same public key as a deploy key on the
> new repo — the key itself is still fine, it just isn't registered there yet.

Create `~/.ssh/config` with an alias per repo, so git knows which key to use for which remote:

```
# ~/.ssh/config
Host github-magento
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_magento
    IdentitiesOnly yes

Host github-platform
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_platform
    IdentitiesOnly yes
```

Lock down permissions on the SSH files (SSH refuses to use keys/config that are too permissive):

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/config ~/.ssh/github_magento ~/.ssh/github_platform
chmod 644 ~/.ssh/github_magento.pub ~/.ssh/github_platform.pub
```

Test both, once your keys have been added on GitHub's side:

```bash
ssh -T github-magento     # expect: "Hi ... You've successfully authenticated"
ssh -T github-platform    # same
```

## Step 5: Clone the repositories

Pick a folder to hold both repos as **siblings** (this exact relative layout matters — every
script assumes `../magento` next to `platform/`):

```bash
mkdir -p ~/Sites/<project> && cd ~/Sites/<project>

git clone git@github-magento:<your-org>/<magento-repo>.git magento
git clone git@github-platform:<your-org>/<this-repo>.git platform

cd platform
```

Every command from here on assumes your working directory is `platform/`.

## Step 6: Composer credentials

Magento's `composer.json` pulls packages from `repo.magento.com` and several private repos
(Amasty, Meetanshi, bird.eu, etc.), all of which need credentials in `magento/auth.json`
(gitignored — never commit real credentials here):

```bash
cd ../magento
cp auth.json.sample auth.json
```

Edit `auth.json` and fill in the `http-basic` blocks with real credentials — ask a teammate for
these (repo.magento.com credentials are your Adobe Commerce Marketplace public/private keys; the
others are per-vendor license keys):

```json
{
    "http-basic": {
        "repo.magento.com": {
            "username": "<your public key>",
            "password": "<your private key>"
        }
    }
}
```

```bash
cd ../platform
```

## Step 7: Local environment file

```bash
cp env/.env.local.example env/.env.local
```

You can leave everything at its default for now — every `bin/*` script refuses to run
until `env/.env.local` exists, so don't skip this step even if you don't change any values. Leave `AWS`/`REMOTE_STORAGE_*` at `CHANGE_ME`; they're filled in during the next step.
`bin/local/setup` asks for the database name/user/passwords (`DB_*`, default `magento`) once,
before MariaDB's first start — they can't simply be edited afterwards (see `README.md`'s
"Environment files").
It also asks for the **Magento mode** (Default / Developer / Production) and **file ownership**
(single owner, or Adobe's two owners with php-fpm as `www-data`). Two owners needs `sudo` once and
a new shell afterwards (or `wsl --shutdown` from Windows); production mode locks code read-only.
Either can be changed later with `bin/mode/<mode>` / `bin/ownership/<model>` — see `README.md`'s
"Magento mode & file permissions".

## Step 8: Local domain & Magento URLs

By default the site runs at `local.magento.test` — but the domain, and a few
related URLs, can be customized per machine. Edit `env/.env.local`:

```
APP_DOMAIN=local.magento.test   # or your own choice, e.g. dev.local.test
```

`APP_DOMAIN` drives `base_url`, the generated TLS cert (Step 10), and the generated
nginx config (`config/nginx-local/default.conf`, rendered from
`default.conf.template` — never edit the generated file directly, it's gitignored
and gets overwritten). `bin/local/setup` prompts for this once and renders
everything from it automatically; by hand, also re-render the nginx config after
changing it:

```bash
sed "s/local\.magento\.test/$APP_DOMAIN/g" \
    config/nginx-local/default.conf.template > config/nginx-local/default.conf
```

Three more fields are optional overrides — leave them blank to keep Magento's own
sane defaults (media/static URLs derived from `base_url`; cookie domain left to the
current host):

```
BASE_MEDIA_URL=
BASE_STATIC_URL=
COOKIE_DOMAIN=
```

`bin/refresh` reapplies whichever of these three you set on every run (see
[Step 16](#step-16-run-the-full-data-refresh)) — `bin/sync-db`'s DB reimport would
otherwise reset them.

Also required — the real production site's own facts, so `bin/sync-db` knows what to
rewrite an imported production dump's URLs/cookie domain *from*:

```
PROD_BASE_URL=<production's base URL, e.g. https://example.com/>
PROD_MEDIA_URL=<production's media/CDN URL>
PROD_COOKIE_DOMAIN=<production's cookie domain>
```

## Step 9: AWS CLI profile and remote storage

`bin/sync-db` (run as part of the refresh in [Step 16](#step-16-run-the-full-data-refresh))
pull the database/media snapshot from S3 using the host's own AWS CLI credentials, via a named
profile — needed regardless of where Magento itself ends up serving `pub/media` from (that's a
separate choice, below). If you don't already have the `aws` CLI or `direnv`, `bin/local/setup`
installs both for you (into `~/.local`, no `sudo` needed) the first time either is missing — no
separate install step required. (Installing `direnv` also adds its shell hook to `~/.bashrc`; open
a new shell — or `source ~/.bashrc` — before relying on `.envrc` auto-loading when you `cd` into
`platform/`.)

```bash
cat > .envrc <<'EOF'
export AWS_PROFILE=<pick-a-profile-name>       # e.g. MagentoS3Dev
export AWS_S3_PROFILE=<pick-a-profile-name>
EOF
direnv allow .
```

`.envrc` is gitignored and holds only the profile **name** — never real keys. Real credentials go
in the standard AWS CLI file `~/.aws/credentials`, entirely outside this repo:

```bash
aws configure --profile <pick-a-profile-name>
```

Ask a teammate for the access key ID/secret (scoped for S3 read access to the `db/` prefix
of the bucket) — `aws configure` prompts for them interactively and never echoes the secret
back, so they never end up in shell history.

Then choose how Magento itself serves `pub/media` — edit `env/.env.local` and set:

```
REMOTE_STORAGE_DRIVER=aws-s3      # or "file" to serve pub/media from local disk instead
REMOTE_STORAGE_BUCKET=<your-bucket>
REMOTE_STORAGE_REGION=us-east-2
REMOTE_STORAGE_KEY=<same access key as above>
REMOTE_STORAGE_SECRET=<same secret as above>
```

If `REMOTE_STORAGE_DRIVER=file`, leave `BUCKET`/`REGION`/`KEY`/`SECRET` blank — `bin/refresh` skips
S3 configuration entirely. Never commit real values here — this file is gitignored, but stay
careful anyway. (`bin/local/setup` automates this whole step interactively, reusing the same
key/secret for both the AWS CLI profile above and this remote-storage config — this manual version
is only for hand-following.)

## Step 10: Generate a locally-trusted HTTPS certificate

```bash
mkcert -install
```

This creates a local Certificate Authority (CA) the first time it runs. It may print a warning
about not being able to update the system trust store without `sudo` — that's fine, the important
part (installing your browser's trust anchor) happens in Step 11.

```bash
mkdir -p config/nginx-local/certs
cd config/nginx-local/certs
mkcert "$APP_DOMAIN"   # local.magento.test unless you changed it in Step 8
cd ../../..
```

This generates `$APP_DOMAIN.pem` and `$APP_DOMAIN-key.pem` in that folder — both
gitignored, and unique to your machine. **Every developer generates their own** —
never share or commit these.

## Step 11: Trust the certificate in your browser

If your browser runs on the **Windows host** (the normal case — Docker/WSL2 is just the backend,
your browser is still Chrome/Edge/Firefox on Windows):

1. Open Windows Explorer and navigate to:
   `\\wsl$\Ubuntu-24.04\home\<your-wsl-username>\.local\share\mkcert\rootCA.pem`
2. Double-click it → **Install Certificate** → select **Local Machine** (requires admin — click
   through the UAC prompt) → **Place all certificates in the following store** → **Trusted Root
   Certification Authorities** → **Finish**.
3. **Fully restart your browser** (not just close the window — quit it from the taskbar/tray, or
   `chrome://restart`) so it re-reads the trust store.

If your browser runs natively inside WSL2/Linux, `mkcert -install` in Step 10 already handled this
for you — no extra action needed.

## Step 12: Point your hosts file at the local domain

This has to happen on the **Windows side** (`C:\Windows\System32\drivers\etc\hosts`), not inside
WSL — Windows resolves DNS for your browser, WSL's own `/etc/hosts` is irrelevant here.

1. Open Notepad **as Administrator** (right-click → Run as administrator).
2. Open `C:\Windows\System32\drivers\etc\hosts`.
3. Add this line, save, close (substituting your own domain if you changed
   `APP_DOMAIN` in Step 8):
   ```
   127.0.0.1 local.magento.test
   ```

## Step 13: Build and start the Docker stack

```bash
bin/platform build
bin/platform up -d
bin/platform ps
```

`up` also starts the shared edge proxy that owns ports 80/443 for every stack on this machine,
and moves any debug port (MariaDB, Valkey, RabbitMQ, OpenSearch, Mailpit) that another stack
already holds to the next free one — it prints each move and saves it to `env/.env.local`. So
another platform checkout (e.g. 2.4.8 next to 2.4.9) can stay running alongside this one; see
README's "Running several stacks at once".

Wait until every service shows `healthy` (or a status with no error) before continuing — some
services (OpenSearch especially) take 30–60 seconds to become healthy on first start. Re-run
`bin/platform ps` to check.

## Step 14: Install Composer dependencies

```bash
bin/composer install
```

This can take several minutes the first time (fresh `vendor/` install, no cache yet).

## Step 15: First-time Magento install

This bootstraps a fresh `app/etc/env.php` and an empty database schema. Real product/order data
comes later, in Step 16 — this step alone gives you an empty-but-functional Magento install.

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

> **If this fails on a `pub/media` permissions check:** the installer checks for the directory but
> won't create it itself. Run this, then retry the command above:
> ```bash
> bin/platform exec --user "$(id -u):$(id -g)" php mkdir -p /var/www/html/pub/media
> ```

Change `--admin-email`/`--admin-password` to whatever you like — these admin credentials get
**replaced** by whatever's in the real database once you run the refresh in Step 16, so don't worry
about memorizing them.

## Step 16: Run the full data refresh

This pulls a real, sanitized copy of the production database and media assets, and finishes
configuring the install:

```bash
bin/refresh
```

Expect this to take **20–40 minutes** the first time (mostly the database import and initial
`setup:di:compile`). It's safe to re-run any time you want fresh data — see
[`README.md`'s "Refreshing local data"](README.md#refreshing-local-data) section for exactly what
it does step by step.

## Step 17 (optional): Customize your admin URI

By default the admin panel lives at `/admin/` (see `MAGENTO_ADMIN_URI` in
`env/.env.local.example`). To change it:

```bash
bin/magento setup:config:set --backend-frontname=your-choice-here -n
bin/magento cache:flush
```

## Step 18: Verify everything works

- **Storefront**: https://local.magento.test/
- **Admin**: https://local.magento.test/admin/ (or whatever you chose in Step 17) —
  credentials come from the **imported database**, not the ones you typed in Step 15. Ask a
  teammate for a local admin login, or check the team's password manager.
- **Mailpit** (catches all outgoing emails locally instead of sending them): http://localhost:8025/

If all three load, you're done.

---

## Day-to-day commands

Once set up, this is what you'll actually use day to day. Full reference in
[`README.md`'s "Day-to-day commands"](README.md#day-to-day-commands):

```bash
bin/platform ps                    # container status
bin/platform up -d                 # start the stack
bin/platform down                  # stop the stack
bin/platform logs -f php           # tail logs for one service
bin/platform exec php bash         # shell into a container

bin/magento cache:flush            # run any bin/magento command
bin/magento setup:upgrade
bin/magento --version

bin/refresh                        # pull fresh DB/media data (20-40 min)
```

On local, a bare `bin/platform exec php ...` (no `--user` flag) already runs as you — the same
identity that owns every file — so you never need `--user` tricks.

## Troubleshooting

**`docker ps` fails / "cannot connect to the Docker daemon"**
Docker Desktop isn't running, or WSL2 integration isn't enabled for your distro. Open Docker
Desktop on Windows, confirm it's running, and check Settings → Resources → WSL Integration.

**Browser shows a certificate warning despite running `mkcert -install`**
You likely installed the CA but didn't fully restart the browser (closing the last window isn't
enough — some browsers keep a background process alive). Fully quit and reopen it. If that doesn't
work, double check you installed the cert to **Local Machine**, not **Current User**, in Step 11.

**Your local domain doesn't resolve / times out**
Check the Windows hosts file edit from Step 12 actually saved (Notepad run as Administrator is
required — a non-elevated Notepad silently fails to save to that path on some Windows setups), and
that the line matches whatever `APP_DOMAIN` you set in Step 8 (not necessarily
`local.magento.test`, if you changed it). Also confirm `bin/platform ps` shows nginx as up.

**`setup:install` fails with a `pub/media` permission error**
See the callout under Step 15 — create the directory first with the correct owner, then retry.

**`bin/refresh` fails partway through `setup:di:compile`**
The script already retries this step 3 times automatically (resetting compiled state each time) —
if it still fails after 3 attempts, check `bin/platform logs php` for the actual PHP error; a
partial/corrupt `composer install` from Step 14 is the most common cause. Try `bin/composer install` again, then re-run `bin/refresh`.

**`git fetch`/`git pull` suddenly fails with "Permission denied (publickey)"**
See the callout under Step 4 — this almost always means a repo was renamed/recreated and the
deploy key needs to be re-added on GitHub's side, not that your local key is broken.

**Everything looks fine but a page 500s or shows a blank white screen**
Check PHP's error log: `bin/platform logs php`. Also check Magento's own logs inside the
container: `bin/platform exec php tail -f var/log/exception.log`.

## Where to go next

This doc only covers first-time setup. For everything else — the permission model, Varnish, S3
remote storage internals, or the full command reference — see [`README.md`](README.md).
