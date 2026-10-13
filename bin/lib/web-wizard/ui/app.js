// Web wizard client — renders the state the server relays from the running
// bash script (bin/local/setup or bin/refresh) and sends answers back.
// Vanilla JS, no dependencies. The DOM helpers, icons and shadcn/ui component
// recipes live in kit.js (loaded first, shared with the dashboard). Tailwind
// classes must stay literal strings here — bin/lib/web-wizard/build-css scans
// this file.
'use strict';

// ---------------------------------------------------------------------------
// State + server API
// ---------------------------------------------------------------------------

const FLOW_TEXT = {
  setup: { name: 'Magento setup', verb: 'Setup', rerun: 'bin/local/setup-wizard' },
  refresh: { name: 'Database refresh', verb: 'Refresh', rerun: 'bin/local/refresh-wizard' },
  version: { name: 'Magento version', verb: 'Version switch', rerun: 'bin/local/version-wizard' },
  ownership: { name: 'File ownership', verb: 'Ownership switch', rerun: 'mdk ownership single|two-owner' },
  rebuild: { name: 'Magento rebuild', verb: 'Rebuild', rerun: 'mdk rebuild-wizard' },
  account: { name: 'AWS connection', verb: 'AWS connection', rerun: 'mdk aws add' },
  pipeline: {
    name: 'Pipeline', verb: 'Pipeline kit', rerun: 'mdk pipeline-wizard',
    review: 'Check everything below. Nothing is written until you continue; the kit goes into magento/deploy/ and is committed on a new local branch — nothing is pushed.',
  },
};

// Finish-screen wording per flow.
const DONE_TEXT = {
  setup: { title: 'Your store is ready', text: 'Everything is installed and running. The stack keeps running after you close this wizard.', details: 'Your store' },
  refresh: { title: 'Database replaced', text: 'The new data is imported, the code is rebuilt and the caches are fresh.', details: 'Details' },
  version: { title: 'Magento version switched', text: 'The new version is installed, the database is upgraded and the code is rebuilt. A snapshot of the previous version is kept for rolling back.', details: 'Details' },
  ownership: { title: 'File ownership switched', text: 'The writable folders have the new owner and php-fpm runs as the new user.', details: 'Details' },
  rebuild: { title: 'Magento is rebuilt', text: 'setup:upgrade, the DI compile and static content are done, the indexes are fresh and the caches flushed.', details: 'Details' },
  account: { title: 'AWS connection ready', text: 'MageDevKit can use this AWS account now. Its login is kept in MageDevKit\'s own files (~/.config/magedevkit/aws), apart from ~/.aws and from your stores.', details: 'Connection' },
  pipeline: { title: 'The store is pipeline-ready', text: 'The pipeline kit is in magento/deploy/ and committed on a local branch. Push it with the store; the AWS side comes from the provision wizard (coming in a later version).', details: 'Pipeline kit', commands: 'Next steps' },
};

const S = {
  state: null,
  logLines: [],
  connection: 'connecting', // connecting | live | reconnecting | lost | closed
  viewKey: null,
  reachable: null,
  reachTimer: null,
  submitting: false,
  notice: null,
  keepUploads: null, // null until the finish page first offers to keep an upload
};


function flowText() {
  return FLOW_TEXT[(S.state && S.state.flow) || 'setup'] || FLOW_TEXT.setup;
}

function isActive() {
  return S.state && (S.state.status === 'running' || S.state.status === 'starting');
}

function currentStep() {
  const st = S.state;
  if (!st || !st.current) return null;
  return { n: st.current, title: (st.steps[st.current - 1] || {}).title || '' };
}

function stepTasks(n) {
  return (S.state && S.state.tasks && S.state.tasks[String(n)]) || [];
}

function runningTask() {
  const st = currentStep();
  if (!st) return null;
  return stepTasks(st.n).find((t) => t.status === 'running') || null;
}

function progressValue() {
  const st = S.state;
  if (!st || !st.steps.length) return 0;
  if (st.status === 'done') return 100;
  const finished = st.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  const current = st.steps.some((s) => s.status === 'current') ? 0.5 : 0;
  return ((finished + current) / st.steps.length) * 100;
}

// ---------------------------------------------------------------------------
// Live connection
// ---------------------------------------------------------------------------

async function loadState() {
  try {
    const res = await getJSON('/api/state');
    if (!res.ok) throw new Error(String(res.status));
    const st = res.data;
    S.logLines = st.log || [];
    delete st.log;
    S.state = st;
    S.connection = 'live';
    render(true);
    return true;
  } catch (e) {
    return false;
  }
}

let eventSource = null;
let lostTimer = null;

function connect() {
  eventSource = new EventSource('/api/events');
  eventSource.addEventListener('state', (e) => {
    const st = JSON.parse(e.data);
    const lines = st.newLog || [];
    delete st.newLog;
    S.state = st;
    if (lines.length) appendLog(lines);
    if (S.connection !== 'live') {
      S.connection = 'live';
      clearTimeout(lostTimer);
    }
    render();
  });
  eventSource.addEventListener('closed', () => {
    S.connection = 'closed';
    eventSource.close();
    render(true);
  });
  eventSource.onerror = () => {
    if (S.connection === 'closed') return;
    S.connection = 'reconnecting';
    render();
    clearTimeout(lostTimer);
    lostTimer = setTimeout(async () => {
      if (S.connection === 'live') return;
      if (!(await loadState())) { S.connection = 'lost'; render(true); }
    }, 8000);
  };
}

// Background tabs give their live connection back (browsers allow only ~6
// per host, so several open tabs could otherwise stall a new one) and
// catch up from /api/state when shown again.
document.addEventListener('visibilitychange', async () => {
  if (S.connection === 'closed' || S.connection === 'lost') return;
  if (document.hidden) {
    if (eventSource) { eventSource.close(); eventSource = null; }
  } else if (!eventSource) {
    if (await loadState()) connect();
  }
});

