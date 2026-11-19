/* «Enchant your prompt» (2026-11-06): next to the long fields of the world and character editors.
   Only on click: the local model (through EGO Core) improves the field — or writes it when empty —
   taking the other fields into account. The result goes straight into the field; «Откатить»
   brings back what was there before. One request at a time. */
(() => {
  'use strict';
  const core = () => window.EGOCore;
  const say = m => (typeof toast === 'function' ? toast(m) : null);
  const WORLD = { 'world-description': 'description', 'world-lore': 'lore', 'world-canon': 'canon' };
  const PERSON = ['description', 'personality', 'appearance', 'behavior', 'scenario', 'greeting'];
  let running = false;

  const val = (root, id) => (root.querySelector('#' + id)?.value || '').trim();

  function target(area) {
    const id = area.id || '';
    if (WORLD[id]) return { kind: 'world', field: WORLD[id] };
    const place = id.match(/^world-place-description-(\d+)$/);
    if (place) return { kind: 'world', field: 'location', place: place[1] };
    const person = id.match(/^cs-(\w+)$/);
    if (person && PERSON.includes(person[1])) return { kind: 'character', field: person[1] };
    return null;
  }

  function context(root, t) {
    if (t.kind === 'world') {
      const ctx = { name: val(root, 'world-name'), genre: val(root, 'world-genre'), subtitle: val(root, 'world-subtitle'),
        description: val(root, 'world-description'), lore: val(root, 'world-lore'), canon: val(root, 'world-canon') };
      if (t.place != null) ctx.place = val(root, 'world-place-name-' + t.place);
      return ctx;
    }
    const ctx = { name: val(root, 'cs-name'), role: val(root, 'cs-role'), world: val(root, 'cs-world'), setting: val(root, 'cs-setting'), tags: val(root, 'cs-tags') };
    PERSON.forEach(f => { ctx[f] = val(root, 'cs-' + f); });
    try {   // the character's world: its description and canon keep the card consistent
      const w = typeof egoWorlds !== 'undefined' && ctx.world ? egoWorlds.find(x => x.name && x.name.toLowerCase() === ctx.world.toLowerCase()) : null;
      if (w) { ctx.world_description = String(w.description || '').slice(0, 1500); ctx.world_canon = String(w.canon || '').slice(0, 1500); }
    } catch { /* no worlds */ }
    return ctx;
  }

  function put(area, text) {
    area.value = text;
    area.dispatchEvent(new Event('input', { bubbles: true }));
  }

  async function run(area, bar) {
    if (running) { say('Помощник уже работает над другим полем'); return; }
    if (!core() || !core().online) { say('Enchant работает через EGO Core — подключитесь к Core'); return; }
    const t = target(area), root = area.closest('#modal-root') || document;
    const before = area.value;
    const button = bar.querySelector('.ego-enchant-go'), undo = bar.querySelector('.ego-enchant-undo');
    running = true;
    button.disabled = true; button.classList.add('busy'); button.lastChild.textContent = 'Колдую…';
    area.classList.add('ego-enchanting'); area.readOnly = true;
    try {
      const ctx = context(root, t);
      delete ctx[t.field];
      const res = await core().api('POST', '/v1/enchant', { kind: t.kind, field: t.field, text: before.slice(0, 6000),
        context: Object.fromEntries(Object.entries(ctx).filter(([, v]) => v).map(([k, v]) => [k, v.slice(0, 6000)])) });
      if (!area.isConnected) return;
      put(area, res.text);
      area.dataset.enchantBefore = before;
      undo.hidden = false;
      say(before.trim() ? 'Поле улучшено — можно откатить' : 'Поле заполнено — можно откатить');
    } catch (e) {
      say(core().describe ? core().describe(e.code) : 'Помощник недоступен');
    } finally {
      running = false;
      area.classList.remove('ego-enchanting'); area.readOnly = false;
      button.disabled = false; button.classList.remove('busy'); button.lastChild.textContent = 'Enchant your prompt';
    }
  }

  function attach(area) {
    if (area.dataset.enchant || !target(area)) return;
    area.dataset.enchant = '1';
    const label = area.closest('label');
    const caption = label?.querySelector(':scope > span');
    const bar = document.createElement('span');
    bar.className = 'ego-enchant';
    bar.innerHTML = '<button type="button" class="ego-enchant-undo" hidden title="Вернуть текст, который был до помощника">↺ Откатить</button>'
      + '<button type="button" class="ego-enchant-go" title="Локальная модель улучшит поле или допишет его с учётом остальных полей"><span class="ego-enchant-star" aria-hidden="true">✦</span><span>Enchant your prompt</span></button>';
    if (caption) { caption.classList.add('ego-enchant-row'); caption.append(bar); }
    else area.insertAdjacentElement('beforebegin', bar);
    const undo = bar.querySelector('.ego-enchant-undo');
    bar.querySelector('.ego-enchant-go').addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); run(area, bar); });
    undo.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      if (area.dataset.enchantBefore == null) return;
      put(area, area.dataset.enchantBefore);
      delete area.dataset.enchantBefore;
      undo.hidden = true;
      say('Текст возвращён');
    });
  }

  function scan() {
    const root = document.getElementById('modal-root');
    if (!root || !root.firstElementChild) return;
    root.querySelectorAll('textarea[id^="world-"], textarea[id^="cs-"]').forEach(attach);
  }
  const root = document.getElementById('modal-root');
  if (root) new MutationObserver(scan).observe(root, { childList: true, subtree: true });
  window.EGOEnchant = Object.freeze({ scan });
})();
