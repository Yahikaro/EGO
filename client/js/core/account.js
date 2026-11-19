/* EGO account sign-in.
   The PC opens the client with a one-time launch code (START_EGO_CLIENT.bat) and needs no login.
   Other devices see the EGO welcome page: email + password -> session. The owner creates the
   accounts in Control Center (Access). The session token is kept in this browser's localStorage
   (7-day sliding window on Core). */
(() => {
  'use strict';
  const KEY = 'ego-account-session';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get() { try { return localStorage.getItem(KEY); } catch { return null; } },
    set(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch { /* storage blocked */ } },
  };
  // Privacy: a laptop keeps a cached copy of stories in this browser. When its account
  // session ends (sign-out here, "sign out everywhere" on the PC, or expiry), remove that
  // copy. Never on the PC itself: there the browser copy is the main story list.
  const REMOTE_DEVICE = !['127.0.0.1', 'localhost', '[::1]', '::1'].includes(location.hostname);
  const DEVICE_DATA = ['ego-client-v03-data', 'ego-v06-characters', 'ego-v07-worlds', 'ego-core-sync-v1',
    'ego-safety-backup-v1', 'ego-data-schema', 'ego-chat-sync-v1', 'ego-device-lock-v1', 'ego-device-nocache-v1'];
  function forgetDevice() {
    store.set(null);
    try { sessionStorage.removeItem('ego-core-session'); } catch { /* blocked */ }
    if (!REMOTE_DEVICE) return false;
    try {
      DEVICE_DATA.forEach(k => localStorage.removeItem(k));
      localStorage.setItem('ego-demo-seeded', '1');   // an emptied device stays empty
    } catch { /* blocked */ }
    return true;
  }

  async function call(method, path, body, token) {
    let response;
    try {
      response = await fetch(path, { method, cache: 'no-store',
        headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: 'Bearer ' + token } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    } catch { const e = new Error('CORE_OFFLINE'); e.code = 'CORE_OFFLINE'; e.text = 'EGO Core недоступен.'; e.hint = 'Проверьте, что сервер запущен и устройство в той же сети.'; throw e; }
    let data = {};
    try { data = await response.json(); } catch { /* empty */ }
    if (!response.ok) { const e = new Error(data.code || 'HTTP_' + response.status); e.code = e.message; e.text = data.message || 'Запрос не выполнен.'; e.hint = data.hint || ''; throw e; }
    return data;
  }

  /* ---- welcome page --------------------------------------------------------------
     The first screen of the client. Left: the EGO wordmark and a scene that writes
     itself in the client's own format (action in italics, speech in plain text).
     Right: the one thing to do now — open stories (PC), sign in (laptop), create an
     account, confirm a code, or continue offline. One load sequence; motion after that
     only answers the person (hover, press). Reduced motion: everything is static. */
  const REDUCED = (() => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } })();
  const SCENE = [
    ['action', 'Огонь в камине вздрагивает, когда ты переступаешь порог библиотеки.'],
    ['speech', '— Ты всё-таки пришёл. Я берегла для тебя кресло у огня.'],
    ['action', 'Лира закрывает книгу, не отмечая страницу, — будто знает, что вы ещё к ней вернётесь.'],
  ];
  const ICONS = {
    offline: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M9 20h6M12 16v4"/></svg>',
    memory: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.2 5.6L20 9.3l-4.4 3.8 1.4 5.9L12 16l-5 3 1.4-5.9L4 9.3l5.8-.7z"/></svg>',
    lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  };

  function startSky(canvas, root) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let w = 0, h = 0, stars = [], mx = 0, my = 0, frame = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    function resize() {
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.min(170, Math.round(w * h / 8500));
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * w, y: Math.random() * h, r: .3 + Math.random() * 1.25,
        v: .03 + Math.random() * .12, p: Math.random() * Math.PI * 2, depth: .2 + Math.random() * .8,
        warm: Math.random() < .18,
      }));
    }
    function draw(t) {
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        if (!REDUCED) { s.y -= s.v * s.depth; if (s.y < -4) { s.y = h + 4; s.x = Math.random() * w; } }
        const twinkle = REDUCED ? .7 : .45 + .55 * Math.sin(t / 900 + s.p);
        const x = s.x + mx * s.depth * 14, y = s.y + my * s.depth * 10;
        ctx.beginPath(); ctx.arc(x, y, s.r, 0, Math.PI * 2);
        ctx.fillStyle = s.warm ? `rgba(255,190,214,${.55 * twinkle})` : `rgba(214,200,255,${.75 * twinkle})`;
        ctx.fill();
      }
    }
    function loop(t) {
      if (!root.isConnected) return;
      if (!document.hidden) draw(t);
      frame = requestAnimationFrame(loop);
    }
    resize();
    window.addEventListener('resize', resize);
    if (!REDUCED) {
      root.addEventListener('pointermove', e => { mx = e.clientX / w - .5; my = e.clientY / h - .5; });
      frame = requestAnimationFrame(loop);
    } else draw(0);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('resize', resize); };
  }

  function typeScene(box, onDone) {
    const lines = SCENE.map(([kind, text]) => {
      const p = document.createElement('p');
      p.className = 'ego-scene-' + kind;
      box.appendChild(p);
      return [p, text];
    });
    if (REDUCED) { lines.forEach(([p, text]) => { p.textContent = text; }); onDone(); return; }
    let li = 0, ci = 0;
    const caret = document.createElement('span'); caret.className = 'ego-caret'; caret.setAttribute('aria-hidden', 'true');
    function step() {
      if (!box.isConnected) return;
      if (li >= lines.length) { lines[lines.length - 1][0].appendChild(caret); onDone(); return; }
      const [p, text] = lines[li];
      p.classList.add('ego-typing');
      ci += 1;
      p.textContent = text.slice(0, ci);
      p.appendChild(caret);
      if (ci >= text.length) { p.classList.remove('ego-typing'); li += 1; ci = 0; setTimeout(step, 520); }
      else setTimeout(step, /[,.—]/.test(text[ci - 1]) ? 120 : 22 + Math.random() * 26);
    }
    setTimeout(step, 1150);
  }

  function ripple(e) {
    const button = e.target.closest('.ego-btn');
    if (!button || button.disabled || REDUCED) return;
    const r = button.getBoundingClientRect(), dot = document.createElement('span');
    dot.className = 'ego-ripple';
    dot.style.left = (e.clientX - r.left) + 'px'; dot.style.top = (e.clientY - r.top) + 'px';
    button.appendChild(dot);
    setTimeout(() => dot.remove(), 700);
  }

  function avatar() {
    try {
      if (typeof people !== 'undefined' && people.lira && typeof avatarUrl === 'function') {
        return `<img src="${esc(avatarUrl('lira', people.lira))}" alt="" data-hide-on-error data-initial="А">`;
      }
    } catch { /* app not loaded */ }
    return 'А';
  }

  // state: {account_exists, accounts}; opts.intro: 'pc' | 'offline' | undefined.
  // Resolves with a session token, 'enter' (go to the stories) or null (continue without connection).
  function welcome(state, opts = {}) {
    return new Promise(resolve => {
      const root = document.createElement('div');
      root.className = 'ego-welcome' + (REDUCED ? ' ego-still' : '');
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-modal', 'true');
      root.setAttribute('aria-labelledby', 'ego-welcome-title');
      const intro = opts.intro || null;
      const mode = intro || 'login';
      root.innerHTML = `
        <canvas class="ego-sky" aria-hidden="true"></canvas>
        <div class="ego-nebula" aria-hidden="true"><i></i><i></i><i></i></div>
        <div class="ego-welcome-grid">
          <section class="ego-hero">
            <div class="ego-mark"><div class="logo">e<span>.</span></div><span>Локальные истории с ИИ</span></div>
            <h1 class="ego-wordmark" aria-label="EGO">${[...'EGO'].map((c, i) => `<span style="--i:${i}">${c}</span>`).join('')}</h1>
            <p class="ego-promise">Ролевые истории, которые пишутся вместе с тобой. Модель работает на этом компьютере — ни одно слово не уходит в сеть.</p>
            <figure class="ego-scene" aria-label="Пример сцены">
              <figcaption><span class="ego-scene-avatar">${avatar()}</span><span><b>Лира</b><small>хранительница библиотеки Эфира</small></span></figcaption>
              <div class="ego-scene-text"></div>
            </figure>
            <ul class="ego-facts">
              <li>${ICONS.offline}<span><b>Без интернета</b>Модель и истории — на твоём ПК</span></li>
              <li>${ICONS.memory}<span><b>Помнит важное</b>Имена, обещания, события сцены</span></li>
              <li>${ICONS.lock}<span><b>Только твоё</b>База зашифрована, вход — по паролю</span></li>
            </ul>
          </section>
          <section class="ego-card" aria-live="polite"></section>
        </div>
        <p class="ego-legal">Все действия и сообщения вымышленные.</p>`;
      document.body.appendChild(root);
      const stopSky = startSky(root.querySelector('.ego-sky'), root);
      typeScene(root.querySelector('.ego-scene-text'), () => root.classList.add('ego-scene-done'));
      root.addEventListener('pointerdown', ripple);
      const card = root.querySelector('.ego-card');
      // Once it has appeared, the card stays visible whatever animation runs next.
      card.addEventListener('animationend', e => { if (e.target === card && e.animationName === 'egoCard') card.classList.add('ego-in'); });
      setTimeout(() => card.classList.add('ego-in'), 2000);   // also when animations never end (background tab)

      const done = value => {
        root.classList.add('ego-welcome-leave');
        setTimeout(() => { if (stopSky) stopSky(); root.remove(); }, REDUCED ? 0 : 420);
        resolve(value);
      };
      const field = (id, label, type, extra = '') =>
        `<label class="ego-field"><span>${label}</span><input id="${id}" type="${type}" ${extra}></label>`;

      function view() {
        let title, body;
        if (mode === 'pc') {
          title = 'Добро пожаловать';
          body = `<p class="ego-lead">EGO запущен на этом компьютере, модель готовится. Продолжай историю или начни новую.</p>
            <button class="ego-btn ego-primary" type="button" data-act="enter"><span>Открыть мои истории</span></button>
            <p class="ego-hint">Чтобы входить с другого устройства, создайте аккаунт в окне сервера: вкладка «Доступ».</p>`;
        } else if (mode === 'offline') {
          title = 'Без подключения';
          body = `<p class="ego-lead">EGO Core сейчас не отвечает, поэтому модель недоступна. Можно посмотреть истории и персонажей в этом браузере.</p>
            <button class="ego-btn ego-primary" type="button" data-act="skip"><span>Открыть без подключения</span></button>
            <p class="ego-hint">Чтобы писать с моделью, запусти START_EGO_CLIENT.bat на компьютере с EGO.</p>`;
        } else {
          title = 'С возвращением';
          body = `${state.account_exists ? '' : '<p class="ego-note">Аккаунтов пока нет. Создайте его в окне сервера на компьютере: вкладка «Доступ».</p>'}
            ${field('ego-email', 'Почта', 'email', 'autocomplete="username" required')}
            ${field('ego-password', 'Пароль', 'password', 'autocomplete="current-password" required')}
            <button class="ego-btn ego-primary" type="submit"><span>Войти</span></button>`;
        }
        card.innerHTML = `<h2 id="ego-welcome-title">${title}</h2><form class="ego-form" novalidate>${body}<div class="ego-error" role="alert"></div></form>
          ${intro !== 'offline' ? `<button type="button" class="ego-link ego-skip" data-act="${intro === 'pc' ? 'enter' : 'skip'}">${intro === 'pc' ? 'Пропустить' : 'Продолжить без подключения'}</button>` : ''}`;
        card.classList.remove('ego-swap'); void card.offsetWidth; card.classList.add('ego-swap');
        const first = card.querySelector('input');
        if (first) setTimeout(() => first.focus({ preventScroll: true }), REDUCED ? 0 : 950);
        card.querySelectorAll('[data-act]').forEach(b => b.onclick = () => act(b.dataset.act));
        card.querySelector('form').onsubmit = e => { e.preventDefault(); submit(); };
      }

      const value = id => (card.querySelector('#' + id)?.value || '').trim();
      function showError(e) {
        const box = card.querySelector('.ego-error');
        if (box) box.innerHTML = `<b>${esc(e.text || e.message)}</b>${e.hint ? `<span>${esc(e.hint)}</span>` : ''}`;
        card.classList.remove('ego-shake'); void card.offsetWidth; if (!REDUCED) card.classList.add('ego-shake');
      }
      async function busy(fn) {
        const button = card.querySelector('.ego-primary');
        if (button) { button.disabled = true; button.classList.add('ego-busy'); }
        try { await fn(); } catch (e) { showError(e); } finally { if (button && button.isConnected) { button.disabled = false; button.classList.remove('ego-busy'); } }
      }

      async function submit() {
        if (mode !== 'login') return;
        await busy(async () => {
          const session = await call('POST', '/v1/account/login', { email: value('ego-email'), password: card.querySelector('#ego-password').value });
          done(session.token);
        });
      }
      function act(name) {
        if (name === 'skip') return done(null);
        if (name === 'enter') return done('enter');
      }
      view();
    });
  }

  /* First screen once per browser session on the PC and when Core is unreachable. */
  const INTRO_KEY = 'ego-welcome-seen';
  async function intro(kind) {
    try { if (sessionStorage.getItem(INTRO_KEY)) return null; sessionStorage.setItem(INTRO_KEY, '1'); } catch { /* blocked */ }
    let state = { account_exists: true, accounts: 1 };
    if (kind === 'pc') { try { state = await call('GET', '/v1/account/state'); } catch { /* older Core */ } }
    return welcome(state, { intro: kind });
  }

  /* ---- entry point used by core-bridge ------------------------------------------ */
  async function start() {
    const saved = store.get();
    if (saved) {
      try { await call('GET', '/v1/account/me', null, saved); return saved; }
      catch (e) {
        if (e.code === 'CORE_OFFLINE') { await intro('offline'); return null; }
        // Stories are already in memory: reload so they are not saved back.
        if (forgetDevice()) { location.reload(); return new Promise(() => {}); }
      }
    }
    let state;
    try { state = await call('GET', '/v1/account/state'); } catch { await intro('offline'); return null; }   // Core unreachable
    const token = await welcome(state);
    store.set(token);
    return token;
  }

  /* ---- settings section ----------------------------------------------------------- */
  const EVENTS = { login: 'вход', password_wrong: 'неверный пароль', locked_passwords: 'блокировка: пароли', logout: 'выход',
    logout_all: 'выход на всех устройствах', user_added: 'аккаунт добавлен', user_removed: 'аккаунт удалён', password_changed: 'пароль изменён' };
  const when = t => new Date(t * 1000).toLocaleString((window.EGOI18n && EGOI18n.lang) || 'ru', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  async function renderSettings(host) {
    const core = window.EGOCore;
    if (!core || !core.online) { host.innerHTML = ''; return; }
    host.innerHTML = '<div class="ego-account-box"><b>Аккаунт</b><p>Загрузка…</p></div>';
    let me;
    try { me = await core.api('GET', '/v1/account/me'); }
    catch { host.innerHTML = ''; return; }
    const account = me.account;
    let net = null;
    try { net = await core.api('GET', '/v1/network'); } catch { /* older Core */ }
    const network = !net ? '' : net.enabled && net.urls.length
      ? `<p class="ego-net">С ноутбука: ${net.urls.map(u => `<b>${esc(u)}</b>`).join(' или ')}<br><small>Отпечаток сертификата: ${esc(net.fingerprint || '—')}</small></p>`
      : net.enabled ? '<p class="ego-warn">Доступ из домашней сети включён, но не запустился — подробности в окне сервера, вкладка «Доступ».</p>'
      : '<p class="ego-net"><small>Доступ из домашней сети выключен. Включите его в окне сервера, вкладка «Доступ».</small></p>';
    if (!account) {
      host.innerHTML = `<div class="ego-account-box"><b>Вход с другого устройства</b>
        <p>Аккаунты создаются в окне сервера на компьютере: вкладка «Доступ» → почта и пароль. Можно добавить несколько, например для друзей.</p>${network}</div>`;
      return;
    }
    const sessions = account.sessions.map(s => `<li>${esc(s.device)}${s.current ? ' <em>· это устройство</em>' : ''}<small>активность ${when(s.last_seen)}</small></li>`).join('') || '<li>Нет вошедших устройств</li>';
    const journal = account.journal.slice(0, 8).map(j => `<li>${esc(EVENTS[j.event] || j.event)} · ${esc(j.device || '—')}<small>${when(j.at)}${j.address ? ' · ' + esc(j.address) : ''}</small></li>`).join('');
    const mine = !!store.get();
    host.innerHTML = `<div class="ego-account-box"><b>Аккаунт</b><p>${esc(account.email)}</p>${network}
      <div class="ego-account-cols"><div><div class="inspector-label">Устройства</div><ul class="ego-list">${sessions}</ul></div>
      <div><div class="inspector-label">Журнал входов</div><ul class="ego-list">${journal}</ul></div></div>
      <div class="ego-account-actions">${mine ? '<button class="primary-small" data-acc="logout">Выйти на этом устройстве</button>' : ''}
      <button class="primary-small" data-acc="all">Выйти на всех устройствах</button></div>
      ${REMOTE_DEVICE ? '<p class="ego-net"><small>При выходе копия историй в этом браузере удаляется. В EGO на компьютере всё сохраняется.</small></p>'
        : '<p class="ego-net"><small>«Выйти на всех устройствах» также удалит копию историй в браузере ноутбука, когда он снова откроет EGO.</small></p>'}</div>`;
    host.querySelector('[data-acc="all"]').onclick = async () => {
      try { await core.api('POST', '/v1/account/logout-all'); } catch { /* already signed out */ }
      if (mine) { forgetDevice(); location.reload(); } else renderSettings(host);
    };
    const out = host.querySelector('[data-acc="logout"]');
    if (out) out.onclick = async () => { try { await core.api('POST', '/v1/account/logout'); } catch { /* ignore */ } forgetDevice(); location.reload(); };
  }

  // Add the section whenever the settings page is drawn (by any script).
  function injectSettings() {
    try {
      if (typeof page === 'undefined' || page !== 'settings') return;
      const target = document.querySelector('#content .generic-page');
      if (!target || target.querySelector('.ego-account-host')) return;
      const host = document.createElement('div'); host.className = 'ego-account-host';
      target.appendChild(host); renderSettings(host);
    } catch { /* settings page layout changed */ }
  }
  function watchSettings() {
    const content = document.getElementById('content');
    if (!content) return;
    new MutationObserver(injectSettings).observe(content, { childList: true });
    injectSettings();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watchSettings); else watchSettings();
  window.addEventListener('ego-core-online', injectSettings);

  window.EGOAccount = Object.freeze({ start, welcome, intro, signOutLocal: forgetDevice, remoteDevice: REMOTE_DEVICE, get token() { return store.get(); } });
})();
