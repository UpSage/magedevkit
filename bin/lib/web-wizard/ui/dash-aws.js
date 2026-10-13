// MageDevKit dashboard, part 5 of 6: the AWS accounts page — MageDevKit's own
// AWS connections (the logins it uses to create and control AWS
// environments): which account each one reaches and whether it works; adding
// (access keys, SSO or a role), importing one profile from ~/.aws, signing in
// to SSO again and removing. Adding and signing in run the AWS connection
// wizard (bin/local/aws-account) in its own tab, so secrets are typed there and
// never pass through this page. Uses dash-core.js; see its header for the
// load order.
'use strict';

const AWS_STATUS = {
  ok: ['Works', 'success'], 'sso-expired': ['Sign-in expired', 'warning'], 'bad-keys': ['Keys rejected', 'destructive'],
  denied: ['Access denied', 'destructive'], unreachable: ['AWS not reachable', 'warning'], error: ['Error', 'destructive'],
  unchecked: ['Checking…', 'outline'],
};
const AWS_TYPE = { keys: 'Access keys', sso: 'SSO', role: 'Role', unknown: 'Unknown' };

async function loadAws(recheck) {
  const res = await getJSON(`/api/aws/accounts${recheck ? '?check=1' : ''}`);
  if (res.status === 403) return lost();
  if (res.status !== 0) S.aws = res.ok ? res.data : S.aws;
  S.error = res.ok ? null : serverError(res);
}

async function startAccountWizard(action, name) {
  const label = { add: 'Add an AWS connection', import: 'Import an AWS profile', login: 'Sign in to SSO again' }[action];
  const tab = openPendingTab('Starting the wizard…');
  const res = await busyApi('/api/aws/accounts/wizard', { action, name });
  if (!res.ok) {
    if (tab) tab.close();
    render();
    return showError(`${label} didn’t start`, res);
  }
  if (!navigateTab(tab, res.data.url)) toast('Your browser blocked the new tab — allow pop-ups for this page and try again.', 'destructive');
  toast(`${label}: the wizard is open in a new tab`);
  refresh();
}

async function removeConnection(c) {
  const ok = await confirmDialog({
    title: `Remove ${c.name}?`,
    text: h('div', { class: 'space-y-2' },
      h('p', { text: `MageDevKit forgets this connection${c.account ? ` to account ${c.account}` : ''}. Its login is deleted from ${S.aws.dir}; your own ~/.aws is not touched.` }),
      h('p', { text: 'Nothing in AWS changes. You can add it again any time.' })),
    confirm: 'Remove', variant: 'destructive',
  });
  if (!ok) return;
  const res = await busyApi('/api/aws/accounts/remove', { name: c.name });
  if (!res.ok) {
    render();
    return showError(`Couldn’t remove ${c.name}`, res);
  }
  toast(`${c.name} removed`);
  refresh();
}

function AwsStatus(c) {
  const [text, variant] = AWS_STATUS[c.status] || [c.status, 'outline'];
  return h('span', { class: 'inline-flex items-center gap-2', title: c.error || '' }, Badge(text, variant, c.status === 'unchecked' ? 'loader' : null, { spin: c.status === 'unchecked' }));
}

function ConnectionRow(c) {
  return TableRow(
    TableCell(h('span', { class: 'font-medium', text: c.name })),
    TableCell(c.account ? h('span', {}, InlineCode(c.account), c.alias ? h('span', { class: 'text-muted-foreground ml-2', text: c.alias }) : null) : '—'),
    TableCell(AWS_TYPE[c.type] || c.type),
    TableCell(c.region || '—'),
    TableCell(AwsStatus(c)),
    TableCell(h('div', { class: 'flex justify-end gap-2' },
      c.type === 'sso' ? Button({ variant: c.status === 'sso-expired' ? 'default' : 'outline', size: 'sm', iconName: 'key', label: 'Sign in', disabled: S.busy, onClick: () => startAccountWizard('login', c.name) }) : null,
      Button({ variant: 'ghost', size: 'sm', iconName: 'x', label: 'Remove', disabled: S.busy, onClick: () => removeConnection(c) })), 'text-right'));
}

