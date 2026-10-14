// MageDevKit dashboard, part 2 of 6: the overview and one environment's
// page — Overview, Containers, Magento (with the Magento releases card and
// banner), Workflows, Pipeline, Logs and Operations tabs. Uses dash-core.js; see its header for the load order.
'use strict';

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function renderOverview() {
  document.title = 'MageDevKit';
  const o = S.overview;
  if (!o) {
    setKids(contentSlot, ErrorBanner() || LoadingText('Looking for environments…'));
    return;
  }
  const kids = [ErrorBanner()];
  if (!o.docker.available) {
    kids.push(Alert({ variant: 'destructive', iconName: 'alert', title: 'Docker isn’t available' },
      h('p', { text: o.docker.error || 'docker ps failed.' }),
      h('p', { text: 'Container status below is unknown. Checkouts found on disk are still listed.' })));
  }
  if (o.warnings && o.warnings.length) {
    kids.push(Alert({ variant: 'warning', iconName: 'alert', title: 'Some folders were skipped' }, o.warnings.map((w) => h('p', { text: w }))));
  }
  kids.push(h('div', { class: 'flex flex-wrap items-center justify-between gap-3' },
    h('div', {},
      h('h1', { class: 'text-xl font-semibold', text: 'Environments' }),
      h('p', { class: 'text-muted-foreground text-sm', text: `${o.environments.length} found · from Docker and ${o.roots.join(', ')}` })),
    h('div', { class: 'flex flex-wrap items-center gap-3' },
      Panel({ size: 'sm', cls: 'flex items-center gap-2 text-sm', title: o.edge.workingDir || '' },
        Dot(o.edge.level), h('span', { class: 'text-muted-foreground', text: 'Edge proxy (80/443)' }), h('span', { text: o.edge.text })),
      Button({ iconName: 'plus', label: 'New instance', disabled: S.busy, onClick: () => newInstance(o) }))));
  if (!o.environments.length) {
    kids.push(Card({}, CardHeader(CardTitle('No environments yet'),
      CardDescription(`Create one with New instance (or bin/local/new-instance NAME): it gets its own folder in ${o.instancesRoot}, then the setup wizard opens.`))));
  }
  kids.push(h('div', { class: 'grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3' }, o.environments.map(EnvCard)));
  setKids(contentSlot, kids);
}

function EnvCard(env) {
  const [stackText, stackVariant] = STACK_TEXT[env.docker.state] || ['Unknown', 'secondary'];
  const running = env.operation && env.operation.running;
  const site = siteUrl(env);
  return Card({ gap: 'sm' },
    CardHeader(
      CardTitle(env.name, 'truncate', env.platformDir),
      CardDescription(h('span', { class: 'block truncate', text: unknown(env.domain) })),
      CardAction(
        Badge(stackText, stackVariant),
        running ? Badge(running.label || running.kind, 'secondary', 'loader', { spin: true })
          : env.lock && env.lock.held ? Badge('Busy', 'warning', 'loader', { spin: true }) : null,
        env.readonly ? Badge('Read-only', 'outline') : null,
        env.releaseUpdate ? Badge(`Magento ${env.releaseUpdate}`, 'warning', 'download') : null)),
    CardContent(
      Fields(
        Field('Magento', env.magentoVersion.value),
        Field('PHP', env.phpVersion),
        Field('Mode', modeText(env.mode)),
        Field('Git', gitText(env.git))),
      Separator('my-3'),
      h('div', { class: 'space-y-1.5' }, SERVICE_ROWS.map(([k, label]) => StatusRow(label, statusFor(env, k))))),
    CardFooter(
      site ? Button({ variant: 'outline', size: 'sm', iconName: 'external', label: 'Open site', href: site, target: '_blank' }) : null,
      Button({ size: 'sm', iconName: 'wrench', label: 'Manage', href: `#/env/${env.id}` }),
      Button({ variant: 'ghost', size: 'sm', iconName: 'file', label: 'Logs', href: `#/env/${env.id}/logs` })));
}

// ---------------------------------------------------------------------------
// Environment page
// ---------------------------------------------------------------------------

function renderEnvPage() {
  const env = S.detail;
  const back = Button({ variant: 'ghost', size: 'sm', iconName: 'arrowLeft', label: 'All environments', href: '#/' });
  if (!env) {
    setKids(contentSlot, back, ErrorBanner() || LoadingText('Loading…'));
    return;
  }
  document.title = `${env.name} — MageDevKit`;
  const [stackText, stackVariant] = STACK_TEXT[env.docker.state] || ['Unknown', 'secondary'];
  const site = siteUrl(env);
  const ro = !!env.readonly;
  const header = h('div', { class: 'flex flex-wrap items-start justify-between gap-4' },
    h('div', { class: 'min-w-0 space-y-1' },
      h('div', { class: 'flex flex-wrap items-center gap-2' },
        h('h1', { class: 'text-xl font-semibold', text: env.name }), Badge(stackText, stackVariant),
        ro ? Badge('Read-only', 'outline') : null),
      h('p', { class: 'text-muted-foreground text-sm', text: `${unknown(env.domain)} · ${env.platformDir}` })),
    h('div', { class: 'flex flex-wrap gap-2' },
      site ? Button({ variant: 'outline', size: 'sm', iconName: 'external', label: 'Open site', href: site, target: '_blank' }) : null,
      Button({ variant: 'outline', size: 'sm', iconName: 'play', label: 'Start', disabled: ro, onClick: () => runAction(env, 'start') }),
      Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Restart', disabled: ro, onClick: () => runAction(env, 'restart', {}, { label: 'Restart all containers' }) }),
      Button({ variant: 'outline', size: 'sm', iconName: 'square', label: 'Stop', disabled: ro, onClick: () => runAction(env, 'stop', {}, { destructive: true }) })));

  const banners = [ErrorBanner()];
  if (env.readonly) banners.push(Alert({ iconName: 'info', title: 'Read-only' }, h('p', { text: env.readonly })));
  banners.push(ReleaseBanner(env));
  if (env.lock && env.lock.held) {
    banners.push(Alert({ variant: 'warning', iconName: 'loader', title: 'An operation is running in this checkout' },
      h('p', { text: env.lock.text || 'The checkout lock (.wizard.lock) is held.' }),
      h('p', { text: 'Actions that change this environment wait until it finishes. Other environments aren’t affected.' })));
  }

  const tabs = Tabs({ items: TABS, value: S.route.tab, hrefFor: (id) => `#/env/${env.id}/${id}`, label: 'Environment sections' });

  const body = {
    overview: OverviewTab, containers: ContainersTab, magento: MagentoTab, modules: ModulesTab, workflows: WorkflowsTab, pipeline: PipelineTab, git: GitTab,
    logs: LogsTab, operations: OperationsTab,
  }[S.route.tab](env);
  setKids(contentSlot, h('div', { class: 'md:hidden' }, back), header, banners, WatchPanel(), h('div', { class: 'md:hidden' }, tabs), body);
}

