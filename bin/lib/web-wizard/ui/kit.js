// Shared UI kit for the web wizard (app.js) and the dashboard (dashboard.js):
// tiny DOM helpers, Lucide icons and shadcn/ui component recipes, plus the
// JSON API helper. A classic script — its top-level declarations are globals
// for the scripts loaded after it. Vanilla JS, no dependencies; icons are
// Lucide (ISC license, https://lucide.dev). Tailwind classes must stay
// literal strings here — bin/lib/web-wizard/build-css scans this file.
'use strict';

// ---------------------------------------------------------------------------
// Tiny DOM helpers
// ---------------------------------------------------------------------------

const SVG_NS = 'http://www.w3.org/2000/svg';

// Props: class, text, on<event> (functions only — strings are ignored, never
// turned into inline handlers), dataset, booleans as properties, the rest as
// attributes.
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) { if (typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v); }
    // Never a script URL, whatever the data said (the CSP blocks it too).
    else if ((k === 'href' || k === 'src') && /^\s*(javascript|vbscript|data):/i.test(String(v))) continue;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (typeof v === 'boolean') el[k] = v;
    else el.setAttribute(k, v);
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of [kids].flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function cn(...parts) {
  return parts.filter(Boolean).join(' ');
}

// Lucide icons (ISC license) — path data inlined so the page needs no assets.
const ICONS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
  circle: '<circle cx="12" cy="12" r="10"/>',
  circleCheck: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  circleX: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
  circleMinus: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>',
  circleDot: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  blocks: '<path d="M10 22V7a1 1 0 0 0-1-1H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5a1 1 0 0 0-1-1H2"/><rect x="14" y="2" width="8" height="8" rx="1"/>',
  search: '<path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronLeft: '<path d="m15 18-6-6 6-6"/>',
  minus: '<path d="M5 12h14"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  ellipsis: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  wifiOff: '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
  // dashboard
  arrowLeft: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  rotateCw: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  square: '<rect width="18" height="18" x="3" y="3" rx="2"/>',
  gitBranch: '<line x1="6" x2="6" y1="3" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  server: '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  layoutGrid: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  hardDrive: '<line x1="22" x2="2" y1="12" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
};

function icon(name, cls) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', cn('size-4 shrink-0', cls));
  svg.innerHTML = ICONS[name] || '';
  return svg;
}

// ---------------------------------------------------------------------------
// shadcn/ui component recipes — the only place these class strings live
// ---------------------------------------------------------------------------

const BUTTON_BASE = 'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[color,box-shadow,background-color] disabled:pointer-events-none disabled:opacity-50 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer';
const BUTTON_VARIANTS = {
  default: 'bg-primary text-primary-foreground shadow-xs hover:bg-primary-hover',
  destructive: 'bg-destructive text-white shadow-xs hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60',
  outline: 'border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:bg-input/30 dark:border-input dark:hover:bg-input/50',
  secondary: 'bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/80',
  ghost: 'hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50',
  link: 'text-link underline-offset-4 hover:underline',
};
const BUTTON_SIZES = {
  default: 'h-9 px-4 py-2 has-[>svg]:px-3',
  sm: 'h-8 gap-1.5 px-3 has-[>svg]:px-2.5',
  lg: 'h-10 px-6 has-[>svg]:px-4',
  icon: 'size-9',
  'icon-sm': 'size-8',
  'icon-xs': 'size-7',
};

// With href it renders an <a> with the same look (shadcn's asChild); a
// target of _blank also gets rel=noopener. Sizes and variants come from the
// maps above — `cls` is for layout (position, margin, shrink) only: a second
// size/gap/padding class there loses to the recipe's in the compiled CSS.
function Button({ variant = 'default', size = 'default', iconName, label, onClick, type = 'button', disabled, title, cls, href, target, download }) {
  const props = {
    class: cn(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], cls),
    onClick, title, 'aria-label': size.startsWith('icon') ? (title || label) : null,
  };
  const kids = [iconName ? icon(iconName) : null, size.startsWith('icon') ? null : label];
  if (href) {
    return h('a', Object.assign(props, {
      href, target, download: download ? '' : null, rel: target === '_blank' ? 'noopener noreferrer' : null,
    }), kids);
  }
  return h('button', Object.assign(props, { type, disabled: !!disabled }), kids);
}

