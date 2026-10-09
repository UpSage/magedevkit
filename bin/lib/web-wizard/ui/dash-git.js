// MageRunner dashboard, part 3 of 4: the Git tab — status, staging (two
// columns with drag and drop), commit, branches, fetch/pull/push. Uses
// dash-core.js; see its header for the load order.
'use strict';

// -- Git tab --------------------------------------------------------------------


const GIT_BADGE = {
  M: ['M', 'secondary', 'Modified'], A: ['A', 'success', 'Added'], D: ['D', 'destructive', 'Deleted'],
  R: ['R', 'secondary', 'Renamed'], C: ['C', 'secondary', 'Copied'], T: ['T', 'secondary', 'Type changed'],
  '?': ['?', 'outline', 'Untracked'], U: ['U', 'warning', 'Conflict'],
};
const AUTH_FAILED = /Authentication failed|could not read Username|terminal prompts disabled|Invalid username or (password|token)|Permission to .* denied|403/i;

function gitUrl(env, rest) {
  return `/api/environments/${encodeURIComponent(env.id)}/git${rest ? `/${rest}` : ''}`;
}

async function loadGit() {
  const res = await getJSON(gitUrl(S.detail || { id: S.route.id }));
  S.git = res.ok ? res.data : null;
  S.gitError = res.ok ? null : errorText(res);
  // Forget selections of files that aren't changed any more.
  if (S.git) {
    const staged = new Set(S.git.files.filter((f) => f.staged).map((f) => f.path));
    const changes = new Set(S.git.files.filter((f) => f.unstaged).map((f) => f.path));
    S.gitSel.staged = new Set([...S.gitSel.staged].filter((p) => staged.has(p)));
    S.gitSel.changes = new Set([...S.gitSel.changes].filter((p) => changes.has(p)));
  }
}

async function gitWrite(env, command, body, okText) {
  const res = await busyApi(gitUrl(env, command), body || {});
  if (!res.ok) { render(); showError(`${okText || command} didn’t work`, res); return false; }
  toast(res.data.message || okText || 'Done', 'success');
  await loadGit();
  render();
  return true;
}

// Fetch / pull / push: a dialog that says what happens, with optional
// one-time GitHub login fields, then the command runs as an operation shown
// in the watch panel.
async function gitNetwork(env, command) {
  const g = S.git || {};
  const what = {
    fetch: `Downloads what’s new on ${g.remote || 'origin'} and updates ahead/behind. Your files don’t change.`,
    pull: `Fast-forwards ${g.branch} to ${g.upstream} (${g.behind || 0} new commit(s)). It never creates a merge commit — if both sides have new commits it stops and says so.`,
    push: g.upstream
      ? `Sends ${g.ahead || 0} commit(s) on ${g.branch} to ${g.upstream}.`
      : `Publishes ${g.branch} to origin as a new branch (git push -u origin ${g.branch}).`,
  }[command];
  const user = Input({ id: 'git-user', placeholder: 'GitHub username', autocomplete: 'username' });
  const token = Input({ id: 'git-token', type: 'password', placeholder: 'Personal access token', autocomplete: 'off' });
  const remember = Checkbox({ id: 'git-remember' });
  const login = Panel({ cls: 'space-y-3 text-left' },
    h('p', { class: 'text-muted-foreground text-xs', text: S.gitNeedsLogin
      ? 'GitHub asked for a login last time. Enter your username and a personal access token (repo scope).'
      : g.savedLogin ? 'Using your saved GitHub login. Fill these in only to use a different one.'
        : 'Only if git has no saved login for GitHub: a personal access token (repo scope).' }),
    h('div', { class: 'space-y-2' }, Label('GitHub username', 'git-user'), user),
    h('div', { class: 'space-y-2' }, Label('Personal access token', 'git-token'), token),
    h('div', { class: 'space-y-1' },
      h('div', { class: 'flex items-center gap-2' }, remember, Label('Remember my credentials', 'git-remember')),
      h('p', { class: 'text-muted-foreground text-xs', text: 'Saved in ~/.git-credentials (readable only by you), and this repo’s git config is set to use it, so the dashboard and your terminal stop asking here. Your global git config isn’t changed. Unticked, they’re used for this one command and never saved.' })));
  const go = await dialog({
    title: { fetch: 'Fetch from origin?', pull: 'Pull?', push: 'Push?' }[command],
    description: [h('p', { text: what }), login],
    actions: [{ label: 'Cancel', value: false }, { label: { fetch: 'Fetch', pull: 'Pull', push: 'Push' }[command], value: true }],
  });
  if (!go) return;
  const body = user.value.trim() || token.value.trim()
    ? { username: user.value.trim(), token: token.value.trim(), remember: remember.querySelector('input').checked } : {};
  const res = await api(gitUrl(env, command), body);
  if (!res.ok) return showError(`git ${command} didn’t start`, res);
  const label = `git ${command}`;
  S.watch = {
    envId: env.id, opId: res.data.operation, label, text: '', rec: { status: 'starting' },
    onDone: (w) => {
      S.gitNeedsLogin = w.rec.status === 'failed' && AUTH_FAILED.test(w.text || '');
      if (S.gitNeedsLogin) toast('GitHub needs a login — run it again with your username and a personal access token.', 'warning');
      loadGit().then(render);
    },
  };
  watchPre = null;
  watchOperation();
}