function WatchPanel() {
  const w = S.watch;
  if (!w || !S.detail || w.envId !== S.detail.id) return h('div', { id: 'watch', class: 'hidden' });
  const active = ['starting', 'running'].includes(w.rec.status);
  if (!watchPre) watchPre = LogBlock();
  setLogText(watchPre, w.text || (active ? 'Waiting for output…' : ''));
  return h('div', { id: 'watch' }, Card({ gap: 'sm', pad: 'sm' },
    CardHeader(
      h('div', { class: 'flex min-h-8 flex-wrap items-center gap-3' },
        OpBadge(w.rec.status),
        h('span', { class: 'font-medium', text: w.label }),
        h('span', { class: 'text-muted-foreground text-xs', text: took(w.rec.startedAt, w.rec.endedAt) || '' })),
      CardAction(h('div', { class: 'flex gap-2' },
        active && w.rec.cancellable ? Button({ variant: 'outline', size: 'sm', label: 'Cancel', onClick: () => cancelOperation(w.envId, w.opId) }) : null,
        Button({ variant: 'ghost', size: 'icon-sm', iconName: 'x', title: 'Hide output', onClick: () => { S.watchHidden = w.opId; S.watch = null; clearTimeout(watchTimer); render(); } })))),
    CardContent(watchPre)));
}

function renderWatch() {
  const slot = document.getElementById('watch');
  if (!slot) return;
  const panel = WatchPanel();
  slot.replaceWith(panel);
  restoreLogScroll(panel);
}

// -- Overview tab ---------------------------------------------------------------

function OverviewTab(env) {
  const mg = env.git || {};
  const pg = env.platformGit || {};
  const versions = env.versions || {};
  const admin = env.adminUri && siteUrl(env, `${env.adminUri}/`);
  const disk = S.disk;
  return h('div', { class: 'grid grid-cols-1 gap-4 lg:grid-cols-2' },
    Card({}, CardHeader(CardTitle('Application')), CardContent(Fields(
      Field('Application', env.name),
      Field('Domain', env.domain),
      Field('Magento', env.magentoVersion.value, env.magentoVersion.source ? `from ${env.magentoVersion.source}` : null),
      Field('PHP', env.phpVersion, `configured, from ${env.versionsFrom}`),
      Field('Mode', env.mode.actual, env.mode.note || (env.mode.configured ? `configured: ${env.mode.configured}` : null)),
      Field('File ownership', env.ownership),
      Field('Media storage', env.storage),
      Field('Admin', admin ? TextLink({ href: admin, text: admin, external: true }) : null),
      Field('Mailpit', env.mailpitPort ? TextLink({ href: `http://localhost:${env.mailpitPort}/`, text: `localhost:${env.mailpitPort}`, external: true }) : null)))),
    Card({}, CardHeader(CardTitle('Health'), CardDescription('From the containers’ own state and healthchecks; Magento is checked separately with a request to /health_check.php.')),
      CardContent(h('div', { class: 'space-y-2' }, SERVICE_ROWS.map(([k, label]) => StatusRow(label, statusFor(env, k))),
        StatusRow('Varnish', env.services.varnish), StatusRow('Mailpit', env.services.mailpit)))),
    Card({}, CardHeader(CardTitle('Repositories'), CardDescription('Read-only: nothing is fetched or changed.')), CardContent(Fields(
      Field('Magento branch', mg.branch), Field('Magento commit', mg.commit, mg.subject), Field('Working tree', mg.tree),
      Field('Platform branch', pg.branch), Field('Platform commit', pg.commit, pg.subject), Field('Working tree', pg.tree)))),
    Card({}, CardHeader(CardTitle('Paths & stack')), CardContent(Fields(
      Field(env.layout === 'instance' ? 'Instance folder' : 'Checkout', env.platformDir),
      env.layout === 'instance' ? Field('Platform repo', env.codeDir) : null,
      Field('Magento', env.magentoDir, env.magentoDirSource ? `(${env.magentoDirSource})` : null),
      Field('Env file', env.envFile),
      Field('Compose project', env.project),
      Field('Versions', Object.entries(versions).filter(([, v]) => v).map(([k, v]) => `${k.replace('_VERSION', '').toLowerCase()} ${v}`).join(' · '), `from ${env.versionsFrom}`)))),
    Card({ cls: 'lg:col-span-2' }, CardHeader(CardTitle('Disk usage'), CardDescription('Measured on request — it can take a minute on a large store.')),
      CardContent(!disk ? Button({ variant: 'outline', size: 'sm', iconName: 'hardDrive', label: 'Measure', onClick: () => loadDisk(env) })
        : disk.status === 'computing' ? LoadingText('Measuring…')
          : disk.status === 'error' ? h('p', { class: 'text-sm text-destructive', text: disk.error })
            : Fields(
              Field('Magento folder', fmtBytes(disk.data.magento)),
              Field('Backups/snapshots', fmtBytes(disk.data.backups)),
              Object.entries(disk.data.volumes || {}).map(([k, v]) => Field(k, v))))),
    env.layout === 'instance' ? Card({ cls: 'lg:col-span-2 border-destructive/40' },
      CardHeader(CardTitle('Danger zone', 'text-destructive'),
        CardDescription('Destroying removes this instance’s containers, its database and other data volumes, and its whole folder — including magento/, the .env and the database backups. There is no undo.')),
      CardFooter(Button({ variant: 'destructive', size: 'sm', iconName: 'x', label: 'Destroy instance',
        disabled: envBlocked(env), onClick: () => destroyInstance(env) }))) : null);
}

