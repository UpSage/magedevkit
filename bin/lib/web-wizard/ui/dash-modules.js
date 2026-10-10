// MageDevKit dashboard, part 4 of 5: the Modules tab — every Magento module
// with its status, package, version and update; search, filters and paging;
// selecting rows; turning modules on or off and updating their packages
// (review → dry run → apply). Uses dash-core.js; see its header for the
// load order.
'use strict';

// -- Modules tab ----------------------------------------------------------------

const MODULE_STATUS = {
  true: ['Enabled', 'success'], false: ['Disabled', 'secondary'], null: ['Not registered', 'outline'],
};
const MODULE_SOURCES = [
  { value: 'third-party', label: 'Third-party' }, { value: 'app/code', label: 'app/code' },
  { value: 'core', label: 'Magento core' }, { value: 'all', label: 'All modules' },
];
const MODULE_STATUSES = [
  { value: 'all', label: 'Any status' }, { value: 'enabled', label: 'Enabled' }, { value: 'disabled', label: 'Disabled' },
  { value: 'update', label: 'Update available' }, { value: 'unchecked', label: 'Can’t check' },
];
// modules.UPDATE_STATUSES → [text, badge variant, icon] (update-available
// shows the version instead of a text).
const UPDATE_BADGE = {
  'up-to-date': ['Up to date', 'outline', 'check'], 'unable-to-check': ['Can’t check', 'outline', 'alert'],
  'not-updatable': ['Not updatable', 'secondary'], 'managed-by-version-wizard': ['Version wizard', 'secondary'],
};
const MODULE_PAGE = 50;
const MODULE_BULK_MAX = 100;

let modSearch = null;    // the search box, kept across re-renders so typing isn't lost

function modulesUrl(env) {
  return `/api/environments/${encodeURIComponent(env.id)}/modules`;
}

async function loadModules() {
  const res = await getJSON(modulesUrl(S.detail || { id: S.route.id }));
  S.modules = res.ok ? res.data : null;
  S.modulesError = res.ok ? null : errorText(res);
  // Forget selected modules that aren't there any more.
  if (S.modules) {
    const names = new Set(S.modules.modules.map((m) => m.name));
    S.modulesSel = new Set([...S.modulesSel].filter((n) => names.has(n)));
  }
}

function moduleMatches(m, f) {
  if (f.status === 'enabled' && m.enabled !== true) return false;
  if (f.status === 'disabled' && m.enabled !== false) return false;
  if (f.status === 'update' && m.update.status !== 'update-available') return false;
  if (f.status === 'unchecked' && m.update.status !== 'unable-to-check') return false;
  // Each module is in exactly one source: core, app/code or third-party.
  if (f.source === 'third-party' && (m.core || m.source === 'app/code')) return false;
  if (f.source === 'core' && !m.core) return false;
  if (f.source === 'app/code' && (m.core || m.source !== 'app/code')) return false;
  const q = f.q.trim().toLowerCase();
  return !q || m.name.toLowerCase().includes(q) || (m.package || '').includes(q);
}

// Production mode always builds: the code is locked and compiled there.
function envProduction(env) {
  return env.mode && (env.mode.actual === 'production' || env.mode.configured === 'production');
}

// "Build Magento afterwards" — ticked; locked on in production mode.
function BuildChoice(env, id, quickText) {
  const production = envProduction(env);
  const box = Checkbox({ id, checked: true, disabled: production });
  const node = h('div', { class: 'space-y-1 text-left' },
    h('div', { class: 'flex items-center gap-2' }, box, Label('Build Magento afterwards', id)),
    h('p', { class: 'text-muted-foreground text-xs', text: production
      ? 'Always on in production mode: the code is locked and compiled, so the change only takes effect with setup:upgrade, di:compile and static content (maintenance mode on meanwhile).'
      : `setup:upgrade, di:compile, static content and cache flush, with maintenance mode on — the same as Build Magento. ${quickText}` }));
  return { node, value: () => production || box.querySelector('input').checked };
}

function selectedModules() {
  return S.modules.modules.filter((m) => S.modulesSel.has(m.name));
}

// Packages behind the given modules that have an update, once each.
function updatablePackages(mods) {
  const seen = new Map();
  for (const m of mods) {
    if (m.update.status === 'update-available' && m.package && !seen.has(m.package)) {
      seen.set(m.package, { package: m.package, version: m.version, latest: m.update.latest, major: !!m.update.major, modules: [] });
    }
    if (seen.has(m.package)) seen.get(m.package).modules.push(m.name);
  }
  return [...seen.values()];
}

