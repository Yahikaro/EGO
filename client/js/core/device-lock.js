/* Protection of a phone or laptop that signs in to EGO (2026-11-06).
   Only on other devices (never on the PC itself, where the browser copy is the main story list
   and Windows sign-in protects it).
   * PIN lock: the client locks when opened and after N minutes without activity; the PIN is kept
     only as a PBKDF2-SHA-256 hash (150 000 rounds, random salt). 5 wrong PINs = this device is
     signed out and its copy of stories is erased.
   * «Не хранить истории на этом устройстве»: the browser copy of stories, characters and worlds
     is erased when the tab closes or the client locks; everything loads again from EGO Core. */
(() => {
  'use strict';
  const LOCK_KEY = 'ego-device-lock-v1', NOCACHE_KEY = 'ego-device-nocache-v1', FAILS_KEY = 'ego-device-lock-fails',
    UNLOCKED_KEY = 'ego-device-unlocked-at';
  const DATA = ['ego-client-v03-data', 'ego-v06-characters', 'ego-v07-worlds', 'ego-core-sync-v1', 'ego-safety-backup-v1',
    'ego-data-schema', 'ego-chat-sync-v1'];
  const ITERATIONS = 150000, MAX_FAILS = 5;
  const acct = () => window.EGOAccount;
  const remote = () => !!(acct() && acct().remoteDevice);
  const signedIn = () => !!(acct() && acct().token);
  const say = m => (typeof toast === 'function' ? toast(m) : null);
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* blocked */ } },
  };
  const ss = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* blocked */ } },
  };
  const config = () => { try { const c = JSON.parse(ls.get(LOCK_KEY) || 'null'); return c && c.hash && c.salt ? c : null; } catch { return null; } };
  const noCache = () => ls.get(NOCACHE_KEY) === '1';
  const cryptoOk = () => !!(window.crypto && crypto.subtle && window.isSecureContext);
  const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

  async function derive(pin, salt, iterations) {
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(pin), 'PBKDF2', false, ['deriveBits']);
    return b64(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, key, 256));
  }
  async function setPin(pin, minutes) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    ls.set(LOCK_KEY, JSON.stringify({ salt: b64(salt), hash: await derive(pin, salt, ITERATIONS), iterations: ITERATIONS, minutes }));
    ls.set(FAILS_KEY, null);
  }
  async function checkPin(pin) {
    const c = config();
    if (!c) return true;
    const got = await derive(pin, unb64(c.salt), c.iterations || ITERATIONS);
    let diff = got.length ^ c.hash.length;
    for (let i = 0; i < Math.min(got.length, c.hash.length); i++) diff |= got.charCodeAt(i) ^ c.hash.charCodeAt(i);
    return diff === 0;
  }
  function wipeStories() { DATA.forEach(k => ls.set(k, null)); ls.set('ego-demo-seeded', '1'); }
  function eraseDevice() {
    wipeStories();
    ls.set(LOCK_KEY, null); ls.set(NOCACHE_KEY, null); ls.set(FAILS_KEY, null); ss.set(UNLOCKED_KEY, null);
    if (acct() && acct().signOutLocal) acct().signOutLocal();
  }

  /* ---- the lock screen ---- */
  let locked = false;
  // While locked with «не хранить» on, background sync must not write the stories back.
  try {
    const realSet = Storage.prototype.setItem;
    Storage.prototype.setItem = function guardedSetItem(key, value) {
      if (locked && this === window.localStorage && DATA.includes(key) && noCache()) return;
      return realSet.call(this, key, value);
    };
  } catch { /* storage unavailable */ }
  function lock() {
    if (locked || !remote() || !config() || !signedIn() || !cryptoOk()) return;
    locked = true;
    ss.set(UNLOCKED_KEY, null);
    if (noCache()) wipeStories();                    // nothing readable stays behind the lock
    document.documentElement.classList.add('ego-locked');
    const box = document.createElement('div');
    box.className = 'ego-lock';
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', 'EGO заблокирован');
    box.innerHTML = `<form class="ego-lock-card" autocomplete="off">
      <div class="logo ego-lock-logo" aria-hidden="true"></div>
      <h2>EGO заблокирован</h2>
      <p>Введите PIN этого устройства.</p>
      <input type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" class="ego-lock-pin" aria-label="PIN" autocomplete="off">
      <p class="ego-lock-error" role="alert"></p>
      <button class="ego-btn ego-primary" type="submit"><span>Открыть</span></button>
      <button class="ego-link" type="button" data-lock-out>Забыли PIN? Выйти на этом устройстве</button>
    </form>`;
    document.body.append(box);
    const input = box.querySelector('.ego-lock-pin'), error = box.querySelector('.ego-lock-error');
    setTimeout(() => input.focus(), 50);
    box.querySelector('[data-lock-out]').onclick = () => {
      if (!confirm('Выйти из аккаунта на этом устройстве? Копия историй в этом браузере будет удалена, в EGO Core они останутся.')) return;
      eraseDevice(); location.reload();
    };
    box.querySelector('form').onsubmit = async e => {
      e.preventDefault();
      const pin = input.value;
      input.value = '';
      if (await checkPin(pin)) {
        ls.set(FAILS_KEY, null);
        ss.set(UNLOCKED_KEY, String(Date.now()));
        locked = false;
        box.remove();
        document.documentElement.classList.remove('ego-locked');
        lastActive = Date.now();
        if (noCache()) location.reload();            // stories come back from EGO Core
        return;
      }
      const fails = Number(ls.get(FAILS_KEY) || 0) + 1;
      ls.set(FAILS_KEY, String(fails));
      if (fails >= MAX_FAILS) {
        eraseDevice();
        alert('PIN введён неверно 5 раз. Это устройство вышло из аккаунта, копия историй в браузере удалена.');
        location.reload();
        return;
      }
      error.textContent = `Неверный PIN. Осталось попыток: ${MAX_FAILS - fails}.`;
      box.querySelector('.ego-lock-card').classList.remove('ego-shake'); void box.offsetWidth;
      box.querySelector('.ego-lock-card').classList.add('ego-shake');
    };
  }

  /* ---- idle timer ---- */
  let lastActive = Date.now(), hiddenAt = null;
  const minutes = () => Math.max(1, Number((config() || {}).minutes) || 15);
  ['pointerdown', 'keydown', 'touchstart', 'wheel'].forEach(t => addEventListener(t, () => { if (!locked) lastActive = Date.now(); }, { passive: true, capture: true }));
  setInterval(() => { if (!locked && config() && Date.now() - lastActive >= minutes() * 60000) lock(); }, 15000);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { hiddenAt = Date.now(); return; }
    if (hiddenAt && config() && Date.now() - hiddenAt >= minutes() * 60000) lock();
    hiddenAt = null;
  });
  // Closing the tab with «не хранить» on: erase the browser copy (the app has saved by now).
  addEventListener('pagehide', () => { if (remote() && noCache()) wipeStories(); });

  // On open: locked, unless this tab was unlocked within the idle window (e.g. a reload).
  function onStart() {
    const unlockedAt = Number(ss.get(UNLOCKED_KEY) || 0);
    if (config() && !(unlockedAt && Date.now() - unlockedAt < minutes() * 60000)) lock();
  }
  window.addEventListener('ego-core-online', onStart);
  setTimeout(onStart, 1500);

  /* ---- settings: «Защита этого устройства» ---- */
  function row(title, text, controls) {
    return `<div class="setting-row"><div><b>${title}</b><p>${text}</p></div><div class="ego-dev-controls">${controls}</div></div>`;
  }
  function render(box) {
    if (!remote()) {
      box.innerHTML = row('Защита этого устройства', 'Это ПК с EGO: истории защищает вход в Windows и шифрование базы. PIN и режим «не хранить истории» доступны на телефоне и ноутбуке.', '');
      return;
    }
    if (!cryptoOk()) {
      box.innerHTML = row('Защита этого устройства', 'Браузер не поддерживает защищённое хранение PIN на этом адресе. Откройте EGO по адресу https://…', '');
      return;
    }
    const c = config();
    const select = `<select class="ego-dev-minutes" aria-label="Блокировать через">${[5, 15, 30, 60].map(m => `<option value="${m}" ${(c ? c.minutes : 15) === m ? 'selected' : ''}>через ${m} мин</option>`).join('')}</select>`;
    box.innerHTML = row('PIN-код этого устройства',
      c ? `PIN задан. EGO блокируется при открытии и после бездействия. 5 неверных попыток — выход из аккаунта и удаление копии историй.`
        : 'Без PIN любой, кто возьмёт разблокированное устройство, сможет открыть EGO. Задайте 4–8 цифр.',
      c ? `${select}<button class="primary-small" type="button" data-dev="change">Сменить PIN</button><button class="primary-small" type="button" data-dev="remove">Убрать PIN</button>`
        : `${select}<button class="primary-small" type="button" data-dev="set">Задать PIN</button>`)
      + row('Не хранить истории на этом устройстве',
        'Копия историй, персонажей и миров в браузере удаляется при закрытии вкладки и при блокировке. При следующем открытии всё загрузится из EGO Core (нужно подключение).',
        `<label class="ego-dev-switch"><input type="checkbox" data-dev="nocache" ${noCache() ? 'checked' : ''}> <span>${noCache() ? 'Включено' : 'Выключено'}</span></label>`)
      + `<form class="ego-dev-form" hidden autocomplete="off"><input type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="Новый PIN, 4–8 цифр" data-pin="a">
         <input type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="Повторите PIN" data-pin="b">
         <button class="primary-small" type="submit">Сохранить PIN</button></form>`;
    const form = box.querySelector('.ego-dev-form');
    const ask = async () => {
      if (!config()) return true;
      const pin = prompt('Введите текущий PIN');
      if (pin == null) return false;
      if (await checkPin(pin)) return true;
      say('Неверный PIN'); return false;
    };
    const open = () => { form.hidden = false; form.querySelector('[data-pin="a"]').focus(); };
    box.querySelector('[data-dev="set"]')?.addEventListener('click', open);
    box.querySelector('[data-dev="change"]')?.addEventListener('click', async () => { if (await ask()) open(); });
    box.querySelector('[data-dev="remove"]')?.addEventListener('click', async () => {
      if (!(await ask())) return;
      ls.set(LOCK_KEY, null); say('PIN убран'); render(box);
    });
    box.querySelector('.ego-dev-minutes').addEventListener('change', e => {
      const cur = config(); if (!cur) return;
      cur.minutes = Number(e.target.value); ls.set(LOCK_KEY, JSON.stringify(cur)); say('Блокировка: через ' + cur.minutes + ' мин');
    });
    form.onsubmit = async e => {
      e.preventDefault();
      const a = form.querySelector('[data-pin="a"]').value, b = form.querySelector('[data-pin="b"]').value;
      if (!/^\d{4,8}$/.test(a)) { say('PIN — от 4 до 8 цифр'); return; }
      if (a !== b) { say('PIN не совпадает'); return; }
      await setPin(a, Number(box.querySelector('.ego-dev-minutes').value) || 15);
      ss.set(UNLOCKED_KEY, String(Date.now()));
      say('PIN сохранён'); render(box);
    };
    box.querySelector('[data-dev="nocache"]').addEventListener('change', e => {
      ls.set(NOCACHE_KEY, e.target.checked ? '1' : null);
      say(e.target.checked ? 'Копия историй будет удаляться при закрытии и блокировке' : 'Копия историй снова хранится в браузере');
      render(box);
    });
  }
  function inject() {
    if (typeof page === 'undefined' || page !== 'settings') return;
    const target = document.querySelector('#content .generic-page');
    if (!target || target.querySelector('.ego-device-settings')) return;
    const box = document.createElement('div');
    box.className = 'ego-device-settings';
    const anchor = target.querySelector('.look-settings');
    anchor ? target.insertBefore(box, anchor) : target.appendChild(box);
    render(box);
  }
  const content = document.getElementById('content');
  if (content) new MutationObserver(inject).observe(content, { childList: true });
  window.EGODeviceLock = Object.freeze({ lock, get locked() { return locked; } });
})();