// Destroy: show what would be lost, ask for the name, then follow the run
// (its record lives in the dashboard's cache — the instance folder goes).
async function destroyInstance(env) {
  const res = await getJSON(`/api/environments/${encodeURIComponent(env.id)}/destroy-check`);
  if (!res.ok) return showError('Can’t destroy this environment', res);
  const c = res.data;
  const work = [c.uncommitted ? `${c.uncommitted} uncommitted file(s)` : null, c.unpushed ? `${c.unpushed} unpushed commit(s)` : null,
    c.stashes ? `${c.stashes} stash(es)` : null].filter(Boolean);
  const typed = Input({ id: 'destroy-name', placeholder: env.id, autocomplete: 'off' });
  const help = h('p', { class: 'text-muted-foreground text-xs', text: `Type ${env.id} to confirm.` });
  const force = Checkbox({ id: 'destroy-force' });
  const ok = await formDialog({
      title: `Destroy ${env.id}?`,
      description: [h('div', { class: 'space-y-3 text-left' },
        DescriptionList([
          ['Folder', InlineCode(c.folder, { cls: 'break-all' })],
          ['Containers & data', c.dockerOk ? (c.volumes.length ? `${c.volumes.length} volume(s): ${c.volumes.join(', ')}` : 'No volumes') : 'Docker isn’t reachable — start it first'],
          ['Database backups', fmtBytes(c.backupsBytes) || '0 B'],
          ['magento/ repository', !c.repo ? 'Not a git checkout' : work.length ? work.join(', ') : 'Everything is committed and on origin'],
        ], { labelWidth: 'md' }),
        work.length ? Alert({ variant: 'destructive', iconName: 'alert', title: 'This work isn’t on GitHub' },
          h('p', { text: `${work.join(', ')} in magento/ will be lost. Push or copy it first — or confirm below.` }),
          h('div', { class: 'flex items-center gap-2' }, force, Label('Delete it anyway — I don’t need that work', 'destroy-force'))) : null,
        h('div', { class: 'space-y-2' }, Label(`Type ${env.id} to confirm`, 'destroy-name'), typed, help))],
      actions: [{ label: 'Cancel', value: false }, { label: 'Destroy instance', value: true, variant: 'destructive' }],
      validate: () => {
        if (typed.value.trim() !== env.id) { markInvalid(typed, help, `Type ${env.id} exactly.`); return false; }
        if (work.length && !force.querySelector('input').checked) { markInvalid(typed, help, 'Tick “Delete it anyway”, or keep the instance and push your work first.'); return false; }
        return true;
      },
  });
  if (!ok) return;
  const start = await api(`/api/environments/${encodeURIComponent(env.id)}/destroy`,
    { confirm: typed.value.trim(), force: force.querySelector('input').checked });
  if (!start.ok) return showError(`${env.id} wasn’t destroyed`, start);

  const status = h('p', { class: 'flex items-center gap-2 text-sm' }, Spinner(), 'Removing containers, data and the folder…');
  const pre = LogBlock({ size: 'sm', cls: 'text-left' });
  let finished = false;
  const poll = async () => {
    if (finished) return;
    const r = await getJSON(`/api/destroyed/${encodeURIComponent(start.data.operation)}`);
    if (finished) return;
    if (r.ok) {
      setLogText(pre, r.data.text || '', true);
      const st = r.data.operation.status;
      if (!['starting', 'running'].includes(st)) {
        finished = true;
        status.replaceChildren(st === 'done' ? Badge('Destroyed', 'success', 'check') : Badge('Stopped with an error', 'destructive', 'x'),
          st === 'done' ? (env.domain ? ` Remove the hosts-file line for ${env.domain} on Windows.` : '') : ' See the output below.');
        toast(st === 'done' ? `${env.id} destroyed` : `Destroying ${env.id} failed`, st === 'done' ? 'success' : 'destructive');
        if (st === 'done') { S.overviewAt = 0; location.hash = '#/'; refresh(); }
        return;
      }
    }
    if (!finished) setTimeout(poll, 1500);
  };
  poll();
  await dialog({ title: `Destroying ${env.id}`, description: [h('div', { class: 'space-y-3' }, status, pre)], actions: [{ label: 'Close', value: null }] });
  // Closed: stop following. The run itself carries on; the overview shows the result.
  if (!finished) {
    finished = true;
    toast(`Still destroying ${env.id} in the background — the overview updates when it’s gone.`);
    S.overviewAt = 0; location.hash = '#/'; refresh();
  }
}

// -- Containers tab -------------------------------------------------------------

