// MageDevKit dashboard, part 1 of 5: state, helpers, polling, actions and the
// page shell. Loaded in order by dashboard.html — dash-core.js, dash-env.js,
// dash-git.js, dash-modules.js, dashboard.js (boot) — as plain scripts sharing one global
// scope; kit.js (DOM helpers, icons, shadcn/ui recipes) comes first.

// MageDevKit dashboard client — every checkout's status, actions and the
// setup / refresh / version wizards. Talks to dashboard.py's JSON API; the
// DOM helpers, icons and shadcn/ui recipes come from kit.js (shared with the
// wizard). Vanilla JS, no dependencies. Tailwind classes must stay literal
// strings here — bin/lib/web-wizard/build-css scans this file.
'use strict';

const POLL_MS = 5000;
const OP_POLL_MS = 2000;
const SERVICE_ROWS = [
  ['docker', 'Docker'], ['nginx', 'Nginx'], ['php', 'PHP-FPM'], ['mariadb', 'MariaDB'],
  ['opensearch', 'OpenSearch'], ['rabbitmq', 'RabbitMQ'], ['valkey', 'Valkey'], ['magento', 'Magento'],
];
const TABS = [
  { id: 'overview', label: 'Overview', iconName: 'layoutGrid' }, { id: 'containers', label: 'Containers', iconName: 'server' },
  { id: 'magento', label: 'Magento', iconName: 'wrench' }, { id: 'modules', label: 'Modules', iconName: 'blocks' },
  { id: 'workflows', label: 'Workflows', iconName: 'zap' },
  { id: 'pipeline', label: 'Pipeline', iconName: 'rocket' }, { id: 'git', label: 'Git', iconName: 'gitBranch' },
  { id: 'logs', label: 'Logs', iconName: 'file' }, { id: 'operations', label: 'Operations', iconName: 'history' },
];
const STACK_TEXT = {
  running: ['Running', 'success'], partial: ['Partly running', 'warning'], stopped: ['Stopped', 'secondary'], absent: ['Not created', 'secondary'],
  unknown: ['Docker unavailable', 'outline'],
};
const OP_BADGE = {
  starting: ['Starting', 'secondary'], running: ['Running', 'secondary'], done: ['Done', 'success'], failed: ['Failed', 'destructive'],
  cancelled: ['Cancelled', 'secondary'], interrupted: ['Interrupted', 'warning'],
};

// The Modules tab's filters on first load and per environment: third-party
// modules, any status, no search.
function modulesFilterDefault() {
  return { q: '', status: 'all', source: 'third-party' };
}

const S = {
  route: { view: 'overview' },
  overview: null,
  detail: null,
  error: null,
  watch: null,        // { envId, opId, text, rec } — the action whose output is shown
  logs: { source: null, lines: '300', text: '', follow: false, loading: false },
  opLog: { opId: null, text: '' },
  disk: null,
  busy: false,
  git: null,          // GET …/git — this environment's magento/ repo
  gitError: null,
  gitSel: { staged: new Set(), changes: new Set() },
  gitDiff: null,      // { path, staged, data }
  gitNeedsLogin: false,
  modules: null,      // GET …/modules — this environment's Magento modules
  modulesError: null,
  modulesFilter: modulesFilterDefault(),
  modulesPage: 1,
  modulesSel: new Set(),  // selected module names (all pages)
};

const root = document.getElementById('app');
let contentSlot = null;
let headRight = null;
let logsPre = null;      // kept across re-renders so scroll position survives
let opLogPre = null;
let watchPre = null;
let gitMessage = null;   // the Git tab's commit message box, kept across re-renders

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// replaceChildren that skips null/false, like h() does.
function setKids(el, ...kids) {
  el.replaceChildren();
  return append(el, kids);
}

function unknown(v) {
  return v == null || v === '' ? 'Unknown' : String(v);
}

const DOT_CLASS = {
  ok: 'bg-success', warn: 'bg-warning', bad: 'bg-destructive', off: 'bg-muted-foreground/40', unknown: 'border border-muted-foreground/60',
};

function Dot(level) {
  return h('span', { class: cn('inline-block size-2 shrink-0 rounded-full', DOT_CLASS[level] || DOT_CLASS.unknown), 'aria-hidden': 'true' });
}

function StatusRow(label, st) {
  st = st || { level: 'unknown', text: 'Unknown' };
  return h('div', { class: 'flex items-center gap-2 text-sm' },
    Dot(st.level), h('span', { class: 'w-24 shrink-0 text-muted-foreground', text: label }), h('span', { class: 'truncate', text: st.text, title: st.text }));
}

