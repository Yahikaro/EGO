/* Phone layout (2026-11-06). Below 760 px the client becomes a phone app:
   * the left rail turns into a bottom tab bar;
   * the story list opens over the screen (the list button in the top bar), and closes on choice;
   * the top bar keeps the list button, the character and one «⋯» menu; scene tools (members,
     chapters, bookmarks, scene, memory, mood, story actions) and the character panel live in it;
   * the composer is compact; windows open full screen;
   * settings keep what matters on a phone: account, profile, this device's protection, look.
     PC-only tools (export/import/reset of the browser copy, backups, storage diagnostics,
     compact mode) are hidden there; they stay on the PC.
   Works in both looks; nothing changes above 760 px. */
(() => {
  'use strict';
  const mq = matchMedia('(max-width: 760px)');
  const root = document.documentElement;
  const on = () => root.classList.contains('ego-mobile');
  const close = () => root.classList.remove('ego-m-list', 'ego-m-inspector', 'ego-m-sheet');

  function apply() {
    root.classList.toggle('ego-mobile', mq.matches);
    if (!mq.matches) close();
  }
  (mq.addEventListener ? mq.addEventListener('change', apply) : mq.addListener(apply));
  apply();
  // notch / home indicator: let the layout use env(safe-area-inset-*)
  const meta = document.querySelector('meta[name=viewport]');
  if (meta && !/viewport-fit/.test(meta.content)) meta.content += ',viewport-fit=cover';

  // Keyboard on iPhone (Safari): the visual viewport shrinks, the layout viewport does not.
  // Measure the keyboard height and lift the composer above it (--kb); hide the tab bar while typing.
  const vv = window.visualViewport;
  function syncKeyboard() {
    if (!vv) return;
    const kb = Math.max(0, Math.round(innerHeight - (vv.offsetTop + vv.height)));
    root.style.setProperty('--kb', kb + 'px');
    root.classList.toggle('ego-kb', mq.matches && kb > 120);
  }
  if (vv) { vv.addEventListener('resize', syncKeyboard); vv.addEventListener('scroll', syncKeyboard); }
  syncKeyboard();

  const scrim = document.createElement('div');
  scrim.className = 'ego-m-scrim';
  scrim.addEventListener('click', close);
  const sheet = document.createElement('div');
  sheet.className = 'ego-m-sheetbox';
  sheet.setAttribute('role', 'dialog');
  sheet.setAttribute('aria-label', 'Действия');
  function mount() { if (!scrim.isConnected) document.body.append(scrim, sheet); }

  /* ---- «⋯» menu of the story ---- */
  const press = sel => () => { close(); const el = document.querySelector(sel); if (el) el.click(); };
  const ITEMS = [
    ['О персонаже и сцене', () => { root.classList.remove('ego-m-sheet'); try { if (typeof inspector === 'function') inspector(); } catch { /* none */ } root.classList.add('ego-m-inspector'); }, () => !!document.getElementById('inspector')],
    ['Память истории', press('#ego-memory-btn'), () => !!document.getElementById('ego-memory-btn')],
    ['Участники сцены', press('#ac-group'), () => !!document.getElementById('ac-group')],
    ['Главы', press('#ac-chapters'), () => !!document.getElementById('ac-chapters')],
    ['Новая глава', press('#ac-new-chapter'), () => !!document.getElementById('ac-new-chapter')],
    ['Сцена', press('#ac-scene'), () => !!document.getElementById('ac-scene')],
    ['Закладки', press('#ac-bookmarks'), () => !!document.getElementById('ac-bookmarks')],
    ['Сменить настроение', press('#theme-shortcut'), () => !!document.getElementById('theme-shortcut')],
    ['Переименовать или удалить историю', press('#chat-menu-button'), () => !!document.getElementById('chat-menu-button')],
  ];
  function openSheet() {
    mount();
    sheet.innerHTML = '<div class="ego-m-grab" aria-hidden="true"></div>' +
      ITEMS.filter(([, , ok]) => ok()).map(([label], i) => `<button type="button" data-m-item="${i}">${label}</button>`).join('') +
      '<button type="button" class="ego-m-cancel">Закрыть</button>';
    const shown = ITEMS.filter(([, , ok]) => ok());
    sheet.querySelectorAll('[data-m-item]').forEach(b => b.addEventListener('click', () => shown[+b.dataset.mItem][1]()));
    sheet.querySelector('.ego-m-cancel').addEventListener('click', close);
    root.classList.add('ego-m-sheet');
  }
  function topbar() {
    if (!on()) return;
    const right = document.querySelector('.topbar .top-right');
    if (!right) return;
    let more = document.getElementById('ego-m-more');
    const chats = typeof page === 'undefined' || page === 'chats';
    if (!more) {
      more = document.createElement('button');
      more.id = 'ego-m-more'; more.type = 'button'; more.className = 'ego-m-more';
      more.setAttribute('aria-label', 'Действия с историей'); more.textContent = '⋯';
      more.addEventListener('click', openSheet);
      right.append(more);
    }
    if (more.hidden === chats) more.hidden = !chats;
  }

  /* ---- taps that close the overlays ---- */
  document.addEventListener('click', e => {
    if (!on() || !e.target.closest) return;
    const t = e.target;
    if (t.closest('#show-side')) {                  // the list button: toggle the story list
      e.preventDefault(); e.stopImmediatePropagation();
      mount(); root.classList.remove('ego-m-inspector', 'ego-m-sheet'); root.classList.toggle('ego-m-list');
      return;
    }
    if (t.closest('#sidebar .conversation, #sidebar #new-chat, #sidebar button[data-world-side], #sidebar .side-item')) setTimeout(close, 0);
    if (t.closest('.rail [data-page]')) close();
    if (t.closest('#close-inspector')) { e.preventDefault(); e.stopImmediatePropagation(); root.classList.remove('ego-m-inspector'); }
  }, true);
  addEventListener('keydown', e => { if (e.key === 'Escape' && on()) close(); });

  /* ---- settings for a phone ---- */
  const HIDE = ['Компактный режим', 'Экспорт историй', 'Импорт историй', 'Удалить все истории'];
  function settings() {
    if (!on() || typeof page === 'undefined' || page !== 'settings') return;
    const gp = document.querySelector('#content .generic-page');
    if (!gp) return;
    gp.classList.add('ego-m-settings');
    const sub = gp.querySelector(':scope > .page-sub');
    const SUB = 'Аккаунт, профиль, защита этого устройства и оформление. Истории хранятся в EGO на компьютере.';
    if (sub && sub.textContent !== SUB) sub.textContent = SUB;
    gp.querySelectorAll(':scope > .setting-row').forEach(row => {
      const title = row.querySelector('b')?.textContent.trim();
      if (HIDE.includes(title) && !row.hidden) row.hidden = true;
    });
    const quality = gp.querySelector(':scope > .quality-settings');
    if (quality && !quality.hidden) quality.hidden = true;
    // order: account, profile, this device, then the look
    const anchor = gp.querySelector(':scope > .page-sub') || gp.firstElementChild;
    let profile = gp.querySelector(':scope > .ego-m-profile');
    if (!profile && window.EGOProfile) {
      profile = document.createElement('div');
      profile.className = 'setting-row ego-m-profile';
      profile.innerHTML = '<div><b>Мой профиль</b><p>Имя, обращение в историях, аватар и цвет.</p></div><button class="primary-small" type="button">Открыть</button>';
      profile.querySelector('button').addEventListener('click', () => window.EGOProfile.open());
    }
    const order = [gp.querySelector(':scope > .ego-account-host'), profile, gp.querySelector(':scope > .ego-device-settings')].filter(Boolean);
    let after = anchor;
    for (const node of order) {
      if (after.nextElementSibling !== node) after.after(node);
      after = node;
    }
  }

  let queued = false;
  function refresh() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; topbar(); settings(); });
  }
  const content = document.getElementById('content');
  if (content) new MutationObserver(refresh).observe(content, { childList: true, subtree: true });
  const bar = document.querySelector('.topbar');
  if (bar) new MutationObserver(refresh).observe(bar, { childList: true, subtree: true });
  (mq.addEventListener ? mq.addEventListener('change', refresh) : mq.addListener(refresh));
  refresh();
  // composer on a phone: one line to start, grows with the text up to ~a third of the screen
  const message = document.getElementById('message');
  function fit() {
    if (!message) return;
    if (!on()) { message.style.height = ''; return; }
    message.style.height = 'auto';
    message.style.height = Math.min(message.scrollHeight, Math.round(innerHeight * .3)) + 'px';
  }
  if (message) { message.addEventListener('input', fit); message.addEventListener('focus', fit); }
  (mq.addEventListener ? mq.addEventListener('change', fit) : mq.addListener(fit));
  document.getElementById('send')?.addEventListener('click', () => setTimeout(fit, 0));
  fit();
  window.EGOMobile = Object.freeze({ close, get on() { return on(); } });
})();