// Commit in a dialog: the staged files, the author, the message.
async function gitCommitDialog(env) {
  const g = S.git;
  const staged = g.files.filter((f) => f.staged);
  if (!gitMessage) gitMessage = Textarea({ id: 'git-message', rows: '4', placeholder: 'What changed, and why' });
  gitMessage.removeAttribute('aria-invalid');
  const action = await formDialog({
      title: `Commit ${staged.length} file(s) on ${g.branch || 'detached HEAD'}`,
      description: [h('div', { class: 'space-y-3 text-left' },
        g.identity
          ? h('p', { text: `As ${g.identity.name} <${g.identity.email}>.` })
          : Alert({ variant: 'warning', iconName: 'alert', title: 'Set your git identity first' },
            h('p', { text: 'Run this once in a terminal, then try again:' }),
            CodeLine('git config --global user.name "Your Name" && git config --global user.email you@example.com')),
        h('ul', { class: 'max-h-32 list-disc space-y-0.5 overflow-auto pl-5' }, staged.slice(0, 50).map((f) => h('li', {}, InlineCode(f.path)))),
        h('div', { class: 'space-y-2' }, Label('Commit message', 'git-message'), gitMessage))],
      actions: [{ label: 'Cancel', value: null }]
        .concat(g.identity ? [g.branch ? { label: 'Commit & push', value: 'push', variant: 'outline' } : null,
          { label: 'Commit', value: 'commit', variant: 'default' }].filter(Boolean) : []),
      validate: () => {
        if (gitMessage.value.trim()) return true;
        gitMessage.setAttribute('aria-invalid', 'true');
        return false;
      },
  });
  if (!action) return;
  if (await gitWrite(env, 'commit', { message: gitMessage.value }, 'Committed')) {
    gitMessage.value = '';
    if (action === 'push') gitNetwork(env, 'push');
  }
}

async function gitForgetLogin(env) {
  const ok = await confirmDialog({
    title: 'Forget the saved GitHub login?',
    text: 'Removes it from ~/.git-credentials. Fetch, pull and push will ask for a username and token again — here and in your terminal.',
    confirm: 'Forget it', variant: 'destructive',
  });
  if (ok) gitWrite(env, 'forget_login', {}, 'Saved login removed');
}

// Deletes a local branch (not the current one; origin is never touched).
// git branch -d first; when that refuses because the branch has commits
// nowhere else, a second dialog says so before forcing (git branch -D).
async function gitDeleteBranch(env, picked) {
  const g = S.git;
  const others = g.branches.filter((b) => b.name !== g.branch);
  if (!others.length) return;
  const select = NativeSelect({
    'aria-label': 'Branch to delete', cls: 'w-full',
    value: others.some((b) => b.name === picked) ? picked : others[0].name,
    options: others.map((b) => ({ value: b.name, label: b.name + (b.upstream ? `  (tracks ${b.upstream})` : '  (local only)') })),
  });
  const ok = await dialog({
    title: 'Delete a local branch?',
    description: [h('div', { class: 'space-y-3 text-left' },
      h('div', { class: 'space-y-2' }, Label('Branch'), select),
      h('p', { text: `Removes it from this computer only (git branch -d) — origin and its copy there aren’t touched. You can’t delete ${g.branch || 'the branch you’re on'}; switch first.` }))],
    actions: [{ label: 'Cancel', value: false }, { label: 'Delete branch', value: true, variant: 'destructive' }],
  });
  if (!ok) return;
  const branch = select.querySelector('select').value;
  let res = await busyApi(gitUrl(env, 'delete_branch'), { branch });
  if (res.status === 409 && /commit\(s\) that no other branch/.test((res.data && res.data.error) || '')) {
    const force = await dialog({
      title: `Delete ${branch} anyway?`,
      description: [Alert({ variant: 'destructive', iconName: 'alert', title: res.data.error }, h('p', { text: res.data.detail || '' }))],
      actions: [{ label: 'Keep it', value: false }, { label: 'Delete anyway', value: true, variant: 'destructive' }],
    });
    if (!force) { render(); return; }
    res = await busyApi(gitUrl(env, 'delete_branch'), { branch, force: true });
  }
  if (!res.ok) { render(); return showError('The branch wasn’t deleted', res); }
  toast(res.data.message, 'success');
  await loadGit();
  render();
}

