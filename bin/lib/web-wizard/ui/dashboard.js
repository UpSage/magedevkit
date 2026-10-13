// MageDevKit dashboard, part 6 of 6: boot. Runs after dash-core.js,
// dash-env.js, dash-git.js, dash-modules.js and dash-aws.js have defined everything.
'use strict';

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

S.route = parseRoute();
render();
refresh();
setInterval(() => { if (S.route.view === 'env' && S.route.tab === 'operations' && !userBusy()) render(); }, 15000);
