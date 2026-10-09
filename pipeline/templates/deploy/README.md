<!-- @@HEADER@@ -->
# deploy/ — pipeline kit

Everything needed to build this store and deploy it to an EC2 box. The
pipeline wizard in MageRunner generates this folder
(`mrn pipeline-wizard`); `deploy/pipeline.json` records which files it
wrote, so the wizard notices hand edits and keeps them unless you choose to
replace them.

## How a deploy works

1. Push to the deploy branch (see `targets/<env>.env` and
   `infra/envs/<env>/terraform.tfvars`).
2. **CodeBuild** runs `buildspec.yml`:
   - builds the php and nginx images from `docker/` at the pins in
     `../.platform/stack.env`, skipping any image already in ECR;
   - runs `scripts/build-release.sh` in that php image: composer, DI compile
     and static content, with no database.
3. **CodeDeploy** runs `appspec.yml` on the box:

   | Hook | Script | What it does |
   |---|---|---|
   | BeforeInstall | `hooks/before-install.sh` | takes the deploy lock, renders settings from SSM |
   | AfterInstall | `hooks/after-install.sh` | syncs the code into `/opt/<app>/magento` |
   | ApplicationStart | `scripts/deploy` | stack at the new pins and images; `setup:upgrade` only when needed |
   | ValidateService | `hooks/validate.sh` | health checks, records the release |

   A failure rolls back to the previous release automatically.

## Layout

| Path | What |
|---|---|
| `targets/<env>.env` | non-secret settings per environment (secrets: SSM `/<app>/<env>/`) |
| `docker/`, `config/` | images and service config: the same as the local instance's |
| `compose/` | the stack on the box (`app.yml`) and its front door, Traefik with Let's Encrypt (`edge.yml`) |
| `scripts/box` | operator commands on the box: status, maintenance, cache-flush, reindex, restart, seed-db |
| `infra/` | Terraform for the AWS side. Applied by the provision wizard (`mrn provision-wizard`), never by the pipeline. |

## On the box

Shell access is through SSM Session Manager, then `sudo -iu deploy`:

```
/opt/<app>/deploy/scripts/box status
/opt/<app>/deploy/scripts/box seed-db s3://bucket/dump.sql.gz   # first deploy: load a database
```
