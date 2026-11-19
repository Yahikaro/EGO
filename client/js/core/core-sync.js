/* EGO Core sync — characters and worlds live in the encrypted EGO Core store (decision D2).
 *
 * Core is the source of truth; localStorage stays as the offline cache in the same formats as
 * before. On connect: local cards and worlds that Core does not have are uploaded (first-time
 * migration, avatars included); records changed on Core are downloaded; records edited offline
 * are uploaded if Core did not change them meanwhile. If both sides changed, Core wins and the
 * user is told. Every later save/delete in the studio is pushed to Core.
 */
(() => {
  'use strict';
  const MAP_KEY = 'ego-core-sync-v1';
  let map = { characters: {}, worlds: {} };
  try { const saved = JSON.parse(localStorage.getItem(MAP_KEY) || 'null'); if (saved && saved.characters && saved.worlds) map = saved; } catch { /* fresh map */ }
  const persistMap = () => { try { localStorage.setItem(MAP_KEY, JSON.stringify(map)); } catch { /* storage full or blocked */ } };

  const sorted = value => Array.isArray(value) ? value.map(sorted)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, sorted(value[k])])) : value;
  const hash = data => JSON.stringify(sorted(data));
  const characterData = p => { const { avatar, ...rest } = p; return rest; };
  const worldData = w => ({ ...w });
  const avatarPrint = p => (typeof p.avatar === 'string' && p.avatar.startsWith('data:')) ? p.avatar.length + ':' + p.avatar.slice(-48) : '';

  const originalStorePeople = storePeople;
  const originalSaveWorlds = saveWorlds;
  let chain = Promise.resolve();
  let conflicts = 0;
  let avatarErrors = [];

  async function uploadAvatar(id, p) {
    // Decoded here, not with fetch(): Safari refuses fetch() of a data: URL under the page's CSP («Load failed»).
    const [head, body] = String(p.avatar).split(',', 2);
    const type = (head.match(/^data:([^;,]+)/) || [])[1] || 'image/jpeg';
    const bytes = head.includes(';base64') ? Uint8Array.from(atob(body), ch => ch.charCodeAt(0)) : new TextEncoder().encode(decodeURIComponent(body));
    const blob = new Blob([bytes], { type });
    await EGOCore.apiRaw('PUT', `/v1/characters/${encodeURIComponent(id)}/avatar`, blob, blob.type || 'image/jpeg');
  }

  async function downloadAvatar(id) {
    const blob = await (await EGOCore.apiRaw('GET', `/v1/characters/${encodeURIComponent(id)}/avatar`)).blob();
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result); reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  /* Avatars (2026-11-08): the picture is kept equal on every device of the account.
     entry.a = local picture print at the last sync, entry.s = server fingerprint at the last sync.
     Changed here -> upload (or delete when removed here); changed on the server (another device) ->
     download (or drop when removed there). A card without a picture shows the placeholder. */
  async function syncAvatar(id, srv, refresh) {
    const entry = map.characters[id], local = avatarPrint(people[id]);
    const serverPrint = typeof srv.avatar === 'string' ? srv.avatar : (srv.has_avatar ? 'present' : '');
    const knownLocal = entry.a || '', knownServer = entry.s;
    /* new here, or the server never got it */ if (local && (local !== knownLocal || (!serverPrint && !knownServer))) { await uploadAvatar(id, people[id]); entry.a = local; refresh.add(id); return; }
    if (!local && knownLocal) {                       // removed on this device
      if (serverPrint) { try { await EGOCore.api('DELETE', `/v1/characters/${encodeURIComponent(id)}/avatar`); } catch { /* already gone */ } }
      entry.a = ''; entry.s = ''; return;
    }
    if (serverPrint && (!local || serverPrint !== knownServer)) {   // new or changed on the server
      people[id] = { ...people[id], avatar: await downloadAvatar(id) }; entry.a = avatarPrint(people[id]); entry.s = serverPrint; return;
    }
    if (!serverPrint && knownServer && local) {       // removed on another device
      const { avatar, ...rest } = people[id]; people[id] = rest; entry.a = ''; entry.s = ''; return;
    }
    entry.s = serverPrint;
  }

  async function syncCharacters() {
    const server = new Map((await EGOCore.api('GET', '/v1/characters')).characters.map(r => [r.id, r]));
    // Deleted locally since the last sync -> delete on Core.
    for (const id of Object.keys(map.characters)) {
      if (people[id] || !server.has(id)) { if (!people[id]) delete map.characters[id]; continue; }
      await EGOCore.api('DELETE', `/v1/characters/${encodeURIComponent(id)}`);
      server.delete(id); delete map.characters[id];
    }
    const refresh = new Set();
    avatarErrors = [];
    for (const id of Object.keys(people)) {
      const p = people[id], data = characterData(p), h = hash(data), entry = map.characters[id], srv = server.get(id);
      if (!srv) {
        if (entry && !builtInKeys.has(id)) { delete people[id]; delete map.characters[id]; continue; }  // deleted on Core
        const saved = await EGOCore.api('PUT', `/v1/characters/${encodeURIComponent(id)}`, { data });
        if (avatarPrint(p)) { await uploadAvatar(id, p); refresh.add(id); }
        map.characters[id] = { v: saved.version, h, a: avatarPrint(p) };
        continue;
      }
      const serverHash = hash(srv.data);
      const localChanged = entry ? h !== entry.h : h !== serverHash;
      const serverChanged = entry ? srv.version !== entry.v : true;
      if (localChanged && entry && !serverChanged) {
        const saved = await EGOCore.api('PUT', `/v1/characters/${encodeURIComponent(id)}`, { data, expected_version: srv.version });
        map.characters[id] = { ...entry, v: saved.version, h };
      } else if (serverChanged && h !== serverHash) {
        if (localChanged && entry) conflicts++;
        people[id] = { ...srv.data, avatar: p.avatar };
        map.characters[id] = { v: srv.version, h: serverHash, a: entry ? entry.a : '', s: entry ? entry.s : undefined };
      } else {
        map.characters[id] = { v: srv.version, h: serverHash, a: entry ? entry.a : '', s: entry ? entry.s : undefined };
      }
      try { await syncAvatar(id, srv, refresh); }
      catch (error) { avatarErrors.push((people[id] && people[id].name || id) + ': ' + (error.code || error.message || error)); }
    }
    // New on Core (another client or the server GUI) -> add locally.
    for (const [id, srv] of server) {
      if (people[id] || map.characters[id]) continue;
      people[id] = { ...srv.data };
      if (srv.has_avatar) people[id].avatar = await downloadAvatar(id);
      map.characters[id] = { v: srv.version, h: hash(srv.data), a: avatarPrint(people[id]), s: typeof srv.avatar === 'string' ? srv.avatar : (srv.has_avatar ? 'present' : '') };
    }
    // Pictures uploaded in this run: remember the fingerprint the server computed for them.
    if (refresh.size) {
      for (const r of (await EGOCore.api('GET', '/v1/characters')).characters) {
        if (refresh.has(r.id) && map.characters[r.id]) map.characters[r.id].s = typeof r.avatar === 'string' ? r.avatar : (r.has_avatar ? 'present' : '');
      }
    }
  }

  async function syncWorlds() {
    const server = new Map((await EGOCore.api('GET', '/v1/worlds')).worlds.map(r => [r.id, r]));
    const local = new Map(egoWorlds.map(w => [w.id, w]));
    for (const id of Object.keys(map.worlds)) {
      if (local.has(id) || !server.has(id)) { if (!local.has(id)) delete map.worlds[id]; continue; }
      await EGOCore.api('DELETE', `/v1/worlds/${encodeURIComponent(id)}`);
      server.delete(id); delete map.worlds[id];
    }
    const next = [];
    for (const w of egoWorlds) {
      const data = worldData(w), h = hash(data), entry = map.worlds[w.id], srv = server.get(w.id);
      if (!srv) {
        if (entry) { delete map.worlds[w.id]; continue; }  // deleted on Core
        const saved = await EGOCore.api('PUT', `/v1/worlds/${encodeURIComponent(w.id)}`, { data });
        map.worlds[w.id] = { v: saved.version, h }; next.push(w); continue;
      }
      const serverHash = hash(srv.data);
      const localChanged = entry ? h !== entry.h : h !== serverHash;
      const serverChanged = entry ? srv.version !== entry.v : true;
      if (localChanged && entry && !serverChanged) {
        const saved = await EGOCore.api('PUT', `/v1/worlds/${encodeURIComponent(w.id)}`, { data, expected_version: srv.version });
        map.worlds[w.id] = { v: saved.version, h }; next.push(w);
      } else if (serverChanged && h !== serverHash) {
        if (localChanged && entry) conflicts++;
        next.push({ ...srv.data, id: w.id }); map.worlds[w.id] = { v: srv.version, h: serverHash };
      } else { map.worlds[w.id] = { v: srv.version, h: serverHash }; next.push(w); }
    }
    for (const [id, srv] of server) {
      if (local.has(id) || next.length >= 100) continue;
      next.push({ ...srv.data, id }); map.worlds[id] = { v: srv.version, h: hash(srv.data) };
    }
    egoWorlds = next;
  }

  function syncAll(reason) {
    chain = chain.then(async () => {
      if (!EGOCore.online) return;
      conflicts = 0;
      // A background run never disturbs an open window (an editor, a modal).
      if (reason === 'timer' && document.querySelector && document.querySelector('#modal-root > *')) return;
      const before = JSON.stringify(map);
      try {
        await syncCharacters();
        await syncWorlds();
        persistMap();
        const changed = JSON.stringify(map) !== before;
        if (reason === 'timer' && !changed) return;        // nothing new: no redraw
        originalStorePeople(); originalSaveWorlds();
        if (typeof page !== 'undefined' && page !== 'chats' && typeof generic === 'function') generic();
        sidebar(); inspector();
        if (typeof page !== 'undefined' && page === 'chats' && typeof setPage === 'function' && changed) setPage('chats');
        if (conflicts) toast(`Персонажи и миры: ${conflicts} изменений из офлайна заменены версией из EGO Core`);
        else if (reason === 'connect') toast('Персонажи и миры синхронизированы с EGO Core');
        if (avatarErrors.length && reason !== 'timer') { console.warn('EGO avatar sync', avatarErrors); toast('Аватары не синхронизированы: ' + avatarErrors.slice(0, 2).join('; ')); }
      } catch (error) {
        persistMap();
        console.error('EGO sync', error);
        if (reason !== 'timer') toast('Синхронизация персонажей: ' + (error && error.code ? EGOCore.describe(error.code) : String(error && error.message || error)));
      }
    });
    return chain;
  }

  // Studio saves/deletes keep working offline (local cache) and are pushed to Core when online.
  let pending = null;
  const schedule = () => { clearTimeout(pending); pending = setTimeout(() => syncAll('save'), 300); };
  window.storePeople = storePeople = function storePeopleAndSync() { const ok = originalStorePeople(); if (ok && EGOCore.online) schedule(); return ok; };
  window.saveWorlds = saveWorlds = function saveWorldsAndSync() { const ok = originalSaveWorlds(); if (ok && EGOCore.online) schedule(); return ok; };
  window.addEventListener('ego-core-online', () => syncAll('connect'));
  // 2026-11-08: keep every device in step without a reload — every minute while connected,
  // and when the tab comes back to the screen. Quiet: errors of these runs are not toasted.
  if (typeof setInterval === 'function') setInterval(() => { if (window.EGOCore && EGOCore.online) syncAll('timer'); }, 60000);
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('visibilitychange', () => { if (!document.hidden && window.EGOCore && EGOCore.online) syncAll('timer'); });
  }

  window.EGOSync = Object.freeze({
    isSynced: id => EGOCore.online && !!map.characters[id],
    syncNow: () => syncAll('manual'),
    status: () => ({ characters: Object.keys(map.characters).length, worlds: Object.keys(map.worlds).length }),
  });
})();