function renderAwsAccounts() {
  document.title = 'AWS accounts — MageDevKit';
  const a = S.aws;
  const head = h('div', { class: 'flex flex-wrap items-start justify-between gap-4' },
    h('div', { class: 'min-w-0 space-y-1' },
      h('h1', { class: 'text-xl font-semibold', text: 'AWS accounts' }),
      h('p', { class: 'text-muted-foreground text-sm', text: 'MageDevKit’s own connections — the logins it uses to create and control AWS environments.' })),
    h('div', { class: 'flex flex-wrap gap-2' },
      Button({ iconName: 'plus', label: 'Add connection', disabled: S.busy, onClick: () => startAccountWizard('add') }),
      Button({ variant: 'outline', iconName: 'download', label: 'Import from ~/.aws', disabled: S.busy, onClick: () => startAccountWizard('import') }),
      Button({ variant: 'outline', iconName: 'rotateCw', label: 'Check again', disabled: S.busy || !a || !a.connections.length, onClick: async () => { await loadAws(true); render(); } })));
  if (!a) {
    setKids(contentSlot, head, ErrorBanner() || LoadingText('Reading your AWS connections…'));
    return;
  }
  const kids = [head, ErrorBanner()];
  if (!a.cli.installed) {
    kids.push(Alert({ variant: 'warning', iconName: 'alert', title: 'The AWS CLI isn’t installed' },
      h('p', { text: 'Add connection installs it for you (AWS CLI v2, checked against AWS’s signature) into ~/.local.' })));
  }
  if (a.check.status === 'error') {
    kids.push(Alert({ variant: 'destructive', iconName: 'alert', title: 'Couldn’t check the connections' }, h('p', { text: a.check.error || 'The check failed.' })));
  }
  if (!a.connections.length) {
    kids.push(Card({}, CardHeader(CardTitle('No AWS connections yet'),
      CardDescription('Add one to let MageDevKit create and control AWS environments in that account. You choose how it signs in:')),
    CardContent(DescriptionList([
      ['Access keys', 'A name, the access key ID (AKIA…), the secret access key and a region. From AWS console → IAM → Users → you → Security credentials → Create access key.'],
      ['SSO', 'A name, your SSO start URL (https://<company>.awsapps.com/start) and its region. You approve the sign-in in your browser, then pick the account and role.'],
      ['A role', 'A name, one of your connections to sign in with, the role ARN (arn:aws:iam::<account>:role/<name>) and, if needed, an MFA device.'],
      ['Import', 'Copies one profile you already use with the aws command from ~/.aws — only the one you pick.'],
    ], { labelWidth: 'sm' }))));
  } else {
    kids.push(Card({},
      CardHeader(CardTitle('Connections'),
        CardDescription(a.check.status === 'checking' ? 'Checking each connection with AWS…'
          : a.check.checkedAt ? `Checked ${when(new Date(a.check.checkedAt * 1000).toISOString())}` : 'Not checked yet')),
      CardContent(Table(
        TableHeader(TableRow(['Name', 'AWS account', 'Sign-in', 'Region', 'Status', ''].map((t) => TableHead(t)))),
        TableBody(a.connections.map(ConnectionRow))))));
  }
  kids.push(Alert({ iconName: 'info', title: 'Kept apart from everything else' },
    h('p', { text: `These logins are stored in ${a.dir} (readable only by you). They are not your ~/.aws profiles, not a store’s S3 keys, and never written into a store’s .env, env.php or Git repository. Store scripts, composer and the containers never see them.` }),
    h('p', { text: 'Creating an environment needs an admin-level login; day-to-day work only needs the limited operator policy each environment comes with.' })));
  if (a.operations && a.operations.length) {
    kids.push(Card({ gap: 'sm' }, CardHeader(CardTitle('Recent runs')),
      CardContent(h('div', { class: 'space-y-1.5' }, a.operations.map((o) => h('div', { class: 'flex items-center gap-2 text-sm' },
        OpBadge(o.status), h('span', { text: o.label || o.kind }), h('span', { class: 'text-muted-foreground ml-auto', text: when(o.startedAt) })))))));
  }
  setKids(contentSlot, kids);
}