// ---------------------------------------------------------------------------
// Page guards: refresh / back / close
// ---------------------------------------------------------------------------

window.addEventListener('beforeunload', (e) => {
  if (isActive() && S.connection !== 'closed') {
    e.preventDefault();
    e.returnValue = '';
  }
});

history.replaceState({ wizard: 'base' }, '');
history.pushState({ wizard: 'live' }, '');
window.addEventListener('popstate', () => {
  if (S.connection === 'closed') return;
  history.pushState({ wizard: 'live' }, '');
  if (isActive()) {
    S.notice = 'Steps can’t be undone while the wizard runs — it continues from here. To change an answer, use Change answers on the review step (it starts again with your answers filled in), or finish or cancel and run it again.';
    render(true);
  }
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

const root = document.getElementById('app');
let mainSlot = null;
let railSlot = null;
let footerSlot = null;
let headSlot = null;
let bannerSlot = null;
let logPre = null;

function render(force) {
  if (!root.firstChild) buildShell();
  const ft = flowText();
  const st = S.state;
  const step = currentStep();
  document.title = step && isActive()
    ? `Step ${step.n}/${st.steps.length} · ${step.title} — ${ft.name}`
    : `${ft.name} wizard`;

  renderHeader();
  renderRail();
  renderBanners();

  const key = viewKey();
  if (force || key !== S.viewKey) {
    S.viewKey = key;
    mainSlot.replaceChildren(renderMain());
    focusFirstInput();
  } else {
    refreshLiveParts();
  }
}

function viewKey() {
  const st = S.state;
  if (S.connection === 'closed') return 'closed';
  if (!st) return 'loading';
  if (st.question) return `q:${st.question.id}`;
  if (st.status === 'done') return 'done';
  if (st.status === 'failed') return 'failed';
  if (st.status === 'cancelled') return 'cancelled';
  return `work:${st.current}`;
}

// Same shell as the dashboard: the dark sidebar holds the flow's name and
// its steps; below md the header and the progress line carry them instead.
function buildShell() {
  const ft = flowText();
  headSlot = h('div');
  railSlot = h('div', { class: 'flex min-h-full flex-col' });
  bannerSlot = h('div', { class: 'space-y-3 empty:hidden' });
  mainSlot = h('div');
  logPre = LogBlock({ size: 'lg', bare: true, cls: 'px-6' });
  const logCard = Collapsible({ title: 'Show details (full log)', iconName: 'terminal' }, logPre,
    h('div', { class: 'mt-3 flex px-6' },
      Button({ variant: 'outline', size: 'sm', iconName: 'download', label: 'Download log', href: '/api/log', download: true })));
  logCard.addEventListener('toggle', () => { if (logCard.open) logPre.scrollTop = logPre.scrollHeight; });

  footerSlot = h('div', { class: 'mx-auto max-w-4xl' });
  root.replaceChildren(AppShell({
    sidebar: railSlot,
    main: [
      PageHeader({
        title: `${ft.name} wizard`, titleId: 'flow-name', subtitle: 'MageDevKit · running on this computer only', width: '4xl', brandCls: 'md:hidden',
        right: h('div', { class: 'ml-auto flex items-center gap-2', id: 'head-right' }),
      }),
      h('main', { class: 'mx-auto min-w-0 max-w-4xl space-y-4 px-4 py-6' }, headSlot, bannerSlot, mainSlot, logCard),
      footerSlot,
    ],
  }));
  appendLog(S.logLines, true);
}

function renderHeader() {
  const st = S.state;
  const ft = flowText();
  document.getElementById('flow-name').textContent = `${ft.name} wizard`;
  const right = document.getElementById('head-right');
  const badge = !st ? Badge('Connecting…', 'secondary')
    : S.connection === 'closed' ? Badge('Closed', 'secondary')
    : st.status === 'done' ? Badge('Finished', 'success', 'check')
    : st.status === 'failed' ? Badge('Stopped with an error', 'destructive', 'x')
    : st.status === 'cancelled' ? Badge('Cancelled', 'secondary')
    : st.cancelAfterTask ? Badge('Stopping after this task', 'warning')
    : Badge('Running', 'secondary', 'loader', { spin: true });
  right.replaceChildren();
  append(right, [
    badge,
    isActive() && S.connection === 'live'
      ? Button({ variant: 'ghost', size: 'sm', label: 'Cancel', onClick: onCancel }) : null,
    ThemeToggle(render)]);

  if (!st || !st.steps.length) { headSlot.replaceChildren(); return; }
  const step = currentStep();
  const label = st.status === 'done' ? `All ${st.steps.length} steps done`
    : step ? `Step ${step.n} of ${st.steps.length} · ${step.title}` : 'Starting…';
  headSlot.replaceChildren(
    h('div', { class: 'space-y-2' },
      h('div', { class: 'flex items-baseline justify-between gap-4' },
        h('p', { class: 'text-sm font-medium', 'aria-live': 'polite', text: label }),
        h('p', { class: 'text-muted-foreground text-xs tabular-nums', text: `${Math.round(progressValue())}%` })),
      Progress(progressValue(), 'Overall progress')));
}

function renderRail() {
  const st = S.state;
  const ft = flowText();
  const version = st && st.platformVersion;
  const steps = st && st.steps.length ? SidebarGroup('Steps', st.steps.map((s, i) => SidebarItem({
    label: `${i + 1}. ${s.title}${s.status === 'skipped' ? ' (skipped)' : ''}`,
    lead: StatusIcon(s.status, { waiting: !!st.question, sidebar: true }),
    active: s.status === 'current', current: 'step',
    muted: s.status === 'upcoming' || s.status === 'skipped', strike: s.status === 'skipped',
  }))) : null;
  railSlot.replaceChildren(...[
    SidebarHeader({ title: `${ft.name} wizard`, subtitle: 'MageDevKit · running on this computer only' }),
    steps,
    PageFooter(version, { sidebar: true }),
  ].filter(Boolean));
  footerSlot.replaceChildren(PageFooter(version, { cls: 'md:hidden' }));
}

function renderBanners() {
  const kids = [];
  if (S.connection === 'reconnecting') {
    kids.push(Alert({ variant: 'warning', iconName: 'wifiOff', title: 'Reconnecting…' },
      h('p', { text: 'Lost the connection to the wizard. It keeps running in the terminal; this page reconnects automatically.' })));
  } else if (S.connection === 'lost') {
    kids.push(Alert({ variant: 'destructive', iconName: 'wifiOff', title: 'The wizard stopped' },
      h('p', { text: 'This page can’t reach it any more — it was closed, or the terminal it ran in was stopped. Start it again from the dashboard (Workflows, or Run again on Operations), or in the instance folder:' }),
      CodeLine((S.state && S.state.rerunCommand) || flowText().rerun)));
  }
  if (S.state && S.state.cancelAfterTask && isActive()) {
    kids.push(Alert({ variant: 'warning', iconName: 'alert', title: 'Stopping after the current task' },
      h('p', { text: 'Nothing new will start. Running it again later continues from what’s done.' })));
  }
  if (S.notice) {
    const n = S.notice;
    kids.push(Alert({ iconName: 'info', title: 'Nothing was undone', onDismiss: () => { S.notice = null; render(); } }, h('p', { text: n })));
  }
  if (isActive() && S.connection === 'live') {
    const task = runningTask();
    if (task && /don.t interrupt/i.test(task.label)) {
      kids.push(Alert({ variant: 'warning', iconName: 'alert', title: 'Keep the terminal open now' },
        h('p', { text: 'The database is being replaced. Closing the terminal at this point would leave it half-imported.' })));
    }
  }
  bannerSlot.replaceChildren(...kids);
}

function renderMain() {
  const st = S.state;
  if (S.connection === 'closed') return ClosedView();
  if (!st) return LoadingView();
  if (st.question) return QuestionView(st.question);
  if (st.status === 'done') return FinishView();
  if (st.status === 'failed') return StoppedView(true);
  if (st.status === 'cancelled') return StoppedView(false);
  return WorkingView();
}

function refreshLiveParts() {
  // The working view has no inputs, so it's simply rebuilt; question and
  // finish views keep their DOM (focus, typed text, upload progress).
  if (S.viewKey && S.viewKey.startsWith('work:')) {
    mainSlot.replaceChildren(WorkingView());
    return;
  }
  const list = mainSlot.querySelector('[data-tasks]');
  if (list) list.replaceWith(TaskList(Number(list.dataset.tasks)));
  const last = mainSlot.querySelector('[data-lastline]');
  if (last) last.textContent = lastLogLine();
}

function focusFirstInput() {
  const el = mainSlot.querySelector('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), [data-autofocus]');
  if (el) setTimeout(() => el.focus({ preventScroll: true }), 30);
}

// Step (rail) and task status: done / failed / skipped|stopped / current|running
// (a spinner, or a dot while it waits for an answer) / upcoming.
// sidebar: colours for the dark menu instead of the page.
function StatusIcon(status, { waiting, sidebar } = {}) {
  if (status === 'done') return icon('circleCheck', sidebar ? 'text-sidebar-success' : 'text-success');
  if (status === 'failed') return icon('circleX', sidebar ? 'text-sidebar-destructive' : 'text-destructive');
  if (status === 'skipped' || status === 'stopped') return icon('circleMinus', sidebar ? null : 'text-muted-foreground');
  if (status === 'upcoming') return icon('circle', sidebar ? null : 'text-muted-foreground/60');
  return waiting ? icon('circleDot', sidebar ? 'text-sidebar-accent-foreground' : 'text-foreground')
    : Spinner(sidebar ? 'text-sidebar-accent-foreground' : 'text-foreground');
}

// -- log ----------------------------------------------------------------------

// "2026-10-08 12:00:00 - " prefix on every log line.
const LOG_STAMP = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d - /;

function appendLog(lines, reset) {
  if (reset) {
    S.logLines = lines.slice(-4000);
    if (logPre) logPre.textContent = S.logLines.join('\n');
  } else {
    S.logLines.push(...lines);
    if (S.logLines.length > 4000) S.logLines.splice(0, S.logLines.length - 4000);
    if (logPre) setLogText(logPre, S.logLines.join('\n'));
  }
}

function lastLogLine() {
  for (let i = S.logLines.length - 1; i >= 0; i--) {
    const l = S.logLines[i].replace(LOG_STAMP, '').trim();
    if (l) return l;
  }
  return '';
}

// -- views --------------------------------------------------------------------

function LoadingView() {
  return Card({},
    CardHeader(h('div', { class: 'flex items-center gap-2' }, Spinner('text-muted-foreground'), CardTitle('Starting…'))),
    CardContent(h('div', { class: 'space-y-2' },
      Skeleton('h-4 w-2/3'), Skeleton('h-4 w-1/2'))));
}

function ClosedView() {
  return Card({},
    CardHeader(CardTitle('Wizard closed'), CardDescription('Your store keeps running — start the wizard again from the dashboard or the terminal whenever you need it.')),
    CardContent(Alert({ iconName: 'info', title: 'You can close this tab now' },
      h('p', {}, 'Press ', Kbd('Ctrl'), ' + ', Kbd('W'), ' (or ', Kbd('⌘'), ' + ', Kbd('W'), ' on a Mac). Browsers only let a page close tabs that a page opened — like the ones the dashboard opens — not tabs opened from the terminal.'))));
}

function TaskList(n) {
  const tasks = stepTasks(n);
  const now = S.state ? S.state.now : Date.now() / 1000;
  const items = tasks.map((t) => {
    const ic = StatusIcon(t.status);
    const secs = t.secs != null ? t.secs : Math.max(0, Math.round(now - t.started));
    return h('li', { class: 'flex items-start gap-3 py-2' },
      h('span', { class: 'mt-0.5' }, ic),
      h('div', { class: 'min-w-0 flex-1' },
        h('p', { class: cn('text-sm', t.status === 'running' ? 'font-medium' : null), text: t.label }),
        t.status === 'running' ? h('p', { class: 'text-muted-foreground mt-0.5 truncate text-xs', 'data-lastline': '', text: lastLogLine() }) : null),
      h('span', {
        class: 'text-muted-foreground shrink-0 text-xs tabular-nums',
        dataset: t.status === 'running' ? { since: String(t.started) } : undefined,
        text: fmtDuration(secs),
      }));
  });
  return h('ul', { class: 'divide-y', 'data-tasks': String(n), 'aria-live': 'polite' }, items);
}

// Elapsed timers tick locally between server updates.
setInterval(() => {
  const skew = S.state ? S.state.now - Date.now() / 1000 : 0;
  document.querySelectorAll('[data-since]').forEach((el) => {
    el.textContent = fmtDuration(Date.now() / 1000 + skew - Number(el.dataset.since));
  });
}, 1000);

function WorkingView() {
  const step = currentStep();
  const n = step ? step.n : 0;
  const tasks = stepTasks(n);
  return Card({},
    CardHeader(
      CardTitle(step ? step.title : 'Starting…', 'text-lg'),
      CardDescription(tasks.length ? 'Working on it — you can watch the details below.' : 'Getting things ready…')),
    CardContent(tasks.length ? TaskList(n)
      : h('div', { class: 'flex items-center gap-3 text-sm' }, Spinner('text-muted-foreground'),
        h('span', { class: 'text-muted-foreground truncate', 'data-lastline': '', text: lastLogLine() }))));
}

// -- questions ------------------------------------------------------------------

function splitDefault(label) {
  // "Question? [value]" -> ["Question?", "value"]
  const m = label.match(/^([\s\S]*?)\s*\[([^\]]*)\]\s*$/);
  return m ? [m[1], m[2]] : [label, ''];
}

function optionTag(label) {
  // "Developer — ... (current, default)" -> ["Developer — ...", "current, default"]
  const m = label.match(/^([\s\S]*?)\s*\(([^()]*default[^()]*)\)\s*$/);
  return m ? [m[1], m[2]] : [label, ''];
}

function draftKey(q) {
  return `wizard-draft:${S.state.flow}:${q.id}`;
}
function loadDraft(q) {
  try { return sessionStorage.getItem(draftKey(q)) || ''; } catch (e) { return ''; }
}
function saveDraft(q, value) {
  try { sessionStorage.setItem(draftKey(q), value); } catch (e) { /* ignore */ }
}
function clearDraft(q) {
  try { sessionStorage.removeItem(draftKey(q)); } catch (e) { /* ignore */ }
}

async function submit(q, value, btn) {
  if (S.submitting) return;
  S.submitting = true;
  if (btn) { btn.disabled = true; btn.prepend(Spinner()); }
  const res = await api('/api/answer', { id: q.id, value });
  S.submitting = false;
  if (res.ok) {
    clearDraft(q);
    S.notice = null;
    return;
  }
  if (btn) { btn.disabled = false; const sp = btn.querySelector('.animate-spin'); if (sp) sp.remove(); }
  if (res.status === 409) toast('Already answered — maybe in another tab. Showing the latest.', 'destructive');
  else toast((res.data && res.data.error) || 'Could not send the answer.', 'destructive');
  loadState();
}

function NotesAlert(notes) {
  if (!notes || !notes.length) return null;
  const clean = notes.map((l) => l.replace(LOG_STAMP, ''));
  const warn = clean.some((l) => /⚠|isn.t|won.t work|didn.t|invalid|already used|already served|can.t|no file|not found|please|error|failed/i.test(l));
  return Alert({ variant: warn ? 'warning' : 'default', iconName: warn ? 'alert' : 'info', title: warn ? 'Please check this' : null },
    clean.map((l) => h('p', { text: l })));
}

function QuestionView(q) {
  const step = currentStep();
  const eyebrow = step ? `Step ${step.n} · ${step.title}` : '';
  switch (q.type) {
    case 'menu': return MenuQuestion(q, eyebrow);
    case 'yesno': return YesNoQuestion(q, eyebrow);
    case 'review': return ReviewQuestion(q, eyebrow);
    case 'confirm_typed': return ConfirmTypedQuestion(q, eyebrow);
    case 'file': return FileQuestion(q, eyebrow);
    default: return TextQuestion(q, eyebrow);
  }
}

function QuestionShell(eyebrow, title, description, body, footer, notes) {
  return Card({},
    CardHeader(
      eyebrow ? h('p', { class: 'text-muted-foreground text-xs font-medium uppercase tracking-wide', text: eyebrow }) : null,
      CardTitle(title, 'text-lg'),
      description ? CardDescription(description) : null),
    notes ? CardContent(notes) : null,
    body ? CardContent(body) : null,
    footer ? CardFooter(footer) : null);
}

function TextQuestion(q, eyebrow) {
  const secret = q.type === 'password';
  let [title, def] = splitDefault(q.label);
  if (q.default) def = q.default;
  const keepCurrent = /Enter = keep/i.test(def);
  const enterMatch = title.match(/\((Enter = [^)]*)\)\s*$/);
  let hint = null;
  if (keepCurrent) hint = 'Leave it blank to keep the current one.';
  else if (def) hint = h('span', {}, 'Leave it blank to keep ', InlineCode(def), '.');
  else if (enterMatch) { title = title.slice(0, enterMatch.index).trim(); hint = `${enterMatch[1].replace('Enter =', 'Leave it blank for')}.`; }
  const id = `q${q.id}`;
  const input = Input({ id, type: secret ? 'password' : 'text', autocomplete: secret ? 'new-password' : 'off', placeholder: keepCurrent ? '' : (def || ''), 'aria-label': title });
  if (!secret) {
    input.value = loadDraft(q);
    input.addEventListener('input', () => saveDraft(q, input.value));
  }
  const btn = Button({ type: 'submit', label: 'Continue' });
  const form = h('form', { class: 'space-y-4', onSubmit: (e) => { e.preventDefault(); submit(q, input.value, btn); } },
    h('div', { class: 'space-y-2' }, input, hint ? h('p', { class: 'text-muted-foreground text-xs' }, hint) : null),
    h('div', { class: 'flex flex-wrap items-center gap-3' }, btn, h('span', { class: 'text-muted-foreground text-xs' }, 'or press ', Kbd('Enter'))));
  return QuestionShell(eyebrow, title, secret ? 'Typing is hidden. It’s never saved by this page or written to the log.' : null, form, null, NotesAlert(q.notes));
}