// The stack's state as a dot level: ok / warn / unknown / off.
function envLevel(e) {
  const d = (e && e.docker) || {};
  return d.state === 'running' ? 'ok' : d.state === 'partial' ? 'warn' : d.state === 'unknown' ? 'unknown' : 'off';
}
function dockerStatus(env) {
  const d = env.docker;
  const [text] = STACK_TEXT[d.state] || ['Unknown'];
  return { level: envLevel(env), text: d.total ? `${text} (${d.running}/${d.total})` : text };
}
// Changes are blocked: read-only here, a request in flight, or another
// operation holds the instance lock.
function envBlocked(env) {
  return !!env.readonly || S.busy || !!(env.lock && env.lock.held);
}

// Whether this environment's platform code has action `name` and may run it.
function actionAvailable(env, name) {
  return !env.readonly && !!env.actions[name] && !!env.actions[name].available;
}

function statusFor(env, key) {
  if (key === 'docker') return dockerStatus(env);
  if (key === 'magento') return env.magento;
  return env.services[key];
}

// Label/value rows for the cards (DescriptionList with the narrow label column).
// Takes Field()s, nulls and arrays of Field()s (from .map).
function Fields(...fields) {
  return DescriptionList(fields.flat(1).filter(Boolean).map((f) => f.row), { labelWidth: 'sm' });
}
function Field(label, value, note) {
  return { row: [label, value, note] };
}

function gitText(g) {
  if (!g) return null;
  if (!g.commit) return g.error || null;
  return `${g.branch || '?'} / ${g.commit}`;
}

function modeText(m) {
  if (!m || !m.actual) return null;
  return m.configured && m.configured !== m.actual ? `${m.actual} (configured: ${m.configured})` : m.actual;
}

function capitalize(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function siteUrl(env, path) {
  return env.domain && /^[a-z0-9.-]+$/i.test(env.domain) ? `https://${env.domain}/${path || ''}` : null;
}

function when(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t).toLocaleString();
}

function took(a, b) {
  const t0 = Date.parse(a); const t1 = b ? Date.parse(b) : Date.now();
  if (Number.isNaN(t0) || Number.isNaN(t1)) return null;
  return fmtDuration((t1 - t0) / 1000);
}

function OpBadge(status) {
  const [text, variant] = OP_BADGE[status] || [capitalize(status || 'unknown'), 'secondary'];
  const busy = status === 'running' || status === 'starting';
  return Badge(text, variant, busy ? 'loader' : null, { spin: busy });
}

// api() with the page marked busy meanwhile (buttons disabled, spinner):
// one request at a time from the toolbar.
async function busyApi(path, body) {
  S.busy = true;
  render();
  try {
    return await api(path, body);
  } finally {
    S.busy = false;
  }
}

function serverError(res) {
  return res.status === 0 ? 'Can’t reach the dashboard server — is bin/local/dashboard still running?' : errorText(res);
}

function errorText(res) {
  const d = res.data || {};
  return d.error || `Request failed (${res.status})`;
}

// ---------------------------------------------------------------------------
// Routing + polling
// ---------------------------------------------------------------------------

function parseRoute() {
  const m = location.hash.match(/^#\/env\/([a-z0-9_-]+)(?:\/([a-z]+))?$/);
  if (m) return { view: 'env', id: m[1], tab: TABS.some((t) => t.id === m[2]) ? m[2] : 'overview' };
  return { view: 'overview' };
}

window.addEventListener('hashchange', () => {
  const prev = S.route;
  S.route = parseRoute();
  if (prev.view !== S.route.view || prev.id !== S.route.id) {
    S.detail = null; S.disk = null; S.opLog = { opId: null, text: '' };
    S.logs = { source: null, lines: '300', text: '', follow: false, loading: false };
    logsPre = null; opLogPre = null;
    S.git = null; S.gitError = null; S.gitDiff = null; S.gitNeedsLogin = false;
    S.gitSel = { staged: new Set(), changes: new Set() };
    gitMessage = null;
    S.modules = null; S.modulesError = null; S.modulesFilter = modulesFilterDefault(); S.modulesPage = 1; S.modulesSel = new Set();
    modSearch = null;
  }
  render();
  refresh();
});

let pollTimer = null;
async function refresh() {
  clearTimeout(pollTimer);
  if (S.route.view === 'env') {
    const res = await getJSON(`/api/environments/${encodeURIComponent(S.route.id)}`);
    if (res.status === 403) return lost();
    // Server unreachable (status 0): keep showing what we had, with the banner.
    if (res.status !== 0) S.detail = res.ok ? res.data : null;
    S.error = res.ok ? null : serverError(res);
    if (S.route.tab === 'logs' && S.logs.follow) await loadLog(true);
    if (S.route.tab === 'git') await loadGit();
    if (S.route.tab === 'modules') await loadModules();
    // The sidebar lists every environment: refreshed every 30 s here.
    if (!S.overview || Date.now() - (S.overviewAt || 0) > 30000) {
      const o = await getJSON('/api/environments');
      if (o.ok) { S.overview = o.data; S.overviewAt = Date.now(); }
    }
  } else {
    const res = await getJSON('/api/environments');
    if (res.status === 403) return lost();
    S.overview = res.ok ? res.data : S.overview;
    S.overviewAt = Date.now();
    S.error = res.ok ? null : serverError(res);
  }
  // Don't rebuild the page under an open dropdown, a focused field or a dialog.
  if (!userBusy()) render();
  if (!document.hidden) pollTimer = setTimeout(refresh, POLL_MS);
}

function lost() {
  S.error = 'This page isn’t signed in to the dashboard any more — open the link printed by bin/local/dashboard.';
  render();
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refresh();
  else clearTimeout(pollTimer);
});

