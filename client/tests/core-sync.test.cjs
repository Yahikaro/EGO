// Run from the client folder: node tests/core-sync.test.cjs
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');

function harness() {
  const server = { characters: new Map(), worlds: new Map(), avatars: new Map() };
  const store = new Map();
  const toasts = [];
  const listeners = {};
  const ctx = {
    console, setTimeout, clearTimeout, JSON, Promise, Map, Object, Array, Error, toasts, Blob, atob, TextEncoder, Uint8Array, String,
    localStorage: { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) },
    addEventListener: (name, fn) => { listeners[name] = fn; },
    dispatch: name => listeners[name](),
    fetch: async url => ({ blob: async () => ({ type: 'image/jpeg', url }) }),
    FileReader: class { readAsDataURL() { this.result = 'data:image/jpeg;base64,FROMCORE'; this.onload(); } },
  };
  ctx.window = ctx;
  const record = (kind, id) => server[kind].get(id);
  const api = async (method, path, body) => {
    const [, , kind, rawId] = path.split('/'); const id = rawId && decodeURIComponent(rawId);
    if (method === 'GET') return { [kind]: [...server[kind].values()].map(r => ({ ...r, ...(kind === 'characters' ? { has_avatar: server.avatars.has(r.id), avatar: server.avatars.get(r.id) || '' } : {}) })) };
    if (method === 'PUT') {
      const old = record(kind, id);
      if (body.expected_version !== undefined && body.expected_version !== (old ? old.version : 0)) { const e = Error('VERSION_CONFLICT'); e.code = 'VERSION_CONFLICT'; throw e; }
      const { avatar, ...data } = body.data;
      const version = old && JSON.stringify(old.data) === JSON.stringify(data) ? old.version : (old ? old.version + 1 : 1);
      server[kind].set(id, { id, version, data }); return { id, version };
    }
    if (method === 'DELETE' && path.endsWith('/avatar')) { server.avatars.delete(id); return { id, deleted: true }; }
    if (method === 'DELETE') { server[kind].delete(id); server.avatars.delete(id); return { id, deleted: true }; }
  };
  const apiRaw = async (method, path) => {
    const id = decodeURIComponent(path.split('/')[3]);
    if (method === 'PUT') server.avatars.set(id, 'put' + (ctx.puts = (ctx.puts || 0) + 1));
    if (method === 'GET') ctx.downloads = (ctx.downloads || 0) + 1;
    return { blob: async () => ({ type: 'image/jpeg' }) };
  };
  vm.createContext(ctx);
  vm.runInContext(`
    var people = { lira: { name: 'Лира', role: 'Хранительница' } };
    var builtInKeys = new Set(['lira']);
    var egoWorlds = [{ id: 'ether', name: 'Эфир', locations: [] }];
    var page = 'chats';
    function storePeople() { localStorage.setItem('people', JSON.stringify(people)); return true; }
    function saveWorlds() { localStorage.setItem('worlds', JSON.stringify(egoWorlds)); return true; }
    function generic() {} function sidebar() {} function inspector() {}
    function toast(m) { toasts.push(m); }
    window.EGOCore = { online: true, api: null, apiRaw: null, describe: c => c };
  `, ctx);
  ctx.EGOCore.api = api; ctx.EGOCore.apiRaw = apiRaw;
  vm.runInContext(fs.readFileSync('js/core/core-sync.js', 'utf8'), ctx);
  return { ctx, server, run: code => vm.runInContext(code, ctx) };
}