function MenuQuestion(q, eyebrow) {
  // "0" = nothing preselected (bin/lib/ui.sh prompt_menu): one must be picked.
  const def = q.default === '0' ? '' : String(q.default || '1');
  const name = `q${q.id}`;
  const options = q.options.map((raw, i) => {
    const [text, tag] = optionTag(raw);
    const value = String(i + 1);
    const [main, sub] = text.split(/\s+—\s+/, 2);
    return RadioCard({ name, value, checked: value === def },
      h('span', { class: 'flex flex-wrap items-center gap-2 text-sm font-medium' }, main, tag ? Badge(tag, 'secondary') : null),
      sub ? h('span', { class: 'text-muted-foreground block text-sm', text: sub }) : null);
  });
  const btn = Button({ type: 'submit', label: 'Continue' });
  const form = h('form', { class: 'space-y-4', onSubmit: (e) => {
    e.preventDefault();
    const sel = form.querySelector('input[type=radio]:checked');
    if (!sel && !def) { toast('Choose one of the options first', 'destructive'); return; }
    submit(q, sel ? sel.value : def, btn);
  } }, h('div', { class: 'grid gap-2', role: 'radiogroup' }, options), btn);
  return QuestionShell(eyebrow, q.label, null, form, null, NotesAlert(q.notes));
}