// ---------------------------------------------------------------------------
// Actions and workflows
// ---------------------------------------------------------------------------

async function runAction(env, name, body, opts = {}) {
  const spec = (S.detail && S.detail.actions && S.detail.actions[name]) || {};
  const label = opts.label || spec.label || name;
  if (!opts.noConfirm && (spec.confirm || opts.confirm)) {
    const ok = await dialog({
      title: `${label}?`,
      description: [h('p', { text: opts.confirm || spec.confirm }), h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` })],
      actions: [{ label: 'Cancel', value: false }, { label: 'Continue', value: true, variant: opts.destructive ? 'destructive' : 'default' }],
    });
    if (!ok) return;
  }
  const res = await api(`/api/environments/${encodeURIComponent(env.id)}/actions/${name}`, body || {});
  if (!res.ok) return showError(`${label} didn’t start`, res);
  toast(`${label} started`);
  S.watch = { envId: env.id, opId: res.data.operation, label, text: '', rec: { status: 'starting' }, onDone: opts.onDone };
  watchPre = null;
  watchOperation();
  refresh();
}

let watchTimer = null;
async function watchOperation() {
  clearTimeout(watchTimer);
  const w = S.watch;
  if (!w) return;
  const res = await getJSON(`/api/environments/${encodeURIComponent(w.envId)}/operations/${encodeURIComponent(w.opId)}/log?lines=400`);
  if (S.watch !== w) return;
  if (res.ok) {
    w.text = res.data.text;
    w.rec = res.data.operation;
  }
  renderWatch();
  if (!res.ok || ['starting', 'running'].includes(w.rec.status)) {
    watchTimer = setTimeout(watchOperation, OP_POLL_MS);
  } else {
    toast(`${w.label}: ${(OP_BADGE[w.rec.status] || [w.rec.status])[0].toLowerCase()}`, w.rec.status === 'done' ? 'default' : 'destructive');
    if (w.onDone) w.onDone(w);
    refresh();
  }
}

async function cancelOperation(envId, opId) {
  const ok = await confirmDialog({
    title: 'Cancel this operation?',
    text: 'The running command is stopped. Anything it already finished stays done.',
    cancel: 'Keep running', confirm: 'Cancel operation', variant: 'destructive',
  });
  if (!ok) return;
  const res = await api(`/api/environments/${encodeURIComponent(envId)}/operations/${encodeURIComponent(opId)}/cancel`, {});
  if (!res.ok) return showError('Couldn’t cancel', res);
  toast('Cancelling…');
}

const WORKFLOW_TEXT = {
  setup: {
    icon: 'box', desc: 'Re-runs the setup wizard for this checkout: configuration, certificate, containers, Magento install and data. Safe to repeat — finished steps are skipped.',
    confirm: null,
  },
  refresh: {
    icon: 'database', desc: 'Replaces this environment’s database with a dump (upload, file or URL), then rebuilds Magento. The current database is backed up first.',
    confirm: 'This opens the refresh wizard. It replaces the current database contents — the wizard asks you to confirm by typing the project name before anything is replaced.',
  },
  version: {
    icon: 'history', desc: 'Upgrades Magento to a version from the m2/ catalog, or rolls back to a snapshot. A snapshot is taken before anything changes.',
    confirm: 'This opens the version wizard. Upgrades and rollbacks change code, containers and the database — it shows a review and asks for confirmation first.',
  },
  rebuild: {
    icon: 'rotateCw', desc: 'composer (optional), setup:upgrade, di:compile, static content, reindex and cache — everything that makes code changes live.',
    confirm: 'Rebuilds Magento: setup:upgrade, di:compile, static content deploy, reindex and cache flush. The store is briefly offline while it compiles.',
  },
  pipeline: {
    icon: 'rocket', desc: 'Writes the pipeline kit (build, deploy scripts, EC2 stack, Terraform) into magento/deploy/ and commits it on a local branch. Nothing is pushed.',
    confirm: null,
  },
};

async function startWorkflow(env, flow, body = {}, opts = {}) {
  const wt = WORKFLOW_TEXT[flow] || {};
  const label = opts.label || (S.detail.workflows[flow] || {}).label || flow;
  const confirmText = opts.noConfirm ? null : opts.confirm || wt.confirm;
  if (confirmText) {
    const ok = await dialog({
      title: `${label}?`,
      description: [h('p', { text: confirmText }), h('p', { class: 'font-medium', text: `Environment: ${env.name} (${unknown(env.domain)})` })],
      actions: [{ label: 'Cancel', value: false }, { label: 'Open wizard', value: true }],
    });
    if (!ok) return;
  }
  // Open the tab now, while the click still counts as a user action
  // (popup blockers), and point it at the wizard once it is up.
  const tab = openPendingTab('Starting the wizard…');
  const res = await busyApi(`/api/environments/${encodeURIComponent(env.id)}/workflows/${flow}`, body);
  if (!res.ok) {
    if (tab) tab.close();
    render();
    return showError(`${label} didn’t start`, res);
  }
  if (!navigateTab(tab, res.data.url)) toast('Your browser blocked the new tab — use “Open wizard” on the Operations tab.', 'destructive');
  toast(`${label} wizard started`);
  location.hash = `#/env/${env.id}/operations`;
  refresh();
}

// The same rules as the server (dashboard.py, ops.PACKAGE_RE, gitrepo.BRANCH_RE)
// and the bash side (bin/lib/util.sh) — checked here only for a quick message.
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const PACKAGE_RE = /^[a-z0-9][a-z0-9._-]*\/[a-z0-9*][a-z0-9._*-]*$/;
const BRANCH_RE = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$/;

async function newInstance(o) {
  const input = Input({ id: 'new-name', placeholder: 'e.g. m249', maxlength: '40', 'aria-describedby': 'new-name-help' });
  const help = h('p', { id: 'new-name-help', class: 'text-muted-foreground text-xs', text: `Lowercase letters, digits, _ and -. Becomes the folder ${o.instancesRoot}/<name> and the project name (APP_NAME).` });
  const taken = new Set((o.environments || []).map((e) => e.id));
  let name = null;
  const go = await formDialog({
    title: 'New instance',
    description: [h('div', { class: 'space-y-2 text-left' }, Label('Name', 'new-name'), input, help)],
    actions: [{ label: 'Cancel', value: false }, { label: 'Create and open setup', value: true }],
    validate: () => {
      name = input.value.trim();
      if (!NAME_RE.test(name)) markInvalid(input, help, 'Use lowercase letters, digits, _ and - only (max 40), starting with a letter or digit.');
      else if (taken.has(name)) markInvalid(input, help, `There is already an environment called ${name}.`);
      else return true;
      return false;
    },
  });
  if (!go) return;
  const tab = openPendingTab('Creating the instance…');
  const res = await busyApi('/api/instances', { name });
  if (!res.ok) {
    if (tab) tab.close();
    render();
    return showError(`Couldn’t create ${name}`, res);
  }
  if (!navigateTab(tab, res.data.url)) toast('Your browser blocked the new tab — use “Open wizard” on the Operations tab.', 'destructive');
  toast(`${name} created — the setup wizard is open`);
  location.hash = `#/env/${name}/operations`;
  refresh();
}

async function reopenWizard(envId, opId) {
  const tab = openPendingTab('Opening the wizard…');
  const res = await getJSON(`/api/environments/${encodeURIComponent(envId)}/operations/${encodeURIComponent(opId)}`);
  if (!res.ok || !res.data.url) {
    if (tab) tab.close();
    return showError('This wizard isn’t running any more', res.ok ? { data: { error: 'Its page has closed.' }, status: 200 } : res);
  }
  if (!navigateTab(tab, res.data.url)) toast('Your browser blocked the new tab — allow pop-ups for this page and try again.', 'destructive');
}

function showError(title, res) {
  const d = res.data || {};
  dialog({
    title,
    description: [
      h('p', { text: d.error || `Request failed (${res.status})` }),
      d.detail ? LogBlock({ size: 'sm', text: d.detail, cls: 'text-left' }) : null,
    ],
    actions: [{ label: 'Close', value: null }],
  });
}

async function loadLog(quiet) {
  const env = S.detail;
  if (!env || !S.logs.source) return;
  S.logs.loading = !quiet;
  if (!quiet) renderLogText();
  const res = await getJSON(`/api/environments/${encodeURIComponent(env.id)}/logs?source=${encodeURIComponent(S.logs.source)}&lines=${S.logs.lines}`);
  S.logs.loading = false;
  S.logs.text = res.ok ? res.data.text : `${errorText(res)}${res.data && res.data.detail ? `\n${res.data.detail}` : ''}`;
  renderLogText();
}

async function loadOpLog(envId, src, opId) {
  S.opLog = { opId, text: 'Loading…' };
  render();
  const res = await getJSON(`/api/environments/${encodeURIComponent(envId)}/logs?source=${encodeURIComponent(src)}&lines=1000`);
  S.opLog.text = res.ok ? res.data.text : errorText(res);
  render();
}

async function loadDisk(env) {
  S.disk = { status: 'computing' };
  render();
  for (let i = 0; i < 120; i++) {
    const res = await getJSON(`/api/environments/${encodeURIComponent(env.id)}/disk`);
    if (!S.detail || S.detail.id !== env.id) return;
    if (!res.ok) { S.disk = { status: 'error', error: errorText(res) }; break; }
    S.disk = res.data;
    if (res.data.status === 'done' || res.data.status === 'error') break;
    render();
    await new Promise((r) => setTimeout(r, 3000));
  }
  render();
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

// The shell: the dark sidebar (environments, and the open
// one's sections) on md and up; below md the header shows the title and the
// environment page shows its sections as Tabs.
let sidebarSlot = null;
let footerSlot = null;
function buildShell() {
  headRight = h('div', { class: 'ml-auto flex items-center gap-2' });
  contentSlot = h('main', { class: 'mx-auto max-w-7xl space-y-6 px-4 py-6' });
  sidebarSlot = h('div', { class: 'flex min-h-full flex-col' });
  footerSlot = h('div', { class: 'mx-auto max-w-7xl' });
  root.replaceChildren(AppShell({
    sidebar: sidebarSlot,
    main: [
      PageHeader({ title: 'MageDevKit', subtitle: 'Magento Developer Kit · environments on this computer', href: '#/', width: '7xl', right: headRight, brandCls: 'md:hidden' }),
      contentSlot,
      footerSlot,
    ],
  }));
}


function renderSidebar() {
  const envs = [...((S.overview && S.overview.environments) || [])];
  const openId = S.route.view === 'env' ? S.route.id : null;
  if (openId && !envs.some((e) => e.id === openId) && S.detail) envs.push(S.detail);
  setKids(sidebarSlot,
    SidebarHeader({ title: 'MageDevKit', subtitle: 'Magento Developer Kit', href: '#/' }),
    SidebarGroup('Dashboard',
      SidebarItem({ href: '#/', iconName: 'layoutGrid', label: 'All environments', active: S.route.view !== 'env' })),
    SidebarGroup('Environments', envs.map((e) => [
      SidebarItem({ href: `#/env/${e.id}`, lead: Dot(envLevel(e)), label: e.name, active: e.id === openId && S.route.tab === 'overview' }),
      e.id === openId ? TABS.filter((t) => t.id !== 'overview').map((t) => SidebarItem({
        href: `#/env/${e.id}/${t.id}`, iconName: t.iconName, label: t.label, active: S.route.tab === t.id, nested: true,
      })) : null,
    ])),
    PageFooter(S.overview && S.overview.platformVersion, { sidebar: true }));
  setKids(footerSlot, PageFooter(S.overview && S.overview.platformVersion, { cls: 'md:hidden' }));
}

function render() {
  if (!root.firstChild) buildShell();
  setKids(headRight,
    S.busy ? Spinner('text-muted-foreground') : null,
    Button({ variant: 'ghost', size: 'icon', iconName: 'rotateCw', title: 'Refresh now', onClick: () => refresh() }),
    ThemeToggle(render));
  renderSidebar();
  if (S.route.view === 'env') renderEnvPage();
  else renderOverview();
}

function ErrorBanner() {
  return S.error ? Alert({ variant: 'destructive', iconName: 'alert', title: 'Problem' }, h('p', { text: S.error })) : null;
}