(async () => {
  const { ctx, server, run } = harness();
  run(`people['user-mira'] = { name: 'Мира', role: 'Капитан', avatar: 'data:image/jpeg;base64,TE9DQUw=' }`);
  await ctx.EGOSync.syncNow();
  assert.deepEqual([...server.characters.keys()].sort(), ['lira', 'user-mira'], 'first connect uploads local cards');
  assert.ok(server.avatars.has('user-mira') && server.worlds.has('ether'), 'avatars and worlds uploaded');
  assert.equal(ctx.EGOSync.isSynced('lira'), true);

  server.characters.set('lira', { id: 'lira', version: 2, data: { name: 'Лира', role: 'Архивариус' } });
  await ctx.EGOSync.syncNow();
  assert.equal(run(`people.lira.role`), 'Архивариус', 'change made on Core is pulled');

  run(`people['user-mira'] = { ...people['user-mira'], role: 'Адмирал' }`);
  await ctx.EGOSync.syncNow();
  assert.equal(server.characters.get('user-mira').data.role, 'Адмирал', 'offline edit pushed when Core unchanged');
  assert.equal(server.characters.get('user-mira').data.avatar, undefined, 'avatar never stored inside card data');

  run(`people.lira = { ...people.lira, role: 'Локальная правка' }`);
  server.characters.set('lira', { id: 'lira', version: 3, data: { name: 'Лира', role: 'Правка на Core' } });
  await ctx.EGOSync.syncNow();
  assert.equal(run(`people.lira.role`), 'Правка на Core', 'conflict: Core wins');
  assert.ok(ctx.toasts.some(t => t.includes('заменены версией из EGO Core')), 'user is told about the conflict');

  run(`delete people['user-mira']`);
  await ctx.EGOSync.syncNow();
  assert.equal(server.characters.has('user-mira'), false, 'local delete removes the card from Core');

  server.characters.set('user-thorn', { id: 'user-thorn', version: 1, data: { name: 'Торн' } });
  server.avatars.set('user-thorn', 'jpeg');
  await ctx.EGOSync.syncNow();
  assert.equal(run(`people['user-thorn'].avatar`), 'data:image/jpeg;base64,FROMCORE', 'new card and avatar from Core');
  server.characters.delete('user-thorn');
  await ctx.EGOSync.syncNow();
  assert.equal(run(`'user-thorn' in people`), false, 'card deleted on Core disappears locally');

  // avatars stay equal on every device (2026-11-08)
  server.characters.set('user-ana', { id: 'user-ana', version: 1, data: { name: 'Ана' } });
  await ctx.EGOSync.syncNow();
  assert.equal(run(`people['user-ana'].avatar`), undefined, 'no picture anywhere: the placeholder is used');
  server.avatars.set('user-ana', 'pic-1');
  await ctx.EGOSync.syncNow();
  assert.equal(run(`people['user-ana'].avatar`), 'data:image/jpeg;base64,FROMCORE', 'picture added on another device arrives');
  const downloads = ctx.downloads;
  await ctx.EGOSync.syncNow();
  assert.equal(ctx.downloads, downloads, 'unchanged picture is not downloaded again (' + ctx.downloads + ' vs ' + downloads + ')');
  server.avatars.set('user-ana', 'pic-2');
  await ctx.EGOSync.syncNow();
  assert.equal(ctx.downloads, downloads + 1, 'picture changed on another device is downloaded again');
  server.avatars.delete('user-ana');
  await ctx.EGOSync.syncNow();
  assert.equal(run(`'avatar' in people['user-ana']`), false, 'picture removed on another device disappears here');
  run(`people['user-ana'].avatar = 'data:image/jpeg;base64,MINE'`);
  await ctx.EGOSync.syncNow();
  assert.ok(server.avatars.has('user-ana'), 'picture added here is uploaded');
  run(`delete people['user-ana'].avatar`);
  await ctx.EGOSync.syncNow();
  assert.equal(server.avatars.has('user-ana'), false, 'picture removed here is removed on the server');
  assert.equal(run(`people['user-ana'].avatar`), undefined, 'and is not brought back');

  run(`egoWorlds = [...egoWorlds, { id: 'w-new', name: 'Новый мир', locations: [] }]`);
  await ctx.EGOSync.syncNow();
  assert.ok(server.worlds.has('w-new'), 'new world pushed');
  console.log('PASS: 11 core-sync tests (migration, pull, push, conflict, deletes both ways, avatars, worlds)');
})().catch(error => { console.error(error); process.exit(1); });
