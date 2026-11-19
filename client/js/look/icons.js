/* EGO icon set for the new look: duotone line icons in the client's cosmic language
   (stars, orbits, planets). Swaps app.js `icons` only while html.look-next is on and
   restores the classic set otherwise. Classes: d = soft fill, f = solid fill,
   i-spin / i-tw / i-float = small hover motions (overhaul.css). */
(() => {
  'use strict';
  if (typeof icons !== 'object' || typeof hydrate !== 'function') return;
  const STAR = (x, y, r, cls = 'f i-tw') =>
    `<path class="${cls}" d="M${x} ${y - r}C${x + r * .18} ${y - r * .18} ${x + r * .18} ${y - r * .18} ${x + r} ${y} ${x + r * .18} ${y + r * .18} ${x + r * .18} ${y + r * .18} ${x} ${y + r} ${x - r * .18} ${y + r * .18} ${x - r * .18} ${y + r * .18} ${x - r} ${y} ${x - r * .18} ${y - r * .18} ${x - r * .18} ${y - r * .18} ${x} ${y - r}Z"/>`;
  const NEXT = {
    // Dialogues: a speech bubble with a star inside
    message: `<path class="d" d="M20.5 11a7.5 7.5 0 0 1-7.5 7.5H6.6L3 21.3V10A7.5 7.5 0 0 1 10.5 2.5h2.5A7.5 7.5 0 0 1 20.5 10z"/><path d="M20.5 11a7.5 7.5 0 0 1-7.5 7.5H6.6L3 21.3V10A7.5 7.5 0 0 1 10.5 2.5h2.5A7.5 7.5 0 0 1 20.5 10z"/>${STAR(11.8, 10.4, 3.4)}`,
    // Characters: two cameo portraits, the front one lit
    users: `<circle cx="15.5" cy="7.5" r="3" opacity=".55"/><path d="M20.5 19.5a5 5 0 0 0-4-4.9" opacity=".55"/><circle class="d" cx="9" cy="8.5" r="3.6"/><circle cx="9" cy="8.5" r="3.6"/><path class="d" d="M2.5 20.5a6.5 6.5 0 0 1 13 0z"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/>`,
    // Worlds: a planet with a tilted ring and a moon
    globe: `<circle class="d" cx="12" cy="12" r="6.2"/><circle cx="12" cy="12" r="6.2"/><g class="i-spin"><ellipse cx="12" cy="12" rx="10.5" ry="3.4" transform="rotate(-24 12 12)"/></g><circle class="f" cx="20.2" cy="4.6" r="1.4"/>`,
    // Library: an open book with a star ribbon
    bookmark: `<path class="d" d="M12 6.5C9.8 4.8 6.5 4.3 3 4.8v13.7c3.5-.5 6.8 0 9 1.7 2.2-1.7 5.5-2.2 9-1.7V4.8c-3.5-.5-6.8 0-9 1.7z"/><path d="M12 6.5C9.8 4.8 6.5 4.3 3 4.8v13.7c3.5-.5 6.8 0 9 1.7 2.2-1.7 5.5-2.2 9-1.7V4.8c-3.5-.5-6.8 0-9 1.7zM12 6.5v13.7"/>${STAR(17, 10.5, 2.6)}`,
    // Settings: a small sun with orbiting dots
    settings: `<circle class="d" cx="12" cy="12" r="3.4"/><circle cx="12" cy="12" r="3.4"/><g class="i-spin"><circle cx="12" cy="12" r="8.4" stroke-dasharray="3 3.6"/><circle class="f" cx="12" cy="3.6" r="1.6"/><circle class="f" cx="19.3" cy="16.2" r="1.3"/><circle class="f" cx="4.7" cy="16.2" r="1.1"/></g>`,
    panel: `<rect class="d" x="3" y="3.5" width="6.5" height="17" rx="3"/><rect x="3" y="3.5" width="18" height="17" rx="4.5"/><path d="M9.5 3.5v17"/>`,
    // Search: a lens with a star in it
    search: `<circle class="d" cx="10.5" cy="10.5" r="7"/><circle cx="10.5" cy="10.5" r="7"/><path d="m20.5 20.5-4.9-4.9"/>${STAR(10.5, 10.5, 2.8)}`,
    plus: `<path d="M12 5v14M5 12h14"/><circle class="f i-tw" cx="18.6" cy="5.4" r="1.3"/>`,
    // Mood (theme): crescent and two stars
    moon: `<path class="d" d="M19.5 14.5A8.5 8.5 0 0 1 9.5 4.5a8.5 8.5 0 1 0 10 10z"/><path d="M19.5 14.5A8.5 8.5 0 0 1 9.5 4.5a8.5 8.5 0 1 0 10 10z"/>${STAR(17.5, 5, 2.2)}${STAR(21, 9.4, 1.3, 'f')}`,
    sliders: `<path d="M3.5 7h17M3.5 17h17"/><circle class="f" cx="9" cy="7" r="2.6"/><circle class="f" cx="15.5" cy="17" r="2.6"/>`,
    // More: a tiny constellation
    more: `<path d="M5 13.5 12 10l7 3.5" opacity=".45"/><circle class="f" cx="5" cy="13.5" r="1.6"/><circle class="f" cx="12" cy="10" r="1.9"/><circle class="f" cx="19" cy="13.5" r="1.6"/>`,
    x: `<path d="M17.5 6.5l-11 11M6.5 6.5l11 11"/>`,
    // Send: a rising comet
    arrowup: `<path d="M12 19V5.5M6.5 11 12 5.5l5.5 5.5"/><circle class="f i-tw" cx="7" cy="19" r="1"/><circle class="f" cx="17" cy="18" r=".8"/>`,
    sparkles: `${STAR(10, 12, 7, 'd')}${STAR(10, 12, 7, '')}${STAR(18.5, 5.5, 2.4)}${STAR(19, 18, 1.6, 'f')}`,
    quote: `<path class="d" d="M4 11h6v7H4zM14 11h6v7h-6z"/><path d="M10 11H4v7h6v-7a6 6 0 0 0-6-6M20 11h-6v7h6v-7a6 6 0 0 0-6-6"/>`,
    copy: `<rect class="d" x="8.5" y="8.5" width="12" height="12" rx="3"/><rect x="8.5" y="8.5" width="12" height="12" rx="3"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>`,
    speaker: `<path class="d" d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4zM15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/>`,
    refresh: `<path d="M20 6.5V11h-4.5M4 17.5V13h4.5"/><path d="M5.6 9.2A7 7 0 0 1 18.4 8l1.6 3M4 13l1.6 3a7 7 0 0 0 12.8-1.2"/>`,
    edit: `<path class="d" d="M15.5 4.5l4 4L9 19H5v-4z"/><path d="M15.5 4.5l4 4L9 19H5v-4zM12.5 21h8"/>`,
    trash: `<path class="d" d="M6.5 7.5h11l-1 13h-9z"/><path d="M3.5 7.5h17M9 7.5V4.5h6v3M6.5 7.5l1 13h9l1-13M10.5 11v6M13.5 11v6"/>`,
    heart: `<path class="d" d="M20.6 5a5.3 5.3 0 0 0-7.6 0L12 6l-1-1a5.3 5.3 0 0 0-7.6 7.5L12 21l8.6-8.5a5.3 5.3 0 0 0 0-7.5z"/><path d="M20.6 5a5.3 5.3 0 0 0-7.6 0L12 6l-1-1a5.3 5.3 0 0 0-7.6 7.5L12 21l8.6-8.5a5.3 5.3 0 0 0 0-7.5z"/>`,
  };
  const CLASSIC = { ...icons };
  let current = null;
  function sync() {
    const next = document.documentElement.classList.contains('look-next');
    if (next === current) return;
    current = next;
    Object.assign(icons, next ? NEXT : CLASSIC);
    hydrate(document);
  }
  new MutationObserver(sync).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  sync();
})();
