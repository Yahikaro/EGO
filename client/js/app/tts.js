/* Local TTS (2026-11-08): speech for AI replies through Core -> TTS worker (XTTS-v2), voice library,
   per-character voice, test panel. Voice identity = people[id].voice ({id, speed, language, emotion});
   emotion only changes delivery and is guessed from the reply's actions unless switched off.
   Synced with Core through the character card (storePeople). User messages are never spoken. */
(function () {
  'use strict';
  const AUTO_KEY = 'ego-tts-auto';
  const EMOTIONS = { neutral: 'Ровно', calm: 'Спокойно', joy: 'Радость', irritation: 'Раздражение', anger: 'Злость',
    sadness: 'Грусть', surprise: 'Удивление', uncertainty: 'Неуверенность', embarrassment: 'Смущение', tiredness: 'Усталость' };
  const CATEGORIES = { female: 'Женский', male: 'Мужской', other: 'Другой' };
  const LANGS = { auto: 'Авто', ru: 'Русский', en: 'English', fr: 'Français', es: 'Español', de: 'Deutsch' };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const tr = s => (typeof t === 'function' ? t(s) : s);
  const say = s => (typeof toast === 'function' ? toast(tr(s)) : null);
  const core = () => (window.EGOCore && EGOCore.online ? EGOCore : null);
  const why = e => (window.EGOCore && e && e.code ? EGOCore.describe(e.code) : tr('Озвучка недоступна'));
  const uiLang = () => (window.EGOI18n && EGOI18n.lang) || 'ru';
  let auto = false;
  try { auto = localStorage.getItem(AUTO_KEY) === '1'; } catch { /* storage blocked */ }
  let voices = null;

  async function loadVoices(force) {
    if (voices && !force) return voices;
    const c = core(); if (!c) return [];
    try { voices = (await c.api('GET', '/v1/tts/voices')).voices || []; } catch { voices = []; }
    return voices;
  }
  const voiceName = id => (voices || []).find(v => v.id === id)?.name;

  /* ---------------------------------------------------------------- speech jobs */
  const cache = new Map();            // request key -> blob URL (one generation per text+voice+settings)
  const inflight = new Map();         // request key -> Promise<url>
  async function synth(body) {
    const key = JSON.stringify(body);
    if (cache.has(key)) return cache.get(key);
    if (inflight.has(key)) return inflight.get(key);
    const run = (async () => {
      const c = core(); if (!c) throw { code: 'TTS_UNAVAILABLE' };
      let job = await c.api('POST', '/v1/tts/speak', body);
      const started = Date.now();
      while (job.status === 'queued' || job.status === 'running') {
        if (Date.now() - started > 15 * 60 * 1000) throw { code: 'TTS_FAILED' };
        await new Promise(r => setTimeout(r, 700));
        job = await c.api('GET', '/v1/tts/jobs/' + job.id);
      }
      if (job.status !== 'done') throw { code: job.error || 'TTS_FAILED' };
      const r = await EGOCore.apiRaw('GET', '/v1/tts/jobs/' + job.id + '/audio');
      const url = URL.createObjectURL(await r.blob());
      cache.set(key, url);
      if (cache.size > 40) { const [k, u] = cache.entries().next().value; URL.revokeObjectURL(u); cache.delete(k); }
      return url;
    })();
    inflight.set(key, run);
    try { return await run; } finally { inflight.delete(key); }
  }

  /* what a reply says aloud: its spoken lines; narration only when there are none */
  function partsOf(message) {
    const blocks = message && Array.isArray(message.blocks) ? message.blocks : [];
    const spoken = blocks.filter(b => b[0] === 'spoken').map(b => b[1]);
    const other = blocks.filter(b => b[0] !== 'spoken' && b[0] !== 'thought').map(b => b[1]);
    const text = (spoken.length ? spoken : other).join('\n').replace(/^[\s—–-]+/gm, '').trim();
    return { text, context: other.join(' ') };
  }
  function characterOf(chat) {
    const id = chat && (chat.person || (Array.isArray(chat.members) && chat.members[0]));
    return id && typeof people !== 'undefined' ? [id, people[id]] : [null, null];
  }
  function requestFor(chat, message) {
    const { text, context } = partsOf(message);
    if (!text) return null;
    const [, person] = characterOf(chat);
    const v = (person && person.voice) || {};
    const known = !v.id || !voices || voices.some(x => x.id === v.id);    // deleted voice -> default voice
    return { text: text.slice(0, 4000), context: context.slice(0, 1500), voice: known ? v.id || null : null,
      speed: Number(v.speed) || 1, language: v.language || 'auto', emotion: v.emotion === 'off' ? 'neutral' : 'auto', ui_language: uiLang() };
  }

  /* ---------------------------------------------------------------- player */
  const audio = new Audio();
  const player = { button: null, key: null, label: '' };
  function mark(button, state) {
    if (!button) return;
    button.classList.remove('tts-busy', 'tts-playing', 'tts-paused', 'tts-error');
    if (state) button.classList.add('tts-' + state);
  }
  function bar() {
    let el = document.getElementById('tts-bar');
    if (!el) {
      el = document.createElement('div'); el.id = 'tts-bar'; el.className = 'tts-bar'; el.hidden = true;
      el.innerHTML = `<span class="tts-bar-label"></span>
        <button type="button" data-tts="toggle" title="Пауза / продолжить">⏸</button>
        <button type="button" data-tts="replay" title="Прослушать снова">↻</button>
        <button type="button" data-tts="stop" title="Остановить">⏹</button>
        <label class="tts-bar-auto" title="Озвучивать ответы автоматически"><input type="checkbox" data-tts="auto"> Авто</label>`;
      document.body.appendChild(el);
      el.addEventListener('click', e => {
        const k = e.target.closest('[data-tts]')?.dataset.tts;
        if (k === 'toggle') { audio.paused ? audio.play().catch(() => {}) : audio.pause(); }
        if (k === 'replay') { audio.currentTime = 0; audio.play().catch(() => {}); }
        if (k === 'stop') stop();
      });
      el.querySelector('[data-tts="auto"]').addEventListener('change', e => setAuto(e.target.checked));
    }
    el.querySelector('[data-tts="auto"]').checked = auto;
    return el;
  }
  function paintBar(text) {
    const el = bar();
    el.hidden = !text;
    el.querySelector('.tts-bar-label').textContent = text || '';
    el.querySelector('[data-tts="toggle"]').textContent = audio.paused ? '▶' : '⏸';
  }
  function stop() {
    audio.pause(); audio.removeAttribute('src');
    mark(player.button, null); player.button = null; player.key = null; paintBar('');
  }
  audio.addEventListener('play', () => { mark(player.button, 'playing'); paintBar(player.label); });
  audio.addEventListener('pause', () => { if (audio.src) { mark(player.button, audio.ended ? null : 'paused'); paintBar(audio.ended ? '' : player.label); } });
  audio.addEventListener('ended', () => { mark(player.button, null); paintBar(''); });
  function setAuto(on) {
    auto = !!on;
    try { localStorage.setItem(AUTO_KEY, auto ? '1' : '0'); } catch { /* storage blocked */ }
    document.querySelectorAll('[data-tts="auto"], [data-tts-auto]').forEach(x => { x.checked = auto; });
  }

  async function speakMessage(chat, index, button) {
    const message = chat && chat.messages && chat.messages[index];
    if (!message || message.by !== 'ai' || message.pending) return false;
    await loadVoices();
    const body = requestFor(chat, message);
    if (!body) { say('В ответе нет текста для озвучки'); return true; }
    const key = JSON.stringify(body);
    if (player.key === key && audio.src) {                 // same reply: pause / resume
      audio.paused ? audio.play().catch(() => {}) : audio.pause();
      return true;
    }
    stop();
    const [, person] = characterOf(chat);
    player.button = button; player.key = key; player.label = (person && person.name) || tr('Озвучка');
    mark(button, 'busy'); paintBar(player.label + ' · ' + tr('готовлю голос…'));
    try {
      const url = await synth(body);
      if (player.key !== key) return true;                 // user moved on meanwhile
      audio.src = url; await audio.play();
    } catch (e) {
      if (player.key === key) { mark(button, 'error'); paintBar(''); player.key = null; }
      say(why(e));
    }
    return true;
  }

  window.EGOSpeak = Object.assign(window.EGOSpeak || {}, {
    speak: ({ chat, index, button }) => { if (!core()) return false; speakMessage(chat, index, button); return true; },
    stop, setAuto, get auto() { return auto; },
  });
  /* auto mode: core-bridge fires this once per finished reply (never on re-render) */
  const spokenAuto = new WeakSet();
  window.addEventListener('ego-ai-reply', e => {
    const { chat, index } = e.detail || {};
    const m = chat && chat.messages && chat.messages[index];
    if (!auto || !m || spokenAuto.has(m) || !core()) return;
    spokenAuto.add(m);
    const button = document.querySelector(`[data-speak="${index}"]`);
    speakMessage(chat, index, button);
  });

  /* ---------------------------------------------------------------- voice library window */
  let tab = 'library', filters = { category: '', language: '', q: '' }, target = null;
  function characters() {
    return typeof people === 'undefined' ? [] : Object.entries(people).filter(([, p]) => p && p.name).map(([id, p]) => [id, p]);
  }
  function open() {
    const root = document.getElementById('modal-root'); if (!root) return;
    root.innerHTML = `<div class="modal-backdrop"><div class="modal ml tts" role="dialog" aria-modal="true" aria-label="Голоса">
      <div class="ml-head"><h2>Голоса</h2><button class="cs-cross" data-tts-close aria-label="Закрыть">×</button></div>
      <div class="ml-tabs" role="tablist">
        <button type="button" data-tts-tab="library">Библиотека</button><button type="button" data-tts-tab="test">Проверка</button>
        <button type="button" data-tts-tab="add">Свой голос</button></div>
      <div class="ml-body tts-body"></div></div></div>`;
    const modal = root.querySelector('.tts');
    root.querySelector('[data-tts-close]').onclick = () => { root.innerHTML = ''; demo.pause(); };
    modal.querySelector('.ml-tabs').addEventListener('click', e => { const b = e.target.closest('[data-tts-tab]'); if (b) { tab = b.dataset.ttsTab; draw(modal); } });
    if (!target) target = (characters()[0] || [null])[0];
    draw(modal);
  }
  async function draw(modal) {
    modal.querySelectorAll('[data-tts-tab]').forEach(b => b.classList.toggle('active', b.dataset.ttsTab === tab));
    const body = modal.querySelector('.tts-body');
    body.onclick = null;
    if (!core()) { body.innerHTML = '<p class="tts-note">Озвучка работает, когда подключено EGO Core.</p>'; return; }
    body.innerHTML = '<p class="tts-note">Загрузка…</p>';
    await loadVoices(true);
    let status = null;
    try { status = await core().api('GET', '/v1/tts/status'); } catch { /* shown below */ }
    const warn = status && !status.installed ? '<p class="tts-warn">Модель озвучки не установлена на ПК (см. docs/TTS.md).</p>' : '';
    if (tab === 'library') drawLibrary(body, warn);
    else if (tab === 'test') drawTest(body, warn, status);
    else drawAdd(body);
  }

  const demo = new Audio();
  async function playDemo(id, button) {
    try {
      const r = await EGOCore.apiRaw('GET', '/v1/tts/voices/' + encodeURIComponent(id) + '/demo');
      demo.src = URL.createObjectURL(await r.blob()); await demo.play();
      mark(button, 'playing'); demo.onended = demo.onpause = () => mark(button, null);
    } catch (e) { say(why(e)); }
  }

  function drawLibrary(body, warn) {
    const chars = characters();
    const person = target && people[target];
    const v = (person && person.voice) || {};
    const list = voices.filter(x => (!filters.category || x.category === filters.category)
      && (!filters.language || (x.languages || []).includes(filters.language))
      && (!filters.q || (x.name + ' ' + (x.description || '') + ' ' + (x.traits || []).join(' ')).toLowerCase().includes(filters.q.toLowerCase())));
    body.innerHTML = `${warn}
      <div class="tts-char">
        <label>Персонаж <select data-f="target">${chars.map(([id, p]) => `<option value="${esc(id)}"${id === target ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
        <span class="tts-current">Голос: <b>${esc(voiceName(v.id) || (v.id ? tr('удалён — используется голос по умолчанию') : tr('по умолчанию')))}</b></span>
        <label>Скорость <input type="range" min="0.7" max="1.3" step="0.05" value="${Number(v.speed) || 1}" data-f="speed"> <output>${(Number(v.speed) || 1).toFixed(2)}</output></label>
        <label>Язык <select data-f="language">${Object.entries(LANGS).map(([k, n]) => `<option value="${k}"${(v.language || 'auto') === k ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
        <label>Подача <select data-f="emotion"><option value="auto">По тексту ответа</option><option value="off"${v.emotion === 'off' ? ' selected' : ''}>Всегда ровно</option></select></label>
        <label class="tts-auto"><input type="checkbox" data-tts-auto${auto ? ' checked' : ''}> Озвучивать ответы автоматически</label>
      </div>
      <div class="tts-filters">
        <select data-flt="category"><option value="">Все голоса</option>${Object.entries(CATEGORIES).map(([k, n]) => `<option value="${k}"${filters.category === k ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
        <select data-flt="language"><option value="">Любой язык</option>${Object.entries(LANGS).filter(([k]) => k !== 'auto').map(([k, n]) => `<option value="${k}"${filters.language === k ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
        <input type="search" data-flt="q" placeholder="Поиск: низкий, мягкий…" value="${esc(filters.q)}">
      </div>
      <div class="tts-grid">${list.map(x => `<article class="tts-card${x.id === v.id ? ' chosen' : ''}">
        <h3>${esc(x.name)} <small>${esc(tr(CATEGORIES[x.category] || ''))}${x.builtin ? '' : ' · ' + esc(tr('свой'))}</small></h3>
        <p>${esc(x.description || '')}</p>
        <div class="tts-traits">${(x.traits || []).map(s => `<span>${esc(s)}</span>`).join('')}</div>
        <p class="tts-meta">${esc((x.languages || []).join(', '))} · ${esc(x.license || '')}</p>
        <div class="tts-actions">${x.demo ? `<button type="button" data-demo="${esc(x.id)}">▶ Прослушать</button>` : ''}
          <button type="button" data-assign="${esc(x.id)}"${x.id === v.id ? ' disabled' : ''}>${x.id === v.id ? 'Назначен' : 'Назначить'}</button>
          ${x.builtin ? '' : `<button type="button" class="danger" data-del="${esc(x.id)}">Удалить</button>`}</div>
      </article>`).join('') || '<p class="tts-note">Нет голосов под этот фильтр.</p>'}</div>`;
    const modal = body.closest('.tts');
    const setVoice = patch => {
      if (!target || !people[target]) return;
      people[target] = { ...people[target], voice: { ...(people[target].voice || {}), ...patch } };
      storePeople();
    };
    body.querySelector('[data-f="target"]')?.addEventListener('change', e => { target = e.target.value; draw(modal); });
    body.querySelector('[data-f="speed"]')?.addEventListener('input', e => { e.target.nextElementSibling.textContent = Number(e.target.value).toFixed(2); });
    body.querySelector('[data-f="speed"]')?.addEventListener('change', e => setVoice({ speed: Number(e.target.value) }));
    body.querySelector('[data-f="language"]')?.addEventListener('change', e => setVoice({ language: e.target.value }));
    body.querySelector('[data-f="emotion"]')?.addEventListener('change', e => setVoice({ emotion: e.target.value }));
    body.querySelector('[data-tts-auto]')?.addEventListener('change', e => setAuto(e.target.checked));
    body.querySelectorAll('[data-flt]').forEach(el => el.addEventListener(el.type === 'search' ? 'change' : 'input', () => { filters[el.dataset.flt] = el.value; drawLibrary(body, warn); }));
    body.onclick = async e => {
      const d = e.target.closest('[data-demo]'), a = e.target.closest('[data-assign]'), del = e.target.closest('[data-del]');
      if (d) playDemo(d.dataset.demo, d);
      if (a) { setVoice({ id: a.dataset.assign }); say('Голос назначен'); drawLibrary(body, warn); }
      if (del) {
        if (!confirm(tr('Удалить свой голос? Персонажи с этим голосом перейдут на голос по умолчанию; их остальные настройки и чаты не изменятся.'))) return;
        try { await core().api('DELETE', '/v1/tts/voices/' + encodeURIComponent(del.dataset.del)); await loadVoices(true); drawLibrary(body, warn); say('Голос удалён'); }
        catch (err) { say(why(err)); }
      }
    };
  }

  function drawTest(body, warn, status) {
    const engines = (status && status.engines) || [{ id: 'xtts', name: 'XTTS-v2' }];
    body.innerHTML = `${warn}<div class="tts-test">
      <textarea data-t="text" rows="4" maxlength="4000">Привет! Это проверка голоса. Как тебе такое звучание?</textarea>
      <div class="tts-row">
        <label>Движок <select data-t="engine">${engines.map(e => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('')}</select></label>
        <label>Голос <select data-t="voice">${voices.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('')}</select></label>
        <label>Язык <select data-t="language">${Object.entries(LANGS).map(([k, n]) => `<option value="${k}">${esc(n)}</option>`).join('')}</select></label>
        <label>Подача <select data-t="emotion">${Object.entries(EMOTIONS).map(([k, n]) => `<option value="${k}">${esc(n)}</option>`).join('')}</select></label>
        <label>Скорость <input type="range" min="0.7" max="1.3" step="0.05" value="1" data-t="speed"></label>
      </div>
      <div class="tts-row"><button type="button" class="ml-compose" data-t="go">Озвучить</button>
        <button type="button" data-t="stop">Остановить</button><span class="tts-time" data-t="time"></span></div>
      <audio controls data-t="audio"></audio>
      <p class="tts-note">Подача меняет темп и живость речи; тембр голоса остаётся прежним. XTTS-v2 не управляет эмоциями напрямую.</p></div>`;
    const q = k => body.querySelector(`[data-t="${k}"]`);
    const player = q('audio');
    q('stop').addEventListener('click', () => player.pause());
    q('go').addEventListener('click', async () => {
      const go = q('go'); go.disabled = true; q('time').textContent = tr('Генерация…');
      const t0 = performance.now();
      try {
        const url = await synth({ text: q('text').value, engine: q('engine').value, voice: q('voice').value, language: q('language').value,
          emotion: q('emotion').value, speed: Number(q('speed').value), ui_language: uiLang() });
        player.src = url; player.play().catch(() => {});
        q('time').textContent = tr('Готово за') + ' ' + ((performance.now() - t0) / 1000).toFixed(1) + ' ' + tr('с');
      } catch (e) { q('time').textContent = why(e); }
      finally { go.disabled = false; }
    });
  }

  /* any audio file the browser can decode -> 24 kHz mono 16-bit WAV (the format Core accepts) */
  async function toWav(file) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const decoded = await ctx.decodeAudioData(await file.arrayBuffer());
    ctx.close && ctx.close();
    const rate = 24000, length = Math.round(decoded.duration * rate);
    const off = new OfflineAudioContext(1, length, rate);
    const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start();
    const pcm = (await off.startRendering()).getChannelData(0);
    const buf = new ArrayBuffer(44 + pcm.length * 2), dv = new DataView(buf);
    const w = (o, s) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); dv.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE'); w(12, 'fmt '); dv.setUint32(16, 16, true);
    dv.setUint16(20, 1, true); dv.setUint16(22, 1, true); dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) dv.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
    let bin = ''; const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return { b64: btoa(bin), seconds: decoded.duration };
  }

  function drawAdd(body) {
    body.innerHTML = `<div class="tts-add">
      <p class="tts-note">Образец остаётся на этом ПК (в зашифрованной базе Core) и никуда не отправляется. Нужно 6–30 секунд чистой речи одного человека, без музыки и шума.</p>
      <label>Название <input data-a="name" maxlength="60" placeholder="Например: Мой голос"></label>
      <label>Описание <input data-a="description" maxlength="300" placeholder="Тембр, манера"></label>
      <label>Категория <select data-a="category">${Object.entries(CATEGORIES).map(([k, n]) => `<option value="${k}">${esc(n)}</option>`).join('')}</select></label>
      <label>Характеристики <input data-a="traits" maxlength="120" placeholder="через запятую: низкий, мягкий"></label>
      <label>Языки <span><input type="checkbox" data-lang="ru" checked> RU <input type="checkbox" data-lang="en"> EN</span></label>
      <label>Файл <input type="file" accept="audio/*" data-a="file"></label>
      <label>Что сказано в образце <textarea data-a="ref_text" rows="2" maxlength="500" placeholder="Необязательно; нужно для движка F5-TTS"></textarea></label>
      <label class="tts-auto"><input type="checkbox" data-a="consent"> Это мой голос, или у меня есть право его использовать</label>
      <button type="button" class="ml-compose" data-a="save">Добавить голос</button></div>`;
    const q = k => body.querySelector(`[data-a="${k}"]`);
    q('save').addEventListener('click', async () => {
      const file = q('file').files && q('file').files[0];
      if (!q('name').value.trim() || !file) { say('Укажите название и файл'); return; }
      if (!q('consent').checked) { say('Подтвердите право на голос'); return; }
      q('save').disabled = true;
      try {
        const wav = await toWav(file);
        if (wav.seconds < 6 || wav.seconds > 30) { say('Нужен образец длиной 6–30 секунд'); return; }
        await core().api('POST', '/v1/tts/voices', { name: q('name').value.trim(), description: q('description').value.trim(),
          category: q('category').value, traits: q('traits').value.split(',').map(s => s.trim()).filter(Boolean),
          languages: [...body.querySelectorAll('[data-lang]:checked')].map(x => x.dataset.lang), ref_text: q('ref_text').value.trim(),
          wav_b64: wav.b64, consent: true });
        say('Голос добавлен'); tab = 'library'; draw(body.closest('.tts'));
      } catch (e) { say(e && e.code ? why(e) : tr('Не удалось прочитать аудиофайл')); }
      finally { q('save').disabled = false; }
    });
  }

  function mount() {
    const group = document.querySelector('.rail-group');
    if (!group || group.querySelector('[data-tts-lib]')) return;
    const b = document.createElement('button');
    b.className = 'rail-btn'; b.dataset.ttsLib = '1'; b.title = 'Голоса'; b.setAttribute('aria-label', 'Голоса');
    b.innerHTML = '<span data-icon="speaker"></span>';
    b.addEventListener('click', open);
    group.appendChild(b);
    if (typeof hydrate === 'function') hydrate(b);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  window.EGOVoices = Object.freeze({ open, voices: () => loadVoices(true) });
})();
