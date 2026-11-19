/* The story screen — one module (2026-11-06).
   Before, three layers drew it: app.js rebuilt the whole story on every change, advanced-chat.js
   added the scene bar on top, core-bridge.js re-bound buttons (regenerate/continue, truncated
   banner) and marked old rows so they would not replay their entrance. Every change rebuilt
   everything, which is why streaming flickered and tabs could be overwritten.

   Now: the screen is built once per story. Each render compares every message with what is on
   screen (a key of its content and state) and replaces only rows that changed; new rows are
   appended (and only they animate). One delegated click handler serves every button, so nothing
   is re-bound. While a reply streams, core-bridge calls EGOChatView.paint() to change just the
   text of that reply. Nothing is drawn while another tab is open. */
(() => {
  'use strict';
  const content = () => document.getElementById('content');
  const scene = () => window.EGOScene || null;            // members, chapters, bookmarks (advanced-chat.js)
  const core = () => window.EGOCore || null;
  const online = () => !!(core() && core().online);
  const busy = () => !!(core() && core().busy);
  const onChats = () => typeof page === 'undefined' || page === 'chats';

  function emptyPage(box) {
    box.innerHTML = '<div class="empty-page"><div class="empty-mark">✧</div><h1>История начинается здесь</h1>' +
      '<p>Создай новую историю или выбери персонажа, чтобы открыть пространство для воображения.</p>' +
      '<button class="primary-small" type="button" data-new>Новая история ↗</button></div>';
  }

  function introText(chat, p) {
    return `${p.neutral ? 'Свободная история без персонажа' : `История с персонажем ${esc(p.name)} · ${esc(p.world)}`}. ` +
      (online() ? 'Ответы генерирует локальная модель через EGO Core.' : 'Это демонстрационная сцена — твои сообщения сохраняются локально.');
  }

  /* one message row: the classic markup plus the online controls, as a DOM element */
  function rowFor(chat, m, i, p, lastAi) {
    const holder = document.createElement('div');
    holder.innerHTML = messageHTML(m, i, p);
    const row = holder.firstElementChild;
    row.dataset.messageIndex = String(i);
    // 2026-11-08: «Озвучить» under every model reply. Base only: a click fires `ego:speak`;
    // the TTS integration listens to it (see window.EGOSpeak below).
    if (m.by === 'ai') {
      const speak = document.createElement('button');
      speak.type = 'button'; speak.dataset.speak = String(i); speak.title = 'Озвучить'; speak.setAttribute('aria-label', 'Озвучить');
      speak.innerHTML = svg('speaker');
      const anchor = row.querySelector('[data-bookmark]');
      anchor ? anchor.before(speak) : (row.querySelector('.msg-toolbar') || row).append(speak);
    }
    if (online()) {
      const regen = row.querySelector('[data-regen]');
      if (regen) {
        if (i !== lastAi || m.by !== 'ai') regen.remove();
        else {
          regen.title = 'Перегенерировать ответ';
          const more = document.createElement('button');
          more.type = 'button'; more.dataset.continue = String(i); more.title = 'Продолжить ответ'; more.innerHTML = svg('arrowup');
          regen.after(more);
        }
      }
      row.querySelectorAll('[data-edit]').forEach(b => { b.title = 'Редактировать (только локально)'; });
      if (i === lastAi && m.by === 'ai' && m.truncated && !busy()) {
        const banner = document.createElement('div');
        banner.className = 'ego-truncated';
        banner.innerHTML = '<span>Ответ обрезан по лимиту длины.</span><button type="button" class="ego-continue">Продолжить</button>';
        (row.querySelector('.msg-body') || row).append(banner);
      }
    }
    if (m.bookmarked) row.classList.add('is-bookmarked');
    return row;
  }

  function keyOf(m, i, p, lastAi) {
    return JSON.stringify([m.by, m.text, m.blocks, !!m.truncated, !!m.bookmarked, !!m.pending, !!m.stopped,
      p.name, p.colour, p.avatar || '', online(), m.by === 'ai' && i === lastAi, m.by === 'ai' && i === lastAi && busy()]);
  }

  const nearBottom = box => box.scrollHeight - box.scrollTop - box.clientHeight < 160;

  function render() {
    if (!onChats()) return;                             // never draw over another tab
    const box = content();
    if (!box) return;
    const chat = current();
    if (!chat) { emptyPage(box); return; }
    const p = personOf(chat);
    let root = box.querySelector(':scope > .story');
    let fresh = false;
    if (!root || root.dataset.chat !== chat.id) {
      box.innerHTML = '<div class="story"><div class="ego-scene-slot"></div><div class="chapter"><span>Глава I · Начало</span></div>' +
        '<div class="scene-intro"><span class="scene-ornament">✧</span><h1></h1><p></p></div><div id="messages"></div></div>';
      root = box.firstElementChild;
      root.dataset.chat = chat.id;
      fresh = true;
    }
    const follow = fresh || nearBottom(box);
    // scene bar (members, chapters, note) — only when it changed
    const slot = root.querySelector('.ego-scene-slot');
    const bar = scene() ? scene().markup(chat) : '';
    if (slot.dataset.html !== bar) { slot.innerHTML = bar; slot.dataset.html = bar; }
    const title = root.querySelector('.scene-intro h1'), intro = root.querySelector('.scene-intro p');
    if (title.textContent !== chat.title) title.textContent = chat.title;
    const text = introText(chat, p);
    if (intro.dataset.html !== text) { intro.innerHTML = text; intro.dataset.html = text; }
    // messages: replace changed rows, append new, drop extra
    const list = root.querySelector('#messages');
    const lastAi = chat.messages.length - 1;
    let appended = false;
    chat.messages.forEach((m, i) => {
      const key = keyOf(m, i, p, lastAi);
      const old = list.children[i];
      if (old && old.dataset.key === key) return;
      const row = rowFor(chat, m, i, p, lastAi);
      row.dataset.key = key;
      if (old) { row.classList.add('seen'); old.replaceWith(row); }      // changed in place: no entrance
      else { if (!fresh) appended = true; list.append(row); }
    });
    while (list.children.length > chat.messages.length) list.lastElementChild.remove();
    if (fresh || (follow && appended) || (follow && busy())) box.scrollTop = box.scrollHeight;
  }

  /* streaming: only the reply's text */
  function paint(chat, index) {
    if (!onChats() || current() !== chat) return;
    const box = content();
    const row = box && box.querySelector(`.story[data-chat="${CSS.escape(chat.id)}"] #messages > [data-message-index="${index}"]`);
    if (!row) { render(); return; }
    const card = row.querySelector('.msg-card');
    if (!card) { render(); return; }
    const follow = nearBottom(box);
    card.innerHTML = (chat.messages[index].blocks || []).map(makeBlock).join('');
    row.dataset.key = '';                               // the final render redraws it once
    if (follow) box.scrollTop = box.scrollHeight;
  }

  /* one handler for every button on the screen */
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast('Текст скопирован'); } catch { fallbackCopy(text); }
  }
  function onClick(e) {
    if (!onChats() || !e.target.closest) return;
    const t = e.target;
    const chat = current();
    if (t.closest('[data-new]')) { newChat(); return; }
    if (!chat) return;
    const S = scene();
    const tool = t.closest('#ac-group, #ac-chapters, #ac-new-chapter, #ac-scene, #ac-bookmarks');
    if (tool && S) {
      ({ 'ac-group': S.groupModal, 'ac-chapters': S.chapterModal, 'ac-new-chapter': S.newChapter, 'ac-scene': S.sceneModal, 'ac-bookmarks': S.bookmarksModal })[tool.id]();
      return;
    }
    const jump = t.closest('[data-ac-jump]');
    if (jump && S) { S.jumpTo(Number(jump.dataset.acJump)); return; }
    const b = t.closest('[data-copy], [data-edit], [data-regen], [data-continue], [data-bookmark], [data-speak], .ego-continue');
    if (!b) return;
    if (b.matches('.ego-continue')) { core() && core().generate(chat, 'continue'); return; }
    if (b.dataset.speak != null) {
      const index = Number(b.dataset.speak), text = plain(chat.messages[index]);
      const handled = window.EGOSpeak && typeof window.EGOSpeak.speak === 'function' && window.EGOSpeak.speak({ chat, index, text, button: b });
      if (!handled) toast('Озвучка скоро');
      return;
    }
    if (b.dataset.copy != null) { copyText(plain(chat.messages[Number(b.dataset.copy)])); return; }
    if (b.dataset.edit != null) { editMessage(Number(b.dataset.edit)); return; }
    if (b.dataset.continue != null) { core() && core().generate(chat, 'continue'); return; }
    if (b.dataset.regen != null) {
      if (online()) { core().generate(chat, 'regenerate'); return; }
      const i = Number(b.dataset.regen);       // the demo without Core: a canned alternative line
      chat.messages[i] = { by: 'ai', blocks: [['action', 'Лира задумчиво смотрит в сторону и на секунду замолкает.'], ['spoken', '— У каждой истории есть несколько вариантов. Может, стоит выбрать другой путь?']] };
      chat.modified = Date.now(); save(); render(); toast('Демонстрационная реплика изменена');
      return;
    }
    if (b.dataset.bookmark != null) {
      const msg = chat.messages[Number(b.dataset.bookmark)];
      msg.bookmarked = !msg.bookmarked; save(); render();
      toast(msg.bookmarked ? 'Закладка добавлена' : 'Закладка удалена');
    }
  }
  const box = content();
  if (box) box.addEventListener('click', onClick);

  /* TTS hook: set window.EGOSpeak = { speak({chat, index, text, button}) { …; return true; } } */
  window.EGOSpeak = window.EGOSpeak || { speak: null };
  window.story = story = render;                     // every existing caller now draws through here
  window.EGOChatView = Object.freeze({ render, paint });
  if (onChats()) render();
})();