// Label/value pairs sent with a question (review step, yes/no details).
function DetailList(options) {
  const rows = [];
  for (let i = 0; i + 1 < options.length; i += 2) rows.push([options[i], options[i + 1]]);
  return DescriptionList(rows);
}

function YesNoQuestion(q, eyebrow) {
  const def = (q.default || 'N').toUpperCase() === 'Y' ? 'Y' : 'N';
  const yes = Button({ variant: def === 'Y' ? 'default' : 'outline', label: 'Yes', onClick: () => submit(q, 'Y', yes) });
  const no = Button({ variant: def === 'N' ? 'default' : 'outline', label: 'No', onClick: () => submit(q, 'N', no) });
  (def === 'Y' ? yes : no).dataset.autofocus = '';
  return QuestionShell(eyebrow, q.label, null, (q.options || []).length >= 2 ? DetailList(q.options) : null,
    [yes, no, h('span', { class: 'text-muted-foreground text-xs' }, Kbd('Enter'), ` = ${def === 'Y' ? 'Yes' : 'No'}`)], NotesAlert(q.notes));
}

function ReviewQuestion(q, eyebrow) {
  const start = Button({ label: S.state.flow === 'setup' ? 'Start installation' : 'Continue', onClick: () => submit(q, 'start', start) });
  start.dataset.autofocus = '';
  const cancel = Button({ variant: 'outline', label: 'Cancel', onClick: async () => {
    const stop = await confirmDialog({
      title: 'Stop here?',
      text: 'Nothing heavy has run yet. Your answers so far are saved, so running the wizard again starts with them as the defaults.',
      cancel: 'Keep going', confirm: 'Stop', variant: 'destructive',
    });
    if (stop) submit(q, 'cancel', cancel);
  } });
  const change = ChangeAnswersButton(q);
  return QuestionShell(eyebrow, q.label, flowText().review || 'Check everything below. The next steps build containers, download code and install — they take a while.',
    h('div', { class: 'space-y-4' }, DetailList(q.options), ChangeAnswersNote()),
    [start, change, cancel], NotesAlert(q.notes));
}