// shadcn Pagination: Previous, the page numbers (… for gaps), Next. page is
// 1-based; onChange(page) gets the page clicked. The links are buttons
// with the Button recipe — ghost, and outline for the current page.
function Pagination({ page, pageCount, onChange, label = 'Pagination' }) {
  const link = (p, kids, { active, disabled, aria, size } = {}) => h('button', {
    type: 'button', class: cn(BUTTON_BASE, BUTTON_VARIANTS[active ? 'outline' : 'ghost'], size || BUTTON_SIZES.icon),
    'aria-current': active ? 'page' : null, 'aria-label': aria, disabled: !!disabled,
    onClick: () => { if (p !== page) onChange(p); },
  }, kids);
  return h('nav', { role: 'navigation', 'aria-label': label, class: 'mx-auto flex w-full justify-center' },
    h('ul', { class: 'flex flex-row items-center gap-1' },
      h('li', {}, link(page - 1, [icon('chevronLeft'), h('span', { class: 'hidden sm:block', text: 'Previous' })],
        { disabled: page <= 1, aria: 'Go to previous page', size: 'h-9 gap-1 px-2.5 sm:pl-2.5' })),
      paginationRange(page, pageCount).map((p) => h('li', {}, p == null
        ? h('span', { class: 'flex size-9 items-center justify-center', 'aria-hidden': 'true' }, icon('ellipsis'))
        : link(p, String(p), { active: p === page }))),
      h('li', {}, link(page + 1, [h('span', { class: 'hidden sm:block', text: 'Next' }), icon('chevronRight')],
        { disabled: page >= pageCount, aria: 'Go to next page', size: 'h-9 gap-1 px-2.5 sm:pr-2.5' }))));
}

// The pages Pagination shows: always 7 slots once there are more than 7
// pages — first, last, the current one with its neighbours, null for a gap.
function paginationRange(page, count) {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i + 1);
  const start = Math.max(2, Math.min(page - 1, count - 4));
  const end = Math.min(count - 1, Math.max(page + 1, 5));
  const out = [1];
  if (start > 2) out.push(null);
  for (let p = start; p <= end; p += 1) out.push(p);
  if (end < count - 1) out.push(null);
  out.push(count);
  return out;
}

// An inline text link (inside sentences, lists and tables).
function TextLink({ href, text, external }) {
  return h('a', {
    class: 'text-link inline-flex min-w-0 items-center gap-1 break-all font-medium underline-offset-4 hover:underline',
    href, target: external ? '_blank' : null, rel: external ? 'noopener noreferrer' : null,
  }, text, external ? icon('external', 'size-3.5') : null);
}

// The light/dark switch shown in both page headers.
function ThemeToggle(onChange) {
  const dark = document.documentElement.classList.contains('dark');
  // Round on purpose (the one round button): rounded-full! beats the base radius.
  return Button({ variant: 'ghost', size: 'icon', iconName: dark ? 'sun' : 'moon', title: dark ? 'Light mode' : 'Dark mode', cls: 'rounded-full!', onClick: () => toggleTheme(onChange) });
}

const PAGE_WIDTHS = { '4xl': 'max-w-4xl', '6xl': 'max-w-6xl', '7xl': 'max-w-7xl' };

// The sticky page header: title, optional subtitle, and a right-hand slot
// (the caller's element, usually 'ml-auto flex items-center gap-2').
// brandCls hides the title where a Sidebar already shows it.
function PageHeader({ title, titleId, subtitle, href, width = '6xl', right, brandCls }) {
  const brand = h('div', { class: cn('min-w-0', brandCls) },
    h('div', { class: 'text-sm font-semibold leading-tight', id: titleId, text: title }),
    subtitle ? h('div', { class: 'text-muted-foreground hidden truncate text-xs sm:block', text: subtitle }) : null);
  // h-16 (border included) — the same height as SidebarHeader, so the two line up.
  return h('header', { class: 'sticky top-0 z-10 h-16 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80' },
    h('div', { class: cn('mx-auto flex h-full items-center gap-3 px-4', PAGE_WIDTHS[width]) },
      href ? h('a', { href, class: 'flex min-w-0 items-center gap-3' }, brand) : brand,
      right));
}

