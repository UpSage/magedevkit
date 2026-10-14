<!-- @@HEADER@@ -->
# deploy/ — pipeline kit

Everything needed to build this store and deploy it to an EC2 box. The
pipeline wizard in MageDevKit generates this folder
(`mdk pipeline-wizard`); `deploy/pipeline.json` records which files it
wrote, so the wizard notices hand edits and keeps them unless you choose to
replace them.

## The AWS side

`infra/stack.json` is a CloudFormation template — one stack per environment,
`mdk-<app>-<env>`, with that environment's values in `infra/<env>.params.json`.
It creates the network, the box, its DNS record, the ECR repos, CodeBuild,
CodeDeploy, CodePipeline, and:

- **`<app>-<env>-box`**, an SSM command document that runs only the
  allow-listed `scripts/box` commands below, as the deploy user;
- **`<app>-<env>-operator`**, an IAM policy for day-to-day work on this
  environment (start/stop the box, run the pipeline, read builds and logs,
  the box commands). Give it to teammates instead of admin access.

The provision wizard (`mdk provision-wizard`) creates and updates the stack;
the pipeline never does. No secret is in the template or in git: the
database, RabbitMQ and crypt keys and the repo.magento.com key are SSM
SecureStrings under `/<app>/<env>/`, written by the provision wizard.

## How a deploy works

1. Push to the deploy branch (`targets/<env>.env`, `infra/<env>.params.json`).
2. **CodeBuild** runs `buildspec.yml`:
   - builds the php and nginx images from `docker/` at the pins in
     `../.platform/stack.env` into `<app>-<env>-php|nginx`, skipping any image
     already in ECR;
   - runs `scripts/build-release.sh` in that php image: composer, DI compile
     and static content, with no database.
3. **CodeDeploy** runs `appspec.yml` on the box:

   | Hook | Script | What it does |
   |---|---|---|
   | BeforeInstall | `hooks/before-install.sh` | takes the deploy lock, renders settings from SSM |
   | AfterInstall | `hooks/after-install.sh` | syncs the code into `/opt/<app>/magento` |
   | ApplicationStart | `scripts/deploy` | stack at the new pins and images; `setup:upgrade` only when needed |
   | ValidateService | `hooks/validate.sh` | health checks, records the release, publishes its status |

   A failure rolls back to the previous release automatically. A new
   environment's first release succeeds with an empty database and waits for
   one (`box refresh s3://…`).

## Not production

When `PRODUCTION=no` (`targets/<env>.env`), the store runs on a copy of live
data, so `env.php` locks: outgoing email off, `NOINDEX,NOFOLLOW`, PayPal and
Braintree in sandbox. Add your own integrations (ERP, marketing, feeds) to
`targets/<env>.config-overrides.php` — written once by the wizard, then
yours. The dump bucket and the box hold personal data: keep them private.

## Layout

| Path | What |
|---|---|
| `targets/<env>.env` | non-secret settings per environment |
| `targets/<env>.config-overrides.php` | your own `env.php` additions for that environment |
| `infra/stack.json`, `infra/<env>.params.json` | the AWS side (CloudFormation) |
| `docker/`, `config/` | images and service config: the same as the local instance's |
| `compose/` | the stack on the box (`app.yml`) and its front door, Traefik with Let's Encrypt (`edge.yml`) |
| `scripts/box` | operator commands on the box |

## On the box

MageDevKit runs these for you. By hand: SSM Session Manager, then `sudo -iu deploy`:

```
/opt/<app>/deploy/scripts/box status
/opt/<app>/deploy/scripts/box refresh s3://bucket/dump.sql.gz   # load or replace the database
/opt/<app>/deploy/scripts/box cache-flush | reindex | build | composer-install | logs php
```

Changes made with `box` are repairs on the live release; the next deploy
replaces them.