function ContainersTab(env) {
  const ro = !!env.readonly;
  const rows = (env.containers || []).map((c) => {
    const st = statusForContainer(c);
    return TableRow(
      TableCell(c.service || '—', 'font-medium'),
      TableCell(c.name, 'text-muted-foreground'),
      TableCell(h('span', { class: 'inline-flex items-center gap-2' }, Dot(st.level), st.text)),
      TableCell(c.state === 'running' ? (when(c.startedAt) || '—') : '—', 'text-muted-foreground'),
      TableCell(c.ports.join(', ') || '—', 'text-muted-foreground text-xs'),
      TableCell(c.service && !ro
        ? Button({ variant: 'ghost', size: 'sm', iconName: 'rotateCw', label: 'Restart', onClick: () => runAction(env, 'restart', { service: c.service }, { label: `Restart ${c.service}` }) })
        : null, 'text-right'));
  });
  return Card({},
    CardHeader(CardTitle('Containers'), CardDescription(`Compose project ${env.project}. Start runs bin/platform up -d (it also re-checks ports and the edge proxy); Stop keeps containers and data.`)),
    CardContent(rows.length
      ? Table(
        TableHeader(TableRow(['Service', 'Container', 'Status', 'Up since', 'Ports', ''].map((t) => TableHead(t)))),
        TableBody(rows))
      : h('p', { class: 'text-sm text-muted-foreground', text: 'No containers — this stack hasn’t been started (or was taken down). Use Start.' })),
    CardFooter(
      Button({ variant: 'outline', size: 'sm', iconName: 'play', label: 'Start', disabled: ro, onClick: () => runAction(env, 'start') }),
      Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Restart all', disabled: ro, onClick: () => runAction(env, 'restart', {}, { label: 'Restart all containers' }) }),
      Button({ variant: 'outline', size: 'sm', iconName: 'square', label: 'Stop', disabled: ro, onClick: () => runAction(env, 'stop', {}, { destructive: true }) })));
}

function statusForContainer(c) {
  if (c.state === 'running') {
    if (c.health === 'healthy') return { level: 'ok', text: `Healthy${c.healthDetail ? ` · ${c.healthDetail}` : ''}` };
    if (c.health === 'starting') return { level: 'warn', text: 'Starting' };
    if (c.health === 'unhealthy') return { level: 'bad', text: 'Unhealthy' };
    return { level: 'ok', text: 'Running (no healthcheck)' };
  }
  if (c.state === 'exited' && ![0, 137, 143, null].includes(c.exitCode)) return { level: 'bad', text: `Exited (${c.exitCode})` };
  return { level: 'off', text: capitalize(c.state) };
}

// -- Magento tab ----------------------------------------------------------------

function ActionCard(title, desc, buttons) {
  return Card({ gap: 'sm' }, CardHeader(CardTitle(title), CardDescription(desc)), CardFooter(buttons));
}

function MagentoTab(env) {
  const ro = !!env.readonly;
  const avail = (n) => actionAvailable(env, n);
  const btn = (name, label, variant = 'outline', body, opts) => Button({ variant, size: 'sm', label, disabled: !avail(name), onClick: () => runAction(env, name, body, opts) });
  const stopped = env.services.php.level !== 'ok';
  const rebuildWf = (env.workflows || {}).rebuild || {};
  const rebuildOk = rebuildWf.available && !envBlocked(env);
  return h('div', { class: 'space-y-4' },
    stopped ? Alert({ variant: 'warning', iconName: 'alert', title: 'PHP-FPM isn’t running' }, h('p', { text: 'Magento commands run inside the php container — start the environment first.' })) : null,
    ReleasesCard(env),
    h('div', { class: 'grid grid-cols-1 gap-4 md:grid-cols-2' },
      ActionCard('Build Magento (code only)', 'setup:upgrade → setup:di:compile → setup:static-content:deploy -f → cache:flush, with maintenance mode on. The containers aren’t touched — use it after CSS/JS, layout or code changes.',
        [btn('magento_build', 'Build Magento', 'default')]),
      ActionCard('Cache', 'bin/magento cache:flush empties every cache storage; cache:clean only removes Magento’s own entries.',
        [btn('cache_flush', 'Flush cache', 'default'), btn('cache_clean', 'Clean cache')]),
      ActionCard('Indexers', 'Show the indexer status, or run a full reindex.',
        [btn('indexer_status', 'Indexer status'), btn('reindex', 'Reindex')]),
      ActionCard('Mode', `Currently ${unknown(modeText(env.mode))}. Switching runs bin/mode/<mode> (production compiles and deploys static content).${env.ownership === 'two-owner' ? ' With two-owner file ownership it needs sudo — run it in the terminal.' : ''}`,
        [btn('mode_show', 'Show mode'),
          ...['developer', 'default', 'production'].map((m) => Button({
            variant: 'outline', size: 'sm', label: capitalize(m),
            disabled: !avail('mode_set') || env.mode.actual === m || env.ownership === 'two-owner',
            onClick: () => runAction(env, 'mode_set', { mode: m }, { label: `Switch to ${m} mode` }),
          }))]),
      ActionCard('Composer', 'composer install and update continue with the code-only build (setup:upgrade, di:compile, static content, cache flush), since composer alone doesn’t make the change live. Upgrading Magento itself belongs to the version wizard (Workflows).',
        [btn('composer_validate', 'Validate'),
          btn('magento_build', 'composer install + build', 'outline', { composer: 'install' }, {
            label: 'composer install + build',
            confirm: 'Installs the packages from composer.lock, then builds Magento (setup:upgrade, di:compile, static content, cache flush) with maintenance mode on. The containers aren’t touched.',
          }),
          Button({ variant: 'outline', size: 'sm', label: 'composer update + build', disabled: !avail('magento_build'), onClick: () => composerUpdate(env) })]),
      ActionCard('Full rebuild', 'Everything the refresh and version wizards run: composer install, a compiled-code reset with php/nginx/varnish restarts, platform config, setup:upgrade, di:compile, static content, reindex and cache. Opens in the wizard, with progress.',
        [Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Full rebuild', disabled: !rebuildOk,
          onClick: () => startWorkflow(env, 'rebuild', {}, { label: 'Full rebuild' }) })]),
      OwnershipCard(env, ro)));
}

// -- Magento releases -----------------------------------------------------------
// From the dashboard's copy of Adobe's release list (releases.py), checked
// against this environment's m2/ catalog, stack and composer.json. The page
// only points at the version wizard — it never upgrades anything itself.

const RELEASE_ROWS = 10;

function releaseDismissKey(env) {
  return `release-dismissed:${env.id}`;
}
function releaseDismissed(env, version) {
  try { return localStorage.getItem(releaseDismissKey(env)) === version; } catch (e) { return false; }
}
function dismissRelease(env, version) {
  try { localStorage.setItem(releaseDismissKey(env), version); } catch (e) { /* ignore */ }
  render();
}

