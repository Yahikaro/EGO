// Client languages: dictionary lookup rules (exact, patterns, glued pieces, typed text)
// and that every built dictionary covers the same keys with the same placeholders.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.join(__dirname, '..');
function load(lang) {
  const written = [];
  const store = { 'ego-lang': lang };
  const ctx = {
    navigator: { languages: ['fr-FR'], language: 'fr-FR' },
    localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    document: { documentElement: {}, write: s => written.push(s), body: null, addEventListener() {} },
    location: { reload() {} },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'js/core/i18n.js'), 'utf8'), ctx);
  return { ctx, written, store };
}

// Russian: nothing is loaded or translated.
let { ctx, written } = load('ru');
assert.strictEqual(ctx.EGOI18n.lang, 'ru');
assert.strictEqual(written.length, 0);
assert.strictEqual(ctx.EGOI18n.t('Сохранить'), 'Сохранить');

// Unknown saved value falls back to the browser language.
({ ctx, written } = load('xx'));
assert.strictEqual(ctx.EGOI18n.lang, 'fr');
assert.ok(written[0].includes('js/i18n/fr.js'));

({ ctx } = load('en'));
ctx.EGOI18n.install({
  'Сохранить': 'Save', 'Твои истории ·': 'Your stories ·', 'Персонаж': 'Character', 'демонстрация': 'demo',
  'Открыть мир': 'Open world', 'Эфир': 'Aether', 'Неверный PIN. Осталось попыток: {0}.': 'Wrong PIN. Attempts left: {0}.',
  'Удалить мир «{0}»? Персонажи и чаты не будут удалены.': 'Delete the world «{0}»? Characters and chats will not be deleted.',
  'Огонь в камине вздрагивает, когда ты переступаешь порог библиотеки.': 'The fire in the hearth flickers as you cross the library\'s threshold.',
});
const t = ctx.EGOI18n.t;
assert.strictEqual(t('Сохранить'), 'Save');
assert.strictEqual(t('  Сохранить '), 'Save');
assert.strictEqual(t('Твои истории · 3'), 'Your stories · 3');
assert.strictEqual(t('Неверный PIN. Осталось попыток: {0}.', 2), 'Wrong PIN. Attempts left: 2.');
assert.strictEqual(t('Удалить мир «Эфир»? Персонажи и чаты не будут удалены.'), 'Delete the world «Aether»? Characters and chats will not be deleted.');
assert.strictEqual(t('Персонаж · демонстрация'), 'Character · demo');
assert.strictEqual(t('Открыть мир Эфир'), 'Open world Aether');
assert.strictEqual(t('Открыть мир Лунария'), 'Open world Лунария');
assert.ok(t('Огонь в камине вздрагивает').startsWith('The fire'));
assert.strictEqual(t('Совсем другой текст пользователя'), 'Совсем другой текст пользователя');

// Built dictionaries: same keys everywhere, placeholders kept.
const dicts = {};
for (const lang of ['en', 'fr', 'es', 'de']) {
  const src = fs.readFileSync(path.join(root, `js/i18n/${lang}.js`), 'utf8');
  const json = src.slice(src.indexOf('EGOI18n.install(') + 16, src.lastIndexOf(')'));
  dicts[lang] = JSON.parse(json);
}
const keys = Object.keys(dicts.en).sort().join('\n');
for (const lang of ['fr', 'es', 'de']) assert.strictEqual(Object.keys(dicts[lang]).sort().join('\n'), keys, lang);
for (const [lang, dict] of Object.entries(dicts)) {
  for (const [ru, value] of Object.entries(dict)) {
    const holes = s => (s.match(/\{\d+\}/g) || []).sort().join();
    assert.strictEqual(holes(value), holes(ru), `${lang}: ${ru}`);
    assert.ok(!/[А-Яа-яЁё]/.test(value) || ru === 'Язык · Language', `${lang} has Cyrillic: ${value}`);
  }
}
console.log(`PASS: i18n tests (lookup rules, ${Object.keys(dicts.en).length} keys in 4 languages)`);
