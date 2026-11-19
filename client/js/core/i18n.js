/* Client languages (2026-11-06): ru (source), en, fr, es, de.
   Russian is written in the code. For another language the page loads js/i18n/<lang>.js,
   and this module replaces Russian texts in the page (text nodes and title/placeholder/
   aria-label/alt) using that dictionary, including texts added later. Story messages and
   anything the user types are never translated. Dictionary keys may hold {0}, {1}…
   placeholders for numbers and names. */
(() => {
  'use strict';
  const LANGUAGES = { ru: 'Русский', en: 'English', fr: 'Français', es: 'Español', de: 'Deutsch' };
  const KEY = 'ego-lang';
  const pick = () => {
    try { const saved = localStorage.getItem(KEY); if (LANGUAGES[saved]) return saved; } catch { /* storage blocked */ }
    const wanted = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || 'en'])
      .map(x => String(x).slice(0, 2).toLowerCase());
    return wanted.find(x => LANGUAGES[x]) || 'en';
  };
  const lang = pick();
  document.documentElement.lang = lang;

  const CYR = /[А-Яа-яЁё]/;
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  const SKIP = 'script,style,textarea,input,[contenteditable],[contenteditable] *,[data-no-i18n],[data-no-i18n] *';
  const NO_ATTR = '[contenteditable],[contenteditable] *,[data-no-i18n],[data-no-i18n] *';
  const STORY = '.msg-card,.msg-card *';   // story text: exact sample sentences only, never patterns
  let exact = null, patterns = [];

  const norm = s => s.replace(/\s+/g, ' ').trim();
  const fill = (template, args) => template.replace(/\{(\d+)\}/g, (m, i) => (args[i] !== undefined ? args[i] : m));

  function lookup(text, allowPatterns = true, depth = 0) {
    const key = norm(text);
    if (!key || !CYR.test(key)) return null;
    if (exact.has(key)) return exact.get(key);
    if (!allowPatterns) return typing(key);
    for (const p of patterns) {
      const m = p.re.exec(key);
      if (m) return fill(p.value, m.slice(1).map(part => (depth < 2 && lookup(part, true, depth + 1)) || part));
    }
    // A known key with a tail or head that has no Cyrillic: "Твои истории · 3", "12 · Ключ".
    const tail = key.match(/[^А-Яа-яЁё]*$/)[0];
    for (let i = key.length - tail.length; i < key.length; i++) {
      const head = key.slice(0, i).trim();
      if (exact.has(head)) return exact.get(head) + key.slice(i);
    }
    const lead = key.match(/^[^А-Яа-яЁё]*/)[0];
    for (let i = lead.length; i > 0; i--) {
      const rest = key.slice(i).trim();
      if (exact.has(rest)) return key.slice(0, i) + exact.get(rest);
    }
    if (depth >= 2) return null;
    const typed = typing(key);
    if (typed != null) return typed;
    // Glued pieces: "Персонаж · демонстрация", "Сцена. Вторая фраза." — translate each piece.
    const parts = key.split(/(\s+·\s+|(?<=[.!?…])\s+)/);
    if (parts.length > 1) {
      let hit = false;
      const out = parts.map((part, i) => {
        if (i % 2 || !CYR.test(part)) return part;
        const t = lookup(part, true, depth + 1);
        if (t != null) hit = true;
        return t != null ? t : part;
      });
      if (hit) return out.join('');
    }
    // "Открыть мир Эфир": a known phrase followed by a name or another known phrase.
    const words = key.split(' ');
    for (let n = words.length - 1; n > 0; n--) {
      const head = words.slice(0, n).join(' ');
      if (exact.has(head)) {
        const rest = words.slice(n).join(' ');
        return exact.get(head) + ' ' + (lookup(rest, true, depth + 1) || rest);
      }
    }
    return null;
  }
  // Text typed out letter by letter (sample scenes): the part shown so far of a known sentence.
  function typing(key) {
    if (key.length < 8 || key.length > 400) return null;
    for (const [ru, value] of exact) {
      if (ru.length > key.length + 2 && ru.startsWith(key)) {
        return value.slice(0, Math.max(1, Math.round(value.length * key.length / ru.length)));
      }
    }
    return null;
  }
  function translate(text) { return exact ? lookup(text) : null; }

  function textNode(node) {
    const parent = node.parentElement;
    if (!parent || parent.closest(SKIP)) return;
    const value = node.nodeValue;
    if (!value || !CYR.test(value)) return;
    const out = lookup(value, !parent.closest(STORY));
    if (out == null) return;
    const lead = value.match(/^\s*/)[0], tail = value.match(/\s*$/)[0];
    node.nodeValue = lead + out + tail;
  }
  function element(el) {
    if (el.closest(NO_ATTR)) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v && CYR.test(v)) { const out = lookup(v); if (out != null) el.setAttribute(a, out); }
    }
    if (el.tagName === 'INPUT' && /^(button|submit)$/i.test(el.type) && CYR.test(el.value)) {
      const out = lookup(el.value); if (out != null) el.value = out;
    }
  }
  function walk(root) {
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) element(root);
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let n;
    while ((n = it.nextNode())) { if (n.nodeType === 3) textNode(n); else element(n); }
  }

  function install(dict) {
    exact = new Map(); patterns = [];
    for (const [ru, value] of Object.entries(dict)) {
      if (typeof value !== 'string' || !value) continue;
      if (/\{\d+\}/.test(ru)) {
        const source = ru.split(/\{\d+\}/).map(s => s.replace(/[.*+?^$()|[\]\\]/g, '\\$&')).join('(.+?)');
        patterns.push({ re: new RegExp('^' + source + '$'), value });
      } else exact.set(norm(ru), value);
    }
    patterns.sort((a, b) => b.re.source.length - a.re.source.length);
    const wrap = name => {
      const original = window[name];
      if (typeof original !== 'function') return;
      window[name] = function (message, ...rest) {
        const text = String(message == null ? '' : message);
        return original.call(window, text.split('\n').map(line => lookup(line) ?? line).join('\n'), ...rest);
      };
    };
    ['alert', 'confirm', 'prompt'].forEach(wrap);
    const start = () => {
      walk(document.body);
      if (CYR.test(document.title)) { const t = lookup(document.title); if (t) document.title = t; }
      new MutationObserver(records => {
        for (const r of records) {
          if (r.type === 'characterData') textNode(r.target);
          else if (r.type === 'attributes') element(r.target);
          else r.addedNodes.forEach(walk);
        }
      }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    };
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
  }

  window.EGOI18n = {
    lang, languages: LANGUAGES,
    /** Translate a Russian UI text for code that builds strings outside the page. */
    t(text, ...args) { const out = exact ? lookup(fill(text, args)) : null; return out != null ? out : fill(text, args); },
    set(next) {
      if (!LANGUAGES[next] || next === lang) return;
      try { localStorage.setItem(KEY, next); } catch { /* storage blocked */ }
      location.reload();
    },
    install,
  };
  // Core answers errors and sends emails in this language (X-EGO-Lang on every /v1 request).
  if (typeof window.fetch === 'function') {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (/^\/v1\//.test(url) || url.startsWith(location.origin + '/v1/')) {
        const headers = new Headers((init && init.headers) || (typeof input !== 'string' && input.headers) || undefined);
        if (!headers.has('X-EGO-Lang')) headers.set('X-EGO-Lang', lang);
        init = { ...(init || {}), headers };
      }
      return originalFetch(input, init);
    };
  }
  // Synchronous on purpose: the dictionary must be ready before the first render.
  if (lang !== 'ru') document.write('<script src="js/i18n/' + lang + '.js"><\/script>');
})();