function VersionWizardButton(env) {
  const wf = (env.workflows || {}).version || {};
  return Button({ size: 'sm', iconName: 'external', label: 'Open the version wizard',
    disabled: !wf.available || envBlocked(env), onClick: () => startWorkflow(env, 'version') });
}

// "Magento 2.4.8-p6 is available" — once per version: dismissing it hides
// it until a newer one comes (remembered in this browser only).
function ReleaseBanner(env) {
  const r = env.releases;
  const v = r && r.latestCompatible;
  if (!v || releaseDismissed(env, v)) return null;
  const rel = r.releases.find((x) => x.version === v) || {};
  return Alert({ variant: 'success', iconName: 'download', title: `Magento ${v} is available`, onDismiss: () => dismissRelease(env, v) },
    h('p', { text: `The version wizard can upgrade this store to it${rel.state === 'derived' ? ' — its stack versions come from the newest catalogued patch, checked against Adobe’s requirements' : ''}. It takes a snapshot first, so the upgrade can be rolled back.${rel.note ? ` ${rel.note}` : ''}` }),
    rel.phpChange ? h('p', { text: phpChangeText(rel) }) : null,
    h('div', { class: 'pt-1' }, VersionWizardButton(env)));
}

function phpChangeText(rel) {
  const { from, to } = rel.phpChange;
  return `It needs PHP ${to}, and composer.json pins PHP ${from} (config.platform.php): the wizard moves that pin to ${to}, so every extension must support PHP ${to}. If composer can’t resolve the upgrade, the wizard puts composer.json and composer.lock back and nothing changes.`;
}

async function checkReleases() {
  const res = await busyApi('/api/releases/refresh', {});
  if (!res.ok) return showError('The release check didn’t run', res);
  toast(res.data.lastError ? 'Couldn’t reach Adobe — showing the last copy' : 'Release list updated', res.data.lastError ? 'warning' : 'success');
  refresh();
}

// Available (the version wizard lists it), with a PHP change (it moves
// composer.json's PHP pin), or not in the wizard yet (no catalog line, or
// the stack check failed).
function ReleaseBadge(rel) {
  if (!rel.compatible) return Badge('Not in the wizard yet', 'secondary');
  if (rel.phpChange) return Badge(`Available · PHP ${rel.phpChange.from} → ${rel.phpChange.to}`, 'warning', 'alert');
  return Badge('Available', 'success', 'check');
}

function releaseDetails(rel) {
  if (!rel.compatible) return rel.reason;
  if (rel.phpChange) return `The upgrade moves composer.json’s PHP pin ${rel.phpChange.from} → ${rel.phpChange.to}; extensions must support PHP ${rel.phpChange.to}.`;
  return rel.note || (rel.state === 'derived'
    ? 'Stack versions from the newest catalogued patch, checked against Adobe’s requirements.' : 'In the m2/ catalog.');
}

function ReleasesCard(env) {
  const r = env.releases;
  if (!r) {
    return Card({}, CardHeader(CardTitle('Magento releases'), CardDescription(env.magentoVersion.value
      ? 'The release data can’t be read right now.' : 'Shown once Magento is installed (its version comes from composer.lock).')));
  }
  const check = r.check || {};
  const checked = check.fetchedAt ? `Adobe’s release list checked ${when(check.fetchedAt)}` : 'Adobe’s release list hasn’t been checked yet — the m2/ catalog only';
  const rows = r.releases.slice(0, RELEASE_ROWS);
  return Card({},
    CardHeader(CardTitle('Magento releases'),
      CardDescription(`${checked}.${check.refreshing ? ' Checking now…' : ''}`),
      CardAction(Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Check now',
        disabled: S.busy || !!check.refreshing, onClick: checkReleases }))),
    CardContent(h('div', { class: 'space-y-4' },
      check.lastError ? Alert({ variant: 'warning', iconName: 'wifiOff', title: 'The last check couldn’t reach Adobe' },
        h('p', { text: `${check.lastError}. Showing ${check.fetchedAt ? 'the copy from before' : 'the m2/ catalog only'}.` })) : null,
      DescriptionList([
        ['Installed', r.installed, env.magentoEdition === 'enterprise' ? 'Adobe Commerce' : null],
        ['Latest release', r.latestPublished],
        ['Latest you can move to', r.latestCompatible || 'Nothing newer that fits this stack'],
      ]),
      rows.length ? Table(
        TableHeader(TableRow(TableHead('Newer release'), TableHead('Status'), TableHead('Details'))),
        TableBody(rows.map((x) => TableRow(
          TableCell(h('span', { class: 'flex items-center gap-2' }, x.version, x.state === 'derived' ? Badge('New', 'outline') : null)),
          TableCell(ReleaseBadge(x)),
          TableCell(releaseDetails(x), 'text-muted-foreground whitespace-normal'))))) : null,
      ReleaseHelp(r),
      r.releases.length > RELEASE_ROWS ? h('p', { class: 'text-muted-foreground text-xs', text: `…and ${r.releases.length - RELEASE_ROWS} more.` }) : null)),
    r.latestCompatible ? CardFooter(VersionWizardButton(env),
      h('span', { class: 'text-muted-foreground text-xs', text: 'Upgrades only through the wizard: review, snapshot, then the upgrade.' })) : null);
}