// Magento-admin style shell: a dark, full-height sidebar (md and up) next to
// the page. Below md the sidebar is hidden and the page brings its own
// navigation (header + Tabs).
function AppShell({ sidebar, main }) {
  return h('div', { class: 'flex min-h-dvh' },
    h('aside', { class: 'bg-sidebar text-sidebar-foreground sticky top-0 hidden h-dvh w-64 shrink-0 flex-col overflow-y-auto border-r border-sidebar-border md:flex', 'aria-label': 'Main navigation' }, sidebar),
    h('div', { class: 'min-w-0 flex-1' }, main));
}
function SidebarHeader({ title, subtitle, href }) {
  // h-16 like PageHeader, so the sidebar header and the page header line up.
  return h(href ? 'a' : 'div', { href, class: cn('flex h-16 shrink-0 items-center gap-3 border-b border-sidebar-border px-4', href ? 'outline-none focus-visible:ring-[3px] focus-visible:ring-sidebar-primary/60' : null) },
    h('div', { class: 'min-w-0' },
      h('div', { class: 'text-sm font-semibold leading-tight text-sidebar-accent-foreground', text: title }),
      subtitle ? h('div', { class: 'truncate text-xs', text: subtitle }) : null));
}
function SidebarGroup(label, ...items) {
  return h('nav', { class: 'py-2', 'aria-label': label },
    h('div', { class: 'px-4 pt-2 pb-1 text-xs font-semibold tracking-wide uppercase', text: label }),
    h('ul', {}, items.flat().filter(Boolean).map((it) => h('li', {}, it))));
}
// One menu entry. lead: a node shown instead of the icon (e.g. a status dot);
// nested: a section under an open entry.
// Without href it's a plain row (e.g. a wizard step); `current` then names
// the aria-current value. muted: upcoming or skipped entries.
function SidebarItem({ href, iconName, lead, label, active, nested, badge, current = 'page', muted, strike }) {
  return h(href ? 'a' : 'div', {
    href, 'aria-current': active ? current : null,
    class: cn('flex items-center gap-3 border-l-4 py-2 pr-4 text-sm transition-colors outline-none [&>svg]:size-4 [&>svg]:shrink-0',
      nested ? 'pl-10' : 'pl-3',
      href ? 'focus-visible:ring-[3px] focus-visible:ring-inset focus-visible:ring-sidebar-primary/60' : null,
      active ? 'border-sidebar-primary bg-sidebar-accent font-medium text-sidebar-accent-foreground'
        : cn('border-transparent', href ? 'hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground' : null, muted ? 'opacity-70' : null)),
  }, lead || (iconName ? icon(iconName) : null),
  h('span', { class: cn('min-w-0 flex-1 truncate', strike ? 'line-through' : null), text: label }), badge || null);
}

// The version line at the bottom of every page (and the sidebar's foot).
function PageFooter(version, { sidebar, cls } = {}) {
  return h('footer', {
    class: cn('border-t px-4 py-4 text-xs', sidebar ? 'mt-auto border-sidebar-border text-sidebar-foreground' : 'text-muted-foreground', cls),
  }, `MageDevKit v${version || '?'} · Magento Developer Kit`);
}

// Drag and drop between DropZones. Each kind of item gets its own
// application/x-<kind>-<from> type, so a zone can tell during dragover
// (when the data itself isn't readable yet) whether it accepts the drag.
function makeDraggable(el, kind, getData) {
  el.draggable = true;
  el.addEventListener('dragstart', (e) => {
    const d = getData();
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData(`application/x-${kind}-${d.from}`, JSON.stringify(d));
    e.dataTransfer.setData('text/plain', (d.paths || []).join('\n'));
    el.dataset.dragging = 'true';
  });
  el.addEventListener('dragend', () => { el.dataset.dragging = 'false'; });
  return el;
}