// "Change answers" at a checkpoint: the script stops (exit 75) and the
// server starts the same wizard again on this page, from its first question,
// with the answers given so far as the defaults — nothing heavy has run yet.
function ChangeAnswersButton(q) {
  const b = Button({ variant: 'outline', iconName: 'arrowLeft', label: 'Change answers', onClick: () => submit(q, 'restart', b) });
  return b;
}
function ChangeAnswersNote() {
  return h('p', { class: 'text-muted-foreground text-xs',
    text: 'Change answers starts again from the first question with your answers filled in — press Enter to keep each one. Nothing has run yet.' });
}

function ConfirmTypedQuestion(q, eyebrow) {
  const expected = q.default;
  const id = `q${q.id}`;
  const input = Input({ id, placeholder: expected });
  const btn = Button({ type: 'submit', variant: 'destructive', label: 'Replace database', disabled: true });
  input.addEventListener('input', () => { btn.disabled = input.value !== expected; });
  const cancel = Button({ variant: 'outline', label: 'Cancel', onClick: () => submit(q, '', cancel) });
  const form = h('form', { class: 'space-y-4', onSubmit: (e) => { e.preventDefault(); if (input.value === expected) submit(q, input.value, btn); } },
    Alert({ variant: 'destructive', iconName: 'alert', title: 'This replaces the whole database' },
      h('p', { text: q.label })),
    h('div', { class: 'space-y-2' },
      Label('To confirm, type the project name:', id),
      h('p', {}, InlineCode(expected, { strong: true })),
      input),
    h('div', { class: 'space-y-2' },
      h('div', { class: 'flex flex-wrap gap-2' }, btn, ChangeAnswersButton(q), cancel),
      ChangeAnswersNote()));
  return QuestionShell(eyebrow, 'Confirm the replacement', null, form, null, NotesAlert(q.notes));
}

