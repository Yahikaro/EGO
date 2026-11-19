/* Media lab (2026-11-08): image generation through Core -> ComfyUI (POST /v1/media/images), video later.
   A detailed prompt, model/LoRA/sampler settings and a folding panel of appearance presets (style, clothes, pose, body, appearance
   age, ethnicity, hair). The presets stay in the client: later they will be checked
   against what the image/video model produced, and the prompt will be improved by the language model.
   Voice lives under chat replies (chat-view.js). */
(() => {
  'use strict';
  const KEY = 'ego-media-presets-v1';
  const GROUPS = [
    ['gender', 'Пол', ['Женский', 'Мужской']],
    ['style', 'Стиль', ['Фотореализм', 'Кинокадр', 'Студийный портрет', 'Аниме', 'Цифровой арт', 'Масляная живопись']],
    ['shot', 'Кадр', ['Портрет', 'По пояс', 'В полный рост', 'Крупный план']],
    ['clothes', 'Одежда', ['Повседневная', 'Деловая', 'Вечернее платье', 'Спортивная', 'Доспехи', 'Фэнтези-наряд', 'Униформа']],
    ['pose', 'Поза', ['Стоя', 'Сидя', 'Лёжа', 'Через плечо', 'В движении']],
    ['body', 'Телосложение', ['Стройное', 'Спортивное', 'Среднее', 'Пышное', 'Мускулистое']],
    ['height', 'Рост', ['Невысокий', 'Средний', 'Высокий']],
    ['age', 'Возраст внешности', ['20–29 лет', '30–39 лет', '40–49 лет', '50+ лет']],
    ['race', 'Внешность', ['Европейская', 'Восточноазиатская', 'Африканская', 'Латиноамериканская', 'Ближневосточная', 'Южноазиатская', 'Смешанная', 'Эльф', 'Демон']],
    // Hairstyle instead of length (2026-11-08); example pictures for each will be generated later.
    ['hairstyle', 'Причёска', ['Короткая стрижка', 'Каре', 'Длинные распущенные', 'Хвост', 'Пучок', 'Косы', 'Кудри', 'Бритая голова']],
    ['hairColour', 'Цвет волос', ['Чёрные', 'Каштановые', 'Русые', 'Блонд', 'Рыжие', 'Седые', 'Яркие']],
  ];
  const VIDEO_GROUPS = [
    ['camera', 'Камера', ['Неподвижная', 'Медленный наезд', 'Облёт', 'С руки']],
    ['length', 'Длительность', ['3 с', '5 с', '8 с']],
  ];
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } };
  let chosen = load(), tab = 'image', mode = 'form', step = 0, dir = 1, visited = new Set();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(chosen)); } catch { /* storage blocked */ } };
  const t = s => (window.EGOI18n ? EGOI18n.t(s) : s);

  const chips = groups => groups.map(([key, label, items]) => `<div class="ml-group"><span>${label}</span><div class="ml-chips">${
    items.map(item => `<button type="button" class="ml-chip${chosen[key] === item ? ' on' : ''}" data-g="${key}" data-v="${item}">${item}</button>`).join('')}</div></div>`).join('');
  const summary = () => [...GROUPS, ...(tab === 'video' ? VIDEO_GROUPS : [])].map(([k]) => chosen[k]).filter(Boolean).map(t).join(' · ');

  const byKey = () => Object.fromEntries([...GROUPS, ...VIDEO_GROUPS].map(g => [g[0], g]));
  /* Quick setup order: gender, ethnicity, age — then head to toe — then the picture itself. */
  function steps() {
    const list = ['gender', 'race', 'age', 'hairstyle', 'hairColour', 'height', 'body'];
    list.push('clothes', 'pose', 'shot', 'style');
    if (tab === 'video') list.push('camera', 'length');
    return list;
  }
  function wizard(box) {
    const list = steps();
    step = Math.max(0, Math.min(step, list.length - 1));
    visited.add(list[step]);
    const key = list[step], last = step === list.length - 1;
    const dots = list.map((k, i) => `<span class="ml-dot${i === step ? ' now' : visited.has(k) ? ' seen' : ''}"></span>`).join('');
    const groupOf = byKey()[key];
    const body = `<h3>${groupOf[1]}</h3><div class="ml-tiles">${groupOf[2].map((item, n) =>
          // Example picture slot: assets/presets/<group>/<number>.webp (added later); until then a neutral placeholder.
          `<button type="button" class="ml-chip ml-tile${chosen[key] === item ? ' on' : ''}" data-g="${key}" data-v="${item}">
            <span class="ml-tile-pic"><img src="assets/presets/${key}/${n + 1}.webp" alt="" loading="lazy"></span><span class="ml-tile-name">${item}</span></button>`).join('')}</div>`;
    box.querySelector('.ml-body').innerHTML = `<div class="ml-wizard ${dir > 0 ? 'ml-in-right' : 'ml-in-left'}">
      <div class="ml-wiz-top"><span class="ml-wiz-title">Быстрая настройка</span><span class="ml-wiz-count">${step + 1} / ${list.length}</span></div>
      <div class="ml-dots" aria-hidden="true">${dots}</div>
      <div class="ml-wiz-step">${body}</div>
      <div class="ml-wiz-nav"><button type="button" class="ml-wiz-back">${step ? 'Назад' : 'К промпту'}</button>
        <button type="button" class="studio-main ml-wiz-next">${last ? 'Готово' : chosen[key] ? 'Далее' : 'Пропустить'}</button></div></div>`;
    box.querySelectorAll('.ml-tile-pic img').forEach(i => i.addEventListener('error', () => i.remove()));   // no example yet: keep the placeholder
    const go = d => { dir = d; step += d; wizard(box); };
    box.querySelector('.ml-wiz-back').onclick = () => { if (step) go(-1); else { mode = 'form'; dir = -1; draw(box); } };
    box.querySelector('.ml-wiz-next').onclick = () => { if (last) { mode = 'form'; dir = -1; draw(box); } else go(1); };
    box.querySelectorAll('.ml-chip').forEach(b => b.onclick = () => {
      chosen[b.dataset.g] = chosen[b.dataset.g] === b.dataset.v ? undefined : b.dataset.v; save();
      if (chosen[b.dataset.g] && !last) setTimeout(() => go(1), 180); else { dir = 0; wizard(box); }   // a choice moves on by itself
    });
  }

  function draw(box) {
    const video = tab === 'video';
    box.querySelector('.ml-tabs').innerHTML = [['image', 'Фото'], ['video', 'Видео']]
      .map(([k, n]) => `<button type="button" data-ml="${k}" class="${k === tab ? 'on' : ''}">${n}</button>`).join('');
    if (mode === 'wizard') { wizard(box); return; }
    box.querySelector('.ml-body').innerHTML = `<div class="${dir < 0 ? 'ml-in-left' : ''}">
      <p class="ml-about">${video ? 'Короткое видео по запросу.' : 'Изображение по запросу: персонаж, сцена или мир.'}</p>
      <p class="ml-about">Опишите подробно: кто в кадре, как выглядит, во что одет, что делает, где находится, свет и настроение.</p>
      <label class="ml-field"><span>Промпт (лучше по-английски)</span><textarea rows="5" data-gen="prompt" ${video ? 'disabled' : ''} placeholder="Например: tall woman with long black hair in a dark coat, standing in the rain by a neon sign, night, soft light, close-up">${esc(gen.prompt || '')}</textarea></label>
      <label class="ml-field"><span>Чего избегать</span><textarea rows="2" data-gen="negative" ${video ? 'disabled' : ''} placeholder="Например: blurry, extra fingers, text, watermark">${esc(gen.negative || '')}</textarea></label>
      <section class="ml-models">
        <label class="ml-field"><span>Модель генерации</span><select data-gen="model" ${video ? 'disabled' : ''}><option value="">Загрузка списка…</option></select></label>
        <div class="ml-params">
          <label class="ml-field"><span>Формат</span><select data-gen="size">${SIZES.map(([k, n]) => `<option value="${k}"${(gen.size || '832x1216') === k ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
          <label class="ml-field"><span>Шаги</span><input type="number" min="1" max="80" data-gen="steps" value="${gen.steps || 30}"></label>
          <label class="ml-field"><span>CFG</span><input type="number" min="1" max="15" step="0.5" data-gen="cfg" value="${gen.cfg || 5}"></label>
          <label class="ml-field"><span>Seed (пусто — случайный)</span><input type="number" min="0" data-gen="seed" value="${gen.seed ?? ''}"></label>
          <label class="ml-field"><span>Сэмплер</span><select data-gen="sampler">${SAMPLERS.map(s => `<option${(gen.sampler || 'dpmpp_2m_sde') === s ? ' selected' : ''}>${s}</option>`).join('')}</select></label>
          <label class="ml-field"><span>Планировщик</span><select data-gen="scheduler">${SCHEDULERS.map(s => `<option${(gen.scheduler || 'karras') === s ? ' selected' : ''}>${s}</option>`).join('')}</select></label>
        </div>
        <div class="ml-params">${[1, 2, 3].map(n => `<label class="ml-field"><span>LoRA ${n}</span><select data-gen="lora${n}"><option value="">—</option></select></label>
          <label class="ml-field"><span>Вес</span><input type="number" min="-2" max="2" step="0.05" data-gen="lora${n}w" value="${gen['lora' + n + 'w'] ?? 0.7}"></label>`).join('')}</div>
      </section>
      <div class="ml-presets-row"><button type="button" class="ml-quick">Пресеты внешности <span>→</span></button>
        <button type="button" class="ml-reset">Сбросить пресеты</button></div>
      <p class="ml-summary">${summary() ? '<b>' + t('Выбрано:') + '</b> ' + summary() : t('Пресеты не выбраны.')}</p>
      <div class="ml-preview">${previewHtml()}</div>
      <button class="studio-main ml-generate" type="button" ${video || busy ? 'disabled' : ''}>Сгенерировать</button>
      <p class="ml-soon ml-status">${video ? 'Видео — позже. Пресеты сохраняются на этом устройстве.' : esc(status)}</p></div>`;
    box.querySelectorAll('[data-ml]').forEach(b => b.onclick = () => { tab = b.dataset.ml; dir = 0; draw(box); });
    box.querySelector('.ml-quick').onclick = () => { mode = 'wizard'; dir = 1; step = 0; visited = new Set(); draw(box); };
    box.querySelector('.ml-reset').onclick = () => { chosen = {}; save(); dir = 0; draw(box); };
    box.querySelectorAll('[data-gen]').forEach(el => el.addEventListener('change', () => { gen[el.dataset.gen] = el.value; saveGen(); }));
    box.querySelector('.ml-generate').onclick = () => generate(box);
    wirePreview(box);
    fillModels(box);
  }

  /* ---- real generation through Core -> ComfyUI (2026-11-08) ---------------------------------- */
  const GEN_KEY = 'ego-media-gen-v1';
  let gen = (() => { try { return JSON.parse(localStorage.getItem(GEN_KEY) || '{}') || {}; } catch { return {}; } })();
  const saveGen = () => { try { localStorage.setItem(GEN_KEY, JSON.stringify(gen)); } catch { /* storage blocked */ } };
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const SIZES = [['832x1216', 'Портрет 832×1216'], ['896x1152', 'Портрет 896×1152'], ['1024x1024', 'Квадрат 1024×1024'], ['1216x832', 'Альбом 1216×832']];
  const SAMPLERS = ['dpmpp_2m_sde', 'dpmpp_2m', 'dpmpp_sde', 'dpmpp_3m_sde', 'euler_ancestral', 'euler', 'uni_pc'];
  const SCHEDULERS = ['karras', 'exponential', 'normal', 'sgm_uniform', 'simple', 'beta'];
  let models = null, busy = false, status = '', lastImage = null;
  const core = () => (window.EGOCore && EGOCore.online ? EGOCore : null);
  /* Presets in English for the image model (CLIP reads English). */
  const EN = {
    'Женский': 'woman', 'Мужской': 'man', 'Фотореализм': 'photorealistic photo, raw photo', 'Кинокадр': 'cinematic film still',
    'Студийный портрет': 'studio portrait photo, softbox lighting', 'Аниме': 'anime style', 'Цифровой арт': 'digital art', 'Масляная живопись': 'oil painting',
    'Портрет': 'portrait', 'По пояс': 'upper body shot', 'В полный рост': 'full body shot', 'Крупный план': 'close-up of face',
    'Повседневная': 'casual clothes', 'Деловая': 'business suit', 'Вечернее платье': 'evening gown', 'Спортивная': 'sportswear',
    'Доспехи': 'armor', 'Фэнтези-наряд': 'fantasy outfit', 'Униформа': 'uniform',
    'Стоя': 'standing', 'Сидя': 'sitting', 'Лёжа': 'lying down', 'Через плечо': 'looking over shoulder', 'В движении': 'walking, in motion',
    'Стройное': 'slim body', 'Спортивное': 'athletic body', 'Среднее': 'average body', 'Пышное': 'curvy body', 'Мускулистое': 'muscular body',
    'Невысокий': 'short', 'Средний': 'average height', 'Высокий': 'tall',
    '20–29 лет': '25 years old', '30–39 лет': '35 years old', '40–49 лет': 'mature, 45 years old', '50+ лет': 'mature, 55 years old',
    'Европейская': 'european', 'Восточноазиатская': 'east asian', 'Африканская': 'african, dark skin', 'Латиноамериканская': 'latina',
    'Ближневосточная': 'middle eastern', 'Южноазиатская': 'south asian', 'Смешанная': 'mixed race', 'Эльф': 'elf, pointed ears', 'Демон': 'demon, horns',
    'Короткая стрижка': 'short hair', 'Каре': 'bob cut', 'Длинные распущенные': 'long loose hair', 'Хвост': 'ponytail', 'Пучок': 'hair bun',
    'Косы': 'braids', 'Кудри': 'curly hair', 'Бритая голова': 'shaved head',
    'Чёрные': 'black hair', 'Каштановые': 'brown hair', 'Русые': 'light brown hair', 'Блонд': 'blonde hair', 'Рыжие': 'red hair', 'Седые': 'grey hair', 'Яркие': 'dyed colorful hair',
  };
  function presetPrompt() {
    const keys = ['style', 'shot', 'age', 'race', 'gender', 'body', 'height', 'hairstyle', 'hairColour', 'clothes', 'pose'];
    return keys.map(k => chosen[k] && EN[chosen[k]]).filter(Boolean).join(', ');
  }
  async function fillModels(box) {
    const c = core();
    if (!c) { status = 'EGO Core не подключён.'; box.querySelector('.ml-status').textContent = status; return; }
    try { models = models?.online ? models : await c.api('GET', '/v1/media/models'); } catch { models = { online: false, checkpoints: [], loras: [] }; }
    const sel = box.querySelector('[data-gen="model"]');
    if (!sel) return;
    if (!models.online) { sel.innerHTML = '<option value="">ComfyUI не запущен</option>'; return; }
    sel.innerHTML = models.checkpoints.map(m => `<option value="${esc(m)}"${gen.model === m ? ' selected' : ''}>${esc(m.replace(/\.safetensors$/, ''))}</option>`).join('');
    [1, 2, 3].forEach(n => { const s = box.querySelector(`[data-gen="lora${n}"]`);
      s.innerHTML = '<option value="">—</option>' + models.loras.map(m => `<option value="${esc(m)}"${gen['lora' + n] === m ? ' selected' : ''}>${esc(m.replace(/\.safetensors$/, ''))}</option>`).join(''); });
  }
  async function imageUrl(id) {
    const r = await EGOCore.apiRaw('GET', '/v1/media/images/' + encodeURIComponent(id));
    return URL.createObjectURL(await r.blob());
  }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function generate(box) {
    const c = core(); if (!c || busy) return;
    box.querySelectorAll('[data-gen]').forEach(el => { gen[el.dataset.gen] = el.value; }); saveGen();
    const prompt = [gen.prompt, presetPrompt()].map(s => (s || '').trim()).filter(Boolean).join(', ');
    const setStatus = s => { status = s; const el = box.querySelector('.ml-status'); if (el) el.textContent = s; };
    if (!prompt) { setStatus('Напишите промпт или выберите пресеты.'); return; }
    const [width, height] = (gen.size || '832x1216').split('x').map(Number);
    const loras = [1, 2, 3].map(n => ({ name: gen['lora' + n], weight: Number(gen['lora' + n + 'w'] || 0.7) })).filter(l => l.name);
    const body = { prompt, negative: gen.negative || '', model: gen.model || '', width, height, steps: Number(gen.steps || 30),
      cfg: Number(gen.cfg || 5), sampler: gen.sampler, scheduler: gen.scheduler, loras, seed: gen.seed === '' || gen.seed == null ? null : Number(gen.seed) };
    busy = true; box.querySelector('.ml-generate').disabled = true; setStatus('Отправлено…');
    try {
      let job = await c.api('POST', '/v1/media/images', body);
      while (job.status === 'queued' || job.status === 'running') {
        setStatus(job.status === 'queued' ? 'В очереди…' : 'Генерация… ' + Math.round(job.seconds) + ' с');
        await sleep(1500);
        job = await c.api('GET', '/v1/media/jobs/' + job.id);
      }
      if (job.status !== 'done') throw Object.assign(new Error(job.error), { code: job.error });
      lastImage = { id: job.image_id, url: await imageUrl(job.image_id), params: { ...body, seed: job.seed } };
      status = 'Готово за ' + job.seconds + ' с · seed ' + job.seed;
      const preview = box.querySelector('.ml-preview');
      if (preview) { preview.innerHTML = previewHtml(); wirePreview(box); }
      setStatus(status);
      if (document.querySelector('.ml-gallery')) loadGallery();
    } catch (e) { setStatus(c.describe(e.code || e.message)); }
    finally { busy = false; const b = box.querySelector('.ml-generate'); if (b) b.disabled = false; }
  }
  async function loadGallery() {
    const c = core(), grid = document.querySelector('.ml-gallery-grid');
    if (!c || !grid) return;
    let list = [];
    try { list = (await c.api('GET', '/v1/media/images')).images || []; } catch { return; }
    if (!list.length) return;
    document.querySelector('.ml-gallery-empty')?.remove();
    const byId = new Map(list.map(i => [i.id, i]));
    grid.innerHTML = list.map(i => `<button type="button" class="ml-gallery-item" data-img="${esc(i.id)}" title="seed ${esc(i.params.seed)}"></button>`).join('');
    for (const b of grid.querySelectorAll('[data-img]')) {
      try {
        const url = await imageUrl(b.dataset.img);
        b.innerHTML = `<img src="${url}" alt="" loading="lazy">`;
        b.addEventListener('click', () => viewer({ ...byId.get(b.dataset.img), url }));
      } catch { /* skip */ }
    }
  }

  /* ---- keep a look and save to the phone (2026-11-08) ---------------------------------------
     «Взять за основу» copies every setting of a picture (prompt, model, LoRA, seed) into the form:
     the same seed with a small prompt change keeps the face close. «Сохранить» opens the phone's
     share sheet («Сохранить изображение») and falls back to an ordinary download. */
  function previewHtml() {
    if (!lastImage) return '';
    return `<img src="${lastImage.url}" alt="">
      <div class="ml-actions"><button type="button" data-act="save">Сохранить</button>
      <button type="button" data-act="base">Взять за основу</button></div>`;
  }
  function wirePreview(box) {
    const p = box.querySelector('.ml-preview');
    if (!p || !lastImage) return;
    p.querySelector('[data-act="save"]')?.addEventListener('click', () => saveImage(lastImage));
    p.querySelector('[data-act="base"]')?.addEventListener('click', () => { useAsBase(lastImage.params); draw(box); });
  }
  function useAsBase(p) {
    if (!p) return;
    const size = p.width + 'x' + p.height;
    gen = { ...gen, prompt: p.prompt || '', negative: p.negative || '', model: p.model || gen.model || '',
      size: SIZES.some(([k]) => k === size) ? size : gen.size, steps: p.steps, cfg: p.cfg,
      sampler: p.sampler || gen.sampler, scheduler: p.scheduler || gen.scheduler, seed: p.seed ?? '' };
    [1, 2, 3].forEach(n => { const l = (p.loras || [])[n - 1]; gen['lora' + n] = l ? l.name : ''; gen['lora' + n + 'w'] = l ? l.weight : 0.7; });
    chosen = {}; save(); saveGen();         // the prompt already holds the presets of that picture
    status = 'Настройки взяты из картинки. Измените позу или одежду в промпте и нажмите «Сгенерировать».';
  }
  async function saveImage(img) {
    const blob = await (await EGOCore.apiRaw('GET', '/v1/media/images/' + encodeURIComponent(img.id))).blob();
    const name = `ego-${img.params?.seed ?? 'image'}-${String(img.id).slice(0, 8)}.png`;
    const file = typeof File === 'function' ? new File([blob], name, { type: blob.type || 'image/png' }) : null;
    if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file] }); return; } catch (e) { if (e && e.name === 'AbortError') return; }
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  function viewer(img) {
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-backdrop"><div class="modal ml ml-viewer" role="dialog" aria-modal="true" aria-label="Просмотр">
      <div class="ml-head"><h2>seed ${esc(img.params?.seed ?? '')}</h2><button class="cs-cross" data-ml-close aria-label="Закрыть">×</button></div>
      <img src="${img.url}" alt="">
      <div class="ml-actions"><button type="button" data-act="save">Сохранить</button>
        <button type="button" data-act="base">Взять за основу</button>
        <button type="button" data-act="delete" class="ml-danger">Удалить</button></div></div></div>`;
    const close = () => { root.innerHTML = ''; };
    root.querySelector('[data-ml-close]').addEventListener('click', close);
    root.querySelector('[data-act="save"]').addEventListener('click', () => saveImage(img));
    root.querySelector('[data-act="base"]').addEventListener('click', () => { useAsBase(img.params); lastImage = img; show(); });
    root.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      if (!confirm('Удалить эту картинку? Это нельзя отменить.')) return;
      try { await EGOCore.api('DELETE', '/v1/media/images/' + encodeURIComponent(img.id)); } catch { return; }
      if (lastImage && lastImage.id === img.id) lastImage = null;
      close();
      const grid = document.querySelector('.ml-gallery-grid'); if (grid) grid.innerHTML = '';
      loadGallery();
    });
  }
  function show() {
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-backdrop"><div class="modal ml" role="dialog" aria-modal="true" aria-label="Генерация медиа">
      <div class="ml-head"><h2>Генерация медиа</h2><button class="cs-cross" data-ml-close aria-label="Закрыть">×</button></div>
      <div class="ml-tabs" role="tablist"></div><div class="ml-body"></div></div></div>`;
    root.querySelector('[data-ml-close]').onclick = () => { root.innerHTML = ''; mode = 'form'; };
    mode = 'form'; dir = 0;
    draw(root.querySelector('.ml'));
  }
  /* The «Медиа» tab: an empty gallery in place of the chat, and a glowing «Составить промпт» button. */
  function gallery(button) {
    document.querySelectorAll('[data-page]').forEach(x => x.classList.remove('selected'));
    button.classList.add('selected');
    document.documentElement.classList.remove('ego-m-list');
    const wrap = document.getElementById('composer-wrap'); if (wrap) wrap.style.display = 'none';
    const name = document.getElementById('top-name'), sub = document.getElementById('top-subtitle'), sym = document.getElementById('top-symbol');
    if (name) name.textContent = t('Медиа'); if (sub) sub.textContent = t('Фото и видео'); if (sym) sym.textContent = '✧';
    const content = document.getElementById('content');
    content.innerHTML = `<section class="ml-gallery"><div class="ml-gallery-empty"><span class="ml-gallery-mark">✧</span>
      <p>Здесь будут сгенерированные вами фото и видео</p></div><div class="ml-gallery-grid"></div>
      <button type="button" class="ml-compose">Составить промпт</button></section>`;
    content.querySelector('.ml-compose').onclick = show;
    loadGallery();
  }
  if (typeof window.setPage === 'function') {        // leaving the tab: the rail button loses its highlight
    const original = window.setPage;
    window.setPage = function (...args) { document.querySelector('[data-media-lab]')?.classList.remove('selected'); return original.apply(this, args); };
  }

  function mount() {
    const group = document.querySelector('.rail-group');
    if (!group || group.querySelector('[data-media-lab]')) return;
    const b = document.createElement('button');
    b.className = 'rail-btn'; b.dataset.mediaLab = '1'; b.title = 'Медиа'; b.setAttribute('aria-label', 'Медиа');
    b.innerHTML = '<span data-icon="sparkles"></span>';
    b.onclick = () => gallery(b);
    group.appendChild(b);
    if (typeof hydrate === 'function') hydrate(b);      // draw the icon (the bar was drawn before this button)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  /* presets(): the current choice ({style, shot, clothes, …}) — for the future generation and its check */
  window.EGOMediaLab = Object.freeze({ open: show, presets: () => ({ ...chosen }), groups: () => [...GROUPS, ...VIDEO_GROUPS] });
})();
