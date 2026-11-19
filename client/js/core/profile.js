/* The owner's profile (2026-11-06): opened from the avatar at the bottom of the left rail.
   Stored in EGO Core (one person per installation), so the PC and the laptop show the same.
   Name and form of address may reach the model (switch); «about me» never does. */
(() => {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const COLOURS = { violet: '#bea4ff', blue: '#8cc8ff', rose: '#ffadc8', mono: '#e8e8f0', teal: '#9ff0e0',
    ember: '#ff8a3d', gold: '#ffd36b', emerald: '#8ff0b4', ice: '#d6f1ff', crimson: '#ff7a86' };
  const PRONOUNS = [['he', 'Он'], ['she', 'Она'], ['they', 'Без рода'], ['other', 'Своё']];
  const core = () => window.EGOCore;
  const say = m => (typeof toast === 'function' ? toast(m) : null);
  let profile = null, avatarUrl = null;

  async function load() {
    if (!core() || !core().online) return null;
    try {
      const data = await core().api('GET', '/v1/profile');
      profile = data.profile;
      if (avatarUrl) { URL.revokeObjectURL(avatarUrl); avatarUrl = null; }
      if (data.has_avatar) {
        try { avatarUrl = URL.createObjectURL(await (await core().apiRaw('GET', '/v1/profile/avatar')).blob()); } catch { /* none */ }
      }
      paint();
      return data;
    } catch { return null; }
  }

  const initial = () => ((profile && (profile.display_name || profile.nickname)) || 'M').trim().charAt(0).toUpperCase() || 'M';
  // Idempotent: touching the DOM only when the face really changes. (Rewriting it every time
  // re-triggered the page observer below and froze the client in an endless loop.)
  function face(node) {
    if (!node) return;
    const colour = COLOURS[profile?.accent_colour] || COLOURS.violet;
    const key = (avatarUrl || initial()) + '|' + colour;
    if (node.dataset.pfKey === key) return;
    node.dataset.pfKey = key;
    node.classList.add('pf-face');
    node.style.setProperty('--pf', colour);
    node.innerHTML = avatarUrl ? `<img src="${avatarUrl}" alt="">` : esc(initial());
  }
  // The rail avatar and the avatar next to your own messages (at most once per frame).
  let queued = false;
  function paint() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; paintNow(); });
  }
  function paintNow() {
    if (!profile) return;
    const rail = document.querySelector('.user-avatar');
    if (rail) { face(rail); rail.title = (profile.display_name || 'Профиль') + ' — открыть профиль'; }
    document.querySelectorAll('.msg-avatar.human').forEach(face);
  }

  async function resized(file) {
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 12e6) throw new Error('Нужна картинка PNG, JPG или WebP до 12 МБ');
    const bitmap = await createImageBitmap(file);
    const side = Math.min(bitmap.width, bitmap.height), size = Math.min(384, side);
    const canvas = Object.assign(document.createElement('canvas'), { width: size, height: size });
    canvas.getContext('2d').drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, size, size);
    return await new Promise(r => canvas.toBlob(r, 'image/webp', .9)) || await new Promise(r => canvas.toBlob(r, 'image/jpeg', .9));
  }

  async function open() {
    if (!core() || !core().online) { say('Профиль хранится в EGO Core — подключитесь, чтобы открыть его'); return; }
    const data = await load();
    if (!data) { say('Не удалось открыть профиль'); return; }
    let draft = { ...profile }, pendingAvatar = null, dropAvatar = false;
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-backdrop"><div class="modal pf" role="dialog" aria-modal="true" aria-labelledby="pf-title">
      <section class="pf-stage">
        <div class="pf-ring"><div class="pf-avatar" id="pf-avatar"></div></div>
        <b class="pf-name" id="pf-name"></b><small class="pf-nick" id="pf-nick"></small>
        <div class="pf-photo"><label class="pf-link">Загрузить фото<input type="file" id="pf-file" accept="image/png,image/jpeg,image/webp" hidden></label><button type="button" class="pf-link" id="pf-drop">убрать</button></div>
        <div class="pf-colours" role="group" aria-label="Цвет профиля">${Object.entries(COLOURS).map(([k, v]) => `<button type="button" class="pf-colour" data-colour="${k}" style="--c:${v}" aria-label="${k}"></button>`).join('')}</div>
        <small class="pf-hint">Цвет профиля</small>
      </section>
      <section class="pf-form">
        <div class="pf-head"><h2 id="pf-title">Мой профиль</h2><button class="cs-cross" data-pf-close aria-label="Закрыть">×</button></div>
        <label>Как к тебе обращаться<input id="pf-display" maxlength="60" placeholder="Например: Алекс"></label>
        <label>Ник<input id="pf-nickname" maxlength="40" placeholder="Необязательно"></label>
        <div class="pf-label">Обращение в историях</div>
        <div class="pf-chips" role="radiogroup">${PRONOUNS.map(([k, n]) => `<button type="button" role="radio" class="pf-chip" data-pron="${k}">${n}</button>`).join('')}</div>
        <label class="pf-other" hidden>Как именно<input id="pf-pron-text" maxlength="40" placeholder="Например: «на вы»"></label>
        <label class="pf-switch"><input type="checkbox" id="pf-use"> <span>Персонажи знают моё имя и обращаются правильно<small>Модель получает только имя, ник и обращение.</small></span></label>
        <label>О себе<small class="pf-private">видишь только ты, модели не передаётся</small><textarea id="pf-about" maxlength="1000" rows="4"></textarea></label>
        <div class="pf-bottom"><button class="studio-outline" data-pf-close>Отмена</button><button class="studio-main" id="pf-save">Сохранить</button></div>
      </section></div></div>`;
    const box = root.querySelector('.pf'), $$ = s => box.querySelector(s);
    const closeAll = () => { root.innerHTML = ''; };
    root.querySelector('.modal-backdrop').onclick = e => { if (e.target === e.currentTarget) closeAll(); };
    box.querySelectorAll('[data-pf-close]').forEach(b => b.onclick = closeAll);
    $$('#pf-display').value = draft.display_name || ''; $$('#pf-nickname').value = draft.nickname || '';
    $$('#pf-pron-text').value = draft.pronouns_text || ''; $$('#pf-about').value = draft.description || '';
    $$('#pf-use').checked = draft.use_in_stories !== false;
    let preview = avatarUrl;
    function draw() {
      draft.display_name = $$('#pf-display').value; draft.nickname = $$('#pf-nickname').value;
      const colour = COLOURS[draft.accent_colour] || COLOURS.violet;
      box.style.setProperty('--pf', colour);
      $$('#pf-avatar').innerHTML = preview ? `<img src="${preview}" alt="">` : esc((draft.display_name || draft.nickname || 'M').trim().charAt(0).toUpperCase() || 'M');
      $$('#pf-name').textContent = draft.display_name || 'Без имени';
      $$('#pf-nick').textContent = draft.nickname ? '«' + draft.nickname + '»' : '';
      box.querySelectorAll('[data-colour]').forEach(b => b.classList.toggle('on', b.dataset.colour === draft.accent_colour));
      box.querySelectorAll('[data-pron]').forEach(b => { const on = b.dataset.pron === draft.pronouns; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); });
      $$('.pf-other').hidden = draft.pronouns !== 'other';
      $$('#pf-drop').hidden = !preview;
    }
    ['#pf-display', '#pf-nickname'].forEach(s => $$(s).addEventListener('input', draw));
    box.querySelectorAll('[data-colour]').forEach(b => b.onclick = () => { draft.accent_colour = b.dataset.colour; draw(); });
    box.querySelectorAll('[data-pron]').forEach(b => b.onclick = () => { draft.pronouns = b.dataset.pron; draw(); });
    $$('#pf-file').onchange = async e => {
      const file = e.target.files?.[0]; if (!file) return;
      try { pendingAvatar = await resized(file); dropAvatar = false; preview = URL.createObjectURL(pendingAvatar); draw(); }
      catch (err) { say(err.message); }
    };
    $$('#pf-drop').onclick = () => { pendingAvatar = null; dropAvatar = true; preview = null; draw(); };
    $$('#pf-save').onclick = async () => {
      const body = { display_name: $$('#pf-display').value.trim(), nickname: $$('#pf-nickname').value.trim(), pronouns: draft.pronouns || 'they',
        pronouns_text: $$('#pf-pron-text').value.trim(), description: $$('#pf-about').value.trim(), accent_colour: draft.accent_colour || 'violet',
        use_in_stories: $$('#pf-use').checked };
      try {
        await core().api('PUT', '/v1/profile', { profile: body });
        if (pendingAvatar) await core().apiRaw('PUT', '/v1/profile/avatar', pendingAvatar, pendingAvatar.type || 'image/webp');
        else if (dropAvatar) await core().api('DELETE', '/v1/profile/avatar');
        closeAll(); await load(); say('Профиль сохранён');
      } catch (e) { say(core().describe ? core().describe(e.code) : 'Не удалось сохранить профиль'); }
    };
    draw();
  }

  function bind() {
    const rail = document.querySelector('.user-avatar');
    if (rail && !rail.dataset.pfBound) {
      rail.dataset.pfBound = '1';
      rail.setAttribute('role', 'button'); rail.setAttribute('tabindex', '0'); rail.setAttribute('aria-label', 'Мой профиль');
      rail.addEventListener('click', open);
      rail.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    }
  }
  bind();
  window.addEventListener('ego-core-online', () => { bind(); load(); });
  const content = document.getElementById('content');
  if (content) new MutationObserver(paint).observe(content, { childList: true, subtree: true });
  window.EGOProfile = Object.freeze({ open, load });
})();
