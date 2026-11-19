/* Phone (2026-11-08): a round «down» button jumps to the newest message; it shows only when
   the chat is scrolled away from the bottom. Hidden above 760 px (CSS). */
(() => {
  'use strict';
  const content = () => document.getElementById('content');
  function mount() {
    const box = content();
    if (!box || document.getElementById('ego-to-bottom')) return;
    const b = document.createElement('button');
    b.id = 'ego-to-bottom'; b.type = 'button'; b.hidden = true;
    b.title = 'Вниз'; b.setAttribute('aria-label', 'Вниз');
    b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" style="transform:rotate(180deg)"><path d="M12 19V5.5M6.5 11 12 5.5l5.5 5.5"/></svg>';
    // An eased glide (~0.5 s) instead of the browser's jump: long chats on iOS «smooth»-scrolled instantly.
    let anim = 0;
    b.onclick = () => {
      cancelAnimationFrame(anim);
      const from = box.scrollTop, start = performance.now(), ms = 520;
      const step = now => {
        const k = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - k, 3);
        box.scrollTop = from + (box.scrollHeight - box.clientHeight - from) * e;
        if (k < 1) anim = requestAnimationFrame(step);
      };
      anim = requestAnimationFrame(step);
    };
    box.addEventListener('touchstart', () => cancelAnimationFrame(anim), { passive: true });
    (document.querySelector('.workspace') || document.body).appendChild(b);
    const update = () => {
      const away = box.scrollHeight - box.scrollTop - box.clientHeight > 240;
      b.hidden = !away || !box.querySelector('.story');
    };
    box.addEventListener('scroll', update, { passive: true });
    new MutationObserver(update).observe(box, { childList: true, subtree: true });
    update();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