// What a file question expects — the bash side passes "kind=…" as an option
// (bin/lib/ui.sh prompt_file_source); the server checks uploads the same way.
const FILE_KINDS = {
  sqlgz: {
    accept: '.gz,application/gzip,application/x-gzip', pattern: /\.gz$/i, maxBytes: Infinity,
    drop: 'Drop the .sql.gz dump here, or click to choose it', hint: 'Gzipped SQL only. It stays on this computer.',
    wrong: ['That isn’t a .gz file', 'Choose the gzipped SQL dump (for example dbbackup.sql.gz).'],
    path: '/home/you/dumps/dbbackup.sql.gz   or   C:\\Users\\you\\Downloads\\dbbackup.sql.gz',
    url: 'https://example.com/dbbackup.sql.gz',
  },
  json: {
    accept: '.json,application/json', pattern: /\.json$/i, maxBytes: 1 << 20,
    drop: 'Drop your auth.json here, or click to choose it', hint: 'Your Composer credentials. Used for this setup only — the uploaded copy is deleted when the wizard finishes.',
    wrong: ['That isn’t a .json file', 'Choose your auth.json (Composer credentials for repo.magento.com).'],
    path: '/home/you/auth.json   or   C:\\Users\\you\\Downloads\\auth.json',
    url: 'https://example.com/auth.json',
  },
};

function fileKind(q) {
  const opt = (q.options || []).find((o) => /^kind=/.test(o));
  return FILE_KINDS[opt ? opt.slice(5) : 'sqlgz'] || FILE_KINDS.sqlgz;
}

function FileQuestion(q, eyebrow) {
  const kind = fileKind(q);
  const tabsDef = [
    { id: 'upload', label: 'Upload', iconName: 'upload' },
    { id: 'path', label: 'File on this computer', shortLabel: 'File', iconName: 'file' },
    { id: 'url', label: 'URL', iconName: 'link' },
  ];
  let active = 'upload';
  try { active = sessionStorage.getItem('wizard-file-tab') || 'upload'; } catch (e) { /* ignore */ }
  const panels = {};

  // Upload panel
  const fileInput = h('input', { type: 'file', accept: kind.accept, class: 'sr-only' });
  const status = h('div', { class: 'space-y-3' });
  let xhr = null;
  const drop = h('label', {
    class: 'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors hover:bg-accent/50 has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50',
  }, fileInput,
    h('div', { class: 'flex size-10 items-center justify-center rounded-full bg-muted' }, icon('upload', 'text-muted-foreground')),
    h('p', { class: 'text-sm font-medium', text: kind.drop }),
    h('p', { class: 'text-muted-foreground text-xs', text: kind.hint }));
  ['dragover', 'dragenter'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('bg-accent'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('bg-accent')));
  drop.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) startUpload(e.dataTransfer.files[0]); });
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) startUpload(fileInput.files[0]); });

  function startUpload(file) {
    if (!kind.pattern.test(file.name)) {
      status.replaceChildren(Alert({ variant: 'destructive', iconName: 'alert', title: kind.wrong[0] }, h('p', { text: kind.wrong[1] })));
      return;
    }
    if (file.size > kind.maxBytes) {
      status.replaceChildren(Alert({ variant: 'destructive', iconName: 'alert', title: 'That file is too big' }, h('p', { text: kind.wrong[1] })));
      return;
    }
    drop.classList.add('hidden');
    const pct = h('span', { class: 'tabular-nums', text: '0%' });
    const sizeText = h('span', { class: 'text-muted-foreground tabular-nums', text: `0 of ${fmtBytes(file.size)}` });
    const barWrap = h('div', {}, Progress(0, 'Upload progress'));
    const cancelBtn = Button({ variant: 'outline', size: 'sm', iconName: 'x', label: 'Cancel upload', onClick: () => xhr && xhr.abort() });
    status.replaceChildren(Panel({ cls: 'space-y-2' },
      h('div', { class: 'flex items-center gap-2 text-sm font-medium' }, icon('file'), h('span', { class: 'min-w-0 flex-1 truncate', text: file.name }), pct),
      barWrap, h('div', { class: 'flex items-center justify-between gap-2 text-xs' }, sizeText, cancelBtn)));
    xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/upload?q=${q.id}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('X-Wizard', '1');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const v = (e.loaded / e.total) * 100;
      pct.textContent = `${Math.floor(v)}%`;
      sizeText.textContent = `${fmtBytes(e.loaded)} of ${fmtBytes(e.total)}`;
      barWrap.replaceChildren(Progress(v, 'Upload progress'));
    };
    const failed = (msg) => {
      drop.classList.remove('hidden');
      fileInput.value = '';
      status.replaceChildren(Alert({ variant: 'destructive', iconName: 'alert', title: 'Upload didn’t finish' }, h('p', { text: msg })));
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status === 200 && data.path) {
        status.replaceChildren(Alert({ variant: 'success', iconName: 'circleCheck', title: 'Uploaded' }, h('p', { text: `${file.name} (${fmtBytes(file.size)}) — continuing…` })));
        submit(q, data.path, null);
      } else {
        failed(data.error || `The server answered ${xhr.status}.`);
      }
    };
    xhr.onerror = () => failed('The connection dropped during the upload. Try again.');
    xhr.onabort = () => failed('Upload cancelled — nothing was kept.');
    xhr.send(file);
  }
  panels.upload = h('div', { class: 'space-y-3' }, drop, status);

  // Path / URL panels
  const pathInput = Input({ placeholder: kind.path, 'aria-label': 'Path to the file' });
  const urlInput = Input({ type: 'url', placeholder: kind.url, 'aria-label': 'URL of the file' });
  pathInput.value = loadDraft(q);
  pathInput.addEventListener('input', () => saveDraft(q, pathInput.value));
  const pathBtn = Button({ type: 'submit', label: 'Use this file' });
  const urlBtn = Button({ type: 'submit', label: 'Download from this URL' });
  panels.path = h('form', { class: 'space-y-3', onSubmit: (e) => { e.preventDefault(); if (pathInput.value.trim()) submit(q, pathInput.value.trim(), pathBtn); } },
    pathInput, h('p', { class: 'text-muted-foreground text-xs', text: 'A path inside WSL, or a Windows path — it’s converted automatically.' }), pathBtn);
  panels.url = h('form', { class: 'space-y-3', onSubmit: (e) => { e.preventDefault(); if (urlInput.value.trim()) submit(q, urlInput.value.trim(), urlBtn); } },
    urlInput, h('p', { class: 'text-muted-foreground text-xs', text: 'Downloaded with curl. Presigned links work; they’re never stored.' }), urlBtn);

  const panelWrap = h('div', { class: 'mt-4' });
  function select(id) {
    active = id;
    try { sessionStorage.setItem('wizard-file-tab', id); } catch (e) { /* ignore */ }
    panelWrap.replaceChildren(panels[id]);
  }
  select(active in panels ? active : 'upload');
  const tabList = Tabs({ items: tabsDef, value: active, onSelect: select, label: 'Where the file comes from' });

  return QuestionShell(eyebrow, q.label, null, h('div', {}, tabList, panelWrap), null, NotesAlert(q.notes));
}

