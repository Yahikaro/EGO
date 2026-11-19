/* Client look switch: classic (current) or «Обновлённый» — the same style refined (overhaul.css).
   Loaded in <head> so the chosen look applies before the first paint.
   ?look=next / ?look=classic in the address also sets it (handy for comparing). */
(() => {
  'use strict';
  const KEY = 'ego-look';
  // «Обновлённый» is the default look (2026-11-06); «Классический» stays one click away.
  let look = 'next';
  try {
    const asked = new URLSearchParams(location.search).get('look');
    if (asked === 'next' || asked === 'classic') localStorage.setItem(KEY, asked);
    look = localStorage.getItem(KEY) === 'classic' ? 'classic' : 'next';
  } catch { /* storage blocked: default look */ }
  const REDUCED = (() => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } })();

  /* Starry sky behind the whole client (the welcome page's background). */
  let sky = null;
  function startSky() {
    if (sky || !document.body) return;
    const nebula = document.createElement('div');
    nebula.className = 'look-nebula'; nebula.setAttribute('aria-hidden', 'true'); nebula.innerHTML = '<i></i><i></i><i></i>';
    const canvas = document.createElement('canvas');
    canvas.className = 'look-sky'; canvas.setAttribute('aria-hidden', 'true');
    document.body.prepend(canvas); document.body.prepend(nebula);
    const ctx = canvas.getContext('2d');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0, h = 0, stars = [], mx = 0, my = 0, frame = 0, running = true;
    // Star colours follow the theme (--t-star / --t-star2, "r,g,b").
    let starRGB = '214,200,255', warmRGB = '255,190,214';
    const readColors = () => {
      const cs = getComputedStyle(document.body);
      starRGB = cs.getPropertyValue('--t-star').trim() || starRGB;
      warmRGB = cs.getPropertyValue('--t-star2').trim() || warmRGB;
      if (REDUCED) draw(0);
    };
    const themeWatch = new MutationObserver(() => setTimeout(readColors, 50));
    themeWatch.observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
    function resize() {
      w = innerWidth; h = innerHeight; canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      stars = Array.from({ length: Math.min(160, Math.round(w * h / 9000)) }, () => ({
        x: Math.random() * w, y: Math.random() * h, r: .3 + Math.random() * 1.2, v: .02 + Math.random() * .1,
        p: Math.random() * 6.3, d: .2 + Math.random() * .8, warm: Math.random() < .16 }));
      if (REDUCED) draw(0);
    }
    function draw(t) {
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        if (!REDUCED) { s.y -= s.v * s.d; if (s.y < -4) { s.y = h + 4; s.x = Math.random() * w; } }
        const a = REDUCED ? .65 : .4 + .6 * Math.sin(t / 950 + s.p);
        ctx.beginPath(); ctx.arc(s.x + mx * s.d * 12, s.y + my * s.d * 8, s.r, 0, 6.283);
        ctx.fillStyle = s.warm ? `rgba(${warmRGB},${.5 * a})` : `rgba(${starRGB},${.75 * a})`; ctx.fill();
      }
    }
    function loop(t) { if (!running) return; if (!document.hidden) draw(t); frame = requestAnimationFrame(loop); }
    const move = e => { mx = e.clientX / w - .5; my = e.clientY / h - .5; };
    resize(); readColors(); addEventListener('resize', resize);
    if (!REDUCED) { addEventListener('pointermove', move); frame = requestAnimationFrame(loop); }
    sky = () => { running = false; themeWatch.disconnect(); cancelAnimationFrame(frame); removeEventListener('resize', resize); removeEventListener('pointermove', move); canvas.remove(); nebula.remove(); sky = null; };
  }

  /* Ripple on press, like the welcome buttons. */
  const RIPPLE = '.new-chat,.send-btn,.primary-small,.outline-btn,.studio-main,.modal-actions button,.advanced-actions button,.rail-btn';
  document.addEventListener('pointerdown', e => {
    if (look !== 'next' || REDUCED) return;
    const button = e.target.closest && e.target.closest(RIPPLE);
    if (!button || button.disabled) return;
    const r = button.getBoundingClientRect(), dot = document.createElement('span');
    dot.className = 'look-ripple';
    dot.style.left = (e.clientX - r.left) + 'px'; dot.style.top = (e.clientY - r.top) + 'px';
    if (getComputedStyle(button).position === 'static') button.style.position = 'relative';
    button.style.overflow = 'hidden';
    button.appendChild(dot);
    setTimeout(() => dot.remove(), 700);
  });

  const apply = value => {
    document.documentElement.classList.toggle('look-next', value === 'next');
    if (value === 'next') { if (document.body) startSky(); else document.addEventListener('DOMContentLoaded', startSky, { once: true }); }
    else if (sky) sky();
  };
  apply(look);

  function set(value) {
    look = value === 'next' ? 'next' : 'classic';
    try { localStorage.setItem(KEY, look); } catch { /* blocked */ }
    apply(look);
    draw();
  }

  /* EGO icon variants (tab icon + the mark in the rail), remembered in this browser. */
  const ICONS = [['orbit', 'Орбита'], ['star', 'Звезда'], ['constellation', 'Созвездие'], ['planet', 'Планета']];
  const ICON_KEY = 'ego-app-icon';
  let icon = 'orbit';
  try { const saved = localStorage.getItem(ICON_KEY); if (ICONS.some(([k]) => k === saved)) icon = saved; } catch { /* default */ }
  function applyIcon() {
    document.documentElement.style.setProperty('--ego-glyph', `url("${new URL(`assets/icons/${icon}-glyph.svg`, document.baseURI).href}")`);  // absolute: a var() url resolves against the stylesheet
    const fav = document.getElementById('ego-favicon') || document.querySelector('link[rel=icon]');
    if (fav) fav.href = `assets/icons/${icon}.svg`;
  }
  function setIcon(value) {
    if (!ICONS.some(([k]) => k === value)) return;
    icon = value;
    try { localStorage.setItem(ICON_KEY, icon); } catch { /* blocked */ }
    applyIcon(); draw();
  }
  applyIcon();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', applyIcon, { once: true });

  // Settings: a small group "Облик клиента" above the local-data group.
  function draw() {
    const row = document.getElementById('ego-look-row');
    if (!row) return;
    row.querySelectorAll('[data-look]').forEach(b => {
      const on = b.dataset.look === look;
      b.classList.toggle('ego-look-on', on);
      b.setAttribute('aria-pressed', String(on));
    });
    document.querySelectorAll('[data-app-icon]').forEach(b => {
      const on = b.dataset.appIcon === icon;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }
  function inject() {
    try {
      if (typeof page === 'undefined' || page !== 'settings') return;
      const target = document.querySelector('#content .generic-page');
      if (!target || target.querySelector('.look-settings')) return;
      const box = document.createElement('div');
      box.className = 'look-settings';
      const L = window.EGOI18n;
      box.innerHTML = (L ? `<div class="setting-row"><div><b>Язык · Language</b><p>Язык интерфейса и ответов модели.</p></div>
        <select class="ego-lang-select" aria-label="Language" data-no-i18n>${Object.entries(L.languages).map(([k, n]) =>
          `<option value="${k}"${k === L.lang ? ' selected' : ''}>${n}</option>`).join('')}</select></div>` : '') +
        `<div class="setting-row" id="ego-look-row"><div><b>Облик клиента</b>
        <p>«Обновлённый» — основной облик EGO: звёздное небо, стеклянные панели, живые планеты. «Классический» — прежний вид.</p></div>
        <div class="ego-look-switch" role="group" aria-label="Облик клиента">
        <button class="primary-small" type="button" data-look="classic">Классический</button>
        <button class="primary-small" type="button" data-look="next">Обновлённый</button></div></div>
        <div class="setting-row"><div><b>Значок EGO</b><p>Значок во вкладке браузера и знак в панели клиента. Цвет знака следует за темой.</p></div>
        <div class="ego-icon-choice" role="group" aria-label="Значок EGO">${ICONS.map(([k, n]) => `<button type="button" data-app-icon="${k}"><img src="assets/icons/${k}.svg" alt="">${n}</button>`).join('')}</div></div>`;
      const anchor = target.querySelector('.quality-settings');
      anchor ? target.insertBefore(box, anchor) : target.appendChild(box);
      const langSelect = box.querySelector('.ego-lang-select');
      if (langSelect) langSelect.onchange = () => L.set(langSelect.value);
      box.querySelectorAll('[data-look]').forEach(b => b.onclick = () => set(b.dataset.look));
      box.querySelectorAll('[data-app-icon]').forEach(b => b.onclick = () => setIcon(b.dataset.appIcon));
      draw();
    } catch { /* settings layout changed */ }
  }
  function watch() {
    const content = document.getElementById('content');
    if (!content) return;
    new MutationObserver(inject).observe(content, { childList: true });
    inject();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
  window.EGOLook = Object.freeze({ get: () => look, set, icon: () => icon, setIcon });
})();
