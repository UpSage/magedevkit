# Local Development Setup

A from-scratch walkthrough for running a Magento 2 store on your own machine with MageRunner,
written for someone who has never used this project. If WSL2, Docker and git are already set up,
skip to [Step 4](#step-4-get-magerunner).

For everything after the first store (the full command reference, the dashboard, Varnish, S3,
modes and permissions, deploying), see [`README.md`](README.md).

## What you're setting up

```
~/Sites/
├── magerunner/      ← MageRunner (this repo): the scripts, wizards and dashboard — no store data
├── m249/            ← a store ("instance"): created by `mrn new m249`
│   ├── magento/     ← the store's code — its own git repo
│   ├── .env         ← its settings (domain, ports, versions, mode, …)
│   └── nginx/ certs/ logs/ db/
└── m248/            ← another store, same shape; both can run at once
```

Everything runs in Docker (Nginx, Varnish, PHP-FPM, MariaDB, Valkey, RabbitMQ, OpenSearch,
Mailpit). You drive it with one command, `mrn` — never raw `docker compose` or `php bin/magento` on
your host.

## Prerequisites checklist

- [ ] Windows 10/11 with WSL2, **or** native Linux (this guide's commands assume Ubuntu on WSL2;
      adjust the package manager otherwise)
- [ ] Docker Desktop, with WSL2 integration enabled for your distro
- [ ] `git`, `curl` and `python3`
- [ ] For a store you clone: its repository URL and branch, plus a GitHub username and a
      personal access token (repo scope) for the account that can read it — or SSH access to it
- [ ] For a fresh store: your repo.magento.com access keys (Commerce Marketplace → My Profile →
      Access Keys), or an `auth.json` that has them
- [ ] Optional: a database dump (`.sql.gz`) to import — a file, a URL, or uploaded on the page
- [ ] About 15 GB free disk space and 16 GB RAM (OpenSearch and MariaDB alone want several GB)

You don't install `mkcert`, the AWS CLI or `direnv` yourself: setup installs whichever one a step
needs into `~/.local/bin`, after checking it against a pinned checksum (the AWS CLI against AWS's
signature).

---

## Step 1: Install WSL2 + Ubuntu (Windows only)

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot if prompted, then launch "Ubuntu 24.04" from the Start menu and finish the first-run prompts
(create a Unix username and password — separate from your Windows login). If WSL is already
installed and you only need the distro, also run `wsl --set-default Ubuntu-24.04`.

## Step 2: Install Docker Desktop + enable WSL2 integration

1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/).
2. Open Docker Desktop → **Settings → Resources → WSL Integration**.
3. Turn on integration for **Ubuntu-24.04**, then **Apply & Restart**.
4. In your Ubuntu shell, check Docker is reachable:
   ```bash
   docker --version
   docker compose version
   docker ps          # an empty table, not a connection error
   ```

## Step 3: Install git, curl and python3

```bash
sudo apt update
sudo apt install -y git curl python3
```

## Step 4: Get MageRunner

```bash
mkdir -p ~/Sites && cd ~/Sites
git clone https://github.com/UpSage/magerunner.git
cd magerunner
bin/local/install-mrn          # puts the `mrn` command on your PATH (~/.local/bin)
```

Open a new terminal if `install-mrn` says it added `~/.local/bin` to your PATH, then check:

```bash
mrn --version
```

Your stores are created **next to** this folder (`~/Sites/<name>`). Set `PLATFORM_INSTANCES_ROOT`
if you want them somewhere else.

## Step 5: Create your first store

```bash
mrn new m249
```

This creates `~/Sites/m249` and opens its **setup wizard** in your browser (add `--terminal` to
answer the same questions in the terminal instead). The name becomes the folder, the Docker project
(`m249-platform`) and `APP_NAME`: lowercase letters, digits, `_` and `-`.

The wizard asks one thing at a time and shows its progress in the sidebar. What it covers is listed
in README's [What setup does](README.md#local-development-setup); the questions that need you:

- **Magento source** — clone your store's repository, or download a new, empty Magento from the
  `m2/` catalog.
  - **Cloning over HTTPS** asks for your GitHub username and token, used for the clone only. Tick
    **Remember** to save it for **this repo only** (`magento/.git/credentials`, readable only by
    you). The name and email for commits in this repo are taken from that GitHub account, so
    they aren't asked separately. Nothing goes into your global git config, so a company store
    and a personal one never share an identity or a token.
  - **Cloning over SSH** (`git@github.com:…`) uses your SSH keys, and asks for the commit name and
    email once.
  - **A fresh download** asks for your repo.magento.com keys (or an `auth.json`), then an admin
    account later on.
- **Store data** — the database dump, and whether it's a copy of production. You don't type the
  live site's URLs: the import reads them from the dump and rewrites them to your local domain.
