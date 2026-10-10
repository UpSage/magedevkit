// MageDevKit dashboard, part 5 of 5: boot. Runs after dash-core.js,
// dash-env.js, dash-git.js and dash-modules.js have defined everything.
'use strict';

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

S.route = parseRoute();
render();
refresh();
setInterval(() => { if (S.route.view === 'env' && S.route.tab === 'operations' && !userBusy()) render(); }, 15000);