// A column that takes drops from another column: header (title, count,
// actions), its rows, and a "drop here" overlay while a drag hovers it.
function DropZone({ title, count, actions, hint, kind, from, onDrop }, ...kids) {
  const type = `application/x-${kind}-${from}`;
  const overlay = h('div', { class: 'pointer-events-none absolute inset-0 hidden items-center justify-center border-2 border-dashed border-ring bg-accent/70 text-sm font-medium text-accent-foreground group-data-[over=true]:flex' }, hint);
  const zone = h('section', { class: 'group relative flex min-w-0 flex-col border bg-card', 'aria-label': title },
    h('div', { class: 'flex flex-wrap items-center gap-2 border-b px-3 py-2' },
      h('h3', { class: 'text-sm font-medium', text: title }), count != null ? Badge(String(count), 'secondary') : null,
      h('div', { class: 'ml-auto flex flex-wrap gap-1' }, actions)),
    h('div', { class: 'min-h-28 flex-1' }, kids),
    overlay);
  let depth = 0;
  const accepts = (e) => [...e.dataTransfer.types].includes(type);
  zone.addEventListener('dragenter', (e) => { if (!accepts(e)) return; e.preventDefault(); depth += 1; zone.dataset.over = 'true'; });
  zone.addEventListener('dragover', (e) => { if (!accepts(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
  zone.addEventListener('dragleave', (e) => { if (!accepts(e)) return; depth = Math.max(0, depth - 1); if (!depth) zone.dataset.over = 'false'; });
  zone.addEventListener('drop', (e) => {
    if (!accepts(e)) return;
    e.preventDefault();
    depth = 0; zone.dataset.over = 'false';
    try { onDrop(JSON.parse(e.dataTransfer.getData(type))); } catch (err) { /* not ours */ }
  });
  return zone;
}

// gap / pad: 'sm' for denser cards (gap-4 / py-4) — never via cls, which
// can't override the recipe's own gap-6 / py-6.
function Card({ cls, gap, pad } = {}, ...kids) {
  return h('div', { class: cn('bg-card text-card-foreground flex flex-col rounded-xl border shadow-sm',
    gap === 'sm' ? 'gap-4' : 'gap-6', pad === 'sm' ? 'py-4' : 'py-6', cls) }, kids);
}
function CardHeader(...kids) {
  return h('div', { class: 'grid auto-rows-min items-start gap-1.5 px-6 has-data-[slot=card-action]:grid-cols-[minmax(0,1fr)_auto]' }, kids);
}
function CardTitle(text, cls, tooltip) {
  return h('h2', { class: cn('leading-snug font-semibold', cls), text, title: tooltip });
}
function CardDescription(...kids) {
  return h('p', { class: 'text-muted-foreground text-sm' }, kids);
}
// Top-right corner of a CardHeader (badges, small buttons).
function CardAction(...kids) {
  return h('div', { class: 'col-start-2 row-span-2 row-start-1 flex flex-col items-end gap-1 self-start justify-self-end', 'data-slot': 'card-action' }, kids);
}
function CardContent(...kids) {
  return h('div', { class: 'px-6' }, kids);
}
function CardFooter(...kids) {
  return h('div', { class: 'flex flex-wrap items-center gap-2 px-6' }, kids);
}

// A plain bordered box inside a card (upload status, an option group).
const PANEL_SIZES = { default: 'p-4', sm: 'px-3 py-2' };
function Panel({ size = 'default', cls, title } = {}, ...kids) {
  return h('div', { class: cn('rounded-lg border', PANEL_SIZES[size], cls), title }, kids);
}

// One line in a bordered list (file lists, ignored paths): items in a row,
// divided from the next by a border. header: the list's muted top line;
// hover: highlights under the pointer. cls is for layout/behaviour only.
const LIST_ROW = 'flex items-center gap-2 border-b px-3 py-1.5 last:border-b-0';
function ListRow({ header, hover, cls, ...rest } = {}, ...kids) {
  return h('div', Object.assign({
    class: cn(LIST_ROW, header ? 'text-muted-foreground text-xs' : null, hover ? 'hover:bg-muted/50' : null, cls),
    role: header ? null : 'listitem',
  }, rest), kids);
}
// A bordered, scrolling box of ListRows (on its own, not inside a DropZone).
function ListBox({ label } = {}, ...rows) {
  return h('div', { class: 'max-h-64 overflow-auto rounded-lg border', role: 'list', 'aria-label': label }, rows);
}

// A Card that opens and closes (native <details>).
function Collapsible({ title, iconName }, ...kids) {
  return h('details', { class: 'group bg-card text-card-foreground rounded-xl border shadow-sm' },
    h('summary', { class: 'flex cursor-pointer list-none items-center gap-2 px-6 py-4 text-sm font-medium select-none [&::-webkit-details-marker]:hidden' },
      iconName ? icon(iconName, 'text-muted-foreground') : null, title,
      icon('chevronDown', 'ml-auto text-muted-foreground transition-transform group-open:rotate-180')),
    h('div', { class: 'pb-4' }, kids));
}

// Tabs. items: [{ id, label, iconName, shortLabel }]. Buttons by default
// (onSelect is called); with hrefFor the triggers are links (hash routes).
// Arrow keys, Home and End move between them.
const TABS_TRIGGER = 'data-[state=active]:bg-background dark:data-[state=active]:text-foreground focus-visible:border-ring focus-visible:ring-ring/50 dark:data-[state=active]:border-input dark:data-[state=active]:bg-input/30 text-foreground dark:text-muted-foreground inline-flex h-[calc(100%-1px)] flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-sm font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:ring-[3px] outline-none data-[state=active]:shadow-sm [&_svg]:size-4 [&_svg]:shrink-0';
function Tabs({ items, value, onSelect, hrefFor, label }) {
  const list = h('div', { role: 'tablist', 'aria-label': label, class: 'bg-muted text-muted-foreground inline-flex h-9 w-full max-w-full items-center overflow-x-auto rounded-lg p-[3px] sm:w-fit' });
  const setActive = (id) => {
    for (const el of list.children) {
      const on = el.dataset.tab === id;
      el.dataset.state = on ? 'active' : 'inactive';
      el.setAttribute('aria-selected', String(on));
      el.tabIndex = on ? 0 : -1;
    }
  };
  append(list, items.map((t) => {
    const props = { role: 'tab', class: TABS_TRIGGER, dataset: { tab: t.id }, onClick: () => { setActive(t.id); if (onSelect) onSelect(t.id); } };
    const kids = [t.iconName ? icon(t.iconName) : null,
      t.shortLabel ? [h('span', { class: 'hidden sm:inline', text: t.label }), h('span', { class: 'sm:hidden', text: t.shortLabel })] : t.label];
    return hrefFor ? h('a', Object.assign(props, { href: hrefFor(t.id) }), kids) : h('button', Object.assign(props, { type: 'button' }), kids);
  }));
  list.addEventListener('keydown', (e) => {
    const tabs = [...list.children];
    const i = tabs.indexOf(document.activeElement);
    const step = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    const next = step ? tabs[(i + step + tabs.length) % tabs.length]
      : e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[tabs.length - 1] : null;
    if (i < 0 || !next) return;
    e.preventDefault();
    next.focus();
    next.click();
    // A link trigger re-renders the page on hashchange: put focus back.
    if (hrefFor) setTimeout(() => { const sel = document.querySelector(`[role=tab][data-tab="${next.dataset.tab}"]`); if (sel) sel.focus(); }, 0);
  });
  setActive(value);
  return list;
}

const INPUT_CLASS = 'placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 aria-invalid:border-destructive';

function Input(props) {
  return h('input', Object.assign({ class: INPUT_CLASS, autocomplete: 'off', spellcheck: 'false' }, props));
}

// An Input with a leading search icon (list filters). props go to the
// <input>; like Checkbox it returns the wrapper — use .querySelector('input').
function SearchInput(props) {
  return h('div', { class: 'relative w-full' },
    icon('search', 'text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2'),
    h('input', Object.assign({ class: cn(INPUT_CLASS, 'pl-8'), type: 'search', autocomplete: 'off', spellcheck: 'false' }, props)));
}

// shadcn Textarea — same tokens as Input.
function Textarea(props) {
  return h('textarea', Object.assign({
    class: 'placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex field-sizing-content min-h-16 w-full rounded-md border bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 aria-invalid:border-destructive',
    spellcheck: 'true',
  }, props));
}

// Shows a validation message under a field: the input gets aria-invalid (red
// border, from INPUT_CLASS) and the help text turns red.
function markInvalid(input, help, text) {
  input.setAttribute('aria-invalid', 'true');
  help.textContent = text;
  help.className = 'text-destructive text-xs';
}

// shadcn NativeSelect. options: [{ value, label }].
function NativeSelect({ options, value, onChange, cls, ...rest }) {
  const select = h('select', Object.assign({ class: cn(INPUT_CLASS, 'cursor-pointer appearance-none pr-9', cls), onChange }, rest),
    options.map((o) => h('option', { value: o.value, text: o.label, selected: o.value === value })));
  return h('div', { class: 'relative w-fit' }, select,
    icon('chevronDown', 'pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 opacity-50'));
}

// Native checkbox styled like shadcn's Checkbox. props go to the <input>;
// indeterminate: true is the "some selected" state of a select-all box (a
// dash; the browser reports the native property as "mixed" itself).
function Checkbox(props) {
  const input = h('input', Object.assign({
    type: 'checkbox',
    class: 'peer border-input dark:bg-input/30 checked:bg-primary checked:border-primary dark:checked:bg-primary indeterminate:bg-primary indeterminate:border-primary dark:indeterminate:bg-primary focus-visible:border-ring focus-visible:ring-ring/50 size-4 shrink-0 cursor-pointer appearance-none border shadow-xs transition-shadow outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50',
  }, props));
  return h('span', { class: 'relative inline-flex size-4 shrink-0' }, input,
    icon('check', 'pointer-events-none absolute inset-0 m-auto hidden size-3.5 text-primary-foreground peer-checked:block'),
    icon('minus', 'pointer-events-none absolute inset-0 m-auto hidden size-3.5 text-primary-foreground peer-indeterminate:block'));
}

// The strip above a table while rows are selected: "N selected" and the
// bulk actions (Buttons). Sticks under the page header while scrolling.
function SelectionBar(text, ...actions) {
  return h('div', { class: 'bg-card sticky top-16 z-10 flex flex-wrap items-center gap-2 border-b py-2', role: 'toolbar', 'aria-label': 'Selected rows' },
    h('span', { class: 'mr-auto text-sm font-medium', text }), actions);
}

// One option of a radio group, as a selectable card (shadcn RadioGroupItem
// inside a bordered label).
function RadioCard({ name, value, checked }, ...kids) {
  return h('label', { class: 'flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-accent/50 has-[:checked]:border-primary has-[:checked]:bg-accent has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50' },
    h('span', { class: 'relative mt-0.5 inline-flex size-4 shrink-0' },
      h('input', { type: 'radio', name, value, checked, class: 'peer border-input dark:bg-input/30 checked:border-primary size-4 shrink-0 cursor-pointer appearance-none rounded-full border shadow-xs outline-none' }),
      h('span', { class: 'pointer-events-none absolute inset-0 m-auto hidden size-2 rounded-full bg-primary peer-checked:block' })),
    h('span', { class: 'min-w-0 flex-1 space-y-0.5' }, kids));
}

function Label(text, forId) {
  return h('label', { class: 'flex items-center gap-2 text-sm leading-none font-medium select-none', for: forId, text });
}

const BADGE_BASE = 'inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 gap-1 [&>svg]:size-3';
const BADGE_VARIANTS = {
  default: 'border-transparent bg-primary text-primary-foreground',
  secondary: 'border-transparent bg-secondary text-secondary-foreground',
  destructive: 'border-transparent bg-destructive text-white dark:bg-destructive/60',
  outline: 'text-foreground',
  success: 'border-transparent bg-success/15 text-success',
  warning: 'border-transparent bg-warning/15 text-warning',
};
function Badge(text, variant = 'secondary', iconName, { spin } = {}) {
  return h('span', { class: cn(BADGE_BASE, BADGE_VARIANTS[variant]) }, iconName ? icon(iconName, spin ? 'animate-spin' : null) : null, text);
}

const ALERT_BASE = 'relative w-full rounded-lg border px-4 py-3 text-sm grid has-[>svg]:grid-cols-[calc(var(--spacing)*4)_1fr] grid-cols-[0_1fr] has-[>svg]:gap-x-3 gap-y-0.5 items-start [&>svg]:size-4 [&>svg]:translate-y-0.5 [&>svg]:text-current';
const ALERT_VARIANTS = {
  default: 'bg-card text-card-foreground',
  destructive: 'text-destructive bg-card [&>svg]:text-current',
  warning: 'text-warning bg-card border-warning/40',
  success: 'text-success bg-card border-success/40',
};
function Alert({ variant = 'default', iconName = 'info', title, onDismiss }, ...desc) {
  return h('div', { role: 'alert', class: cn(ALERT_BASE, ALERT_VARIANTS[variant], onDismiss ? 'pr-10' : null) },
    icon(iconName),
    title ? h('div', { class: 'col-start-2 min-h-4 font-medium tracking-tight', text: title }) : null,
    desc.length ? h('div', { class: cn('col-start-2 grid justify-items-start gap-1 text-sm [&_p]:leading-relaxed', variant === 'default' ? 'text-muted-foreground' : 'opacity-90') }, desc) : null,
    onDismiss ? Button({ variant: 'ghost', size: 'icon-xs', iconName: 'x', title: 'Dismiss', cls: 'absolute top-2 right-2', onClick: onDismiss }) : null);
}

function Progress(value, label) {
  const bar = h('div', { class: 'bg-primary h-full w-full flex-1 transition-transform duration-500' });
  bar.style.transform = `translateX(-${100 - Math.max(0, Math.min(100, value))}%)`;
  return h('div', {
    class: 'bg-primary/20 relative h-2 w-full overflow-hidden rounded-full',
    role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(Math.round(value)), 'aria-label': label,
  }, bar);
}

function Separator(cls) {
  return h('div', { class: cn('bg-border shrink-0 h-px w-full', cls), role: 'separator' });
}

function Skeleton(cls) {
  return h('div', { class: cn('bg-accent animate-pulse rounded-md', cls) });
}

function Spinner(cls) {
  return icon('loader', cn('animate-spin', cls));
}

// "Loading…" line with a spinner, for content that hasn't arrived yet.
function LoadingText(text) {
  return h('div', { class: 'text-muted-foreground flex items-center gap-2 text-sm' }, Spinner(), text);
}

function Kbd(text) {
  return h('kbd', { class: 'bg-muted text-muted-foreground pointer-events-none inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium', text });
}

function InlineCode(text, { strong, cls } = {}) {
  return h('code', { class: cn('rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.8125rem]', strong ? 'font-semibold' : null, cls), text });
}

function CodeLine(text, { copy = true } = {}) {
  return h('div', { class: 'flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2' },
    h('code', { class: 'min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-[0.8125rem]', text }),
    copy ? Button({ variant: 'ghost', size: 'icon-xs', iconName: 'copy', title: 'Copy', onClick: () => copyText(text) }) : null);
}

// A block of log / command output. bare: no frame (inside a card that
// already has one).
const LOG_SIZES = { sm: 'max-h-60', md: 'max-h-72', lg: 'max-h-80', xl: 'max-h-[60vh]' };
function LogBlock({ size = 'md', bare, text, cls } = {}) {
  return h('pre', {
    class: cn('overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap break-all', LOG_SIZES[size],
      bare ? 'text-muted-foreground' : 'rounded-md border bg-muted/50 p-3', cls),
    text,
  });
}

// A unified diff as a LogBlock, with added / removed / hunk lines tinted.
function DiffBlock(text, { size = 'xl' } = {}) {
  const pre = LogBlock({ size });
  append(pre, (text || '').split('\n').map((line) => h('span', {
    class: cn('block',
      line.startsWith('+') && !line.startsWith('+++') ? 'bg-success/10 text-success' : null,
      line.startsWith('-') && !line.startsWith('---') ? 'bg-destructive/10 text-destructive' : null,
      line.startsWith('@@') ? 'text-link' : null,
      /^(diff |index |\+\+\+ |--- )/.test(line) ? 'text-muted-foreground' : null),
    text: line || ' ',
  })));
  return pre;
}

// Label/value rows. rows: [label, value, note] arrays (falsy rows are
// skipped); an empty value shows as "Unknown".
const DL_COLS = { sm: 'sm:grid-cols-[8rem_minmax(0,1fr)]', md: 'sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]', lg: 'sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]' };
function DescriptionList(rows, { labelWidth = 'md', center } = {}) {
  const kids = [];
  for (const row of rows) {
    if (!row) continue;
    const [label, value, note] = row;
    const empty = value == null || value === '';
    kids.push(h('dt', { class: 'text-muted-foreground', text: label }),
      h('dd', { class: 'min-w-0 break-words' },
        empty ? h('span', { class: 'text-muted-foreground', text: 'Unknown' }) : value,
        note ? h('span', { class: 'text-muted-foreground ml-2 text-xs', text: note }) : null));
  }
  return h('dl', { class: cn('grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:gap-y-3 [&>dd]:mb-3 sm:[&>dd]:mb-0', DL_COLS[labelWidth], center ? 'sm:items-center' : null) }, kids);
}

function Table(...kids) {
  return h('div', { class: 'relative w-full overflow-x-auto' }, h('table', { class: 'w-full caption-bottom text-sm' }, kids));
}
function TableHeader(...rows) {
  return h('thead', { class: '[&_tr]:border-b [&_tr]:hover:bg-transparent' }, rows);
}
function TableBody(...rows) {
  return h('tbody', { class: '[&_tr:last-child]:border-0' }, rows);
}
function TableRow(...cells) {
  return h('tr', { class: 'hover:bg-muted/50 border-b transition-colors' }, cells);
}
function TableHead(content, cls) {
  return h('th', { class: cn('text-muted-foreground h-10 px-2 text-left align-middle font-medium whitespace-nowrap', cls) }, content);
}
function TableCell(content, cls) {
  return h('td', { class: cn('p-2 align-middle whitespace-nowrap', cls) }, content);
}

const TOAST_VARIANTS = {
  default: ['', 'check'], success: ['text-success', 'circleCheck'], warning: ['text-warning', 'alert'], destructive: ['text-destructive', 'alert'],
};
function toast(message, variant = 'default') {
  const [cls, ic] = TOAST_VARIANTS[variant] || TOAST_VARIANTS.default;
  const t = h('div', {
    class: cn('pointer-events-auto flex w-full max-w-sm items-center gap-2 rounded-lg border bg-popover px-4 py-3 text-sm text-popover-foreground shadow-lg transition-opacity duration-300', cls),
  }, icon(ic), message);
  document.getElementById('toasts').append(t);
  setTimeout(() => { t.classList.add('opacity-0'); setTimeout(() => t.remove(), 300); }, 3200);
}

function copyText(text) {
  const done = () => toast('Copied to the clipboard');
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
function fallbackCopy(text, done) {
  const ta = h('textarea', { class: 'fixed -left-[9999px]', 'aria-hidden': 'true' });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed — select the text instead', 'destructive'); }
  ta.remove();
}

// Native <dialog>, styled like shadcn's Dialog. Resolves with the clicked
// action's value (or null on Esc / outside click).
function dialog({ title, description, actions }) {
  return new Promise((resolve) => {
    const dlg = h('dialog', {
      class: 'bg-background text-foreground m-auto w-full max-w-[calc(100%-2rem)] gap-4 rounded-lg border p-6 shadow-lg sm:max-w-lg backdrop:bg-black/50',
    });
    const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    append(dlg, [
      h('div', { class: 'flex flex-col gap-2 text-center sm:text-left' },
        h('h2', { class: 'text-lg leading-none font-semibold', text: title }),
        h('div', { class: 'text-muted-foreground text-sm space-y-2' }, description)),
      h('div', { class: 'mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end' },
        actions.map((a) => Button({ variant: a.variant || 'outline', label: a.label, onClick: () => close(a.value) }))),
    ]);
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(null); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) close(null); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

// A dialog that stays open until its answer passes `validate` (or is a
// cancel: null/false). validate(value) returns true to accept; on false it
// has already marked what's wrong (markInvalid), and the dialog shows again.
async function formDialog({ title, description, actions, validate }) {
  for (;;) {
    const value = await dialog({ title, description, actions });
    if (!value || !validate || validate(value)) return value;
  }
}

// Yes/no confirmation: resolves true only for the confirm button.
// text: a paragraph, or nodes; variant 'destructive' for irreversible steps.
function confirmDialog({ title, text, confirm = 'Continue', cancel = 'Cancel', variant = 'default' }) {
  return dialog({
    title,
    description: [typeof text === 'string' ? h('p', { text }) : text],
    actions: [{ label: cancel, value: false }, { label: confirm, value: true, variant }],
  }).then((v) => v === true);
}

// A labelled form control with optional help text underneath (the help
// element is returned by FormHelp, so markInvalid can rewrite it).
function FormField(label, id, control, help) {
  return h('div', { class: 'space-y-2' }, Label(label, id), control, help || null);
}

function FormHelp(text, id) {
  return h('p', { id, class: 'text-muted-foreground text-xs', text });
}

// Replaces a log <pre>'s text, keeping it pinned to the bottom when the
// reader was already there (or `stick`), and leaving it alone when they had
// scrolled up to read.
function setLogText(pre, text, stick) {
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent = text;
  if (atBottom || stick) pre.scrollTop = pre.scrollHeight;
}

// ---------------------------------------------------------------------------
// Server API — every POST carries X-Wizard (the servers' CSRF check)
// ---------------------------------------------------------------------------

// Both never throw: a dropped connection (server stopped, laptop asleep)
// comes back as { ok: false, status: 0 } with an error text, so callers'
// busy/submitting flags are always reset.
const OFFLINE = 'Couldn’t reach the server — is it still running? Check the terminal, then try again.';

async function request(path, init) {
  let res;
  try { res = await fetch(path, init); } catch (e) { return { ok: false, status: 0, data: { error: OFFLINE } }; }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty */ }
  return { ok: res.ok, status: res.status, data };
}

function api(path, body, method = 'POST') {
  return request(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Wizard': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
}

function getJSON(path) {
  return request(path, { credentials: 'same-origin', cache: 'no-store' });
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// A new tab opened right away, while the click still counts as a user action
// (popup blockers), showing `text` until navigateTab points it somewhere.
// null if the browser blocked it. The tab never gets a handle on this page.
function openPendingTab(text) {
  const tab = window.open('', '_blank');
  if (tab) {
    tab.opener = null;
    tab.document.title = text;
    tab.document.body.textContent = text;
  }
  return tab;
}

// Points a pending tab at an http(s) URL from the server; anything else
// closes it instead of loading it.
function navigateTab(tab, url) {
  if (!tab) return false;
  if (!/^https?:\/\//i.test(String(url || ''))) { tab.close(); return false; }
  tab.location.href = url;
  return true;
}

// True while someone is typing or a dialog is open — background re-renders
// wait, so they never steal focus or wipe a half-typed value.
function userBusy() {
  const a = document.activeElement;
  return !!document.querySelector('dialog[open]')
    || !!(a && (a.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName)));
}

// Flips light/dark, remembers it (theme.js applies it on load), then lets
// the page re-render its toggle.
function toggleTheme(onChange) {
  const dark = !document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', dark);
  try { localStorage.setItem('wizard-theme', dark ? 'dark' : 'light'); } catch (e) { /* ignore */ }
  if (onChange) onChange();
}

// 75 -> "1m 15s", 9 -> "9s".
function fmtDuration(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
}

// 1536 -> "1.5 KB"; null stays null.
function fmtBytes(n) {
  if (n == null) return null;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
