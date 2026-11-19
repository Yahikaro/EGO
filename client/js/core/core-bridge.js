/* EGO Core bridge — connects the existing visual client to the local EGO Server Core.
 *
 * Online when the page is served by Core (http://127.0.0.1:<port>/app/) and opened with a
 * one-time launch code (START_EGO_CLIENT.bat). Otherwise the client stays the offline
 * visual preview exactly as in. Existing localStorage formats are unchanged; chats
 * only gain an optional `coreChatId`.
 */
(() => {
  'use strict';
  const TOKEN_KEY = 'ego-core-session';
  const TERMINAL = new Set(['completed', 'stopped', 'failed']);
  const ERROR_TEXT = {
    MODEL_NOT_PROVIDED: 'В Core не настроена модель',
    RUNTIME_NOT_READY: 'Модель ещё не загружена или в состоянии ошибки',
    CORE_BUSY: 'Core занят, попробуйте через пару секунд',
    CORE_STOPPING: 'Core останавливается',
    GENERATION_BUSY: 'В этой истории уже идёт генерация',
    VERSION_CONFLICT: 'История изменилась в другом окне — повторите',
    NOTHING_TO_REGENERATE: 'Перегенерировать можно только последний ответ',
    NOTHING_TO_CONTINUE: 'Продолжить можно только последний ответ',
    CONTEXT_BUDGET_EXCEEDED: 'Сообщение или профиль персонажа не помещаются в контекст модели',
    OUTPUT_LIMIT_EXCEEDED: 'Достигнут предел длины ответа',
    SERVER_RESTARTED: 'Core был перезапущен во время ответа',
    CONTENT_BLOCKED: 'Этот запрос или ответ нарушает жёсткие ограничения EGO и не был сохранён',
    UNAUTHORIZED: 'Сессия истекла — откройте клиент заново через START_EGO_CLIENT.bat',
  };

  const state = { online: false, token: null, busy: null };
  // Stage 4b: texts come from the Core catalog (GET /v1/errors); ERROR_TEXT is the offline fallback.
  let serverErrors = {};
  const describe = code => {
    const entry = serverErrors[code];
    if (entry) return entry.hint ? `${entry.message} ${entry.hint}` : entry.message;
    return ERROR_TEXT[code] || ('Ошибка Core: ' + (code || 'неизвестно'));
  };
  async function loadErrorCatalog() {
    try { serverErrors = (await api('GET', '/v1/errors')).errors || {}; } catch { /* keep the fallback table */ }
  }

  class CoreError extends Error { constructor(code) { super(code); this.code = code; } }

  async function api(method, path, body) {
    let response;
    try {
      response = await fetch(path, {
        method, cache: 'no-store',
        headers: { Authorization: 'Bearer ' + state.token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch { throw new CoreError('CORE_OFFLINE'); }
    let data = null;
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) {
      const code = (data && data.code) || ('HTTP_' + response.status);
      if (code === 'UNAUTHORIZED') setOnline(false);
      throw new CoreError(code);
    }
    return data;
  }

  const uuid = () => crypto.randomUUID();

  /* ---- content <-> visual blocks ------------------------------------------------------
   * Core stores plain text: *actions* in asterisks, speech as plain text (the same convention
   * the roleplay rules ask the model to use). The client renders it as its existing blocks. */
  function toBlocks(text) {
    const blocks = [];
    const pushSpoken = chunk => chunk.split(/\n{2,}/).map(s => s.trim()).filter(Boolean)
      .forEach(s => blocks.push(['spoken', s]));
    const pushAction = chunk => { const s = chunk.trim(); if (s) blocks.push(['action', s]); };
    let index = 0;
    const pattern = /\*([^*]+)\*/g;
    let match;
    while ((match = pattern.exec(text))) {
      pushSpoken(text.slice(index, match.index));
      pushAction(match[1]);
      index = pattern.lastIndex;
    }
    const tail = text.slice(index);
    const open = tail.indexOf('*');
    if (open >= 0) { pushSpoken(tail.slice(0, open)); pushAction(tail.slice(open + 1)); }  // still streaming
    else pushSpoken(tail);
    return blocks.length ? blocks : [['action', '…']];
  }

  function fromBlocks(message) {
    if (message.by === 'user') return String(message.text || '');
    return (message.blocks || []).map(([type, text]) => (type === 'action' || type === 'thought') ? `*${text}*` : text).join('\n\n');
  }

  /* ---- character / scene profile sent with each generation ---------------------------- */
  function characterFor(chat) {
    const ids = (Array.isArray(chat.members) && chat.members.length ? chat.members : [chat.person])
      .filter(id => people[id]).slice(0, 5);
    const describePerson = p => [
      p.name && `Имя: ${p.name}`, p.age && `Возраст: ${p.age}`, p.role && `Роль: ${p.role}`,
      p.appearance && `Внешность: ${p.appearance}`, p.personality && `Характер: ${p.personality}`,
      p.description && `Описание: ${p.description}`,
    ].filter(Boolean).join('\n');
    if (!ids.length) {
      // Story without a character: a neutral narrator, still under EGO's roleplay rules.
      const scene = [chat.sceneNote && `Заметка сцены: ${chat.sceneNote}`].filter(Boolean).join('\n');
      return { name: 'Рассказчик', description: (`Рассказчик (ведущий) свободной истории. Главный герой — сам пользователь, рассказчик им не является.\n` +
        `Повествование ведётся во втором лице: обращайся к герою на «ты» и описывай, что он видит, слышит и что происходит вокруг («Перед тобой…», «Ты слышишь…»). ` +
        `Никогда не пиши от лица героя и не решай за него, что он делает, говорит, думает или чувствует. ` +
        `Описывай мир, обстановку, последствия действий героя и говори за второстепенных персонажей, которых вводит сюжет. ` +
        `Заканчивай ход ситуацией, на которую герой может отреагировать.\n${scene}`).trim().slice(0, 4000) };
    }
    const lead = people[ids[0]];
    const world = [lead.world && `Мир: ${lead.world}`, lead.setting && `Место: ${lead.setting}`,
      lead.scenario && `Сценарий: ${lead.scenario}`, chat.sceneNote && `Заметка сцены: ${chat.sceneNote}`]
      .filter(Boolean).join('\n');
    const opening = chat.messages.find(m => m.by === 'ai');
    const intro = !chat.coreChatId && opening ? `\nНачало сцены (уже было сказано):\n${fromBlocks(opening)}` : '';
    if (ids.length > 1) {
      const cast = ids.map(id => describePerson(people[id])).join('\n\n');
      return { name: 'Рассказчик', description: (`Ты ведёшь групповую сцену и говоришь за всех этих персонажей, помечая, кто говорит:\n${cast}\n${world}${intro}`).slice(0, 4000) };
    }
    // Decision D2: a character stored in Core is referenced by id; Core composes the
    // profile (with its world) from encrypted storage. Scene notes still need text.
    if (window.EGOSync && EGOSync.isSynced(ids[0]) && !chat.sceneNote) return { id: ids[0] };
    return { name: String(lead.name || 'Персонаж').slice(0, 120), description: (`${describePerson(lead)}\n${world}${intro}`).slice(0, 4000) };
  }

  async function ensureCoreChat(chat) {
    if (!chat.coreChatId) {
      const created = await api('POST', '/v1/chats', { operation_id: uuid(), title: String(chat.title || 'EGO').slice(0, 200) });
      chat.coreChatId = created.id;
      save();
    }
    const list = await api('GET', '/v1/chats');
    const row = list.find(c => c.id === chat.coreChatId);
    if (!row) { chat.coreChatId = null; save(); return ensureCoreChat(chat); }
    return row;
  }

  /* ---- streaming: SSE over fetch (Authorization header), cursor resume, polling fallback --- */
  async function* snapshots(generationId, signal) {
    let cursor = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const response = await fetch(`/v1/generations/${generationId}/events`, {
          headers: { Authorization: 'Bearer ' + state.token, ...(cursor ? { 'Last-Event-ID': cursor } : {}) },
          signal, cache: 'no-store',
        });
        if (!response.ok || !response.body) throw new Error('stream');
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          let split;
          while ((split = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, split); buffer = buffer.slice(split + 2);
            let id = null, type = null; const data = [];
            for (const line of frame.split('\n')) {
              if (line.startsWith('id: ')) id = line.slice(4);
              else if (line.startsWith('event: ')) type = line.slice(7);
              else if (line.startsWith('data: ')) data.push(line.slice(6));
            }
            if (type !== 'generation' || !data.length) continue;
            if (id) cursor = id;
            const snapshot = JSON.parse(data.join('\n'));
            yield snapshot;
            if (TERMINAL.has(snapshot.state)) return;
          }
        }
      } catch (error) {
        if (signal && signal.aborted) return;
      }
      await new Promise(r => setTimeout(r, 400 * (attempt + 1)));
    }
    for (;;) {  // Polling fallback, same as the server GUI playground.
      const snapshot = await api('GET', `/v1/generations/${generationId}`);
      yield snapshot;
      if (TERMINAL.has(snapshot.state)) return;
      await new Promise(r => setTimeout(r, 250));
    }
  }

  /* ---- painting while a reply streams (2026-11-06) ----------------------------------------
     Before: every ~100 ms the whole story was rebuilt — all messages replayed their entrance
     animation (flicker) and the story was written over whatever tab the user had opened.
     Now only the reply's text changes, only while the user looks at that story. */
  const paintStream = (chat, index) => { if (window.EGOChatView) window.EGOChatView.paint(chat, index); };
  const quietStory = chat => { if (typeof page === 'undefined' || page === 'chats') story(); };

  /* ---- generation flow ------------------------------------------------------------------- */
  async function runGeneration(chat, kind, userText) {
    if (state.busy) { toast('Дождитесь окончания текущего ответа'); return; }
    const character = characterFor(chat);
    const row = await ensureCoreChat(chat).catch(e => { toast(describe(e.code)); return null; });
    if (!row) return false;
    const payload = { operation_id: uuid(), chat_id: row.id, expected_version: row.version,
      ...(character.id ? { character_id: character.id } : { character_name: character.name, character_description: character.description }) };
    if (kind === 'send') payload.content = userText;
    const path = kind === 'send' ? '/v1/generations' : kind === 'regenerate' ? '/v1/generations/regenerate' : '/v1/generations/continue';
    let generation;
    try { generation = (await api('POST', path, payload)).generation; }
    catch (e) { toast(describe(e.code)); return false; }

    const lastIndex = chat.messages.length - 1;
    const base = kind === 'continue' ? fromBlocks(chat.messages[lastIndex]) : '';
    const previous = kind === 'regenerate' ? chat.messages[lastIndex] : null;
    const target = kind === 'send' ? chat.messages.push({ by: 'ai', blocks: [['action', '…']], pending: true }) - 1 : lastIndex;
    const controller = new AbortController();
    state.busy = { generationId: generation.id, controller };
    setComposerBusy(true);
    let last = null, renderedAt = 0;
    try {
      for await (const snapshot of snapshots(generation.id, controller.signal)) {
        last = snapshot;
        const text = snapshot.text || '';
        if (text && Date.now() - renderedAt > 100) {
          chat.messages[target] = { by: 'ai', blocks: toBlocks(base ? base + ' ' + text : text), pending: true };
          renderedAt = Date.now(); paintStream(chat, target);
        }
      }
    } catch (e) { last = last || { state: 'failed', error_code: e.code }; }
    finally { state.busy = null; setComposerBusy(false); }

    const text = (last && last.text) || '';
    if (last && last.state === 'completed' && text) {
      // Core's committed message is authoritative (continuation de-duplicates overlap).
      let committed = base ? base + ' ' + text : text;
      try {
        const reply = (await api('GET', `/v1/chats/${row.id}/messages`)).filter(m => m.role === 'assistant').pop();
        if (reply) committed = reply.content;
      } catch { /* keep the streamed text */ }
      chat.messages[target] = { by: 'ai', blocks: toBlocks(committed), ...(last.finish_reason === 'length' ? { truncated: true } : {}) };
    } else if (last && last.state === 'stopped' && text && kind === 'send') {
      chat.messages[target] = { by: 'ai', blocks: toBlocks(text), stopped: true };  // Core also keeps it
    } else {
      // Failed/stopped regenerate or continue: Core keeps the original reply, so do we.
      if (kind === 'send') chat.messages.splice(target, 1);
      else chat.messages[target] = previous || chat.messages[target];
      if (kind === 'continue') chat.messages[target] = { by: 'ai', blocks: toBlocks(base) };
      if (last && last.state === 'failed') toast(describe(last.error_code));
    }
    chat.modified = Date.now(); save(); sidebar(); quietStory(chat); inspector();
    // once per finished reply (tts.js auto-speech listens; re-renders never fire it)
    if (chat.messages[target] && chat.messages[target].by === 'ai' && !chat.messages[target].pending && last && (last.state === 'completed' || last.state === 'stopped'))
      window.dispatchEvent(new CustomEvent('ego-ai-reply', { detail: { chat, index: target } }));
    return true;
  }

  async function stopGeneration() {
    if (!state.busy) return;
    try { await api('POST', `/v1/generations/${state.busy.generationId}/stop`, { operation_id: uuid() }); }
    catch (e) { toast(describe(e.code)); }
  }

  /* ---- UI integration (wraps the existing globals, keeps offline behaviour) -------------- */
  const offlineSend = send;
  window.send = send = function onlineSend() {
    if (!state.online) return offlineSend();
    if (state.busy) return stopGeneration();
    const el = $('message'), text = el.value.trim(), chat = current();
    if (!text || !chat) return;
    if (text.length > 4000) { toast('Максимум 4000 символов'); return; }
    const userIndex = chat.messages.push({ by: 'user', text }) - 1;
    chat.modified = Date.now(); el.value = ''; $('counter').textContent = '0 / 4000';
    save(); sidebar(); story(); inspector();
    runGeneration(chat, 'send', text).then(ok => {
      if (ok === false) {  // Not accepted by Core: keep the text for retry, drop the local echo.
        if (chat.messages[userIndex] && chat.messages[userIndex].text === text) chat.messages.splice(userIndex, 1);
        el.value = text; save(); quietStory(chat);
      }
    });
  };
  $('send').onclick = () => send();

  /* ---- per-chat preset (stage 4a): stored in Core by chat_id ---------------------------- */
  let presetCache = null;
  async function presetList() {
    if (!presetCache) presetCache = (await api('GET', '/v1/presets')).presets;
    return presetCache;
  }

  async function renderChatPreset() {
    const host = $('inspect-content');
    const chat = current();
    if (!state.online || !host || !chat) return;
    let box = document.getElementById('ego-chat-preset');
    if (!box) {
      box = document.createElement('section');
      box.id = 'ego-chat-preset';
      box.innerHTML = '<div class="inspector-label">Пресет чата</div>' +
        '<select class="ego-preset-select" aria-label="Пресет чата"></select>' +
        '<p class="ego-preset-note"></p>';
      host.append(box);
    }
    const select = box.querySelector('select'), note = box.querySelector('.ego-preset-note');
    try {
      const presets = await presetList();
      select.innerHTML = '<option value="">Настройки сервера (по умолчанию)</option>' + presets.map(p =>
        `<option value="${p.id}">${p.name}${p.measured ? ` · ${p.measured.decode_tokens_per_second} ток/с` : ''}</option>`).join('');
      const current = chat.coreChatId ? (await api('GET', `/v1/chats/${chat.coreChatId}/settings`)).preset_id : null;
      select.value = current || '';
      const showNote = id => { const p = presets.find(x => x.id === id); note.textContent = p ? p.description : 'Параметры активного профиля сервера.'; };
      showNote(select.value);
      select.onchange = async () => {
        try {
          const row = await ensureCoreChat(chat);
          await api('PUT', `/v1/chats/${row.id}/settings`, { preset_id: select.value || null });
          showNote(select.value);
          toast('Пресет чата сохранён');
        } catch (e) { toast(describe(e.code)); }
      };
    } catch (e) { note.textContent = 'Пресеты недоступны: ' + describe(e.code); }
  }

  const baseInspector = inspector;
  window.inspector = inspector = function inspectorWithPreset() { baseInspector(); renderChatPreset(); };

  // Message buttons (regenerate, continue, truncated banner) live in js/app/chat-view.js.

  function setComposerBusy(busy) {
    const button = $('send');
    button.title = busy ? 'Остановить генерацию' : 'Отправить сообщение';
    button.classList.toggle('ego-stop', busy);
    button.innerHTML = busy ? '<span class="ego-stop-square"></span>' : svg('arrowup');
  }

  function setOnline(online, info) {
    state.online = online;
    if (!online) { try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage may be blocked */ } state.token = null; }
    const label = document.querySelector('.offline-label');
    if (label) label.innerHTML = online ? `<span class="mini-dot ego-online"></span> CORE · ${info || 'ONLINE'}` : '<span class="mini-dot"></span> VISUAL PREVIEW';
    const note = document.querySelector('.composer-note');
    if (note) note.textContent = online
      ? 'Все действия и сообщения вымышленные · Локальная модель через EGO Core · Enter — отправить, Shift+Enter — новая строка'
      : 'Локальный визуальный прототип · AI не подключён · Enter — отправить, Shift+Enter — новая строка';
  }

  async function refreshStatus() {
    if (!state.online) return;
    try {
      const model = await api('GET', '/v1/runtime/model');
      setOnline(true, model.ready ? (model.name || 'READY').replace(/\.gguf$/i, '') : (model.state || 'LOADING'));
    } catch (e) {
      if (e.code === 'UNAUTHORIZED' && window.EGOAccount && window.EGOAccount.token === state.token) {
        window.EGOAccount.signOutLocal(); location.reload(); return;   // account session ended (logout elsewhere / expired)
      }
      if (e.code !== 'UNAUTHORIZED') setOnline(true, 'OFFLINE');
    }
  }

  let connect = async function connectOnce() {
    if (location.protocol === 'file:') { if (window.EGOAccount && window.EGOAccount.intro) window.EGOAccount.intro('offline'); return setOnline(false); }
    const code = new URLSearchParams(location.hash.slice(1)).get('code');
    if (code) {
      history.replaceState(null, '', location.pathname + location.search);  // Drop the one-time code.
      try {
        const response = await fetch('/v1/client/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
        if (response.ok) {
          state.token = (await response.json()).token; try { sessionStorage.setItem(TOKEN_KEY, state.token); } catch { /* ignore */ }
          if (window.EGOAccount && window.EGOAccount.intro) window.EGOAccount.intro('pc');   // first screen; Core connects behind it
        }
      } catch { /* Core unreachable */ }
    }
    if (!state.token) { try { state.token = sessionStorage.getItem(TOKEN_KEY); } catch { /* ignore */ } }
    // Stage 4g: devices without a launch code sign in with the EGO account.
    if (!state.token && window.EGOAccount) state.token = await window.EGOAccount.start();
    if (!state.token) { setOnline(false); toast('Нет подключения к EGO Core — открыт визуальный режим'); return; }
    setOnline(true);
    await refreshStatus();
    if (state.online) await loadErrorCatalog();
    if (state.online) { story(); toast('Подключено к EGO Core'); window.dispatchEvent(new Event('ego-core-online')); }
  };

  // Raw fetch with the session token, for binary routes (avatars).
  async function apiRaw(method, path, body, contentType) {
    let response;
    try {
      response = await fetch(path, { method, cache: 'no-store', body,
        headers: { Authorization: 'Bearer ' + state.token, ...(contentType ? { 'Content-Type': contentType } : {}) } });
    } catch { throw new CoreError('CORE_OFFLINE'); }
    if (!response.ok) {
      let code = 'HTTP_' + response.status;
      try { code = (await response.json()).code || code; } catch { /* binary body */ }
      throw new CoreError(code);
    }
    return response;
  }

  window.EGOCore = Object.freeze({ toBlocks, fromBlocks, characterFor, api, apiRaw, describe, get online() { return state.online; },
    get busy() { return !!state.busy; }, generate: (chat, kind) => runGeneration(chat, kind) });
  // A new launch link opened in an existing tab only changes the hash (no reload).
  window.addEventListener('hashchange', () => { if (location.hash.includes('code=')) connect(); });
  let statusTimer = null;
  const originalConnect = connect;
  connect = async () => { await originalConnect(); if (state.online && !statusTimer) statusTimer = setInterval(refreshStatus, 10000); };
  connect();
})();
