/* Living planets for worlds (new look). Every world gets its own planet, derived from
   its id/name and tone: a banded giant, a cratered rock, an ocean world or a night world
   of city lights; some get rings or a moon. The surface slowly turns. Opening a world
   flies the camera into its planet, then the world page opens with the planet on top.
   Active only with html.look-next; reduced motion = static planets, no flight. */
(() => {
  'use strict';
  if (typeof egoWorlds === 'undefined') return;
  const REDUCED = (() => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } })();
  const on = () => document.documentElement.classList.contains('look-next');
  const TONES = {
    // light, main, deep, highlight, shade
    violet: ['#efe2ff', '#8b6bd8', '#24164a', '#c9b2ff', '#5b3fae'],
    blue: ['#dff3ff', '#3f86d6', '#0b1a3f', '#9fd4ff', '#1f4f9a'],
    rose: ['#ffe0e6', '#c2557a', '#36101f', '#ffb3c4', '#8a2e4c'],
    teal: ['#dcfff6', '#2fa59a', '#0a2c2e', '#9ff0e0', '#16706a'],
    ember: ['#ffd9b0', '#9a4a2a', '#1c0f0c', '#ff8a3d', '#4a2a22'],
    gold: ['#fff3c9', '#c79a3a', '#2e2108', '#ffd36b', '#7a5a1c'],
    emerald: ['#dbffe6', '#2f9a5e', '#08261a', '#8ff0b4', '#176a3e'],
    ice: ['#f2fbff', '#8fbcd6', '#122634', '#d6f1ff', '#4f7a94'],
    crimson: ['#ffd6d6', '#b0303f', '#2a080d', '#ff7a86', '#6e1a26'],
  };
  const KINDS = ['gas', 'rock', 'ocean', 'night', 'ember'];
  const TONE_KEYS = Object.keys(TONES);
  const randomSeed = () => { try { return crypto.getRandomValues(new Uint32Array(1))[0] >>> 1; } catch { return Math.floor(Math.random() * 2147483647); } };
  // The planet type follows the world's meaning; the id only decides when nothing matches.
  const MEANING = [
    ['night', /киберпанк|неон|мегапол|город|cyber|neon|city|metropol/i],
    ['ember', /пепел|пепельн|руин|выжжен|вулкан|пламя|огнен|пустош|апокалип|ash|ruin|volcan|burn|wasteland/i],
    ['ocean', /остров|море|морск|океан|волн|залив|архипел|island|sea|ocean|tide/i],
    ['gas', /эфир|мисти|маги|туман|сновид|астрал|небес|облак|ether|myst|magic|dream|astral|sky/i],
    ['rock', /камен|скал|горн|пустын|лун|кратер|stone|rock|desert|moon|crater/i],
  ];
  function kindFor(world, seed) {
    const head = `${world.name || ''} ${world.genre || ''}`, body = `${world.subtitle || ''} ${world.description || ''}`;
    for (const text of [head, body]) for (const [kind, re] of MEANING) if (re.test(text)) return kind;
    return KINDS[seed % 4];
  }
  function hash(text) { let h = 2166136261; for (const c of String(text)) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function rng(seed) { return () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  let uid = 0;
  const f1 = n => n.toFixed(1);

  // One surface tile (x 30..170) — drawn twice side by side so it can scroll forever.
  function surface(kind, pal, r) {
    const parts = [];
    if (kind === 'gas') {
      let y = 30;
      while (y < 170) {
        const h = 6 + r() * 16, col = [pal[1], pal[3], pal[4], pal[2]][Math.floor(r() * 4)];
        parts.push(`<rect x="30" y="${f1(y)}" width="140" height="${f1(h)}" fill="${col}" opacity="${(.25 + r() * .35).toFixed(2)}"/>`);
        y += h;
      }
      parts.push(`<ellipse cx="${f1(60 + r() * 80)}" cy="${f1(85 + r() * 40)}" rx="13" ry="6" fill="${pal[0]}" opacity=".45"/>`);
    } else if (kind === 'rock') {
      for (let i = 0; i < 3; i++) parts.push(`<ellipse cx="${f1(30 + r() * 140)}" cy="${f1(50 + r() * 100)}" rx="${f1(18 + r() * 20)}" ry="${f1(10 + r() * 12)}" fill="${pal[4]}" opacity=".35"/>`);
      for (let i = 0; i < 9; i++) {
        const cx = 30 + r() * 140, cy = 40 + r() * 120, cr = 3 + r() * 10;
        parts.push(`<circle cx="${f1(cx)}" cy="${f1(cy)}" r="${f1(cr)}" fill="${pal[2]}" opacity=".38"/><circle cx="${f1(cx - cr * .25)}" cy="${f1(cy - cr * .25)}" r="${f1(cr * .8)}" fill="none" stroke="${pal[0]}" stroke-opacity=".18" stroke-width="1"/>`);
      }
    } else if (kind === 'ocean' || kind === 'night') {
      // continents; for a night world they are darker and the lights come separately
      for (let i = 0; i < 5; i++) {
        const cx = 30 + r() * 140, cy = 50 + r() * 100, rx = 10 + r() * 22, ry = 6 + r() * 12;
        parts.push(`<ellipse cx="${f1(cx)}" cy="${f1(cy)}" rx="${f1(rx)}" ry="${f1(ry)}" transform="rotate(${(r() * 60 - 30).toFixed(0)} ${f1(cx)} ${f1(cy)})" fill="${kind === 'night' ? pal[4] : pal[4]}" opacity="${kind === 'night' ? .9 : .75}"/>`);
        if (kind === 'ocean') parts.push(`<ellipse cx="${f1(cx + 4)}" cy="${f1(cy - 2)}" rx="${f1(rx * .45)}" ry="${f1(ry * .4)}" fill="${pal[3]}" opacity=".35"/>`);
      }
      for (let i = 0; i < 4; i++) parts.push(`<rect x="30" y="${f1(40 + r() * 120)}" width="140" height="2.2" fill="#fff" opacity="${kind === 'night' ? .06 : .12}"/>`);
    } else {   // ember: charcoal crust, ash plains, glowing cracks
      for (let i = 0; i < 4; i++) parts.push(`<ellipse cx="${f1(30 + r() * 140)}" cy="${f1(45 + r() * 110)}" rx="${f1(16 + r() * 22)}" ry="${f1(8 + r() * 12)}" fill="#6b6460" opacity=".28"/>`);
      for (let i = 0; i < 7; i++) {
        let x = 30 + r() * 140, y = 40 + r() * 120, d = `M${f1(x)} ${f1(y)}`;
        for (let k = 0; k < 4; k++) { x += (r() - .5) * 26; y += (r() - .3) * 18; d += ` L${f1(x)} ${f1(y)}`; }
        parts.push(`<path d="${d}" fill="none" stroke="${pal[3]}" stroke-width="${f1(1 + r() * 1.6)}" stroke-linecap="round" opacity=".85"/>`);
      }
    }
    return parts.join('');
  }
  // City lights: warm clusters along coasts, shown only on the night side (masked).
  function lights(r) {
    const parts = [];
    for (let c = 0; c < 7; c++) {
      const cx = 30 + r() * 140, cy = 50 + r() * 100;
      for (let i = 0; i < 9; i++) {
        const a = r() * 6.283, d = r() * 9;
        parts.push(`<circle cx="${f1(cx + Math.cos(a) * d)}" cy="${f1(cy + Math.sin(a) * d * .6)}" r="${f1(.5 + r() * .9)}" fill="#ffcf7a" opacity="${(.55 + r() * .45).toFixed(2)}"/>`);
      }
      parts.push(`<circle cx="${f1(cx)}" cy="${f1(cy)}" r="7" fill="#ffb35a" opacity=".16"/>`);
    }
    return parts.join('');
  }

  /* ---- 2026-11-06: planets assembled from presets (planet.v === 2) -------------------------
     «Случайная планета» picks every layer on its own: surface type, palette, atmosphere,
     polar caps, a storm eye, rings (style and tilt) and 0–2 moons. Old saved seeds keep the
     look they had (no v). */
  const TYPES = [
    // key, name, palettes that suit it
    ['gas', 'Газовый гигант', ['violet', 'gold', 'rose', 'blue', 'ember']],
    ['rock', 'Каменная планета', ['ice', 'gold', 'violet', 'crimson']],
    ['ocean', 'Океанический мир', ['blue', 'teal', 'emerald']],
    ['night', 'Мир-город', ['violet', 'blue', 'rose']],
    ['lava', 'Вулканический мир', ['ember', 'crimson', 'gold']],
    ['ice', 'Ледяной мир', ['ice', 'blue', 'teal']],
    ['desert', 'Пустынный мир', ['gold', 'ember', 'rose']],
    ['jungle', 'Мир джунглей', ['emerald', 'teal', 'gold']],
    ['crystal', 'Кристаллический мир', ['violet', 'teal', 'ice', 'rose']],
    ['storm', 'Штормовой гигант', ['blue', 'violet', 'teal', 'gold']],
    ['toxic', 'Ядовитый мир', ['emerald', 'gold', 'teal']],
  ];
  const TONE_NAMES = { violet: 'аметист', blue: 'сапфир', rose: 'роза', teal: 'бирюза', ember: 'янтарь', gold: 'золото', emerald: 'изумруд', ice: 'лёд', crimson: 'рубин' };
  const pickOf = (list, r) => list[Math.floor(r() * list.length)];
  function recipe(seed) {
    const r = rng(seed ^ 0x51ed27);
    const type = pickOf(TYPES, r);
    const toneKey = r() < .2 ? pickOf(TONE_KEYS, r) : pickOf(type[2], r);       // sometimes an exotic colour
    const atmosphere = type[0] === 'night' ? pickOf(['none', 'haze'], r) : pickOf(['none', 'none', 'clouds', 'clouds', 'haze', 'aurora'], r);
    const caps = ['rock', 'ocean', 'desert', 'jungle', 'ice'].includes(type[0]) && r() < .45;
    const eye = ['gas', 'storm'].includes(type[0]) ? r() < .75 : r() < .08;
    const rings = type[0] === 'night' ? 'none' : pickOf(['none', 'none', 'none', 'thin', 'wide', 'double'], r);
    const tilt = Math.round(r() * 56 - 28);
    const moons = r() < .45 ? 0 : r() < .7 ? 1 : 2;
    return { type: type[0], typeName: type[1], toneKey, atmosphere, caps, eye, rings, tilt, moons };
  }
  function describe(world) {
    if (!(world && world.planet && world.planet.v === 2)) return '';
    const p = recipe(world.planet.seed >>> 0);
    const bits = [p.typeName, TONE_NAMES[p.toneKey]];
    if (p.atmosphere === 'clouds') bits.push('облака');
    if (p.atmosphere === 'haze') bits.push('дымка');
    if (p.atmosphere === 'aurora') bits.push('сияние');
    if (p.caps) bits.push('полярные шапки');
    if (p.eye) bits.push('вихрь');
    if (p.rings !== 'none') bits.push(p.rings === 'double' ? 'двойные кольца' : p.rings === 'wide' ? 'широкие кольца' : 'тонкие кольца');
    if (p.moons) bits.push(p.moons === 1 ? '1 луна' : '2 луны');
    return bits.join(' · ');
  }
  function wavy(y, amp, r) {
    let d = `M30 ${f1(y)}`;
    for (let x = 30; x < 170; x += 14) d += ` Q${f1(x + 7)} ${f1(y + (r() - .5) * amp * 2)} ${f1(x + 14)} ${f1(y)}`;
    return d;
  }
  function layer(type, pal, r) {
    const parts = [];
    const blob = (n, fill, op, rx0, ry0) => { for (let i = 0; i < n; i++) { const cx = 30 + r() * 140, cy = 45 + r() * 110, rx = rx0 + r() * rx0, ry = ry0 + r() * ry0;
      parts.push(`<ellipse cx="${f1(cx)}" cy="${f1(cy)}" rx="${f1(rx)}" ry="${f1(ry)}" transform="rotate(${(r() * 60 - 30).toFixed(0)} ${f1(cx)} ${f1(cy)})" fill="${fill}" opacity="${op}"/>`); } };
    const cracks = (n, colour, w, op) => { for (let i = 0; i < n; i++) { let x = 30 + r() * 140, y = 40 + r() * 120, d = `M${f1(x)} ${f1(y)}`;
      for (let k = 0; k < 4; k++) { x += (r() - .5) * 26; y += (r() - .3) * 18; d += ` L${f1(x)} ${f1(y)}`; }
      parts.push(`<path d="${d}" fill="none" stroke="${colour}" stroke-width="${f1(w + r() * w)}" stroke-linecap="round" opacity="${op}"/>`); } };
    if (type === 'gas' || type === 'storm') {
      let y = 30;
      while (y < 170) { const h = 5 + r() * (type === 'storm' ? 10 : 16);
        parts.push(`<path d="${wavy(y + h / 2, type === 'storm' ? 5 : 2, r)}" fill="none" stroke="${pickOf([pal[1], pal[3], pal[4], pal[2], pal[0]], r)}" stroke-width="${f1(h)}" opacity="${(.25 + r() * .4).toFixed(2)}"/>`); y += h; }
    } else if (type === 'rock') {
      blob(3, pal[4], .35, 16, 9);
      for (let i = 0; i < 10; i++) { const cx = 30 + r() * 140, cy = 40 + r() * 120, cr = 3 + r() * 10;
        parts.push(`<circle cx="${f1(cx)}" cy="${f1(cy)}" r="${f1(cr)}" fill="${pal[2]}" opacity=".38"/><circle cx="${f1(cx - cr * .25)}" cy="${f1(cy - cr * .25)}" r="${f1(cr * .8)}" fill="none" stroke="${pal[0]}" stroke-opacity=".2" stroke-width="1"/>`); }
    } else if (type === 'ocean' || type === 'jungle' || type === 'night') {
      const land = type === 'jungle' ? '#1f6b3a' : pal[4];
      blob(type === 'jungle' ? 8 : 5, land, type === 'night' ? .9 : .8, 11, 6);
      if (type === 'jungle') blob(5, '#3fa35a', .45, 6, 4);
      if (type === 'ocean') blob(4, pal[3], .3, 5, 3);
    } else if (type === 'lava') {
      blob(4, '#4a3a36', .5, 15, 8);
      cracks(9, pal[3], 1.1, .9);
    } else if (type === 'ice') {
      blob(4, pal[0], .45, 15, 8);
      cracks(8, pal[4], .6, .55);
    } else if (type === 'desert') {
      for (let y = 38; y < 168; y += 7 + r() * 7) parts.push(`<path d="${wavy(y, 3, r)}" fill="none" stroke="${pickOf([pal[4], pal[0], pal[3]], r)}" stroke-width="${f1(1.5 + r() * 3)}" opacity="${(.25 + r() * .3).toFixed(2)}"/>`);
      cracks(3, pal[2], 1.4, .55);
    } else if (type === 'crystal') {
      for (let i = 0; i < 12; i++) { const cx = 30 + r() * 140, cy = 40 + r() * 120, s = 5 + r() * 12;
        parts.push(`<polygon points="${f1(cx)},${f1(cy - s)} ${f1(cx + s * .6)},${f1(cy)} ${f1(cx)},${f1(cy + s)} ${f1(cx - s * .6)},${f1(cy)}" fill="${pickOf([pal[0], pal[3], pal[4]], r)}" opacity="${(.3 + r() * .4).toFixed(2)}" stroke="#fff" stroke-opacity=".25" stroke-width=".6"/>`); }
    } else if (type === 'toxic') {
      blob(7, pickOf(['#9be35a', '#c8e04a', pal[3]], r), .4, 10, 6);
      for (let i = 0; i < 14; i++) parts.push(`<circle cx="${f1(30 + r() * 140)}" cy="${f1(40 + r() * 120)}" r="${f1(1 + r() * 3)}" fill="#e8ff9a" opacity="${(.25 + r() * .4).toFixed(2)}"/>`);
    }
    return parts.join('');
  }
  function clouds(r) {
    const parts = [];
    for (let i = 0; i < 9; i++) { const cx = 30 + r() * 140, cy = 40 + r() * 120;
      parts.push(`<ellipse cx="${f1(cx)}" cy="${f1(cy)}" rx="${f1(10 + r() * 20)}" ry="${f1(2.5 + r() * 4)}" fill="#fff" opacity="${(.28 + r() * .3).toFixed(2)}"/>`); }
    return parts.join('');
  }
  function composed(world, size, still, seed) {
    const p = recipe(seed), r = rng(seed), pal = TONES[p.toneKey];
    const id = 'pl' + (++uid);
    const speed = (34 + (seed % 40)).toFixed(0), cloudSpeed = (Number(speed) * .6).toFixed(0);
    const base = { night: pal[2], lava: '#231714', jungle: '#2c6fa0', toxic: '#3c4a1c', ice: pal[1], desert: pal[3] }[p.type] || pal[1];
    const tile = layer(p.type, pal, r);
    const halo = /^#[0-9a-f]{6}$/i.test(world.glow || '') ? world.glow : (TONES[world.tone] || pal)[3];
    const tw = t => `${t}<g transform="translate(140 0)">${t}</g>`;
    const blur = { gas: 2.2, storm: 1.6, ocean: 1.3, jungle: 1.1, night: 1.4, toxic: 1.6, ice: .7, desert: .9 }[p.type] ?? .6;
    let ringBack = '', ringFront = '';
    if (p.rings !== 'none') {
      const rot = `rotate(${p.tilt} 100 100)`;
      const one = (rx, ry, w, col, op) => [`<path d="M${100 - rx} ${100 + 4}a${rx} ${ry} 0 0 1 ${rx * 2} -8" fill="none" stroke="${col}" stroke-opacity="${op * .7}" stroke-width="${w}" transform="${rot}"/>`,
        `<path d="M${100 + rx} ${100 - 4}a${rx} ${ry} 0 0 1 ${-rx * 2} 8" fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${w}" transform="${rot}"/>`];
      const sets = p.rings === 'thin' ? [one(84, 18, 2, pal[0], .85)] : p.rings === 'wide' ? [one(86, 20, 9, pal[3], .55), one(86, 20, 2, pal[0], .7)] : [one(80, 17, 4, pal[3], .7), one(95, 21, 2.5, pal[0], .6)];
      ringBack = sets.map(s => s[0]).join(''); ringFront = sets.map(s => s[1]).join('');
    }
    const moons = Array.from({ length: p.moons }, (_, i) => {
      const dist = 86 + i * 11 + r() * 5, mr = 4 + r() * 5, dur = 22 + r() * 30, angle = i * 150 + r() * 60 - 30;
      return `<g class="pl-moon" style="animation-duration:${dur.toFixed(0)}s"><g transform="rotate(${angle.toFixed(0)} 100 100)"><circle cx="${f1(100 + dist)}" cy="100" r="${f1(mr)}" fill="url(#${id}m)"/></g></g>`;
    }).join('');
    const caps = p.caps ? `<g clip-path="url(#${id}c)"><ellipse cx="100" cy="30" rx="58" ry="${f1(12 + r() * 8)}" fill="#fff" opacity=".75" filter="url(#${id}b)"/><ellipse cx="100" cy="170" rx="54" ry="${f1(10 + r() * 8)}" fill="#fff" opacity=".65" filter="url(#${id}b)"/></g>` : '';
    const ex = 60 + r() * 70, ey = 80 + r() * 45;
    // a storm: soft oval with a spiral of light, no dark centre (that read as an eye)
    const eye = p.eye ? `<g clip-path="url(#${id}c)" filter="url(#${id}g)" opacity=".85"><ellipse cx="${f1(ex)}" cy="${f1(ey)}" rx="16" ry="8.5" fill="${pal[4]}" opacity=".75"/>`
      + `<path d="M${f1(ex - 11)} ${f1(ey)} a11 6 0 0 1 22 0 a7 3.8 0 0 1 -14 0 a3.5 2 0 0 1 7 0" fill="none" stroke="${pal[0]}" stroke-width="1.6" stroke-linecap="round" opacity=".8"/></g>` : '';
    const atmo = p.atmosphere === 'clouds' ? `<g clip-path="url(#${id}c)" filter="url(#${id}b)"><g class="pl-surface" style="animation-duration:${cloudSpeed}s">${tw(clouds(rng(seed ^ 0xc10d)))}</g></g>`
      : p.atmosphere === 'haze' ? `<circle cx="100" cy="100" r="70" fill="${pal[3]}" opacity=".18"/>`
      : p.atmosphere === 'aurora' ? `<g clip-path="url(#${id}c)" filter="url(#${id}g)"><path d="M45 52 Q100 30 155 52" fill="none" stroke="#7dffc4" stroke-width="5" opacity=".55"/><path d="M50 150 Q100 170 150 150" fill="none" stroke="#c99bff" stroke-width="4" opacity=".45"/></g>` : '';
    const night = p.type === 'night'
      ? `<circle cx="100" cy="100" r="70" fill="url(#${id}t)"/><g clip-path="url(#${id}c)" mask="url(#${id}n)"><g class="pl-surface" style="animation-duration:${speed}s">${tw(lights(rng(seed ^ 0x9e37)))}</g></g>` : '';
    const glowLava = p.type === 'lava' ? `<g clip-path="url(#${id}c)" filter="url(#${id}g)" opacity=".7"><g class="pl-surface" style="animation-duration:${speed}s">${tw(tile)}</g></g>` : '';
    return `<svg class="look-planet${still ? ' still' : ''}" viewBox="0 0 200 200" width="${size}" height="${size}" style="width:${size}px;height:${size}px" aria-hidden="true" data-kind="${p.type}">
      <defs>
        <radialGradient id="${id}h" cx="50%" cy="50%" r="50%"><stop offset=".62" stop-color="${halo}" stop-opacity="${p.atmosphere === 'haze' ? .6 : .45}"/><stop offset="1" stop-color="${halo}" stop-opacity="0"/></radialGradient>
        <radialGradient id="${id}s" cx="32%" cy="28%" r="80%"><stop offset="0" stop-color="#fff" stop-opacity=".42"/><stop offset=".38" stop-color="#fff" stop-opacity="0"/><stop offset=".72" stop-color="#000" stop-opacity=".35"/><stop offset="1" stop-color="#000" stop-opacity=".78"/></radialGradient>
        <radialGradient id="${id}m" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="${pal[1]}"/></radialGradient>
        <linearGradient id="${id}t" x1="0" y1="0" x2="1" y2=".25"><stop offset=".38" stop-color="#000" stop-opacity="0"/><stop offset=".62" stop-color="#000" stop-opacity=".82"/></linearGradient>
        <linearGradient id="${id}ng" x1="0" y1="0" x2="1" y2=".25"><stop offset=".42" stop-color="#000"/><stop offset=".66" stop-color="#fff"/></linearGradient>
        <mask id="${id}n" maskUnits="userSpaceOnUse" x="0" y="0" width="200" height="200"><rect width="200" height="200" fill="url(#${id}ng)"/></mask>
        <clipPath id="${id}c"><circle cx="100" cy="100" r="70"/></clipPath>
        <filter id="${id}b" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${blur}"/></filter>
        <filter id="${id}g" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.4"/></filter>
      </defs>
      <circle cx="100" cy="100" r="98" fill="url(#${id}h)"/>
      ${ringBack}
      <circle cx="100" cy="100" r="70" fill="${base}"/>
      <g clip-path="url(#${id}c)"><g filter="url(#${id}b)"><g class="pl-surface" style="animation-duration:${speed}s">${tw(tile)}</g></g></g>
      ${glowLava}${caps}${eye}${atmo}${night}
      <circle cx="100" cy="100" r="70" fill="url(#${id}s)"/>
      <circle cx="100" cy="100" r="70" fill="none" stroke="${pal[0]}" stroke-opacity=".35" stroke-width="1.2"/>
      ${ringFront}${moons}
    </svg>`;
  }

  // Planet SVG for a world. size in px; still = no motion.
  function planetSVG(world, size, still = false) {
    // A randomized planet (world.planet.seed from the editor) is fully random — type and body
    // colours; otherwise it is seeded by the world's id and its type follows the world's meaning.
    const rolled = world.planet && Number.isInteger(world.planet.seed) ? world.planet.seed >>> 0 : null;
    const seed = rolled ?? hash(world.id || world.name || 'world');
    if (rolled !== null && world.planet.v === 2) return composed(world, size, still, seed);
    const r = rng(seed);
    const kind = rolled !== null ? KINDS[seed % KINDS.length] : kindFor(world, seed);
    const pal = rolled !== null ? TONES[TONE_KEYS[(seed >>> 7) % TONE_KEYS.length]] : (TONES[world.tone] || TONES.violet);
    const rings = kind !== 'night' && (seed >> 3) % 3 === 0, moon = (seed >> 5) % 2 === 0;
    const id = 'pl' + (++uid);
    const tile = surface(kind, pal, r);
    const speed = (36 + (seed % 40)).toFixed(0);
    const base = kind === 'night' ? pal[2] : kind === 'ember' ? '#231714' : pal[1];
    // The glow around the planet is the one thing the user picks.
    const halo = /^#[0-9a-f]{6}$/i.test(world.glow || '') ? world.glow : (TONES[world.tone] || pal)[3];
    const ringBack = rings ? `<path d="M22 108a78 20 0 0 1 156-16" fill="none" stroke="${pal[3]}" stroke-opacity=".55" stroke-width="5" transform="rotate(-16 100 100)"/>` : '';
    const ringFront = rings ? `<path d="M178 92a78 20 0 0 1-156 16" fill="none" stroke="${pal[0]}" stroke-opacity=".8" stroke-width="5" transform="rotate(-16 100 100)"/>` : '';
    const moonEl = moon ? `<g class="pl-moon" style="animation-duration:${30 + (seed % 25)}s"><circle cx="190" cy="100" r="7" fill="url(#${id}m)"/></g>` : '';
    const nightLayer = kind === 'night'
      ? `<g clip-path="url(#${id}c)" mask="url(#${id}n)"><g class="pl-surface" style="animation-duration:${speed}s">${(t => t + `<g transform="translate(140 0)">${t}</g>`)(lights(rng(seed ^ 0x9e37)))}</g></g>`
      : '';
    const blur = kind === 'gas' ? 2.4 : kind === 'ocean' || kind === 'night' ? 1.4 : kind === 'ember' ? .5 : .7;
    return `<svg class="look-planet${still ? ' still' : ''}" viewBox="0 0 200 200" width="${size}" height="${size}" style="width:${size}px;height:${size}px" aria-hidden="true" data-kind="${kind}">
      <defs>
        <radialGradient id="${id}h" cx="50%" cy="50%" r="50%"><stop offset=".62" stop-color="${halo}" stop-opacity="${kind === 'night' ? .3 : .45}"/><stop offset="1" stop-color="${halo}" stop-opacity="0"/></radialGradient>
        <radialGradient id="${id}s" cx="32%" cy="28%" r="80%"><stop offset="0" stop-color="#fff" stop-opacity=".42"/><stop offset=".38" stop-color="#fff" stop-opacity="0"/><stop offset=".72" stop-color="#000" stop-opacity=".35"/><stop offset="1" stop-color="#000" stop-opacity=".78"/></radialGradient>
        <radialGradient id="${id}m" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="${pal[1]}"/></radialGradient>
        <linearGradient id="${id}t" x1="0" y1="0" x2="1" y2=".25"><stop offset=".38" stop-color="#000" stop-opacity="0"/><stop offset=".62" stop-color="#000" stop-opacity=".82"/></linearGradient>
        <linearGradient id="${id}ng" x1="0" y1="0" x2="1" y2=".25"><stop offset=".42" stop-color="#000"/><stop offset=".66" stop-color="#fff"/></linearGradient>
        <mask id="${id}n" maskUnits="userSpaceOnUse" x="0" y="0" width="200" height="200"><rect width="200" height="200" fill="url(#${id}ng)"/></mask>
        <clipPath id="${id}c"><circle cx="100" cy="100" r="70"/></clipPath>
        <filter id="${id}b" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${blur}"/></filter>
        <filter id="${id}g" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.2"/></filter>
      </defs>
      <circle cx="100" cy="100" r="98" fill="url(#${id}h)"/>
      ${ringBack}
      <circle cx="100" cy="100" r="70" fill="${base}"/>
      <g clip-path="url(#${id}c)"><g filter="url(#${id}b)"><g class="pl-surface" style="animation-duration:${speed}s">${tile}<g transform="translate(140 0)">${tile}</g></g></g></g>
      ${kind === 'ember' ? `<g clip-path="url(#${id}c)" filter="url(#${id}g)" opacity=".7"><g class="pl-surface" style="animation-duration:${speed}s">${tile}<g transform="translate(140 0)">${tile}</g></g></g>` : ''}
      ${kind === 'night' ? `<circle cx="100" cy="100" r="70" fill="url(#${id}t)"/>` : ''}
      <circle cx="100" cy="100" r="70" fill="url(#${id}s)"/>
      ${nightLayer}
      <circle cx="100" cy="100" r="70" fill="none" stroke="${pal[0]}" stroke-opacity=".35" stroke-width="1.2"/>
      ${ringFront}${moonEl}
    </svg>`;
  }

  /* ---- placing planets ---- */
  const worldOf = id => (typeof worldById === 'function' ? worldById(id) : egoWorlds.find(w => w.id === id));
  function decorate() {
    if (!on()) return;
    document.querySelectorAll('[data-world-card]').forEach(card => {
      const orbit = card.querySelector('.world-orbit');
      const world = worldOf(card.dataset.worldCard);
      if (!orbit || !world || orbit.querySelector('.look-planet')) return;
      orbit.innerHTML = planetSVG(world, 230, REDUCED);
      if (/^#[0-9a-f]{6}$/i.test(world.glow || '')) card.style.setProperty('--world-hue', world.glow + '66');
      orbit.classList.add('has-planet');
      orbit.setAttribute('role', 'button');
      orbit.setAttribute('tabindex', '0');
      orbit.setAttribute('aria-label', 'Открыть мир ' + world.name);
    });
    document.querySelectorAll('[data-world-side]').forEach(button => {
      const avatar = button.querySelector('.world-side-avatar');
      const world = worldOf(button.dataset.worldSide);
      if (!avatar || !world || avatar.querySelector('.look-planet')) return;
      avatar.innerHTML = planetSVG(world, 40, true);
      avatar.classList.add('has-planet');
    });
  }
  function decorateDetail(id) {
    if (!on()) return;
    const world = worldOf(id);
    const slot = document.querySelector('#modal-root .world-detail-orbit');
    if (!world || !slot || slot.querySelector('.look-planet')) return;
    slot.innerHTML = planetSVG(world, 300, REDUCED);
    slot.classList.add('has-planet');
  }
  ['content', 'side-content'].forEach(idName => {
    const node = document.getElementById(idName);
    if (node) new MutationObserver(decorate).observe(node, { childList: true, subtree: true });
  });
  new MutationObserver(() => { if (on()) decorate(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

  // Every way into a world (atlas, sidebar, search) gets the planet on the world page.
  if (typeof openWorldDetail === 'function') {
    const original = openWorldDetail;
    window.openWorldDetail = function (id) { const result = original.apply(this, arguments); decorateDetail(id); return result; };
  }

  /* ---- flight into the planet ---- */
  let flying = false;
  function flyInto(planet, world, done) {
    if (REDUCED || flying || !planet.animate) { done(); return; }
    flying = true;
    const rect = planet.getBoundingClientRect();
    const pal = TONES[world.tone] || TONES.violet;
    const veil = document.createElement('div');
    veil.className = 'look-flight';
    veil.style.background = `radial-gradient(circle at ${rect.left + rect.width / 2}px ${rect.top + rect.height / 2}px, ${pal[1]}, ${pal[2]} 55%, #05040a)`;
    const ghost = document.createElement('div');
    ghost.className = 'look-flight-planet';
    Object.assign(ghost.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' });
    ghost.innerHTML = planetSVG(world, rect.width);
    document.body.append(veil, ghost);
    const cx = innerWidth / 2 - (rect.left + rect.width / 2), cy = innerHeight / 2 - (rect.top + rect.height / 2);
    const scale = Math.hypot(innerWidth, innerHeight) / (rect.width * .62) * 1.15;
    const easing = 'cubic-bezier(.55,0,.25,1)';
    ghost.animate([{ transform: 'translate(0,0) scale(1)' }, { transform: `translate(${cx}px,${cy}px) scale(1.25)`, offset: .35 }, { transform: `translate(${cx}px,${cy}px) scale(${scale})` }], { duration: 1050, easing, fill: 'forwards' });
    veil.animate([{ opacity: 0 }, { opacity: .55, offset: .4 }, { opacity: 1 }], { duration: 1050, easing, fill: 'forwards' });
    setTimeout(() => {
      done();
      const fade = { duration: 520, easing: 'ease-out', fill: 'forwards' };
      ghost.animate([{ opacity: 1 }, { opacity: 0 }], fade);
      let cleaned = false;
      const cleanup = () => { if (cleaned) return; cleaned = true; veil.remove(); ghost.remove(); flying = false; };
      veil.animate([{ opacity: 1 }, { opacity: 0 }], fade).onfinish = cleanup;
      setTimeout(cleanup, 650);   // never leave the veil over the app (background tabs may hold animation events)
    }, 1000);
  }
  function openWithFlight(id, planet, event) {
    const world = worldOf(id);
    if (!world || !planet) return false;
    event.preventDefault(); event.stopPropagation();
    flyInto(planet, world, () => window.openWorldDetail(id));
    return true;
  }
  document.addEventListener('click', e => {
    if (!on() || !e.target.closest) return;
    const button = e.target.closest('[data-world-open]');
    const orbit = e.target.closest('.world-orbit.has-planet');
    const card = (button || orbit) && (button || orbit).closest('[data-world-card]');
    if (!card) return;
    openWithFlight(card.dataset.worldCard, card.querySelector('.look-planet'), e);
  }, true);
  document.addEventListener('keydown', e => {
    if (!on() || (e.key !== 'Enter' && e.key !== ' ')) return;
    const orbit = e.target.closest && e.target.closest('.world-orbit.has-planet');
    const card = orbit && orbit.closest('[data-world-card]');
    if (card) openWithFlight(card.dataset.worldCard, orbit.querySelector('.look-planet'), e);
  }, true);

  decorate();
  // Glow choices for the editor: one per tone (its highlight colour).
  const GLOWS = TONE_KEYS.map(k => ({ tone: k, color: TONES[k][3] }));
  window.EGOPlanets = Object.freeze({ planetSVG, randomSeed, GLOWS, describe, TYPES: TYPES.map(t => t[0]) });
})();