async function gitNewBranch(env) {
  const g = S.git;
  const name = Input({ id: 'git-branch-name', placeholder: 'feature/my-change' });
  const help = h('p', { class: 'text-muted-foreground text-xs', text: 'Letters, digits, . _ - and /.' });
  const from = NativeSelect({
    'aria-label': 'Start from', cls: 'w-full',
    options: [{ value: 'HEAD', label: `Current (${g.branch || 'detached HEAD'})` }]
      .concat(g.branches.filter((b) => b.name !== g.branch).map((b) => ({ value: b.name, label: b.name })))
      .concat(g.remoteBranches.map((r) => ({ value: r, label: `${r} (remote)` }))),
    value: 'HEAD',
  });
  const sw = Checkbox({ id: 'git-branch-switch', checked: true });
  const ok = await formDialog({
      title: 'New branch',
      description: [h('div', { class: 'space-y-3 text-left' },
        h('div', { class: 'space-y-2' }, Label('Name', 'git-branch-name'), name, help),
        h('div', { class: 'space-y-2' }, Label('Start from'), from),
        h('div', { class: 'flex items-center gap-2' }, sw, Label('Switch to it', 'git-branch-switch')))],
      actions: [{ label: 'Cancel', value: false }, { label: 'Create branch', value: true }],
      validate: () => {
        if (BRANCH_RE.test(name.value.trim()) && !name.value.includes('..')) return true;
        markInvalid(name, help, 'Use letters, digits, . _ - and / only, starting with a letter or digit.');
        return false;
      },
  });
  if (!ok) return;
  gitWrite(env, 'create_branch', {
    name: name.value.trim(), from: from.querySelector('select').value, switch: sw.querySelector('input').checked,
  }, 'Branch created');
}

async function gitSwitch(env, branch) {
  const g = S.git;
  const dirty = g.files.length;
  const ok = await dialog({
    title: `Switch to ${branch}?`,
    description: [
      h('p', { text: 'The store’s code changes to that branch right away. After switching branches you may need to flush the cache — or run setup:upgrade if the branch has module changes.' }),
      dirty ? h('p', { text: `You have ${dirty} changed file(s). git keeps them if they don’t clash with the other branch; otherwise it refuses and nothing changes.` }) : null,
    ],
    actions: [{ label: 'Cancel', value: false }, { label: 'Switch', value: true }],
  });
  if (ok) gitWrite(env, 'switch', { branch }, `Switched to ${branch}`);
}

async function gitDiscard(env, paths) {
  const typed = Input({ id: 'git-discard-confirm', placeholder: 'discard' });
  const help = h('p', { class: 'text-muted-foreground text-xs', text: 'Type discard to confirm.' });
  const ok = await formDialog({
      title: `Discard changes in ${paths.length} file(s)?`,
      description: [
        h('p', { text: 'Tracked files go back to their last committed version; new (untracked) files are deleted. This can’t be undone.' }),
        h('ul', { class: 'max-h-40 list-disc space-y-0.5 overflow-auto pl-5 text-left' }, paths.slice(0, 50).map((p) => h('li', {}, InlineCode(p)))),
        paths.length > 1 ? h('div', { class: 'space-y-2 text-left' }, typed, help) : null,
      ],
      actions: [{ label: 'Cancel', value: false }, { label: 'Discard', value: true, variant: 'destructive' }],
      validate: () => {
        if (paths.length === 1 || typed.value.trim().toLowerCase() === 'discard') return true;
        markInvalid(typed, help, 'Type discard to confirm.');
        return false;
      },
  });
  if (!ok) return;
  gitWrite(env, 'discard', { paths }, 'Changes discarded');
}