// -- cancel ---------------------------------------------------------------------

async function onCancel() {
  const st = S.state;
  const ft = flowText();
  if (!isActive()) return;
  let actions;
  let description;
  const task = runningTask();
  if (st.question || !task) {
    description = h('p', { text: `${ft.verb} stops right here. Running it again later continues from what’s already done, with your answers as the defaults.` });
    actions = [{ label: 'Keep going', value: null }, { label: 'Stop now', value: 'now', variant: 'destructive' }];
  } else {
    const critical = /don.t interrupt/i.test(task.label);
    description = [
      h('p', {}, 'Running now: ', h('strong', { text: task.label }), '.'),
      h('p', { text: critical
        ? 'This task must finish — stopping it halfway would leave a half-imported database. You can stop right after it.'
        : 'Stopping in the middle of a task can leave it half-done. Stopping after it is safer.' }),
    ];
    actions = [{ label: 'Keep going', value: null }];
    if (!critical) actions.push({ label: 'Stop now', value: 'now', variant: 'outline' });
    actions.push({ label: 'Stop after this task', value: 'after_task', variant: 'destructive' });
  }
  const mode = await dialog({ title: `Stop the ${ft.name.toLowerCase()}?`, description, actions });
  if (mode) {
    const res = await api('/api/cancel', { mode });
    if (!res.ok) toast('Could not cancel — try Ctrl+C twice in the terminal.', 'destructive');
  }
}

// -- finish / stopped -------------------------------------------------------------

function resultsOf(kind) {
  return ((S.state && S.state.results) || []).filter((r) => r.kind === kind);
}

function ResultRow(r) {
  let value = r.value;
  if (r.kind === 'link') {
    value = TextLink({ href: r.value, text: r.value, external: true });
  } else if (r.kind === 'copy' || r.kind === 'path') {
    value = h('span', { class: 'flex min-w-0 items-center gap-1' },
      InlineCode(r.value, { cls: 'min-w-0 break-all' }),
      Button({ variant: 'ghost', size: 'icon-xs', iconName: 'copy', title: 'Copy', cls: 'shrink-0', onClick: () => copyText(r.value) }));
  }
  return [r.label, value];
}

function ResultList(items) {
  return DescriptionList(items.map(ResultRow), { labelWidth: 'lg', center: true });
}

function CloseFooter() {
  return [
    Button({ variant: 'outline', iconName: 'download', label: 'Download log', href: '/api/log', download: true }),
    Button({ variant: 'secondary', label: 'Close wizard', onClick: async () => {
      await api('/api/quit', {});
      S.connection = 'closed';
      if (eventSource) { eventSource.close(); eventSource = null; }
      // Closes the tab when the browser allows it (tabs the dashboard opened);
      // otherwise the "Wizard closed" card says how to close it.
      window.close();
      render(true);
    } }),
  ];
}

function FinishView() {
  const ft = flowText();
  const domain = (resultsOf('domain')[0] || {}).value;
  const hosts = (resultsOf('hosts')[0] || {}).value;
  const ca = resultsOf('path').find((r) => /certificate authority/i.test(r.label));
  const links = resultsOf('link').filter((r) => /storefront|admin/i.test(r.label));
  const info = (S.state.results || []).filter((r) => ['text', 'copy', 'path', 'link'].includes(r.kind) && r !== ca && !links.includes(r));
  const commands = resultsOf('command');
  const isSetup = S.state.flow === 'setup';
  const done = DONE_TEXT[S.state.flow] || DONE_TEXT.setup;

  const reachBadge = h('div', { class: 'flex' });
  const openButtons = h('div', { class: 'flex flex-wrap gap-2' });
  const updateReach = () => {
    const ok = S.reachable === true;
    reachBadge.replaceChildren(ok ? Badge('Reachable from this browser', 'success', 'check')
      : Badge('Waiting for the site to be reachable…', 'secondary', 'loader', { spin: true }));
    openButtons.replaceChildren(...links.map((l, i) => Button({
      variant: ok && i === 0 ? 'default' : 'outline', iconName: 'external', label: `Open ${l.label.toLowerCase()}`,
      href: l.value, target: '_blank',
    })));
  };
  if (domain) {
    updateReach();
    startReachCheck(domain, updateReach);
  }

  const windowsSteps = [];
  if (isSetup && hosts) {
    windowsSteps.push(h('li', { class: 'space-y-2' },
      h('p', { class: 'text-sm font-medium', text: 'Add this line to the Windows hosts file' }),
      CodeLine(hosts),
      h('p', { class: 'text-muted-foreground text-xs' },
        'Open Notepad as Administrator → File › Open → ', InlineCode('C:\\Windows\\System32\\drivers\\etc\\hosts'), ' → add the line → save.')));
  }
  if (isSetup && ca) {
    windowsSteps.push(h('li', { class: 'space-y-2' },
      h('p', { class: 'text-sm font-medium', text: 'If the browser warns about the certificate, trust the local certificate authority once' }),
      CodeLine(ca.value),
      h('p', { class: 'text-muted-foreground text-xs', text: 'Open that path in File Explorer → double-click rootCA.pem → Install Certificate → Local Machine → Trusted Root Certification Authorities → restart the browser.' })));
  }

  return h('div', { class: 'space-y-4' },
    Card({},
      CardHeader(
        h('div', { class: 'flex size-10 items-center justify-center rounded-full bg-success/15 text-success' }, icon('check', 'size-5')),
        CardTitle(done.title, 'text-xl'),
        CardDescription(done.text)),
      domain ? CardContent(h('div', { class: 'flex flex-col gap-5' }, reachBadge, openButtons)) : null),
    windowsSteps.length ? Card({},
      CardHeader(CardTitle('One last thing on Windows'), CardDescription('The wizard can’t change Windows settings from WSL — these take a minute and are only needed once.')),
      CardContent(h('ol', { class: 'list-decimal space-y-5 pl-5 marker:text-muted-foreground' }, windowsSteps))) : null,
    info.length ? Card({}, CardHeader(CardTitle(done.details)), CardContent(ResultList(info))) : null,
    commands.length ? Card({},
      CardHeader(CardTitle(done.commands || 'Everyday commands'), CardDescription('Run these in the terminal.')),
      CardContent(h('div', { class: 'space-y-3' }, commands.map((c) => h('div', { class: 'space-y-1.5' }, h('p', { class: 'text-sm', text: c.label }), CodeLine(c.value)))))) : null,
    Card({}, CardFooter(CloseFooter())));
}