- **Project** — the local domain (default `m249.test`) and the admin path.
- **Database login** — names and passwords for the local MariaDB (asked once, before the database
  is created).
- **Mode & versions** — Magento mode (developer for day-to-day work), file ownership (single owner
  unless you need Adobe's two-owner model), and the stack versions for this Magento version.
- **Review** — check everything; **Change answers** goes back with your answers kept. After
  **Start**, the wizard builds the containers, installs and imports on its own. The first run takes
  a while (the images build once; a large dump takes longer).
- **Media storage** — local disk, or an S3 bucket through an AWS profile (keys stay in
  `~/.aws/credentials`).

When it finishes, the page shows links, the hosts-file line and the commands you'll use day to day.

## Step 6: Point your hosts file at the local domain (Windows)

Windows resolves names for your browser, so this goes in `C:\Windows\System32\drivers\etc\hosts`,
not WSL's `/etc/hosts`:

1. Open Notepad **as Administrator** (right-click → Run as administrator).
2. Open `C:\Windows\System32\drivers\etc\hosts`.
3. Add the line the finish screen showed, then save:
   ```
   127.0.0.1 m249.test
   ```

Each store needs its own line; they all point at the same shared proxy on 127.0.0.1.

## Step 7: Trust the local certificate (once per machine)

If your browser runs on Windows (the usual case):

1. In Explorer, open
   `\\wsl$\Ubuntu-24.04\home\<your-wsl-username>\.local\share\mkcert\rootCA.pem`.
2. Double-click it → **Install Certificate** → **Local Machine** (accept the admin prompt) →
   **Place all certificates in the following store** → **Trusted Root Certification
   Authorities** → **Finish**.
3. **Fully restart the browser** (quit it from the tray, or `chrome://restart`).

This authority signs every store's certificate, so you only do it once. If your browser runs inside
WSL/Linux, setup's `mkcert -install` already took care of it.

## Step 8: Check it works

- **Storefront:** `https://m249.test/`
- **Admin:** `https://m249.test/<admin path>/` — for an imported database, log in with an admin
  account from that database (ask a teammate).
- **Mailpit** (catches every outgoing email): the address on the finish screen
  (`http://localhost:8025/` unless another store already uses that port).

**Cloned store only — one more command, once:** setup sets up Varnish's purge target and the
full-page cache for fresh downloads. For a cloned store, run:

```bash
cd ~/Sites/m249
mrn magento setup:config:set --http-cache-hosts="varnish:80" -n
mrn magento cache:enable full_page
```

---

## Day-to-day commands

Inside the store's folder (or from anywhere with `mrn -i m249 …`). Full reference in
[`README.md`'s "Day-to-day commands"](README.md#day-to-day-commands):

```bash
mrn platform ps                    # container status
mrn platform up -d                 # start the stack (after a reboot, Docker restart, …)
mrn platform stop                  # stop it (containers and data are kept)
mrn platform logs -f php           # tail one service's logs

mrn magento cache:flush            # any bin/magento command
mrn build                          # after code/CSS/JS changes: setup:upgrade, compile, static, cache
mrn refresh-wizard                 # replace the database from a new dump
mrn dashboard                      # every store on this machine, in the browser
```

## Troubleshooting

**`docker ps` fails / "cannot connect to the Docker daemon"**
Docker Desktop isn't running, or WSL integration isn't on for your distro. Start Docker Desktop
and check Settings → Resources → WSL Integration.

**`mrn: command not found`**
Open a new terminal (so `~/.local/bin` is on your PATH), or run `~/Sites/magerunner/bin/local/install-mrn`
again.

**The browser shows a certificate warning**
The browser wasn't fully restarted after Step 7, or the certificate went to **Current User**
instead of **Local Machine**. Fully quit the browser and reopen it; redo Step 7 if needed.

**The local domain doesn't resolve / times out**
Check the hosts-file line saved (Notepad must run as Administrator) and matches the store's domain
(`APP_DOMAIN` in its `.env`). Check `mrn platform ps` shows nginx running.

**Setup or a refresh failed partway**
Run it again — every step skips what's already done. The wizard's **Run again** does the same. The
full log is in the store's `logs/` folder (`logs/setup-<date>.log`), and the dashboard's
Operations tab lists every run.

**A page 500s or shows a blank screen**
`mrn platform logs php` for PHP errors, and Magento's own log:
`mrn platform exec php tail -f var/log/exception.log`.

**Push or fetch asks for a login again / "Authentication failed"**
The token expired or lost access. On the dashboard's Git tab, **Forget** the saved login and
fetch again with a new token (it's saved for that repo only).

## Where to go next

This guide covers the first store. For everything else — more stores, the dashboard, switching
Magento versions, the deploy kit, Varnish, S3, modes and permissions — see [`README.md`](README.md).