async function gitShowDiff(env, path, staged) {
  if (S.gitDiff && S.gitDiff.path === path && S.gitDiff.staged === staged) { S.gitDiff = null; render(); return; }
  const res = await getJSON(`${gitUrl(env, 'diff')}?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}`);
  if (!res.ok) return showError('Couldn’t show the diff', res);
  S.gitDiff = { path, staged, data: res.data };
  render();
  const el = document.getElementById('git-diff');
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// One column's file rows. Every row can be dragged to the other column
// (with the other selected rows of its column), or moved with its arrow
// button — both run the same git add / git restore --staged as the toolbar.
function GitFileList(env, files, which, busy) {
  const sel = S.gitSel[which];
  const staged = which === 'staged';
  if (!files.length) {
    return h('p', { class: 'text-muted-foreground px-3 py-6 text-center text-sm',
      text: staged ? 'Nothing staged — drag files here, or use → on the left.' : 'No unstaged changes.' });
  }
  const all = files.every((f) => sel.has(f.path));
  const head = h('div', { class: 'text-muted-foreground flex items-center gap-2 border-b px-3 py-1.5 text-xs' },
    Checkbox({ 'aria-label': staged ? 'Select all staged files' : 'Select all unstaged files', checked: all,
      onChange: (e) => { files.forEach((f) => (e.target.checked ? sel.add(f.path) : sel.delete(f.path))); render(); } }),
    h('span', { text: sel.size ? `${files.filter((f) => sel.has(f.path)).length} selected` : 'Select all' }));
  const rows = files.map((f) => {
    const code = f.conflict ? 'U' : f.untracked ? '?' : (staged ? f.x : f.y);
    const [txt, variant, title] = GIT_BADGE[code] || [code, 'secondary', code];
    const partly = f.staged && f.unstaged && !f.untracked && !f.conflict;
    const open = S.gitDiff && S.gitDiff.path === f.path && S.gitDiff.staged === staged;
    const row = h('div', {
      class: cn('flex items-center gap-2 border-b px-3 py-1.5 last:border-b-0 hover:bg-muted/50 data-[dragging=true]:opacity-50',
        f.conflict || busy ? null : 'cursor-grab active:cursor-grabbing'),
    },
    Checkbox({ 'aria-label': `Select ${f.path}`, checked: sel.has(f.path),
      onChange: (e) => { if (e.target.checked) sel.add(f.path); else sel.delete(f.path); render(); } }),
    h('span', { title }, Badge(txt, variant)),
    h('span', { class: 'flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5' },
      InlineCode(f.path, { cls: 'break-all' }),
      f.orig ? h('span', { class: 'text-muted-foreground text-xs', text: `from ${f.orig}` }) : null,
      partly ? Badge('partly staged', 'outline') : null),
    Button({ variant: open ? 'secondary' : 'ghost', size: 'icon', iconName: 'file', title: `Diff of ${f.path}`, cls: 'size-7',
      onClick: () => gitShowDiff(env, f.path, staged) }),
    f.conflict ? null : Button({ variant: 'ghost', size: 'icon-xs', iconName: staged ? 'arrowLeft' : 'arrowRight',
      title: staged ? `Unstage ${f.path}` : `Stage ${f.path}`, disabled: busy,
      onClick: () => gitWrite(env, staged ? 'unstage' : 'stage', { paths: [f.path] }) }));
    if (!f.conflict && !busy) {
      makeDraggable(row, 'git', () => ({
        from: which, paths: sel.has(f.path) ? files.filter((x) => sel.has(x.path) && !x.conflict).map((x) => x.path) : [f.path],
      }));
    }
    return row;
  });
  return h('div', {}, head, rows);
}

function GitTab(env) {
  const g = S.git;
  if (!g) return S.gitError ? Alert({ variant: 'destructive', iconName: 'alert', title: 'Can’t read the repository' }, h('p', { text: S.gitError }))
    : LoadingText('Reading the repository…');
  const ro = !!env.readonly;
  const busy = envBlocked(env);
  const stagedFiles = g.files.filter((f) => f.staged);
  const changedFiles = g.files.filter((f) => f.unstaged);
  const conflicts = g.files.filter((f) => f.conflict);

  // -- branch ------------------------------------------------------------------
  const picker = NativeSelect({
    'aria-label': 'Branch', cls: 'w-auto min-w-56',
    value: g.branch || '',
    options: (g.branch ? [] : [{ value: '', label: 'Detached HEAD' }])
      .concat(g.branches.map((b) => ({ value: b.name, label: b.name + (b.ahead || b.behind ? `  (↑${b.ahead} ↓${b.behind})` : '') })))
      .concat(g.remoteBranches.filter((r) => !g.branches.some((b) => r === `origin/${b.name}`)).map((r) => ({ value: r, label: `${r} (remote)` }))),
  });
  const pickerSelect = picker.querySelector('select');
  const syncBadges = [
    !g.branch ? Badge('Detached HEAD', 'warning') : null,
    g.branch && !g.upstream ? Badge('Not on origin yet', 'secondary') : null,
    g.upstream && g.ahead ? Badge(`${g.ahead} ahead`, 'default', 'upload') : null,
    g.upstream && g.behind ? Badge(`${g.behind} behind`, 'warning', 'download') : null,
    g.upstream && !g.ahead && !g.behind ? Badge(`Up to date with ${g.upstream}`, 'success', 'check') : null,
  ];
  const branchCard = Card({},
    CardHeader(
      CardTitle(g.branch || 'Detached HEAD'),
      CardDescription(`${g.upstream ? `Tracks ${g.upstream}` : 'No upstream'} · last fetched ${when(g.lastFetch) || 'never'} — ahead/behind counts are from that fetch.`),
      CardAction(h('div', { class: 'flex flex-wrap justify-end gap-1' }, syncBadges))),
    CardContent(h('div', { class: 'flex flex-wrap items-center gap-2' },
      picker,
      Button({ variant: 'outline', size: 'sm', label: 'Switch', disabled: busy,
        onClick: () => { const v = pickerSelect.value; if (v && v !== g.branch) gitSwitch(env, v); } }),
      Button({ variant: 'outline', size: 'sm', iconName: 'plus', label: 'New branch', disabled: busy, onClick: () => gitNewBranch(env) }),
      Button({ variant: 'ghost', size: 'sm', iconName: 'x', label: 'Delete branch', disabled: busy || g.branches.filter((b) => b.name !== g.branch).length === 0,
        onClick: () => gitDeleteBranch(env, pickerSelect.value) }))),
    CardFooter(
      Button({ variant: 'outline', size: 'sm', iconName: 'rotateCw', label: 'Fetch', disabled: busy, onClick: () => gitNetwork(env, 'fetch') }),
      g.upstream && g.behind ? Button({ variant: 'outline', size: 'sm', iconName: 'download', label: `Pull ${g.behind}`, disabled: busy, onClick: () => gitNetwork(env, 'pull') }) : null,
      g.branch && (g.ahead || !g.upstream) ? Button({ size: 'sm', iconName: 'upload', label: g.upstream ? `Push ${g.ahead}` : 'Publish branch', disabled: busy, onClick: () => gitNetwork(env, 'push') }) : null,
      S.gitNeedsLogin ? Badge('GitHub login needed', 'warning', 'alert') : null,
      g.savedLogin ? h('span', { class: 'ml-auto flex items-center gap-2' }, Badge('Saved GitHub login', 'success', 'check'),
        Button({ variant: 'ghost', size: 'sm', label: 'Forget', disabled: busy, onClick: () => gitForgetLogin(env) })) : null));

  // -- changes -----------------------------------------------------------------
  const selStaged = [...S.gitSel.staged];
  const selChanges = [...S.gitSel.changes];
  const changesCard = Card({},
    CardHeader(CardTitle('Changes'), CardDescription(`${changedFiles.length} not staged · ${stagedFiles.length} staged${g.filesTotal > g.files.length ? ` · showing the first ${g.files.length} of ${g.filesTotal}` : ''}`)),
    conflicts.length ? CardContent(Alert({ variant: 'warning', iconName: 'alert', title: `${conflicts.length} file(s) have merge conflicts` },
      h('p', { text: 'Resolve them in your editor, then stage them here.' }))) : null,
    CardContent(h('div', { class: 'space-y-3' },
      h('p', { class: 'text-muted-foreground text-xs',
        text: 'Drag files between the columns (or use the arrows) to stage and unstage — git add / git restore --staged. M modified · A added · D deleted · R renamed · ? untracked · U conflict · ! ignored' }),
      h('div', { class: 'grid grid-cols-1 gap-4 md:grid-cols-2' },
        DropZone({
          title: 'Unstaged', count: changedFiles.length, kind: 'git', from: 'staged', hint: 'Drop to unstage',
          onDrop: (d) => gitWrite(env, 'unstage', { paths: d.paths }),
          actions: [
            Button({ variant: 'outline', size: 'sm', iconName: 'arrowRight', label: `Stage${selChanges.length ? ` (${selChanges.length})` : ''}`,
              disabled: busy || !selChanges.length, onClick: () => gitWrite(env, 'stage', { paths: selChanges }) }),
            Button({ variant: 'ghost', size: 'sm', label: 'Stage all', disabled: busy || !changedFiles.length, onClick: () => gitWrite(env, 'stage', { paths: 'all' }) }),
            Button({ variant: 'ghost', size: 'sm', iconName: 'x', label: 'Discard', disabled: busy || !selChanges.length, onClick: () => gitDiscard(env, selChanges) }),
          ],
        }, GitFileList(env, changedFiles, 'changes', busy)),
        DropZone({
          title: 'Staged', count: stagedFiles.length, kind: 'git', from: 'changes', hint: 'Drop to stage',
          onDrop: (d) => gitWrite(env, 'stage', { paths: d.paths }),
          actions: [
            Button({ variant: 'outline', size: 'sm', iconName: 'arrowLeft', label: `Unstage${selStaged.length ? ` (${selStaged.length})` : ''}`,
              disabled: busy || !selStaged.length, onClick: () => gitWrite(env, 'unstage', { paths: selStaged }) }),
            Button({ variant: 'ghost', size: 'sm', label: 'Unstage all', disabled: busy || !stagedFiles.length, onClick: () => gitWrite(env, 'unstage', { paths: 'all' }) }),
            stagedFiles.length ? Button({ size: 'sm', iconName: 'check', label: `Commit (${stagedFiles.length})`, disabled: busy, onClick: () => gitCommitDialog(env) }) : null,
          ],
        }, GitFileList(env, stagedFiles, 'staged', busy))),
      (g.ignored || []).length ? Collapsible({ title: `Ignored by .gitignore (${g.ignoredTotal})`, iconName: 'circleMinus' },
        h('div', { class: 'space-y-2 px-6' },
          h('p', { class: 'text-muted-foreground text-xs', text: 'git never stages these unless .gitignore changes. Whole ignored folders are listed once.' }),
          h('ul', { class: 'max-h-64 overflow-auto border' }, g.ignored.map((p) => h('li', { class: 'flex items-center gap-2 border-b px-3 py-1 last:border-b-0' },
            Badge('!', 'outline'), InlineCode(p, { cls: 'break-all' })))),
          g.ignoredTotal > g.ignored.length ? h('p', { class: 'text-muted-foreground text-xs', text: `Showing ${g.ignored.length} of ${g.ignoredTotal}.` }) : null)) : null)));


  // -- diff --------------------------------------------------------------------
  const d = S.gitDiff;
  const diffCard = d ? Card({},
    CardHeader(CardTitle(d.path), CardDescription(d.staged ? 'Staged changes (what the next commit contains)' : d.data.untracked ? 'New file' : 'Changes not staged yet'),
      CardAction(Button({ variant: 'ghost', size: 'icon-sm', iconName: 'x', title: 'Close the diff', onClick: () => { S.gitDiff = null; render(); } }))),
    CardContent(d.data.binary ? h('p', { class: 'text-muted-foreground text-sm', text: 'Binary file — no text diff.' })
      : h('div', { class: 'space-y-2' }, DiffBlock(d.data.text || '(no differences)'),
        d.data.truncated ? h('p', { class: 'text-muted-foreground text-xs', text: 'Only the first 200 KB are shown.' }) : null))) : null;
  if (diffCard) diffCard.id = 'git-diff';

  // -- history -----------------------------------------------------------------
  const historyCard = Card({},
    CardHeader(CardTitle('Recent commits'), CardDescription('“Not pushed” means no branch on origin has it yet (as of the last fetch).')),
    CardContent(g.commits.length ? Table(
      TableHeader(TableRow(['Commit', 'Message', 'Author', 'When', ''].map((t) => TableHead(t)))),
      TableBody(g.commits.map((c) => TableRow(
        TableCell(InlineCode(c.hash)),
        TableCell(c.subject, 'whitespace-normal'),
        TableCell(c.author, 'text-muted-foreground'),
        TableCell(when(c.date) || '—', 'text-muted-foreground'),
        TableCell(c.pushed ? null : Badge('Not pushed', 'warning'), 'text-right')))))
      : h('p', { class: 'text-muted-foreground text-sm', text: 'No commits yet.' })));

  return h('div', { class: 'space-y-4' },
    ro ? Alert({ iconName: 'info', title: 'Read-only' }, h('p', { text: 'This environment is read-only here, so git changes are off.' })) : null,
    branchCard,
    changesCard,
    diffCard,
    historyCard);
}