// What to do about the releases above: how an upgrade goes, what a PHP
// change means, and how a release that isn't in the wizard yet gets there.
function ReleaseHelp(r) {
  const latest = r.releases.find((x) => x.version === r.latestCompatible);
  const missing = r.releases.filter((x) => !x.compatible);
  return h('div', { class: 'space-y-3' },
    latest ? Alert({ iconName: 'info', title: 'How to upgrade' },
      h('p', { text: `Open the version wizard (also under Workflows → Switch Magento version) and pick ${latest.version}, or another release marked Available. It shows a review with the stack changes, takes a snapshot of the code and database, then upgrades with the store in maintenance mode — and it can roll back to that snapshot later.` }),
      latest.phpChange ? h('p', { text: phpChangeText(latest) }) : null) : null,
    missing.length ? Alert({ iconName: 'info', title: 'Releases the version wizard doesn’t offer yet' },
      h('p', { text: `${missing.map((x) => x.version).join(', ')} — the reason is in the table. The version wizard only offers releases whose stack versions this kit knows: a folder in the platform repo’s m2/, or a new patch whose versions passed Adobe’s requirements.` }),
      h('p', {}, 'To offer one, compare with Adobe’s requirements (', InlineCode('bin/m2/check-requirements'),
        '), then add its folder — e.g. ', InlineCode(`m2/${missing[0].version.split('-')[0]}/version.env`),
        ' (see m2/README.md). It shows up here and in the wizard after that.')) : null);
}