// Enable / disable: what depends on these (or what they need), and whether
// to build afterwards, then one bin/magento call for all of them.
async function modulesToggle(env, mods, op) {
  const all = new Map(S.modules.modules.map((x) => [x.name, x]));
  const names = new Set(mods.map((m) => m.name));
  const blockers = [...new Set(mods.flatMap((m) => (op === 'disable'
    ? m.dependents.filter((d) => (all.get(d) || {}).enabled)
    : m.sequence.filter((d) => (all.get(d) || {}).enabled === false))))].filter((d) => !names.has(d));
  const build = BuildChoice(env, 'mod-build', 'Unticked, only the module list changes; build before using the store.');
  const title = mods.length === 1 ? `${capitalize(op)} ${mods[0].name}?` : `${capitalize(op)} ${mods.length} modules?`;
  const go = await dialog({
    title,
    description: [
      h('p', { text: `Runs bin/magento module:${op} --clear-static-content ${mods.length === 1 ? mods[0].name : 'with all of them'}. Magento checks the dependencies itself and refuses a change that would break another module.` }),
      mods.length > 1 ? h('p', { class: 'text-foreground break-words', text: mods.map((m) => m.name).join(', ') }) : null,
      blockers.length ? Alert({ variant: 'warning', iconName: 'alert', title: op === 'disable' ? 'Enabled modules depend on these' : 'These need modules that are disabled' },
        h('p', { text: blockers.join(', ') }),
        h('p', { text: op === 'disable' ? 'Magento will refuse until they’re disabled too.' : 'Magento will refuse until they’re enabled too.' })) : null,
      build.node,
      h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` }),
    ],
    actions: [{ label: 'Cancel', value: false }, { label: capitalize(op), value: true, variant: op === 'disable' ? 'destructive' : 'default' }],
  });
  if (!go) return;
  const withBuild = build.value();
  const what = mods.length === 1 ? mods[0].name : `${mods.length} modules`;
  runAction(env, `module_${op}`, { modules: mods.map((m) => m.name), build: withBuild }, {
    noConfirm: true, label: `${capitalize(op)} ${what}${withBuild ? ' + build' : ''}`,
    onDone: (w) => { if (w.rec.status === 'done') { S.modulesSel.clear(); loadModules().then(render); } },
  });
}

// Updating packages: review (installed → latest, minor/patch or major),
// a dry run that resolves everything and changes nothing, then — only
// after that dry run passed, for exactly the same packages — the update.
async function packagesUpdate(env, pkgs) {
  const majors = pkgs.filter((p) => p.major).length;
  // One package: it was picked on purpose, major or not. Several: majors
  // only when asked for.
  const includeMajor = Checkbox({ id: 'upd-major', checked: pkgs.length === 1 });
  const build = BuildChoice(env, 'upd-build', 'Unticked (developer/default mode), only setup:upgrade and cache:flush run — the store may need a build before it looks right.');
  const go = await dialog({
    title: pkgs.length === 1 ? `Update ${pkgs[0].package}?` : `Update ${pkgs.length} packages?`,
    description: [
      h('p', { text: 'Only these packages and their own dependencies move (composer require --update-with-dependencies). Minor and patch updates keep composer.json’s constraint; a major update sets it to ^<latest>. A dry run comes first — nothing changes until it passes and you apply it.' }),
      h('div', { class: 'max-h-64 overflow-auto text-left' }, Table(
        TableHeader(TableRow(TableHead('Package'), TableHead('Installed → latest'), TableHead('Kind'))),
        TableBody(pkgs.map((p) => TableRow(TableCell(p.package), TableCell(`${p.version || '?'} → ${p.latest}`),
          TableCell(p.major ? Badge('Major', 'warning') : Badge('Minor / patch', 'secondary'))))))),
      majors && pkgs.length > 1 ? h('div', { class: 'space-y-1 text-left' },
        h('div', { class: 'flex items-center gap-2' }, includeMajor, Label(`Include major versions (${majors})`, 'upd-major')),
        h('p', { class: 'text-muted-foreground text-xs', text: 'A major version can change how the module works. Left unticked, those packages are skipped.' })) : null,
      build.node,
      h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` }),
    ],
    actions: [{ label: 'Cancel', value: false }, { label: 'Run dry run', value: true }],
  });
  if (!go) return;
  const chosen = pkgs.filter((p) => !p.major || includeMajor.querySelector('input').checked);
  if (!chosen.length) return toast('Nothing to update — every package there is a major update.', 'warning');
  const packages = chosen.map((p) => p.package);
  const withBuild = build.value();
  const what = packages.length === 1 ? packages[0] : `${packages.length} packages`;
  runAction(env, 'module_update', { packages, dryRun: true, build: withBuild }, {
    noConfirm: true, label: `Dry run: update ${what}`,
    onDone: async (w) => {
      if (w.rec.status !== 'done') {
        toast('The dry run found a problem — its output names the packages involved. Leave those out and try again.', 'warning');
        return;
      }
      const apply = await confirmDialog({
        title: `Dry run passed — update ${what}?`,
        text: [h('p', { text: `Composer can install these versions. Applying updates ${packages.join(', ')}${withBuild ? ', then builds Magento with maintenance mode on' : ', then runs setup:upgrade and cache:flush'}. If composer fails, composer.json and composer.lock are put back.` }),
          h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` })],
        confirm: 'Apply update',
      });
      if (!apply) return;
      runAction(env, 'module_update', { packages, dryRun: false, build: withBuild }, {
        noConfirm: true, label: `Update ${what}${withBuild ? ' + build' : ''}`,
        onDone: () => { S.modulesSel.clear(); loadModules().then(render); },
      });
    },
  });
}

function checkUpdates(env) {
  runAction(env, 'module_check_updates', {}, {
    noConfirm: true, label: 'Check for module updates',
    onDone: () => loadModules().then(render),
  });
}

function UpdateCell(m) {
  const u = m.update;
  if (u.status === 'update-available') {
    return h('span', { class: 'flex items-center gap-1', title: u.note || null },
      Badge(u.latest, 'warning', 'download'), u.major ? Badge('Major', 'outline') : null);
  }
  const [text, variant, ic] = UPDATE_BADGE[u.status] || [u.status, 'outline'];
  return h('span', { class: 'inline-flex', title: u.reason || u.note || null }, Badge(text, variant, ic));
}

function ModulesTable(env) {
  const data = S.modules;
  const f = S.modulesFilter;
  const rows = data.modules.filter((m) => moduleMatches(m, f));
  const pages = Math.max(1, Math.ceil(rows.length / MODULE_PAGE));
  S.modulesPage = Math.min(S.modulesPage, pages);
  const first = (S.modulesPage - 1) * MODULE_PAGE;
  const shown = rows.slice(first, first + MODULE_PAGE);
  const busy = envBlocked(env);
  const avail = (n) => !env.readonly && env.actions[n] && env.actions[n].available;

  // Select all = every matching row on every page, never core (tick those
  // one by one with the core filter on).
  const selectable = rows.filter((m) => m.enabled != null && !m.core);
  const selCount = selectable.filter((m) => S.modulesSel.has(m.name)).length;
  const all = Checkbox({
    'aria-label': 'Select all matching modules', checked: selectable.length > 0 && selCount === selectable.length,
    indeterminate: selCount > 0 && selCount < selectable.length, disabled: !selectable.length,
    onChange: (e) => {
      for (const m of selectable) { if (e.target.checked) S.modulesSel.add(m.name); else S.modulesSel.delete(m.name); }
      redrawModules();
    },
  });

  const body = shown.map((m) => {
    const [text, variant] = MODULE_STATUS[m.enabled];
    const op = m.enabled ? 'disable' : 'enable';
    return TableRow(
      TableCell(m.enabled == null ? null : Checkbox({
        'aria-label': `Select ${m.name}`, checked: S.modulesSel.has(m.name),
        onChange: (e) => { if (e.target.checked) S.modulesSel.add(m.name); else S.modulesSel.delete(m.name); redrawModules(); },
      }), 'w-8'),
      TableCell(h('div', { class: 'min-w-0' },
        h('div', { class: 'font-medium', text: m.name }),
        h('div', { class: 'text-muted-foreground text-xs', text: m.package || (m.source === 'app/code' ? 'app/code — no package' : 'no module.xml found') }))),
      TableCell(Badge(text, variant)),
      TableCell(m.version || m.setupVersion || '—', 'text-muted-foreground'),
      TableCell(UpdateCell(m)),
      TableCell(m.core ? Badge('Core', 'secondary') : Badge(m.source === 'app/code' ? 'app/code' : 'Third-party', 'outline')),
      TableCell(h('div', { class: 'flex justify-end gap-2' },
        m.update.status === 'update-available' ? Button({
          size: 'sm', label: 'Update', disabled: busy || !avail('module_update'),
          onClick: () => packagesUpdate(env, updatablePackages([m])),
        }) : null,
        m.enabled == null ? null : Button({
          variant: 'outline', size: 'sm', label: capitalize(op), disabled: busy || !avail(`module_${op}`),
          onClick: () => modulesToggle(env, [m], op),
        }))));
  });

  let bar = null;
  if (S.modulesSel.size) {
    const sel = selectedModules();
    const pkgs = updatablePackages(sel);
    const toEnable = sel.filter((m) => m.enabled === false);
    const toDisable = sel.filter((m) => m.enabled === true);
    const tooMany = (n) => n > MODULE_BULK_MAX;
    bar = SelectionBar(`${sel.length} selected`,
      Button({ size: 'sm', iconName: 'download', label: `Update selected (${pkgs.length})`,
        disabled: busy || !pkgs.length || tooMany(pkgs.length) || !avail('module_update'), onClick: () => packagesUpdate(env, pkgs) }),
      Button({ variant: 'outline', size: 'sm', label: `Enable selected (${toEnable.length})`,
        disabled: busy || !toEnable.length || tooMany(toEnable.length) || !avail('module_enable'), onClick: () => modulesToggle(env, toEnable, 'enable') }),
      Button({ variant: 'outline', size: 'sm', label: `Disable selected (${toDisable.length})`,
        disabled: busy || !toDisable.length || tooMany(toDisable.length) || !avail('module_disable'), onClick: () => modulesToggle(env, toDisable, 'disable') }),
      Button({ variant: 'ghost', size: 'sm', label: 'Clear', onClick: () => { S.modulesSel.clear(); redrawModules(); } }));
  }

  return h('div', { id: 'modules-table', class: 'space-y-3' },
    bar,
    h('p', { class: 'text-muted-foreground text-xs', text: rows.length
      ? `Showing ${first + 1}–${first + shown.length} of ${rows.length} matching · ${data.modules.length} modules in total`
      : `${data.modules.length} modules in total` }),
    rows.length ? Table(
      TableHeader(TableRow(TableHead(all, 'w-8'), TableHead('Module'), TableHead('Status'), TableHead('Installed'), TableHead('Update'),
        TableHead('Source'), TableHead('', 'text-right'))),
      TableBody(body)) : h('p', { class: 'text-muted-foreground text-sm', text: 'No modules match these filters.' }),
    pages > 1 ? Pagination({ page: S.modulesPage, pageCount: pages, label: 'Module pages',
      onChange: (p) => { S.modulesPage = p; redrawModules(); } }) : null);
}

// Filters, paging and selection only redraw the table, so the search box
// keeps its focus.
function redrawModules() {
  const slot = document.getElementById('modules-table');
  if (slot && S.detail && S.modules) slot.replaceWith(ModulesTable(S.detail));
}

function UpdatesNote(updates) {
  if (!updates) return 'Updates: not checked yet.';
  const at = when(updates.checkedAt) || updates.checkedAt;
  if (!updates.ok) return `Updates: the last check (${at}) failed.`;
  return `Updates checked ${at}${updates.stale ? ' — composer.lock changed since, check again' : ''}.`;
}

function ModulesTab(env) {
  const data = S.modules;
  if (!data) return S.modulesError ? Alert({ variant: 'destructive', iconName: 'alert', title: 'Can’t read the modules' }, h('p', { text: S.modulesError }))
    : LoadingText('Reading the modules…');
  const f = S.modulesFilter;
  if (!modSearch) {
    modSearch = SearchInput({ 'aria-label': 'Search modules', placeholder: 'Search by module or package name' });
    modSearch.querySelector('input').addEventListener('input', (e) => { S.modulesFilter.q = e.target.value; S.modulesPage = 1; redrawModules(); });
  }
  const filter = (key, options) => NativeSelect({
    'aria-label': key === 'status' ? 'Status' : 'Source', options, value: f[key],
    onChange: (e) => { f[key] = e.target.value; S.modulesPage = 1; redrawModules(); },
  });
  const enabled = data.modules.filter((m) => m.enabled).length;
  const updates = data.modules.filter((m) => m.update.status === 'update-available').length;
  const avail = !env.readonly && env.actions.module_check_updates && env.actions.module_check_updates.available;
  return h('div', { class: 'space-y-4' },
    env.services.php.level !== 'ok' ? Alert({ variant: 'warning', iconName: 'alert', title: 'PHP-FPM isn’t running' },
      h('p', { text: 'The list is read from the files, but checking for updates, updating and enabling or disabling run in the php container — start the environment first.' })) : null,
    data.error ? Alert({ variant: 'warning', iconName: 'alert', title: 'Can’t read which modules are on' }, h('p', { text: data.error })) : null,
    data.updates && !data.updates.ok ? Alert({ variant: 'warning', iconName: 'alert', title: 'The last update check failed' },
      h('p', { text: 'Private repositories need their credentials in magento/auth.json. Composer said:' }),
      LogBlock({ size: 'sm', text: data.updates.error || 'No output.' })) : null,
    Card({},
      CardHeader(CardTitle('Modules'),
        CardDescription(`${enabled} enabled · ${data.modules.length - enabled} disabled or not registered · ${updates} with an update. ${UpdatesNote(data.updates)}`),
        CardAction(Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Check for updates',
          disabled: envBlocked(env) || !avail, onClick: () => checkUpdates(env) }))),
      CardContent(h('div', { class: 'space-y-4' },
        h('div', { class: 'flex flex-col gap-2 sm:flex-row sm:items-center' },
          h('div', { class: 'min-w-0 flex-1' }, modSearch), filter('status', MODULE_STATUSES), filter('source', MODULE_SOURCES)),
        ModulesTable(env)))));
}
