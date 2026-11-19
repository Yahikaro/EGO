// Run from the client folder: node tests/core-bridge.test.cjs
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let offlineSends = 0;
const sendButton = { onclick: null };
const ctx = {
  window: {}, console, setTimeout, setInterval, URLSearchParams, Date, JSON,
  location: { protocol: 'file:', hash: '', pathname: '/index.html', search: '' },
  history: { replaceState() {} }, sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { querySelector: () => null },
  crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
  addEventListener() {},
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(`
  var people = { lira: { name: 'Лира', age: '27', role: 'Хранительница', world: 'Эфир', setting: 'Библиотека', description: 'Ироничная.' } };
  var chats = [], active = null;
  function $(id) { return id === 'send' ? sendButton : { value: '', textContent: '' }; }
  function send() { offlineSend(); }
  function bindMessages() {}
  function save() {} function story() {} function sidebar() {} function inspector() {} function current() { return null; }
  function toast() {} function svg() { return ''; }
`, Object.assign(ctx, { sendButton, offlineSend: () => { offlineSends++; } }));
vm.runInContext(fs.readFileSync('js/core/core-bridge.js', 'utf8'), ctx);
const core = ctx.EGOCore;

assert.equal(core.online, false, 'file:// stays the offline visual preview');
ctx.send();
assert.equal(offlineSends, 1, 'offline send keeps its behaviour');

const text = '*Я улыбаюсь и закрываю книгу.* Привет.\n\nКак дела? *Смотрю на тебя.*';
const blocks = core.toBlocks(text);
assert.deepEqual(JSON.parse(JSON.stringify(blocks)), [
  ['action', 'Я улыбаюсь и закрываю книгу.'], ['spoken', 'Привет.'], ['spoken', 'Как дела?'], ['action', 'Смотрю на тебя.']]);
assert.deepEqual(JSON.parse(JSON.stringify(core.toBlocks('Слова *и действие в процессе'))),
  [['spoken', 'Слова'], ['action', 'и действие в процессе']], 'unclosed action while streaming');
assert.equal(core.fromBlocks({ by: 'ai', blocks }), '*Я улыбаюсь и закрываю книгу.*\n\nПривет.\n\nКак дела?\n\n*Смотрю на тебя.*');
assert.deepEqual(JSON.parse(JSON.stringify(core.toBlocks(core.fromBlocks({ by: 'ai', blocks })))), JSON.parse(JSON.stringify(blocks)), 'round trip');

const single = core.characterFor({ person: 'lira', messages: [{ by: 'ai', blocks: [['spoken', 'Привет.']] }] });
assert.equal(single.name, 'Лира');
assert.match(single.description, /Возраст: 27/);
assert.match(single.description, /Начало сцены/);
const linked = core.characterFor({ person: 'lira', coreChatId: 'x', messages: [{ by: 'ai', blocks: [['spoken', 'Привет.']] }] });
assert.doesNotMatch(linked.description, /Начало сцены/, 'opening is sent only before Core has the chat');
assert.ok(single.description.length <= 4000);
const neutral = core.characterFor({ person: '', messages: [] });
assert.equal(neutral.name, 'Рассказчик', 'story without a character gets a narrator, not Lira');
assert.doesNotMatch(neutral.description, /Лира/);
console.log('PASS: 10 core-bridge tests (offline mode, blocks, streaming, round trip, character profile, no character)');