// composer update: everything, or only the packages typed in.
async function composerUpdate(env) {
  const input = Input({ id: 'cu-packages', placeholder: 'e.g. amasty/shipping-rules vendor/other  (leave empty for all)' });
  const help = h('p', { id: 'cu-help', class: 'text-muted-foreground text-xs', text: 'Space-separated vendor/package names. Empty updates every package.' });
  let packages = [];
  const ok = await formDialog({
    title: 'composer update?',
      description: [
        h('p', { text: 'Updates packages to the newest versions composer.json allows and rewrites composer.lock, then builds Magento (setup:upgrade, di:compile, static content, cache flush) with maintenance mode on. Test the store afterwards, then commit composer.lock (Git tab).' }),
        h('div', { class: 'space-y-2 text-left' }, Label('Only these packages (optional)', 'cu-packages'), input, help),
        h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` }),
      ],
      actions: [{ label: 'Cancel', value: false }, { label: 'Update and build', value: true, variant: 'destructive' }],
    validate: () => {
      packages = input.value.trim().split(/\s+/).filter(Boolean);
      if (packages.every((p) => PACKAGE_RE.test(p)) && packages.length <= 20) return true;
      markInvalid(input, help, 'Use vendor/package names only (lowercase, at most 20).');
      return false;
    },
  });
  if (!ok) return;
  runAction(env, 'magento_build', { composer: 'update', packages }, {
    noConfirm: true, label: `composer update${packages.length ? ` ${packages.join(' ')}` : ''} + build`,
  });
}

// File ownership: single ↔ two-owner. Needs sudo (usermod/chown), which the
// dashboard can't ask for — so it runs as a small wizard in a new tab that
// asks for the sudo password on its page.
function OwnershipCard(env, ro) {
  const wf = (env.workflows || {}).ownership || {};
  const models = [
    ['single', 'Single owner', 'php-fpm and the CLI both run as you — simplest for local work.'],
    ['two-owner', 'Two owners (www-data)', 'php-fpm runs as www-data, the CLI as you with group www-data — like a typical server.'],
  ];
  return ActionCard('File ownership', `Currently ${env.ownership === 'two-owner' ? 'two owners (www-data)' : 'single owner'}. Switching may need your sudo password — the wizard asks for it on its page.`,
    models.map(([m, label]) => Button({
      variant: 'outline', size: 'sm', label,
      disabled: envBlocked(env) || !wf.available || env.ownership === m,
      onClick: () => startWorkflow(env, 'ownership', { model: m }, {
        label: `Switch file ownership to ${label.toLowerCase()}`,
        confirm: `${models.find((x) => x[0] === m)[2]} The wizard opens in a new tab: it unlocks the code, applies the new ownership (sudo password asked there if needed) and recreates php.${m === 'two-owner' ? ' You are added to the www-data group — open a new terminal (or run wsl --shutdown) afterwards.' : ''}`,
      }),
    })));
}

// -- Workflows tab --------------------------------------------------------------

function WorkflowsTab(env) {
  return h('div', { class: 'space-y-4' },
    Alert({ iconName: 'info', title: 'These open the same wizards as bin/local/*-wizard' },
      h('p', { text: 'Each one runs this checkout’s own script in a new tab, with its questions, progress and Cancel. It keeps running if you close the tab — reopen it from Operations.' })),
    h('div', { class: 'grid grid-cols-1 gap-4 lg:grid-cols-3' },
      ['setup', 'refresh', 'version'].map((flow) => {
        const wf = env.workflows[flow] || {};
        const wt = WORKFLOW_TEXT[flow];
        return Card({ gap: 'sm' },
          CardHeader(h('div', { class: 'flex items-center gap-2' }, icon(wt.icon, 'text-muted-foreground'), CardTitle(wf.label || flow)), CardDescription(wt.desc)),
          CardFooter(Button({
            variant: flow === 'setup' ? 'outline' : 'default', size: 'sm', iconName: 'external', label: 'Open wizard',
            disabled: !wf.available || envBlocked(env), onClick: () => startWorkflow(env, flow),
          }), !wf.available ? h('span', { class: 'text-muted-foreground text-xs', text: 'Not available in this checkout' }) : null));
      })));
}

// -- Pipeline tab ---------------------------------------------------------------

const KIT_STATUS = {
  missing: ['Not set up', 'secondary'], current: ['Current', 'success'], outdated: ['Update available', 'warning'], edited: ['Edited by hand', 'warning'],
};

// Pipeline readiness of this environment's Magento repo, before it's pushed:
// the kit (deploy/), its targets, the pins and the git state. Read-only —
// the only action opens the pipeline wizard; pushing stays with you.
function PipelineTab(env) {
  const p = env.pipeline || {};
  const wf = (env.workflows || {}).pipeline || {};
  const g = p.git || {};
  const [statusText, statusVariant] = KIT_STATUS[p.status] || KIT_STATUS.missing;
  const blocked = !wf.available || envBlocked(env);
  const runLabel = p.status === 'missing' ? 'Make pipeline-ready' : p.status === 'outdated' ? 'Update the kit' : 'Open the pipeline wizard';
  const runButton = (variant) => Button({
    variant, size: 'sm', iconName: 'rocket', label: runLabel, disabled: blocked,
    onClick: () => startWorkflow(env, 'pipeline', {}, { label: runLabel }),
  });

  const banners = [];
  if (!p.isRepo) {
    banners.push(Alert({ variant: 'warning', iconName: 'alert', title: 'magento/ isn’t a git checkout' },
      h('p', { text: 'The pipeline kit is committed with the store, so magento/ has to be a clone of the company repository.' })));
  }
  if ((p.edited || []).length || (p.missingFiles || []).length) {
    banners.push(Alert({ variant: 'warning', iconName: 'alert', title: 'Kit files changed since the wizard wrote them' },
      h('p', { text: 'The wizard keeps these edits unless you choose to replace them when you run it again.' }),
      h('ul', { class: 'list-disc space-y-0.5 pl-5' },
        (p.edited || []).map((f) => h('li', {}, InlineCode(f), ' edited')),
        (p.missingFiles || []).map((f) => h('li', {}, InlineCode(f), ' missing')))));
  }
  if (p.pins && !p.pins.inSync) {
    banners.push(Alert({ variant: 'warning', iconName: 'alert', title: 'Version pins differ from this instance' },
      h('p', { text: 'The pipeline deploys what .platform/stack.env says. Run the wizard again to bring it in line with the instance (after a version switch, for example).' }),
      DescriptionList(p.pins.diff.map((d) => [d.key, `repo ${d.repo || 'unset'} · instance ${d.instance || 'unset'}`]), { labelWidth: 'md' })));
  }

  const kitCard = Card({},
    CardHeader(
      CardTitle('Pipeline kit'),
      CardDescription('magento/deploy/ — build spec, deploy scripts and the EC2 stack (CloudFormation), generated by the pipeline wizard.'),
      CardAction(Badge(statusText, statusVariant))),
    CardContent(DescriptionList([
      ['Kit version', p.templateVersion ? `v${p.templateVersion}${p.kitVersion && p.kitVersion > p.templateVersion ? ` (v${p.kitVersion} available)` : ''}` : null,
        p.kitVersion ? `wizard generates v${p.kitVersion}` : null],
      ['Files', p.files ? `${p.files} generated` : null,
        (p.edited || []).length || (p.missingFiles || []).length ? `${(p.edited || []).length} edited, ${(p.missingFiles || []).length} missing` : null],
      ['Version pins', p.pins ? (p.pins.inSync ? 'In sync with this instance' : `${p.pins.diff.length} differ`) : null],
      ['Old pipeline files', (p.oldFiles || []).length ? h('span', {}, (p.oldFiles || []).map((f, i) => [i ? ', ' : '', InlineCode(f)])) : 'None',
        (p.oldFiles || []).length ? 'removed by the wizard' : null],
    ], { labelWidth: 'md' })),
    CardFooter(runButton(p.status === 'current' ? 'outline' : 'default'),
      !wf.available ? h('span', { class: 'text-muted-foreground text-xs', text: 'Needs magento/ to be a git checkout' }) : null));

  const targets = p.targets || [];
  const targetsCard = Card({},
    CardHeader(CardTitle('Targets'), CardDescription('Each environment the kit deploys to. Pushing the kit to a target’s branch is what starts its pipeline.')),
    CardContent(targets.length
      ? Table(
        TableHeader(TableRow(['Environment', 'Store', 'AWS', 'Deploys from', 'Kit on that branch'].map((t) => TableHead(t)))),
        TableBody(targets.map((t) => TableRow(
          TableCell(t.env, 'font-medium'),
          TableCell(t.domain ? TextLink({ href: `https://${t.domain}/`, text: t.domain, external: true }) : '—'),
          TableCell(`${t.account || t.profile || '?'} · ${t.region || '?'}`, 'text-muted-foreground'),
          TableCell(`${t.repo || '?'} @ ${t.branch || '?'}`, 'text-muted-foreground'),
          TableCell((g.kitOnBranch || {})[t.branch]
            ? Badge(`On origin/${t.branch}`, 'success', 'check')
            : Badge(`Not on origin/${t.branch} yet`, 'secondary'))))))
      : h('p', { class: 'text-muted-foreground text-sm', text: 'No targets yet — the pipeline wizard asks for the first one (for example dev).' })));

  const ahead = g.ahead != null ? `${g.ahead} ahead, ${g.behind} behind ${g.upstream}` : null;
  const repoCard = Card({},
    CardHeader(CardTitle('Repository'), CardDescription('As of the last fetch. Fetch, commit, pull and push are on the Git tab.')),
    CardContent(DescriptionList([
      ['Branch', g.branch],
      ['Uncommitted kit changes', g.kitChanges ? `${g.kitChanges} file(s) in deploy/ or .platform/` : 'None'],
      ['Upstream', ahead || (g.branch ? 'Not pushed yet' : null)],
    ], { labelWidth: 'md' })),
    g.branch && g.branch.startsWith('pipeline/') && !g.upstream
      ? CardContent(h('div', { class: 'space-y-1.5' },
        h('p', { class: 'text-sm', text: 'Publish the kit branch (Git tab → Publish branch, or in a terminal), then open a pull request into the target’s branch:' }),
        CodeLine(`git -C ${env.magentoDir} push -u origin ${g.branch}`))) : null,
    CardFooter(Button({ variant: 'outline', size: 'sm', iconName: 'gitBranch', label: 'Open the Git tab', href: `#/env/${env.id}/git` })));

  return h('div', { class: 'space-y-4' }, banners,
    h('div', { class: 'grid grid-cols-1 gap-4 lg:grid-cols-2' }, kitCard, repoCard),
    targetsCard);
}

// -- Logs tab -------------------------------------------------------------------

