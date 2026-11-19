/* EGO chat memory window (stage 4h).
   Core keeps per-chat facts and a summary and feeds them to the model. Here the user
   sees them, adds their own, edits, pins and deletes; automatic extraction runs every
   N new messages (default 10) between replies. Opens only by button. */
(() => {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const core = () => window.EGOCore;
  const chatNow = () => (typeof current === 'function' ? current() : null);
  const KIND = { summary: 'Конспект', fact: 'Авто', manual: 'Вручную' };
  const say = msg => (typeof toast === 'function' ? toast(msg) : null);
  const errorText = e => (core() && core().describe ? core().describe(e.code || e.message) : 'Ошибка');
  let root = null, pollTimer = null;

  function close() {
    clearTimeout(pollTimer);
    if (root) { root.remove(); root = null; }
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) { if (e.key === 'Escape') close(); }

  async function open() {
    const chat = chatNow();
    if (!core() || !core().online) { say('Память чата доступна при подключении к EGO Core'); return; }
    if (!chat) { say('Сначала откройте историю'); return; }
    close();
    root = document.createElement('div');
    root.className = 'modal-backdrop ego-memory-backdrop';
    root.innerHTML = '<div class="modal ego-memory" role="dialog" aria-modal="true" aria-labelledby="ego-memory-title"><p>Загрузка…</p></div>';
    root.addEventListener('mousedown', e => { if (e.target === root) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(root);
    if (!chat.coreChatId) { draw(chat, null); return; }
    await refresh(chat);
  }

  async function refresh(chat) {
    try { draw(chat, await core().api('GET', `/v1/chats/${chat.coreChatId}/memory`)); }
    catch (e) { draw(chat, null, errorText(e)); }
  }

  const when = t => t ? new Date(t * 1000).toLocaleString((window.EGOI18n && EGOI18n.lang) || 'ru', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
  function lastText(view) {
    if (!view.last_run) return 'Автоматическое обновление ещё не запускалось.';
    const r = view.last_result || '';
    const what = r.startsWith('facts:') ? `новых фактов: ${r.slice(6)}` : r === 'summary' ? 'старые факты сжаты в конспект' : 'не удалось (модель была занята или ответила не по формату)';
    return `Последнее обновление ${when(view.last_run)} — ${what}.`;
  }

  function draw(chat, view, error) {
    if (!root) return;
    const modal = root.querySelector('.ego-memory');
    if (!view) {
      modal.innerHTML = `<div class="modal-top"><div class="overline">EGO · MEMORY</div><button class="subtle-btn" data-m="close" aria-label="Закрыть">✕</button></div>
        <h2 id="ego-memory-title">Память чата</h2>
        <p>${esc(error || 'Память появится, когда в этой истории будет первый ответ модели.')}</p>`;
      modal.querySelector('[data-m="close"]').onclick = close;
      return;
    }
    const s = view.settings;
    const items = view.items.map(i => `
      <li class="ego-mem-item ${i.kind === 'summary' ? 'ego-mem-summary' : ''}" data-id="${esc(i.id)}">
        <div class="ego-mem-meta"><span class="ego-mem-kind">${KIND[i.kind] || i.kind}${i.edited_by === 'user' && i.kind !== 'manual' ? ' · изменено' : ''}</span>
          <button class="ego-mem-pin ${i.pinned ? 'on' : ''}" data-act="pin" title="${i.pinned ? 'Открепить' : 'Закрепить: всегда в памяти модели'}" aria-pressed="${i.pinned}">${i.pinned ? '★' : '☆'}</button>
          <button data-act="edit" title="Изменить">✎</button><button data-act="delete" title="Удалить">✕</button></div>
        <p class="ego-mem-text">${esc(i.text)}</p></li>`).join('');
    modal.innerHTML = `<div class="modal-top"><div class="overline">EGO · MEMORY</div><button class="subtle-btn" data-m="close" aria-label="Закрыть">✕</button></div>
      <h2 id="ego-memory-title">Память чата</h2>
      <p>Модель помнит эти факты в этой истории — и только в ней. Закреплённые ★ попадают в память всегда.</p>
      <div class="ego-mem-settings">
        <label class="ego-mem-switch"><input type="checkbox" data-m="enabled" ${s.enabled ? 'checked' : ''}> Запоминать автоматически</label>
        <label class="ego-mem-interval">каждые <input type="number" min="4" max="200" step="1" value="${s.interval}" data-m="interval" aria-label="Интервал в сообщениях"> сообщений</label>
        <button class="primary-small" data-m="extract" ${view.new_messages ? '' : 'disabled'} title="${view.new_messages ? 'Извлечь факты из новых сообщений сейчас' : 'Нет новых сообщений'}">Обновить сейчас</button>
      </div>
      <p class="ego-mem-status">${esc(lastText(view))} Новых сообщений с тех пор: ${view.new_messages}.${view.archived_count ? ` В архиве (уже в конспекте): ${view.archived_count}.` : ''}</p>
      <ul class="ego-mem-list">${items || '<li class="ego-mem-empty">Пока пусто. Факты появятся автоматически или добавьте свой ниже.</li>'}</ul>
      <form class="ego-mem-add"><textarea maxlength="300" rows="2" placeholder="Например: «Пользователя зовут Алекс» или «Мы договорились встретиться у маяка»" aria-label="Новый факт"></textarea>
        <div class="ego-mem-add-row"><label><input type="checkbox" data-m="pin-new"> Закрепить</label><button class="primary-small" type="submit">Добавить</button></div></form>`;
    const id = chat.coreChatId;
    const act = async (fn, okText) => { try { const next = await fn(); if (next) draw(chat, next); if (okText) say(okText); } catch (e) { say(errorText(e)); } };
    modal.querySelector('[data-m="close"]').onclick = close;
    modal.querySelector('[data-m="enabled"]').onchange = e => act(() => core().api('PUT', `/v1/chats/${id}/memory/settings`, { enabled: e.target.checked }),
      e.target.checked ? 'Автоматическая память включена' : 'Автоматическая память выключена');
    modal.querySelector('[data-m="interval"]').onchange = e => {
      const n = Math.max(4, Math.min(200, Math.round(Number(e.target.value) || 10)));
      act(() => core().api('PUT', `/v1/chats/${id}/memory/settings`, { interval: n }), `Обновление памяти каждые ${n} сообщений`);
    };
    modal.querySelector('[data-m="extract"]').onclick = async e => {
      e.target.disabled = true; e.target.textContent = 'Обновляется…';
      try { await core().api('POST', `/v1/chats/${id}/memory/extract`); }
      catch (err) { say(errorText(err)); e.target.textContent = 'Обновить сейчас'; e.target.disabled = false; return; }
      const started = view.last_run || 0, deadline = Date.now() + 90000;
      const poll = async () => {
        if (!root) return;
        try {
          const next = await core().api('GET', `/v1/chats/${id}/memory`);
          if ((next.last_run || 0) !== started) { draw(chat, next); say('Память обновлена'); return; }
        } catch { /* keep polling */ }
        if (Date.now() < deadline) pollTimer = setTimeout(poll, 1500);
      };
      pollTimer = setTimeout(poll, 1200);
    };
    modal.querySelectorAll('.ego-mem-item').forEach(li => {
      const item = view.items.find(i => i.id === li.dataset.id);
      li.querySelector('[data-act="pin"]').onclick = () => act(() => core().api('PUT', `/v1/chats/${id}/memory/${item.id}`, { pinned: !item.pinned }));
      li.querySelector('[data-act="delete"]').onclick = () => act(() => core().api('DELETE', `/v1/chats/${id}/memory/${item.id}`), 'Удалено из памяти');
      li.querySelector('[data-act="edit"]').onclick = () => {
        const limit = item.kind === 'summary' ? 1500 : 300;
        li.querySelector('.ego-mem-text').outerHTML = `<form class="ego-mem-edit"><textarea maxlength="${limit}" rows="${item.kind === 'summary' ? 6 : 2}" aria-label="Текст записи">${esc(item.text)}</textarea>
          <div class="ego-mem-add-row"><button type="button" class="ego-link-btn" data-act="cancel">Отмена</button><button class="primary-small" type="submit">Сохранить</button></div></form>`;
        const form = li.querySelector('.ego-mem-edit');
        form.querySelector('textarea').focus();
        form.querySelector('[data-act="cancel"]').onclick = () => draw(chat, view);
        form.onsubmit = ev => { ev.preventDefault(); const text = form.querySelector('textarea').value.trim(); if (text) act(() => core().api('PUT', `/v1/chats/${id}/memory/${item.id}`, { text }), 'Сохранено'); };
      };
    });
    modal.querySelector('.ego-mem-add').onsubmit = ev => {
      ev.preventDefault();
      const text = ev.target.querySelector('textarea').value.trim();
      if (!text) return;
      act(() => core().api('POST', `/v1/chats/${id}/memory`, { text, pinned: ev.target.querySelector('[data-m="pin-new"]').checked }), 'Добавлено в память');
    };
  }

  /* ---- entry points: topbar button and inspector section ------------------------------ */
  function placeButton() {
    const bar = document.querySelector('.top-right');
    if (!bar || document.getElementById('ego-memory-btn')) return;
    const button = document.createElement('button');
    button.className = 'subtle-btn';
    button.id = 'ego-memory-btn';
    button.title = 'Память чата';
    button.setAttribute('aria-label', 'Память чата');
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4a3 3 0 0 0-3 3v.2A3 3 0 0 0 4 10a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3h0V4z"/><path d="M15 4a3 3 0 0 1 3 3v.2A3 3 0 0 1 20 10a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3h0V4z"/></svg>';
    button.onclick = open;
    bar.insertBefore(button, document.getElementById('toggle-inspector'));
  }
  function sync() {
    placeButton();
    const button = document.getElementById('ego-memory-btn');
    if (button) button.hidden = !(core() && core().online && chatNow());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();
  window.addEventListener('ego-core-online', sync);
  const content = document.getElementById('content');
  if (content) new MutationObserver(sync).observe(content, { childList: true });

  window.EGOMemory = Object.freeze({ open, close });
})();
