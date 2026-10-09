// Applies the saved/preferred color scheme before first paint (loaded
// synchronously in <head>; inline scripts are blocked by the CSP).
(function () {
  var saved = null;
  try { saved = localStorage.getItem('wizard-theme'); } catch (e) { /* storage blocked */ }
  var dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', dark);
})();