function LogsTab(env) {
  const sources = env.logSources || [];
  if (!S.logs.source && sources.length) S.logs.source = sources.find((s) => s.id === 'service:php') ? 'service:php' : sources[0].id;
  const select = NativeSelect({
    'aria-label': 'Log source', cls: 'w-auto min-w-64', value: S.logs.source,
    options: sources.map((s) => ({ value: s.id, label: s.label })),
    onChange: (e) => { S.logs.source = e.target.value; loadLog(); },
  });
  const lines = NativeSelect({
    'aria-label': 'Lines', cls: 'w-auto', value: S.logs.lines,
    options: ['100', '300', '1000', '2000'].map((n) => ({ value: n, label: `${n} lines` })),
    onChange: (e) => { S.logs.lines = e.target.value; loadLog(); },
  });
  const follow = Checkbox({ id: 'logs-follow', checked: S.logs.follow, onChange: (e) => { S.logs.follow = e.target.checked; } });
  if (!logsPre) {
    logsPre = LogBlock({ size: 'xl', cls: 'min-h-48' });
    setTimeout(() => loadLog(), 0);
  }
  return Card({},
    CardHeader(CardTitle('Logs'), CardDescription('Passwords, keys and tokens are masked (••••) before logs reach this page.')),
    CardContent(h('div', { class: 'space-y-3' },
      h('div', { class: 'flex flex-wrap items-center gap-2' }, select, lines,
        Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Reload', onClick: () => loadLog() }),
        h('div', { class: 'ml-2 flex items-center gap-2' }, follow, Label('Follow (every 5 s)', 'logs-follow'))),
      logsPre)));
}

function renderLogText() {
  if (!logsPre) return;
  setLogText(logsPre, S.logs.loading ? 'Loading…' : (S.logs.text || '(empty)'), S.logs.loading);
}

// -- Operations tab -------------------------------------------------------------

// The newest run of a workflow — only that one offers "Run again".
function lastRun(ops, kind) {
  return ops.find((o) => o.kind === kind) || null;
}

function OperationsTab(env) {
  const ops = env.operations || [];
  if (!opLogPre) opLogPre = LogBlock({ size: 'xl' });
  if (opLogPre.dataset.op !== S.opLog.opId || opLogPre.textContent !== S.opLog.text) {
    const other = opLogPre.dataset.op !== (S.opLog.opId || '');
    opLogPre.dataset.op = S.opLog.opId || '';
    setLogText(opLogPre, S.opLog.text, other);  // a newly picked log starts at its end
  }
  const rows = ops.map((op) => h('li', { class: 'flex flex-wrap items-center gap-3 py-3' },
    OpBadge(op.status),
    h('div', { class: 'min-w-0 flex-1' },
      h('p', { class: 'text-sm font-medium', text: op.label || op.kind }),
      h('p', { class: 'text-muted-foreground text-xs', text: [
        op.source === 'wizard' ? 'started from the terminal' : 'started from the dashboard',
        when(op.startedAt), took(op.startedAt, op.endedAt),
        op.status === 'running' && op.step ? `step ${op.stepNumber}/${op.steps} · ${op.step}` : null,
        op.status === 'failed' && op.step ? `stopped at ${op.step}` : null,
        op.exitCode != null ? `exit ${op.exitCode}` : null,
      ].filter(Boolean).join(' · ') }),
      op.error ? h('p', { class: 'text-destructive text-xs', text: op.error }) : null),
    h('div', { class: 'flex gap-2' },
      op.hasUrl ? Button({ variant: 'default', size: 'sm', iconName: 'external', label: 'Open wizard', onClick: () => reopenWizard(env.id, op.id) }) : null,
      !op.hasUrl && ['failed', 'cancelled', 'interrupted'].includes(op.status) && ['setup', 'refresh', 'version', 'pipeline', 'rebuild'].includes(op.kind) && op === lastRun(ops, op.kind)
        ? Button({
          variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Run again',
          disabled: envBlocked(env) || !(env.workflows[op.kind] || {}).available,
          onClick: () => startWorkflow(env, op.kind, op.kind === 'rebuild' && (op.args || []).length
            ? { composer: op.args[0], packages: op.args.slice(1) } : {}, { label: op.label }),
        }) : null,
      op.status === 'running' && op.source === 'dashboard' && op.cancellable
        ? Button({ variant: 'outline', size: 'sm', label: 'Cancel', onClick: () => cancelOperation(env.id, op.id) }) : null,
      Button({ variant: S.opLog.opId === op.id ? 'secondary' : 'ghost', size: 'sm', iconName: 'file', label: 'Log', onClick: () => loadOpLog(env.id, `op:${op.id}`, op.id) }))));
  const older = (env.olderLogs || []).map((r) => h('li', { class: 'flex items-center gap-3 py-2 text-sm' },
    Badge('No record', 'outline'),
    h('span', { class: 'flex-1', text: `${capitalize(r.kind)} · ${r.stamp.replace(/^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)$/, '$1-$2-$3 $4:$5:$6')}` }),
    Button({ variant: S.opLog.opId === r.logFile ? 'secondary' : 'ghost', size: 'sm', iconName: 'file', label: 'Log', onClick: () => loadOpLog(env.id, `file:${r.logFile.replace(/^logs\//, '')}`, r.logFile) })));
  return h('div', { class: 'space-y-4' },
    Card({}, CardHeader(CardTitle('Operations'), CardDescription('Setup, refresh and version runs and dashboard actions in this checkout, newest first. Their state is kept on disk (logs/operations/), so it survives closing this page or restarting the dashboard.')),
      CardContent(ops.length ? h('ul', { class: 'divide-y' }, rows) : h('p', { class: 'text-sm text-muted-foreground', text: 'Nothing recorded yet.' }))),
    older.length ? Card({}, CardHeader(CardTitle('Earlier runs'), CardDescription('Run logs without a record — terminal runs, or wizards from an older copy of the platform scripts. Their outcome isn’t known.')),
      CardContent(h('ul', { class: 'divide-y' }, older))) : null,
    S.opLog.opId ? Card({}, CardHeader(CardTitle('Log')), CardContent(opLogPre)) : null);
}