function startReachCheck(domain, update) {
  if (S.reachTimer) return;
  const check = async () => {
    try {
      await fetch(`https://${domain}/`, { mode: 'no-cors', cache: 'no-store', credentials: 'omit' });
      S.reachable = true;
    } catch (e) {
      S.reachable = false;
    }
    update();
    if (S.reachable) { clearInterval(S.reachTimer); }
  };
  check();
  S.reachTimer = setInterval(check, 3000);
}

function StoppedView(failed) {
  const st = S.state;
  const ft = flowText();
  const failedStep = st.steps.findIndex((s) => s.status === 'failed');
  const n = failedStep >= 0 ? failedStep + 1 : st.current;
  const title = n ? `Step ${n} · ${(st.steps[n - 1] || {}).title || ''}` : '';
  const badTask = stepTasks(n).find((t) => t.status === 'failed');
  const tail = S.logLines.slice(-25).join('\n');
  const kept = st.uploadsKept || [];
  let keepBox = null;
  if (kept.length) {
    // Kept by default — told to the server once, not on every re-render
    // (that would undo an unticked box).
    if (S.keepUploads === null) {
      S.keepUploads = true;
      api('/api/keep-uploads', { keep: true });
    }
    keepBox = Panel({ cls: 'space-y-2' },
      h('div', { class: 'flex items-center gap-2' },
        Checkbox({
          id: 'keep-uploads', checked: S.keepUploads,
          onChange: (e) => { S.keepUploads = e.target.checked; api('/api/keep-uploads', { keep: S.keepUploads }); },
        }),
        Label('Keep the uploaded dump for the next run', 'keep-uploads')),
      h('p', { class: 'text-muted-foreground text-xs', text: 'Next time choose “File on this computer” and use this path, so you don’t have to upload it again:' }),
      kept.map((p) => CodeLine(p)));
  }
  return h('div', { class: 'space-y-4' },
    failed
      ? Alert({ variant: 'destructive', iconName: 'circleX', title: `${ft.verb} stopped at ${title}` },
        badTask ? h('p', {}, 'Failed task: ', h('strong', { text: badTask.label })) : null,
        st.error ? h('p', { text: st.error }) : h('p', { text: 'See the last lines of the log below.' }))
      : Alert({ iconName: 'info', title: `${ft.verb} cancelled${title ? ` at ${title}` : ''}` },
        h('p', { text: 'Stopped on request. Nothing after that point ran.' })),
    failed ? Card({},
      CardHeader(CardTitle('Last lines of the log')),
      CardContent(LogBlock({ text: tail }))) : null,
    Card({},
      CardHeader(CardTitle('How to continue'), CardDescription(failed
        ? 'Fix the cause above (the message usually says what), then click Run again. It skips everything that’s already done and remembers your answers.'
        : 'Click Run again whenever you’re ready. It skips everything that’s already done and remembers your answers.')),
      CardContent(h('div', { class: 'space-y-3' }, keepBox,
        h('p', { class: 'text-muted-foreground text-xs', text: 'After closing this page you can also start it from the dashboard (Workflows), or in the terminal:' }),
        CodeLine(st.rerunCommand || ft.rerun))),
      CardFooter(Button({ iconName: 'rotateCw', label: 'Run again', onClick: (e) => onRunAgain(e.currentTarget) }), CloseFooter())));
}

// Starts the same flow again on this page (POST /api/restart) — the server
// keeps the same link; the page follows the new run over the live connection.
async function onRunAgain(btn) {
  btn.disabled = true;
  const res = await api('/api/restart', {});
  if (!res.ok) {
    toast((res.data && res.data.error) || 'Couldn’t start it again', 'destructive');
    btn.disabled = false;
    return;
  }
  S.notice = null;
  toast(`${flowText().verb} started again`);
}

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.defaultPrevented || document.querySelector('dialog[open]')) return;
  const q = S.state && S.state.question;
  if (!q || q.type !== 'yesno') return;
  if (e.target instanceof HTMLButtonElement) return;
  const def = (q.default || 'N').toUpperCase() === 'Y' ? 'Y' : 'N';
  e.preventDefault();
  submit(q, def, null);
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

(async function boot() {
  render(true);
  if (await loadState()) connect();
  else { S.connection = 'lost'; render(true); }
})();
