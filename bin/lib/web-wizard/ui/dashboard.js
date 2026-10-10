// MageDevKit dashboard, part 4 of 4: boot. Runs after dash-core.js,
// dash-env.js and dash-git.js have defined everything.
'use strict';

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

S.route = parseRoute();
render();
refresh();
setInterval(() => { if (S.route.view === 'env' && S.route.tab === 'operations' && !userBusy()) render(); }, 15000);
