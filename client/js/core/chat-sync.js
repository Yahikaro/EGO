/* Shared story list between this PC and other devices (2026-11-06).
   Core keeps every story that ever got a reply; this module keeps the browser's list in step:
   - stories created on another device appear here (character, members, opening scene, messages);
   - new messages written on another device are pulled in;
   - a story deleted here is deleted in Core (queued while offline); one deleted elsewhere
     disappears here.
   The browser copy stays the fast cache; Core is the source of truth for replies. */
(() => {
  'use strict';
  if (typeof chats === 'undefined' || typeof save !== 'function') return;
  const KEY = 'ego-chat-sync-v1';
  const core = () => window.EGOCore;
  let busy = false, timer = null;

  const loadState = () => { try { return { known: [], pending: [], ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { known: [], pending: [] }; } };
  const saveState = s => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* quota */ } };
  const isUuid = v => typeof v === 'string' && /^[0-9a-f-]{36}$/.test(v);
  const replying = chat => chat === (typeof current === 'function' ? current() : null) && document.querySelector('#send.ego-stop');

  function cleanMessage(m) {
    if (!m || typeof m !== 'object') return null;
    if (m.by === 'user') return { by: 'user', text: String(m.text || '').slice(0, 32768) };
    if (Array.isArray(m.blocks)) return { by: 'ai', blocks: m.blocks.filter(b => Array.isArray(b) && b.length === 2).slice(0, 40).map(([t, x]) => [String(t).slice(0, 16), String(x).slice(0, 8000)]) };
    return null;
  }
  const fromCore = m => m.role === 'user' ? { by: 'user', text: m.content } : { by: 'ai', blocks: core().toBlocks(m.content) };

  async function coreMessages(id) {
    let all = [], before = null;
    for (let page = 0; page < 50; page++) {
      const batch = await core().api('GET', `/v1/chats/${id}/messages${before ? `?before=${before}` : ''}`);
      all = batch.concat(all);
      if (batch.length < 100) break;
      before = batch[0].id;
    }
    return all.filter(m => m.status === 'completed' || m.status === 'stopped').map(fromCore);
  }

  async function sync() {
    if (busy || !core() || !core().online) return;
    busy = true;
    let changed = false;
    try {
      const state = loadState();
      const localIds = new Set(chats.filter(c => isUuid(c.coreChatId)).map(c => c.coreChatId));
      // 1. stories deleted in this browser since the last sync -> delete in Core (retry later if it fails)
      state.pending = [...new Set([...state.pending, ...state.known.filter(id => !localIds.has(id))])];
      const stillPending = [];
      for (const id of state.pending) {
        try { await core().api('DELETE', `/v1/chats/${id}`); }
        catch (e) { if (e.code !== 'NOT_FOUND') stillPending.push(id); }
      }
      state.pending = stillPending;
      const library = (await core().api('GET', '/v1/chats/library')).chats || [];
      const rows = new Map(library.map(r => [r.id, r]));
      // 2. stories deleted on another device disappear here (only ones we have seen in Core before)
      if (library.length < 500) {
        const before = chats.length;
        chats = chats.filter(c => !(c.coreSynced && isUuid(c.coreChatId) && !rows.has(c.coreChatId) && !state.pending.includes(c.coreChatId)));
        if (chats.length !== before) changed = true;
      }
      for (const chat of chats) {
        const row = isUuid(chat.coreChatId) && rows.get(chat.coreChatId);
        if (!row) continue;
        chat.coreSynced = true;
        // 3. our metadata -> Core, once per story (character, members, opening scene)
        if (!chat.metaPushed) {
          const opening = typeof chat.openingCount === 'number' ? chat.openingCount : Math.max(0, chat.messages.length - row.messages);
          chat.openingCount = opening;
          try {
            await core().api('PUT', `/v1/chats/${chat.coreChatId}/meta`, { meta: { person: chat.person || '', members: Array.isArray(chat.members) ? chat.members.slice(0, 5) : [],
              opening: chat.messages.slice(0, Math.min(opening, 5)).map(cleanMessage).filter(Boolean) }, title: String(chat.title || 'EGO').slice(0, 200) });
            chat.metaPushed = true; changed = true;
          } catch { /* next time */ }
        }
        // 4. replies written on another device -> pull them in
        const opening = chat.openingCount || 0;
        if (row.messages > chat.messages.length - opening && !replying(chat)) {
          try {
            chat.messages = chat.messages.slice(0, opening).concat(await coreMessages(chat.coreChatId));
            chat.modified = Date.parse(row.last_message_at || '') || Date.now();
            changed = true;
          } catch { /* next time */ }
        }
      }
      // 5. stories created on another device -> appear here
      for (const row of library) {
        if (localIds.has(row.id) || chats.some(c => c.coreChatId === row.id)) continue;
        // only stories made in the client (they carry client metadata): not Control Center
        // playground chats or diagnostic smoke chats
        if (!row.meta || typeof row.meta !== 'object' || !('person' in row.meta)) continue;
        if (!row.messages && !(row.meta.opening && row.meta.opening.length)) continue;
        const meta = row.meta;
        try {
          const opening = (Array.isArray(meta.opening) ? meta.opening : []).map(cleanMessage).filter(Boolean).slice(0, 5);
          const person = typeof meta.person === 'string' && typeof people !== 'undefined' && people[meta.person] ? meta.person : '';
          chats.push({ id: 'core-' + row.id, coreChatId: row.id, person, title: row.title,
            members: (Array.isArray(meta.members) ? meta.members : []).filter(id => typeof people !== 'undefined' && people[id]),
            messages: opening.concat(await coreMessages(row.id)), openingCount: opening.length,
            modified: Date.parse((row.last_message_at || row.created_at || '').replace(' ', 'T') + 'Z') || Date.now(),
            coreSynced: true, metaPushed: true });
          changed = true;
        } catch { /* next time */ }
      }
      state.known = chats.filter(c => isUuid(c.coreChatId)).map(c => c.coreChatId);
      saveState(state);
      if (changed) {
        save();
        if (typeof active !== 'undefined' && !chats.some(c => c.id === active)) active = chats[0]?.id || '';
        if (typeof page !== 'undefined' && page === 'chats' && typeof render === 'function') render();
      }
    } catch { /* offline or old Core: try again later */ }
    finally { busy = false; }
  }

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(sync, ms); }
  window.addEventListener('ego-core-online', () => schedule(300));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(500); });
  // a story deleted from the menu reaches Core within a moment
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('#delete-chat')) schedule(400); }, true);
  setInterval(() => { if (!document.hidden) sync(); }, 20000);
  window.EGOChatSync = Object.freeze({ sync });
})();
