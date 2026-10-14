/* backyard_birds: what BirdNET-Go has heard today.

   Nine layouts off one payload, all printed matter: cream paper, engraved
   type, hairline rules. The everyday pages (list, latest, gallery, chart)
   put the hero bird's picture to the edge of the page with the rest named
   beside it, or set the birds as specimen cards or a ledger; plate, bleed
   and cluster are pages from the book. Pictures and the faces arrive
   inlined from the server: the render sandbox has no network.

   A photo shown among classic plates is toned to a warm monotone so a garden
   where only some birds have a plate still reads as one set, unless the cell
   asks for natural colour. */

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const BIRD = '<svg class="ico" viewBox="0 0 256 256" aria-hidden="true"><path d="M176,72a16,16,0,1,1-16-16A16,16,0,0,1,176,72Zm68,8a12,12,0,0,1-5.34,10L220,102.42V120A108.12,108.12,0,0,1,112,228H24A20,20,0,0,1,8.41,195.5l.15-.18L92,95.18V76.89C92,41.28,120.57,12.17,155.69,12H156a63.94,63.94,0,0,1,60.58,43.29L238.66,70A12,12,0,0,1,244,80Zm-33.63,0-10.69-7.13a12,12,0,0,1-5-7A40,40,0,0,0,156,36h-.19c-21.95.11-39.8,18.45-39.8,40.89V99.52a12,12,0,0,1-2.79,7.69L32.57,204H53.05l69.74-83.68a12,12,0,1,1,18.43,15.36L84.29,204H112a84.09,84.09,0,0,0,84-84V96a12,12,0,0,1,5.35-10Z"/></svg>';

function ago(m) {
  if (m == null) return '';
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ago`;
}

const ORD = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
  'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth'];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];
const TENTH = ['', '', 'twentieth', 'thirtieth', 'fortieth', 'fiftieth', 'sixtieth', 'seventieth', 'eightieth', 'ninetieth'];

/* "the thirty-eighth time" for the engraved caption; plain digits past 99 */
function ordinalWords(n) {
  if (!n || n < 1) return '';
  if (n < 20) return ORD[n];
  if (n < 100) {
    const t = Math.floor(n / 10), u = n % 10;
    return u ? `${TENS[t]}-${ORD[u]}` : TENTH[t];
  }
  return `${n}th`;
}

/* A picture, or the bird icon on a plain ground when there is none. Photos
   among plates are toned (see the header note). */
function pic(p, cls, toneAll) {
  if (!p || !p.src) return `<div class="${cls} nopic">${BIRD}</div>`;
  /* A plate (or a painting in the plate style) is never cropped: it is fitted
     whole and the frame is filled with its own paper colour, so the bird is
     always all there. A photo has no paper to extend, so it fills the frame. */
  if (p.paper) {
    return `<div class="${cls} fit" style="background:${esc(p.paper)}"><img src="${p.src}" alt=""></div>`;
  }
  const tone = toneAll && p.kind === 'photo' ? ` toned${toneAll === 'light' ? ' light' : ''}` : '';
  const pos = p.focus ? `object-position:${(p.focus[0] * 100).toFixed(1)}% ${(p.focus[1] * 100).toFixed(1)}%` : '';
  return `<div class="${cls}${tone}"><img src="${p.src}" alt="" style="${pos}"></div>`;
}

/* A display size that never breaks a word: the smaller of the style's own
   size and what fits the longest word (or the whole name, for one line) in
   the width the caption has. em is the face's average uppercase or lowercase
   advance, measured against the bundled fonts. */
function fit(name, maxCqmin, widthCqw, em, oneLine) {
  const words = String(name || '').split(/\s+/);
  const chars = oneLine ? String(name || '').length : Math.max(...words.map((w) => w.length), 1);
  return `font-size:min(${maxCqmin}cqmin, ${(widthCqw / (chars * em)).toFixed(2)}cqw)`;
}

/* For a caption allowed two lines: size to the longer half of the name,
   split at the word break nearest the middle. */
function twoLines(name, maxCqmin, widthCqw, em) {
  const n = String(name || '');
  const words = n.split(/\s+/);
  let best = n.length;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(' ').length, b = words.slice(i).join(' ').length;
    best = Math.min(best, Math.max(a, b));
  }
  return `min(${maxCqmin}cqmin, ${(widthCqw / (best * em)).toFixed(2)}cqw)`;
}

const tag = (s) => (s.new ? '<span class="new">new</span>' : s.returning ? `<span class="new back" title="${esc(s.returning)}">back</span>` : '');

/* The bird's call as a line: the sonogram's brightest pitch over time, in
   one or more strokes. Drawn under the bird's name on the single-bird
   layouts when the cell asks for it. */
function song(d, cls) {
  const pts = d.sono && d.sono.song;
  if (!d.song_line || !pts || pts.length < 2) return '';
  const strokes = [];
  let cur = [];
  pts.forEach(([x, y], i) => {
    if (i && x - pts[i - 1][0] > 0.06) { if (cur.length > 1) strokes.push(cur); cur = []; }
    cur.push([x, y]);
  });
  if (cur.length > 1) strokes.push(cur);
  if (!strokes.length) return '';
  const lines = strokes.map((st) => `<polyline points="${st.map(([x, y]) => `${(x * 100).toFixed(1)},${(2 + (1 - y) * 20).toFixed(1)}`).join(' ')}"/>`).join('');
  return `<svg class="song ${cls || ''}" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">${lines}</svg>`;
}

/* The chorus: today's birds along the dawn, each at the time it was first
   heard. Birds that arrived long before the main run (an owl at midnight)
   are listed in a note rather than stretching the strip. Cards take lanes
   above and below the line (left and right in a tall cell) so neighbours
   clear. */
function chorus(d, showSci, tone, portrait) {
  const mins = (t) => { const [h, m] = String(t || '').split(':').map(Number); return Number.isFinite(h) ? h * 60 + (m || 0) : null; };
  const all = d.species.filter((s) => mins(s.first) != null).sort((a, b) => mins(a.first) - mins(b.first));
  if (!all.length) return `${head(d)}<div class="msg"><div class="msg-b">Nothing heard yet today.</div></div>`;
  /* the main run: the longest stretch with no gap over 90 minutes */
  let runs = [[all[0]]];
  for (let i = 1; i < all.length; i++) {
    if (mins(all[i].first) - mins(all[i - 1].first) <= 90) runs[runs.length - 1].push(all[i]);
    else runs.push([all[i]]);
  }
  const birds = runs.reduce((best, r) => (r.length > best.length ? r : best), runs[0]);
  const earlier = all.filter((b) => mins(b.first) < mins(birds[0].first));
  const later = all.filter((b) => mins(b.first) > mins(birds[birds.length - 1].first));
  const nowM = d.now_hour * 60 + (mins(d.now) % 60 || 0);
  const from = Math.floor(mins(birds[0].first) / 30) * 30;
  let to = Math.ceil((mins(birds[birds.length - 1].first) + 1) / 30) * 30;
  if (to - from < 60) to = from + 60;
  const span = to - from;
  const step = span <= 120 ? 15 : span <= 300 ? 30 : 60;
  const pos = (m) => ((m - from) / span) * 100;
  const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  /* lanes: a card takes the first lane whose last card is a card's width back */
  const gap = portrait ? 12 : 15;
  const laneEnd = [];
  const half = portrait ? 5 : 8;
  const laned = birds.map((b) => {
    /* a card at the very start or end is held inside the strip */
    const x = Math.min(Math.max(pos(mins(b.first)), half), 100 - half);
    let lane = laneEnd.findIndex((end) => x - end >= gap);
    if (lane < 0) lane = Math.min(laneEnd.length, 5);
    laneEnd[lane] = Math.max(laneEnd[lane] || -99, x);
    return { b, x, lane };
  });
  const ticks = [];
  for (let h = from; h <= to; h += step) ticks.push(`<div class="ch-tick${h % 60 === 0 ? ' maj' : ''}" style="--p:${pos(h).toFixed(2)}%"><em>${h % 60 === 0 || step < 60 ? hhmm(h) : ''}</em></div>`);
  const nowMark = nowM >= from && nowM <= to ? `<div class="ch-now" style="--p:${pos(nowM).toFixed(2)}%"></div>` : '';
  const dense = laned.some(({ lane }) => lane >= 2);
  const items = laned.map(({ b, x, lane }) => `
      <div class="ch-b l${lane}" style="--p:${x.toFixed(2)}%">
        ${pic(b.pic, 'ch-pic', tone)}
        <div class="ch-x"><div class="ch-t">${esc(b.first)}</div>
        <div class="ch-n">${esc(b.name)}${tag(b)}</div>
        ${showSci && b.sci ? `<div class="ch-s">${esc(b.sci)}</div>` : ''}</div>
      </div>`).join('');
  const aside = [...earlier.map((b) => `${esc(b.name)} ${esc(b.first)}`), ...later.map((b) => `${esc(b.name)} ${esc(b.first)}`)];
  return `
    <div class="ch">
      <div class="ch-head"><span class="ch-k">${esc(d.label)} · ${esc(d.date)}</span><span class="ch-title">Dawn chorus</span>
        <span class="ch-sub">${birds.length} birds first heard ${esc(hhmm(mins(birds[0].first)))} to ${esc(hhmm(mins(birds[birds.length - 1].first)))}${aside.length ? ` · ${earlier.length ? 'earlier' : 'later'}: ${aside.join(', ')}` : ''}</span></div>
      <div class="ch-strip${dense ? ' dense' : ''}"><div class="ch-line"></div>${ticks.join('')}${nowMark}${items}</div>
    </div>${foot(d)}`;
}

/* After dark, when nothing has called for a while: the moon and the last
   bird heard, instead of a stale day page. */
function night(d) {
  const sky = d.sky || {};
  const L = d.latest || {};
  const ph = sky.moon || 0;
  /* the lit part of the moon: a disc, a dark disc over it, and an ellipse
     whose width follows the phase */
  const k = Math.cos(ph * 2 * Math.PI);
  const right = ph < 0.5;
  const moon = `<svg class="nt-moon" viewBox="-52 -52 104 104" aria-hidden="true">
      <circle r="48" class="lit"/>
      <path class="dark" d="M0,-48 A48,48 0 0,${right ? 0 : 1} 0,48 A${(48 * Math.abs(k)).toFixed(1)},48 0 0,${(right ? k > 0 : k < 0) ? 0 : 1} 0,-48 Z"/>
    </svg>`;
  const since = d.asleep && d.asleep.quiet_min >= 60 ? `${Math.floor(d.asleep.quiet_min / 60)} h ${d.asleep.quiet_min % 60} min` : `${(d.asleep || {}).quiet_min || 0} min`;
  return `
    <div class="nt">
      ${moon}
      <div class="nt-t">The garden is asleep</div>
      <div class="nt-s">Nothing heard for ${since}${L.name ? `. Last was the ${esc(L.name)} at ${esc(L.last)}` : ''}.</div>
      <div class="nt-f">${esc(sky.moon_name || '')}${sky.sunrise ? ` · sunrise ${esc(sky.sunrise)}` : ''} · ${d.species_today} species to-day</div>
    </div>`;
}

const ROMAN = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
function roman(n) {
  let out = '';
  for (const [v, r] of ROMAN) while (n >= v) { out += r; n -= v; }
  return out || 'I';
}

/* The figure caption's dial: calls per hour round a 24-hour clock, midnight
   at the top, the current hour marked on the rim. */
function dial(hourly, nowHour) {
  const h = (hourly || []).slice(0, 24);
  const mx = Math.max(1, ...h);
  const r0 = 30, span = 58;
  const at = (i, r) => {
    const a = ((i + 0.5) / 24) * 2 * Math.PI - Math.PI / 2;
    return [(Math.cos(a) * r).toFixed(1), (Math.sin(a) * r).toFixed(1)];
  };
  const bars = h.map((v, i) => {
    if (!v) return '';
    const [x1, y1] = at(i, r0 + 1.5), [x2, y2] = at(i, r0 + 1.5 + (v / mx) * span);
    return `<line class="${v === mx ? 'pk' : ''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }).join('');
  const ticks = Array.from({ length: 24 }, (_, i) => {
    const a = (i / 24) * 2 * Math.PI - Math.PI / 2, c = Math.cos(a), sn = Math.sin(a);
    const r1 = i % 6 ? 93 : 90;
    return `<line x1="${(c * r1).toFixed(1)}" y1="${(sn * r1).toFixed(1)}" x2="${(c * 96).toFixed(1)}" y2="${(sn * 96).toFixed(1)}"/>`;
  }).join('');
  const labels = [[0, '0', 0, -101], [6, '6', 103, 0], [12, '12', 0, 104], [18, '18', -103, 0]]
    .map(([, t, x, y]) => `<text x="${x}" y="${y}">${t}</text>`).join('');
  const [nx1, ny1] = at(nowHour, 97), [nx2, ny2] = at(nowHour, 108);
  return `<svg class="fg-dial" viewBox="-116 -116 232 232" aria-hidden="true">
    <circle class="g" r="${r0 + span / 2}"/><circle class="g" r="${r0 + span}"/>
    <circle class="ax" r="${r0}"/><circle class="ax" r="96"/>
    <g class="tk">${ticks}</g><g class="br">${bars}</g>
    <line class="now" x1="${nx1}" y1="${ny1}" x2="${nx2}" y2="${ny2}"/>
    <g class="lb">${labels}</g></svg>`;
}

function head(d) {
  /* the printed header every everyday page shares: the garden and the day
     in small caps over a double rule */
  return `<div class="ph"><span class="ph-k">${esc(d.label)} · heard ${esc(d.period || 'to-day')}</span><b class="ph-d">${esc(d.date)}</b></div><div class="ph-rule"></div>`;
}

function strip(d) {
  const mx = Math.max(1, ...d.hourly_total);
  const peak = d.hourly_total.indexOf(mx);
  const labels = d.chart_labels || 'none';
  const lab = (v, h) => (v && (labels === 'counts' || (labels === 'peak' && h === peak)) ? `<b>${v}</b>` : '');
  return `<div class="strip${labels !== 'none' ? ' labelled' : ''}">${d.hourly_total.map((v, h) => `
    <div class="sb">${lab(v, h)}<i style="height:${Math.max(4, (v / mx) * 100)}%" class="${h === d.now_hour ? 'now' : v ? 'on' : ''}"></i>
    <em>${h % 6 === 0 ? String(h).padStart(2, '0') : ''}</em></div>`).join('')}</div>`;
}

function foot(d) {
  /* show: status and credits; credits: the attributions alone; hide (or the
     older false): nothing */
  const mode = d.footer === false ? 'hide' : d.footer || 'show';
  if (mode === 'hide') return '';
  const bits = [...(mode === 'credits' ? [] : d.notices || []), ...(d.credits || [])];
  return bits.length ? `<div class="credit">${bits.map(esc).join(' · ')}</div>` : '';
}

/* ---------------- layouts ---------------- */

/* The hero bird's picture to the edge of the page, its name set over the
   paper. A plate is multiplied onto the paper so its own paper disappears;
   a photo fades into the paper along the edge it meets the text. */
function hero(d, L, tone, big) {
  const p = L.pic;
  const kick = `${esc(d.hero_kick || 'Just heard')} · ${esc(L.last)}`;
  const sub = `${d.show_sci !== false && L.sci ? `${esc(L.sci)} · ` : ''}heard the ${ordinalWords(L.count) || `${L.count}`} time ${esc(d.period || 'to-day')}`;
  let picture;
  if (!p || !p.src) picture = `<div class="ev-nopic">${BIRD}</div>`;
  else {
    const plate = p.kind !== 'photo';
    const pos = p.focus ? `object-position:${(p.focus[0] * 100).toFixed(1)}% ${(p.focus[1] * 100).toFixed(1)}%` : '';
    const cls = plate ? 'ev-plate' : `ev-photo${tone && !plate ? ` toned${tone === 'light' ? ' light' : ''}` : ''}`;
    picture = `<div class="${cls}"><img src="${p.src}" alt="" style="${pos}"></div>`;
  }
  return `<div class="ev-hero${big ? ' big' : ''}">${picture}<div class="ev-fade"></div>
      <div class="ev-cap"><div class="ev-kick">${kick}</div><div class="ev-name" style="${fit(L.name, big ? 8.4 : 6.6, big ? 46 : 32, 0.56)}">${esc(L.name)}.${tag(L)}</div><div class="ev-sub">${sub}</div>${song(d, 'ev-song')}</div>
    </div>`;
}

function list(d, showSci, tone) {
  const L = d.latest;
  const others = d.species.filter((s) => s.name !== L.name);
  const rows = others.map((s) => `
    <div class="ev-row">
      <div class="ev-rn"><div class="ev-rname">${esc(s.name)}${tag(s)}</div><div class="ev-rsub">${showSci && s.sci ? `${esc(s.sci)} · ` : ''}${esc(ago(s.ago_min))}</div></div>
      <div class="ev-rct">${s.count}</div>
    </div>`).join('');
  return `
    <div class="ev">
      ${hero(d, L, tone, false)}
      <div class="ev-side">${head(d)}<div class="ev-rows fitrows">${rows}</div>
        <div class="ev-foot">${strip(d)}<span class="ev-tot">${d.species_today} species · ${d.detections_today} calls</span></div>
      </div>
    </div>${foot(d)}`;
}

function latest(d, showSci, tone) {
  const L = d.latest;
  const others = d.species.filter((s) => s.name !== L.name).sort((a, b) => b.count - a.count);
  const rows = others.map((s) => `<div class="ev-row small"><div class="ev-rn"><div class="ev-rname">${esc(s.name)}${tag(s)}</div></div><div class="ev-rct">${s.count}</div></div>`).join('');
  return `
    <div class="ev ev-latest">
      ${hero(d, L, tone, true)}
      <div class="ev-side">${head(d)}
        <div class="ev-stats"><div><b>${L.count}</b><span>calls, this bird</span></div><div><b>${Math.round(L.conf * 100)}%</b><span>certain</span></div><div><b>${d.species_today}</b><span>species to-day</span></div></div>
        <div class="ev-also">Also heard to-day</div>
        <div class="ev-rows fitrows">${rows}</div>
        <div class="ev-foot">${strip(d)}<span class="ev-tot">${d.detections_today} calls</span></div>
      </div>
    </div>${foot(d)}`;
}

function gallery(d, showSci, tone) {
  const tiles = d.species.slice(0, 12).map((s) => `
    <div class="gc${s.new ? ' isnew' : ''}">
      ${pic(s.pic, 'gc-p', tone)}
      <div class="gc-n">${esc(s.name)}${tag(s)}</div>
      ${showSci && s.sci ? `<div class="gc-s">${esc(s.sci)}</div>` : ''}
      <div class="gc-c">${s.count}<span> calls · ${esc(ago(s.ago_min))}</span></div>
    </div>`).join('');
  return `${head(d)}<div class="grid n${Math.min(d.species.length, 12)}">${tiles}</div>
    <div class="ev-foot bare"><span class="ev-tot">${d.species_today} species · ${d.detections_today} calls</span></div>${foot(d)}`;
}

function chart(d) {
  /* hours with any calls, padded to a sensible day; owls widen it to the night */
  const used = d.hourly_total.map((v, h) => (v ? h : null)).filter((h) => h != null);
  const from = Math.min(5, ...used), to = 23;
  const hours = [];
  for (let h = from; h <= to; h++) hours.push(h);
  const rows = [...d.species].sort((a, b) => b.count - a.count);
  const mx = Math.max(1, ...rows.flatMap((s) => hours.map((h) => s.hourly[h] || 0)));
  const labels = d.chart_labels || 'none';
  /* a label in every cell that has calls, or only in each bird's busiest hour */
  const cell = (v, h, rowMax) => {
    if (h > d.now_hour) return '<i class="fut"></i>';
    if (!v) return '<i></i>';
    const l = v / mx;
    const show = labels === 'counts' || (labels === 'peak' && v === rowMax);
    return `<i class="${l > 0.55 ? 'l3' : l > 0.25 ? 'l2' : 'l1'}">${show ? `<b>${v}</b>` : ''}</i>`;
  };
  return `${head(d)}
    <div class="chart${labels !== 'none' ? ' labelled' : ''}"><div class="crows">
      ${rows.map((s, i) => { const rowMax = Math.max(...hours.map((h) => s.hourly[h] || 0)); return `<div class="crow"><div class="ci">${i + 1}.</div><div class="cn">${esc(s.name)}${tag(s)}</div>
        <div class="cells">${hours.map((h) => cell(s.hourly[h] || 0, h, rowMax)).join('')}</div><div class="cc">${s.count}</div></div>`; }).join('')}</div>
      <div class="crow axis"><div class="ci"></div><div class="cn"></div><div class="cells">${hours.map((h) => `<em>${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</em>`).join('')}</div><div class="cc"></div></div>
    </div><div class="ev-foot bare"><span class="ev-tot">${d.species_today} species · ${d.detections_today} calls · calls per hour, darker is busier</span></div>${foot(d)}`;
}

/* The atlas: every bird ever heard here as a stamp on a sheet, the ones
   heard most days first, with the empty perforations of the stamps still to
   come. A life list that fills as visitors arrive. */
function atlas(d, showSci, tone) {
  const A = d.atlas || { birds: [], since: '', days: 0 };
  const birds = A.birds || [];
  const day = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || ''); if (!m) return esc(iso || ''); const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']; return `${Number(m[3])} ${months[Number(m[2]) - 1]}`; };
  const stamps = birds.map((b) => `
    <div class="at-s${d.species.some((s) => s.name === b.name) ? ' heard' : ''}">
      ${d.pictures === 'none' ? `<div class="at-p nopic">${BIRD}</div>` : pic(b.pic, 'at-p', tone)}
      <div class="at-n">${esc(b.name)}</div>
      ${showSci && b.sci ? `<div class="at-sci">${esc(b.sci)}</div>` : ''}
      <div class="at-m">first ${day(b.first)} · ${b.days} day${b.days === 1 ? '' : 's'}</div>
    </div>`).join('');
  /* a few empty perforations after the last stamp, for what is still to come */
  /* the empty perforations after the last stamp: the rest of its row, six across or four in a tall cell */
  const cols = (d.cell_portrait ? 4 : 6);
  const empty = Array.from({ length: birds.length % cols ? cols - (birds.length % cols) : 0 }, () => '<div class="at-s at-empty"></div>').join('');
  const kick = A.since ? `${birds.length} species since ${day(A.since)} · ${A.days} day${A.days === 1 ? '' : 's'} logged` : `${birds.length} species`;
  return `
    <div class="ph"><span class="ph-k">${esc(d.label)} · life list</span><b class="ph-d">${esc(kick)}</b></div><div class="ph-rule"></div>
    <div class="at-grid">${stamps}${empty}</div>${foot(d)}`;
}

function plate(d, showSci, tone) {
  const L = d.latest;
  const others = d.species.filter((s) => s.name !== L.name).slice(0, 8);
  const nth = ordinalWords(L.count);
  return `
    <div class="page">
      <div class="pframe">${pic(L.pic, 'pp', tone)}</div>
      <div class="ptext">
        <div class="pkick">${d.hero_kick ? `${esc(d.hero_kick)} · ` : ''}Last heard ${esc(L.last)}${L.first ? ` · first at ${esc(L.first)}` : ''}</div>
        <div class="pname">${esc(L.name)}.</div>
        ${showSci && L.sci ? `<div class="psci">${esc(L.sci)}.</div>` : ''}
        ${song(d, 'p-song')}
        <div class="prule"></div>
        <div class="pline">${nth ? `Heard the ${esc(nth)} time to-day` : ''}, ${Math.round(L.conf * 100)} per cent. certain.</div>
        <div class="pkick sp">In the garden to-day · ${d.species_today} species</div>
        <div class="pcols">${others.map((s) => `<div><span>${esc(s.name)}</span><b>${s.count}</b></div>`).join('')}</div>
        <div class="pcredit">${esc((L.pic && L.pic.credit) || '')}</div>
      </div>
    </div>`;
}

function bleed(d, showSci, tone, caption) {
  const L = d.latest;
  const sci = showSci && L.sci ? esc(L.sci) : '';
  const when = ago(L.ago_min);
  let cap;
  if (caption === 'editorial') {
    cap = `<div class="ed-fade"></div><div class="ed">
      <div class="ed-k">${d.hero_kick ? esc(d.hero_kick) : `Heard ${esc(when)}`}</div>
      <div class="ed-n" style="${fit(L.name, 24, 46, 0.5)}">${esc(L.name)}</div>
      ${sci ? `<div class="ed-s">${sci}</div>` : ''}
      ${song(d, 'ed-song')}
      <div class="ed-st"><span><b>${L.count}</b> calls today</span><span><b>${d.species_today}</b> species</span></div></div>`;
  } else if (caption === 'notebook') {
    cap = `<div class="nb">
      <div class="nb-d">${esc(d.date)} · ${esc(L.last)}</div>
      <div class="nb-n">${esc(L.name)}!</div>
      ${sci ? `<div class="nb-s">${sci}</div>` : ''}
      <div class="nb-t">heard ${esc(when)} &mdash; <u>${L.count}</u> times today</div>
      <div class="nb-t">${d.species_today} species so far &#10003;</div>
      <div class="nb-c">BirdNET ${Math.round(L.conf * 100)}% sure</div></div>`;
  } else if (caption === 'folio') {
    const nth = ordinalWords(L.count);
    cap = `<div class="fo-frame"></div>
      <div class="fo-head"><span>${esc(d.label)}</span><span>Pl. ${roman(L.order || 1)}.</span></div>
      <div class="fo-cap">
        <div class="fo-n" style="${fit(`${L.name}.`, 7.4, 84, 0.68, true)}">${esc(L.name)}.</div>
        ${sci ? `<div class="fo-s">${sci}.</div>` : ''}
        ${song(d, 'fo-song')}
        <div class="fo-l">${nth ? `Heard the ${esc(nth)} time to-day` : 'Heard to-day'}, last at ${esc(L.last)}.</div></div>`;
  } else if (caption === 'museum') {
    cap = `<div class="mu">
        <div class="mu-h">${esc(d.label)}</div>
        <div class="mu-h2">Collection of Birds · Reg. No. ${esc(d.date_iso.slice(0, 4))}.${String(d.detections_today).padStart(4, '0')}</div>
        <dl class="mu-f">
          <dt>Name</dt><dd>${esc(L.name)}</dd>
          ${sci ? `<dt>Species</dt><dd class="it">${sci}</dd>` : ''}
          <dt>Date</dt><dd>${esc(d.date)}, ${esc(L.last)}</dd>
          <dt>Calls</dt><dd>${L.count} to-day</dd>
          <dt>Det.</dt><dd>BirdNET, ${Math.round(L.conf * 100)}%</dd>
        </dl></div>`;
  } else if (caption === 'figure') {
    const hourly = L.hourly || [];
    const peak = hourly.length ? hourly.indexOf(Math.max(...hourly)) : -1;
    cap = `<div class="fg">
      <div class="fg-t">
        <div class="fg-k">Fig. ${L.order || 1}.</div>
        <div class="fg-n" style="font-size:${twoLines(L.name, 7.6, 40, 0.5)}">${esc(L.name)}</div>
        ${sci ? `<div class="fg-s">${sci}</div>` : ''}
      </div>
      ${dial(hourly, d.now_hour)}
      <div class="fg-c"><b>Diurnal calling</b>, ${esc(d.date)}. Calls per hour round the clock, n&nbsp;=&nbsp;${L.count}${peak >= 0 ? `; most at ${String(peak).padStart(2, '0')}:00` : ''}. The mark on the rim is now.</div></div>`;
  } else if (caption === 'cyanotype') {
    const main = sci || esc(L.name);
    cap = `<div class="cy-edge"></div><div class="cy">
      <div class="cy-n" style="${fit(`${main}.`, 6.4, 80, 0.46, true)}">${main}.</div>
      <div class="cy-s">${sci ? `${esc(L.name)} · ` : ''}heard ${L.count} times · ${esc(d.date)}</div></div>`;
  } else if (caption === 'stamp') {
    cap = `<div class="st-card"></div><div class="st-face"></div>
      <div class="st-top">${esc(d.label)}</div>
      <div class="st-val"><b>${L.count}</b><span>calls</span></div>
      <div class="st-bot"><div class="st-n" style="${fit(L.name, 6.4, 70, 0.68, true)}">${esc(L.name)}</div>${sci ? `<div class="st-s">${sci}</div>` : ''}</div>`;
  } else if (caption === 'woodblock') {
    const seal = String(L.name || '').split(/[\s-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
    cap = `<div class="wb-col">
        <div class="wb-title"><span>${esc(L.name)}</span></div>
        ${sci ? `<div class="wb-sub"><span>${sci}</span></div>` : ''}
      </div>
      <div class="wb-note">heard ${L.count} times · ${esc(d.date)}</div>
      <div class="wb-seal">${esc(seal)}</div>`;
  } else if (caption === 'cameo') {
    cap = `<div class="cm-ring"></div>
      <div class="cm-cap"><div class="cm-n" style="${fit(L.name, 6.2, 70, 0.68, true)}">${esc(L.name)}</div>
        <div class="cm-s">${sci ? `${sci} · ` : ''}heard ${esc(when)}</div></div>`;
  } else if (caption === 'nouveau') {
    cap = `<div class="nv-arch"></div>
      <div class="nv-band"><div class="nv-n" style="${fit(L.name, 6.6, 66, 0.52, true)}">${esc(L.name)}</div>
        <div class="nv-s">${sci ? `${sci} · ` : ''}${L.count} calls to-day</div></div>`;
  } else if (caption === 'sonogram') {
    const sn = d.sono;
    cap = `<div class="sn">
      <div class="sn-h"><span class="sn-n" style="${fit(L.name, 5.6, 56, 0.68, true)}">${esc(L.name)}</span>${sci ? `<span class="sn-s">${sci}</span>` : ''}</div>
      <div class="sn-img">${sn && sn.src ? `<img src="${sn.src}" alt="">` : `<div class="sn-none">No recording of this call yet</div>`}</div>
      <div class="sn-c"><span>${sn && sn.at ? `Call recorded ${esc(sn.at)}` : 'Sonogram'} · ${L.count} calls to-day</span><span>time &rarr; · pitch &uarr;</span></div></div>`;
  } else if (caption === 'tag') {
    cap = `<div class="tg"><svg class="tg-bg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polygon points="0,12 8,0 100,0 100,100 8,100 0,88"/></svg>
      <div class="tg-k">${esc(d.label).toUpperCase()} · NO. ${String(d.detections_today).padStart(4, '0')}</div>
      <div class="tg-n">${esc(L.name)}</div>
      ${sci ? `<div class="tg-s">${sci}</div>` : ''}
      <div class="tg-f">HEARD.... ${esc(d.date_iso)} ${esc(L.last)}<br>CALLS.... ${L.count} today<br>CONF..... ${L.conf.toFixed(2)}<br>SPECIES.. ${d.species_today} today</div></div>`;
  } else if (caption === 'poster') {
    cap = `<div class="po">
      <div class="po-n" style="${fit(L.name, 15, 33, 0.74)}">${esc(L.name)}</div>
      ${sci ? `<div class="po-s">${sci}</div>` : ''}
      <div class="po-st"><div><b>${L.count}</b><span>calls today</span></div><div><b>${esc(L.last)}</b><span>last heard</span></div></div></div>`;
  } else {
    cap = `<div class="bd">
      <div class="bd-t"><div class="kick">${esc(d.hero_kick || 'Just heard')} · ${esc(when)}</div><div class="bd-n" style="${fit(L.name, 7.4, 62, 0.56, true)};--two:${twoLines(L.name, 7.4, 56, 0.56)}">${esc(L.name)}${tag(L)}</div></div>
      <div class="bd-c a"><b>${L.count}</b><span>calls today</span></div>
      <div class="bd-c k"><b>${d.species_today}</b><span>species</span></div></div>`;
  }
  const credit = L.pic && L.pic.credit ? `<div class="bcredit">${esc(L.pic.credit)}</div>` : '';
  const paper = L.pic && L.pic.paper ? ` style="background:${esc(L.pic.paper)}"` : '';
  return `<div class="bl cap-${caption}${L.pic && L.pic.paper ? ' on-paper' : ''}"${paper}>${pic(L.pic, 'bp', tone)}${cap}${credit}</div>`;
}

/* Cluster: today's busiest birds together in one picture. With AI paintings
   it is the birds' own paintings blended into one scene (or, until that is
   ready, cut out and laid on one sheet); otherwise a numbered montage of
   their plates and photos, numbered to match the key. */
/* The cluster as one printed page, in one of five looks (C.mode): a
   specimen sheet, a numbered plate with a key, a wreath round a title, a
   field notebook, or a specimen drawer. The server has laid the page out
   and says where each label goes (b.at, as fractions of the page). */
function sheet(d, C) {
  const mode = C.mode || 'sheet';
  const birds = C.birds || [];
  const lines = C.lines || ['name'];
  /* key: everything; under: the text under each bird alone; none: the picture alone */
  const text = d.cluster_text || 'key';
  const keyed = mode === 'numbered' && text === 'key';
  /* the text under a bird is HTML over the picture, never baked into it,
     built from the lines the cell asks for */
  const labelOf = (b) => lines.map((l) => {
    if (l === 'name') return `<span class="sh-ln">${esc(b.name)}</span>`;
    if (l === 'sci') return b.sci ? `<span class="sh-ln sh-sci">${esc(b.sci)}</span>` : '';
    if (l === 'count') return `<span class="sh-ln sh-sm">${b.count} call${b.count === 1 ? '' : 's'} to-day</span>`;
    if (l === 'last') return b.last ? `<span class="sh-ln sh-sm">last heard ${esc(b.last)}</span>` : '';
    if (l === 'rarity') return b.rarity ? `<span class="sh-ln sh-sm">${esc(b.rarity)}</span>` : '';
    if (l === 'new') return b.new ? '<span class="sh-ln"><span class="sh-new">new</span></span>' : '';
    return '';
  }).join('');
  const labels = text === 'none' ? '' : birds.map((b, i) => {
    const [x, y] = b.at || [b.box[0] + b.box[2] / 2, b.box[1] + b.box[3]];
    const t = keyed ? String(i + 1) : labelOf(b);
    if (!t) return '';
    const rot = mode === 'notebook' && b.rot ? `;transform:translate(-50%, 15%) rotate(${-b.rot}deg)` : '';
    return `<span class="sh-l" style="left:${(x * 100).toFixed(2)}%;top:${(y * 100).toFixed(2)}%${rot}">${t}</span>`;
  }).join('');
  const waiting = (C.waiting || []).length;
  const verb = C.waiting_verb || 'paint';
  const line = `${esc(d.label)} · heard to-day · ${esc(d.date)} · ${d.species_today} species${waiting ? ` · ${waiting} still to ${verb}` : ''}`;
  let extra = `<div class="sh-c">${line}</div>`;
  if (mode === 'wreath') {
    extra = `<div class="sh-w"><div class="sh-wk">${esc(d.label)}</div><div class="sh-wt">Heard to-day</div>
      <div class="sh-wd">${esc(d.date)} · ${d.species_today} species${waiting ? ` · ${waiting} still to paint` : ''}</div></div>`;
  } else if (mode === 'notebook') {
    extra = `<div class="sh-nb">${esc(d.label)}, ${esc(d.date)} &mdash; ${d.species_today} species heard${waiting ? `, ${waiting} still to draw` : ''}</div>`;
  } else if (mode === 'drawer') {
    extra = `<div class="sh-plaque">${esc(d.label)} · ${esc(d.date)}</div>`;
  }
  const page = `<div class="cl-sheet sh-s sh-${keyed ? 'numbered' : mode}" style="--r:${C.ratio};--lf:${(C.label || 0.017) * (d.text_scale || 1)}"><img src="${C.pic.src}" alt="">${labels}${text === 'key' && mode !== 'numbered' ? extra : ''}</div>`;
  if (keyed) {
    const rows = birds.map((b, i) => `<li><em>${i + 1}.</em><span>${esc(b.name)}</span><b>${b.count}</b></li>`).join('');
    return `
    <div class="cl cl-numbered" style="background:${esc(C.pic.paper)}">
      <div class="cl-pic">${page}</div>
      <div class="cl-key">
        <div class="cl-k">${esc(d.label)} · ${esc(d.date)}</div>
        <div class="cl-t">Heard to-day</div>
        <ol class="fitrows">${rows}</ol>
        <div class="cl-f">${d.species_today} species · ${d.detections_today} calls to-day${waiting ? ` · ${waiting} still to paint` : ''}</div>
      </div>
    </div>${foot(d)}`;
  }
  return `<div class="sh" style="background:${mode === 'drawer' ? '#3a2c20' : esc(C.pic.paper)}">${page}</div>${foot(d)}`;
}

function cluster(d, showSci, tone) {
  const C = d.cluster || { birds: [] };
  if (C.sheet && C.pic) return sheet(d, C);
  const birds = C.birds || [];
  const text = d.cluster_text || 'key';
  const numbered = !C.pic && text === 'key';
  /* on the blended scene (or the collage standing in for it) the labels go
     where each bird sat on the collage; on the montage, under each tile */
  const lines = C.lines || ['name'];
  const labelOf = (b) => lines.map((l) => {
    if (l === 'name') return `<span class="sh-ln">${esc(b.name)}</span>`;
    if (l === 'sci') return b.sci ? `<span class="sh-ln sh-sci">${esc(b.sci)}</span>` : '';
    if (l === 'count') return `<span class="sh-ln sh-sm">${b.count} call${b.count === 1 ? '' : 's'} to-day</span>`;
    return '';
  }).join('');
  const boxes = Array.isArray(C.boxes) && C.boxes.length === birds.length ? C.boxes : null;
  const labels = text !== 'none' && boxes ? birds.map((b, i) => {
    const [x, y, w, h] = boxes[i];
    const t = labelOf(b);
    return t ? `<span class="sh-l" style="left:${((x + w / 2) * 100).toFixed(2)}%;top:${((y + h) * 100).toFixed(2)}%">${t}</span>` : '';
  }).join('') : '';
  const caption = (b, i) => (text === 'none' ? '' : `<figcaption>${numbered ? i + 1 : esc(b.name)}</figcaption>`);
  const art = C.pic
    ? `<div class="cl-sheet sh-s" style="--r:${C.aspect === '3:4' ? 0.75 : 4 / 3};--lf:${(C.label || 0.017) * (d.text_scale || 1)}"><img src="${C.pic.src}" alt="">${labels}</div>`
    : `<div class="cl-mont n${Math.min(birds.length, 6)}">${birds.map((b, i) => `
        <figure>${pic(b.pic, 'cl-tile', tone)}${caption(b, i)}</figure>`).join('')}</div>`;
  const rows = birds.map((b, i) => `
      <li>${numbered ? `<em>${i + 1}.</em>` : ''}<span>${esc(b.name)}${showSci && b.sci ? `<i>${esc(b.sci)}</i>` : ''}</span><b>${b.count}</b></li>`).join('');
  const waiting = (C.waiting || []).length ? `<div class="cl-f">Still to paint: ${C.waiting.map(esc).join(', ')}</div>` : '';
  const paper = C.pic && C.pic.paper ? ` style="background:${esc(C.pic.paper)}"` : '';
  const key = text === 'key' ? `
      <div class="cl-key">
        <div class="cl-k">${esc(d.label)} · ${esc(d.date)}</div>
        <div class="cl-t">Heard to-day</div>
        <ol class="fitrows">${rows}</ol>
        <div class="cl-f">${d.species_today} species · ${d.detections_today} calls to-day</div>${waiting}
      </div>` : '';
  return `
    <div class="cl${numbered ? ' montage' : ''}${key ? '' : ' nokey'}"${paper}>
      <div class="cl-pic">${art}</div>${key}
    </div>${foot(d)}`;
}

/* Chromium ignores @font-face inside a shadow root, so the caption faces are
   registered on the host document, once per set: the id is a hash of the CSS,
   and two cells sharing a style share the rules. */
function installFonts(shadow, css) {
  if (!css) return;
  const doc = (shadow && shadow.host && shadow.host.ownerDocument) || (typeof document !== 'undefined' ? document : null);
  if (!doc || !doc.head) return;
  let h = 0;
  for (let i = 0; i < css.length; i += 97) h = (h * 31 + css.charCodeAt(i)) >>> 0;
  const id = `backyard-birds-fonts-${h.toString(36)}-${css.length}`;
  if (doc.getElementById(id)) return;
  const el = doc.createElement('style');
  el.id = id;
  el.textContent = css;
  doc.head.appendChild(el);
}

export default function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  const size = (ctx && ctx.cell && ctx.cell.size) || 'md';
  const layout = d.layout || 'list';
  const showSci = d.show_sci !== false;
  /* false, true (full sepia) or 'light' (a warm cast over most of the colour) */
  const tone = String(d.pictures || '').startsWith('plate') && d.photo_tone !== 'natural' && (d.photo_tone === 'light' ? 'light' : true);

  let body;
  if (d.error) {
    body = `<div class="msg"><div class="msg-t">Backyard birds</div><div class="msg-b">${esc(d.error)}</div></div>`;
  } else if (d.empty || !d.latest) {
    body = `${head(d)}<div class="msg"><div class="msg-b">Nothing heard yet today.</div>
            <div class="msg-h">BirdNET-Go lists a bird once it is heard above the confidence you set.</div></div>`;
  } else if (d.asleep) body = night(d);
  else if (layout === 'chorus') body = chorus(d, showSci, tone, !!(shadow.host && shadow.host.clientHeight > shadow.host.clientWidth));
  else if (layout === 'latest') body = latest(d, showSci, tone);
  else if (layout === 'gallery') body = gallery(d, showSci, tone);
  else if (layout === 'chart') body = chart(d);
  else if (layout === 'plate') body = plate(d, showSci, tone);
  else if (layout === 'bleed') body = bleed(d, showSci, tone, d.caption || 'band');
  else if (layout === 'cluster') body = cluster(d, showSci, tone);
  else if (layout === 'atlas') { d.cell_portrait = !!(shadow.host && shadow.host.clientHeight > shadow.host.clientWidth); body = atlas(d, showSci, tone); }
  else body = list(d, showSci, tone);

  installFonts(shadow, d.font_css);
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="bb lay-${esc(layout)}${d.asleep ? ' lay-night' : ''} size-${esc(size)} lv-${esc(d.panel || 'colour')} font-${esc(d.font || 'classic')}" style="--ts:${Number(d.text_scale) > 0 ? Number(d.text_scale) : 1}">${body}</div>
    <style>
      :host { display: block; height: 100%; }
      .bb {
        container-type: size; height: 100%; width: 100%; overflow: hidden; position: relative;
        display: flex; flex-direction: column;
        background: var(--surface, #fff); color: var(--text-primary, #111);
        font-family: var(--font-family, inherit); font-variant-numeric: tabular-nums;
        --ink: var(--text-primary, #111); --paper: var(--surface, #fff);
        --hi: var(--accent-2, #f2c230); --alert: var(--accent-1, #c8281e);
        --muted: var(--surface-sunken, #e9e6df);
        --rule: clamp(2px, 0.5cqmin, 5px);
        /* the type on the printed looks: a roman and a small-caps face */
        --bb-serif: 'IM Fell English', Georgia, serif; --bb-sc: 'IM Fell English SC', Georgia, serif;
      }
      .font-playfair { --bb-serif: 'Playfair Display', Georgia, serif; --bb-sc: 'Playfair Display', Georgia, serif; }
      .font-typewriter { --bb-serif: 'Special Elite', 'Courier New', monospace; --bb-sc: 'Special Elite', 'Courier New', monospace; }
      .font-hand { --bb-serif: Caveat, cursive; --bb-sc: Caveat, cursive; }
      .font-modern { --bb-serif: 'Archivo Narrow', Arial, sans-serif; --bb-sc: 'Archivo Black', Arial, sans-serif; }
      .font-theme { --bb-serif: var(--font-family, inherit); --bb-sc: var(--font-family, inherit); }
      .ico { width: 100%; height: 100%; fill: currentColor; display: block; }
      img { display: block; width: 100%; height: 100%; object-fit: cover; }
      .nopic { background: var(--muted); color: var(--text-secondary, #666); padding: 18%; }
      .toned { background: #f3ead6; }
      .bb .fit img { object-fit: contain; }
      .toned img { filter: grayscale(1) sepia(.5) contrast(1.12) brightness(1.04); mix-blend-mode: multiply; }
      .toned.light { background: #f8f2e4; }
      .toned.light img { filter: saturate(.7) sepia(.2) contrast(1.04); }
      .new {
        display: inline-block; margin-left: 1.4cqmin; padding: 0.3cqmin 1cqmin; vertical-align: 0.4cqmin;
        background: var(--alert); color: #fff; font-size: 2.2cqmin; font-weight: 800;
        letter-spacing: .08em; text-transform: uppercase; line-height: 1.2;
      }
      .kick { font-size: 2.6cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--alert); }
      .credit, .bcredit { font-size: 1.9cqmin; opacity: .6; padding: 0.8cqmin 2.4cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

      /* the everyday pages: list, latest, gallery and chart are printed
         matter like the plates, on the same paper in the same type */
      .lay-list, .lay-latest, .lay-gallery, .lay-chart { background: #f3ead6; color: #2a2116; font-family: var(--bb-serif); --paper: #f3ead6; --ink: #2a2116; --muted: #d9cba5; }
      .ph { display: flex; justify-content: space-between; align-items: baseline; gap: 2cqmin; padding: 0 0 .6cqmin; }
      .ph-k { font-family: var(--bb-sc); font-size: 2.5cqmin; letter-spacing: .13em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ph-d { font-weight: 400; font-size: 4.2cqmin; letter-spacing: .04em; white-space: nowrap; }
      .ph-rule { border-top: 1px solid var(--ink); position: relative; margin-bottom: .8cqmin; flex: 0 0 auto; }
      .ph-rule::after { content: ""; position: absolute; left: 0; right: 0; top: 2px; border-top: 1px solid var(--ink); }
      .lay-gallery .ph, .lay-chart .ph { padding: 3cqmin 3.4cqmin .6cqmin; }
      .lay-gallery .ph-rule, .lay-chart .ph-rule { margin: 0 3.4cqmin .8cqmin; }

      /* list and latest: the hero to the edge, the rest beside it */
      .ev { flex: 1; min-height: 0; display: flex; }
      .ev-hero { flex: 0 0 40%; min-width: 0; position: relative; }
      .ev-hero.big { flex-basis: 54%; }
      .ev-plate, .ev-photo, .ev-nopic { position: absolute; inset: 0; overflow: hidden; }
      .ev-plate img, .ev-photo img { width: 100%; height: 100%; object-fit: cover; }
      .ev-plate img { mix-blend-mode: multiply; }
      .ev-photo { mask-image: linear-gradient(to right, #000 78%, transparent 100%); -webkit-mask-image: linear-gradient(to right, #000 78%, transparent 100%); }
      .ev-nopic { padding: 26% 26% 40%; color: #cdbf9d; }
      .ev-cap { position: absolute; left: 3.4cqmin; right: 2cqmin; bottom: 3cqmin; z-index: 2; }
      /* the paper rises over the foot of the picture so the name sits on paper, not on feathers */
      .ev-fade { position: absolute; left: 0; right: 0; bottom: 0; height: 46%; background: linear-gradient(to top, var(--paper) 42%, rgba(243, 234, 214, 0)); z-index: 1; }
      .ev-hero.big .ev-fade { height: 46%; background: linear-gradient(to top, var(--paper) 50%, rgba(243, 234, 214, 0)); }
      .ev-kick { font-family: var(--bb-sc); font-size: 2.3cqmin; letter-spacing: .14em; color: #6b5a3a; }
      .ev-name { font-family: var(--bb-sc); font-size: 6.6cqmin; line-height: 1.02; letter-spacing: .02em; margin-top: .4cqmin; }
      .ev-hero.big .ev-name { font-size: 8.4cqmin; }
      .ev-sub { font-style: italic; font-size: 2.9cqmin; margin-top: .6cqmin; }
      .ev-song { width: 60%; height: 4cqmin; margin-top: 1cqmin; }
      .ev-side { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 3cqmin 3.4cqmin 2.2cqmin 3cqmin; }
      .ev-rows { flex: 1 1 0; min-height: 0; }
      .ev-row { display: flex; align-items: baseline; gap: 2cqmin; padding: 1.3cqmin 0; border-bottom: 1px solid #c9b98f; }
      .ev-row.small { padding: .9cqmin 0; }
      .ev-rn { flex: 1; min-width: 0; }
      .ev-rname { font-family: var(--bb-sc); font-size: 3.7cqmin; letter-spacing: .03em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ev-row.small .ev-rname { font-size: 3.3cqmin; }
      .ev-rsub { font-style: italic; font-size: 2.4cqmin; opacity: .75; margin-top: -.3cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ev-rct { font-size: 4.8cqmin; font-variant-numeric: tabular-nums; min-width: 7cqmin; text-align: right; }
      .ev-row.small .ev-rct { font-size: 3.8cqmin; }
      .ev-stats { display: flex; gap: 3cqmin; padding: 1cqmin 0 1.4cqmin; border-bottom: 1px solid #c9b98f; }
      .ev-stats > div { flex: 0 0 auto; }
      .ev-stats b { display: block; font-weight: 400; font-size: 6cqmin; line-height: 1; font-variant-numeric: tabular-nums; }
      .ev-stats span { font-family: var(--bb-sc); font-size: 2cqmin; letter-spacing: .12em; }
      .ev-also { font-family: var(--bb-sc); font-size: 2.3cqmin; letter-spacing: .14em; padding: 1.4cqmin 0 .4cqmin; }
      .ev-foot { flex: 0 0 auto; display: flex; align-items: flex-end; gap: 2.4cqmin; margin-top: 1.4cqmin; }
      .ev-foot.bare { padding: 0 3.4cqmin 1.6cqmin; justify-content: space-between; }
      .ev-tot { font-family: var(--bb-sc); font-size: 2.3cqmin; letter-spacing: .12em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .strip { flex: 1; display: flex; gap: .5cqmin; height: 6cqmin; border-bottom: 1px solid var(--ink); }
      .sb { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; }
      .sb i { display: block; background: #d9cba5; }
      .sb i.on { background: var(--ink); opacity: .72; }
      .sb i.now { background: #b4351f; opacity: 1; }
      .sb em { font-style: normal; font-family: var(--bb-sc); font-size: 1.7cqmin; height: 2.2cqmin; line-height: 2.2cqmin; text-align: center; white-space: nowrap; overflow: visible; }
      .sb b { font-size: 1.8cqmin; text-align: center; }
      .new { background: #b4351f; color: #fff; font-family: var(--bb-sc); font-weight: 400; vertical-align: .6cqmin; }
      @container (max-aspect-ratio: 1) {
        .ev { flex-direction: column; }
        .ev-hero { flex: 0 0 44%; }
        .ev-hero.big { flex-basis: 52%; }
        .ev-photo { mask-image: linear-gradient(to bottom, #000 76%, transparent 100%); -webkit-mask-image: linear-gradient(to bottom, #000 76%, transparent 100%); }
        .ev-fade { height: 40%; }
        .ev-side { padding-top: 2cqmin; }
        .ev-name { font-size: 7.2cqmin; }
      }
      @container (max-height: 260px) { .ev-rsub, .ev-foot .strip { display: none; } }

      /* gallery: specimen cards on mounts */
      .grid { flex: 1; min-height: 0; display: grid; gap: 2cqmin; padding: .6cqmin 3.4cqmin 1cqmin; grid-template-columns: repeat(4, 1fr); grid-auto-rows: 1fr; }
      .grid.n1 { grid-template-columns: 1fr; } .grid.n2 { grid-template-columns: repeat(2, 1fr); }
      .grid.n3 { grid-template-columns: repeat(3, 1fr); } .grid.n4 { grid-template-columns: repeat(4, 1fr); }
      .grid.n5, .grid.n6 { grid-template-columns: repeat(3, 1fr); }
      .grid.n7, .grid.n8 { grid-template-columns: repeat(4, 1fr); }
      .grid.n9, .grid.n10, .grid.n11, .grid.n12 { grid-template-columns: repeat(4, 1fr); }
      @container (max-aspect-ratio: 1) {
        .grid { grid-template-columns: repeat(2, 1fr) !important; }
        .grid.n7, .grid.n8, .grid.n9, .grid.n10, .grid.n11, .grid.n12 { grid-template-columns: repeat(3, 1fr) !important; }
      }
      .gc { background: #fffdf6; box-shadow: 0 0 0 1px #c9b98f; padding: 1.2cqmin 1.2cqmin 1cqmin; display: flex; flex-direction: column; min-height: 0; text-align: center; }
      .gc-p { flex: 1; min-height: 0; overflow: hidden; }
      .gc-p img { object-fit: cover; }
      .gc-p.fit { padding: .6cqmin; }
      .gc-p.fit img { mix-blend-mode: multiply; }
      .gc-n { font-family: var(--bb-sc); font-size: 2.7cqmin; letter-spacing: .03em; margin-top: .8cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .gc-s { font-style: italic; font-size: 1.9cqmin; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .gc-c { font-size: 3.2cqmin; margin-top: .2cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .gc-c span { font-size: 1.9cqmin; font-style: italic; opacity: .75; }
      .gc.isnew { box-shadow: 0 0 0 1px #c9b98f, inset 0 0 0 2px #b4351f; }
      @container (max-height: 320px) { .gc-s, .gc-c span { display: none; } }

      /* chart: a ledger of calls per hour */
      .chart { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 3.4cqmin; gap: .6cqmin; }
      .crows { flex: 1 1 0; min-height: 0; display: flex; flex-direction: column; flex-wrap: wrap; align-content: flex-start; overflow: hidden; }
      .crow { flex: 1 1 0; width: 100%; min-height: 4.4cqmin; max-height: 13cqmin; display: flex; align-items: center; gap: 1.6cqmin; border-bottom: 1px solid #c9b98f; }
      .crow .ci { width: 3.2cqmin; font-size: 2.2cqmin; opacity: .6; }
      .crow .cn { width: 30%; font-family: var(--bb-sc); font-size: 3.1cqmin; letter-spacing: .02em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cells { flex: 1; display: flex; gap: .35cqmin; height: 58%; }
      .cells i { flex: 1; background: transparent; box-shadow: inset 0 -1px 0 #d9cba5; }
      .cells i.l1 { background: color-mix(in srgb, var(--ink) 30%, var(--paper)); box-shadow: none; }
      .cells i.l2 { background: color-mix(in srgb, var(--ink) 62%, var(--paper)); box-shadow: none; }
      .cells i.l3 { background: var(--ink); box-shadow: none; }
      .cells i.fut { background: repeating-linear-gradient(45deg, transparent 0 3px, #d9cba5 3px 4px); box-shadow: none; }
      .cells b { display: block; font-size: 1.9cqmin; text-align: center; line-height: 1; color: var(--paper); mix-blend-mode: difference; }
      .crow .cc { width: 8cqmin; text-align: right; font-size: 3.8cqmin; font-variant-numeric: tabular-nums; }
      .crow.axis { flex: 0 0 auto; height: 3.2cqmin; border-bottom: 0; }
      .axis em { flex: 1; font-style: normal; font-family: var(--bb-sc); font-size: 1.9cqmin; letter-spacing: .08em; }

      /* atlas: stamps on a sheet, perforated edges, the empty ones still to come */
      .lay-atlas { background: #f3ead6; color: #2a2116; font-family: var(--bb-serif); --paper: #f3ead6; --ink: #2a2116; }
      .lay-atlas .ph { padding: 3cqmin 3.4cqmin .6cqmin; }
      .lay-atlas .ph-rule { margin: 0 3.4cqmin .8cqmin; }
      .lay-atlas .ph-d { font-size: 2.6cqmin; font-family: var(--bb-sc); letter-spacing: .1em; }
      .at-grid { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); grid-auto-rows: minmax(0, 1fr); gap: 1.6cqmin; padding: .6cqmin 3.4cqmin 1.4cqmin; overflow: hidden; }
      .at-s { background: #fffdf6; padding: 1cqmin 1cqmin .8cqmin; text-align: center; display: flex; flex-direction: column; min-height: 0; min-width: 0; position: relative;
        box-shadow: 0 0 0 1px #c9b98f; border: .6cqmin dotted #f3ead6; outline: 1px solid #c9b98f; outline-offset: -.6cqmin; }
      .at-s.heard { background: #fff; }
      .at-s.at-empty { background: transparent; box-shadow: none; }
      /* the picture fills what the text leaves and never sets the stamp's height */
      .at-p { flex: 1; min-height: 0; overflow: hidden; position: relative; }
      .at-p img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
      .at-p.fit img { object-fit: contain; }
      .at-p.fit { padding: .3cqmin; }
      .at-p.fit img { mix-blend-mode: multiply; }
      .at-p.nopic { padding: 18%; }
      .at-n { font-family: var(--bb-sc); font-size: 2.1cqmin; letter-spacing: .03em; margin-top: .5cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .at-sci { font-style: italic; font-size: 1.6cqmin; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .at-m { font-size: 1.6cqmin; opacity: .8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      @container (max-aspect-ratio: 1) { .at-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } .at-n { font-size: 2.3cqmin; white-space: normal; line-height: 1.1; max-height: 2.3em; } .at-sci, .at-m { font-size: 1.9cqmin; } }
      @container (max-height: 320px) { .at-sci { display: none; } }
      .lv-gray16 .lay-atlas, .lv-gray4 .lay-atlas, .lv-mono .lay-atlas { background: #fff; color: #000; --paper: #fff; --ink: #000; }
      .lv-gray16 .at-s, .lv-gray4 .at-s, .lv-mono .at-s { box-shadow: 0 0 0 1px #000; outline-color: #000; border-color: #fff; }

      /* greyscale and mono panels: white paper, black ink */
      .lv-gray16 .lay-list, .lv-gray4 .lay-list, .lv-mono .lay-list, .lv-gray16 .lay-latest, .lv-gray4 .lay-latest, .lv-mono .lay-latest,
      .lv-gray16 .lay-gallery, .lv-gray4 .lay-gallery, .lv-mono .lay-gallery, .lv-gray16 .lay-chart, .lv-gray4 .lay-chart, .lv-mono .lay-chart { background: #fff; color: #000; --paper: #fff; --ink: #000; }
      .lv-gray16 .ev-fade, .lv-gray4 .ev-fade, .lv-mono .ev-fade { background: linear-gradient(to top, #fff 42%, rgba(255, 255, 255, 0)); }
      .lv-gray16 .ev-kick, .lv-gray4 .ev-kick, .lv-mono .ev-kick { color: #000; }
      .lv-gray16 .gc, .lv-gray4 .gc, .lv-mono .gc { background: #fff; box-shadow: 0 0 0 1px #000; }
      .lv-gray16 .ev-row, .lv-gray4 .ev-row, .lv-mono .ev-row, .lv-gray16 .crow, .lv-gray4 .crow, .lv-mono .crow, .lv-gray16 .ev-stats, .lv-gray4 .ev-stats, .lv-mono .ev-stats { border-bottom-color: #000; }
      .lv-gray16 .sb i.now, .lv-gray4 .sb i.now, .lv-mono .sb i.now, .lv-gray16 .new, .lv-gray4 .new, .lv-mono .new { background: #000; }
      .lv-mono .ev-name, .lv-mono .ev-rname, .lv-mono .gc-n, .lv-mono .crow .cn { -webkit-text-stroke: .35px currentColor; }

      /* the empty and error pages */
      .msg { font-family: var(--bb-serif); }
      .msg-t { font-family: var(--bb-sc); font-weight: 400; }
      .msg-b { font-weight: 400; font-style: italic; }

      /* plate: a page from the book */
      .lay-plate { background: #f3ead6; color: #2a2116; }
      .page { flex: 1; min-height: 0; display: flex; gap: 4cqmin; padding: 3.6cqmin; }
      .pframe { flex: 0 0 42%; background: #fffdf6; padding: 1.4cqmin; box-shadow: 0 0 0 1px #c9b98f; }
      .pp { width: 100%; height: 100%; overflow: hidden; }
      .pp img { object-fit: contain; }
      .ptext { flex: 1; min-width: 0; display: flex; flex-direction: column; font-family: var(--bb-serif); }
      .pkick { font-family: var(--bb-sc); font-size: 2.8cqmin; letter-spacing: .14em; }
      .pkick.sp { margin-top: 3.6cqmin; }
      .pname { font-family: var(--bb-sc); font-size: 8.8cqmin; letter-spacing: .06em; line-height: 1; margin-top: 1.4cqmin; }
      .psci { font-style: italic; font-size: 4.2cqmin; margin-top: .6cqmin; }
      .prule { height: 1px; background: #2a2116; margin: 2.6cqmin 0 2cqmin; box-shadow: 0 2px 0 #f3ead6, 0 3px 0 #2a2116; }
      .pline { font-style: italic; font-size: 3.6cqmin; }
      .pcols { columns: 2; column-gap: 4cqmin; margin-top: 1.2cqmin; font-size: 3.4cqmin; }
      .pcols div { display: flex; justify-content: space-between; border-bottom: 1px solid #b7a67c; padding: .5cqmin 0; break-inside: avoid; }
      .pcols span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 1cqmin; }
      .pcredit { margin-top: auto; font-style: italic; font-size: 2.2cqmin; opacity: .75; }
      .pcols { overflow: hidden; }
      @container (max-aspect-ratio: 1) {
        .page { flex-direction: column; gap: 3cqmin; }
        .pframe { flex: 1 1 0; min-height: 0; }
        .ptext { flex: 0 0 auto; }
        .pkick.sp { margin-top: 2.4cqmin; }
        .pcols div:nth-child(n+7) { display: none; }
        .pcredit { margin-top: 1.6cqmin; }
      }
      @container (max-aspect-ratio: 0.62) { .pcols { columns: 1; } .pcols div:nth-child(n+5) { display: none; } }

      /* bleed: the picture is the page. Each caption owns part of the cell and
         the picture gets the rest, so a caption never sits on the bird. */
      .bl { position: absolute; inset: 0; background: #f4ecd8; color: #1d160e; --capw: 0px; --caph: 0px; }
      .bp { position: absolute; top: 0; left: 0; right: 0; bottom: 0; }
      .bp.fit { padding: 3cqmin; }
      .bp.nopic { padding: 25%; }
      .cap-band .bp { bottom: 16cqmin; }
      .cap-poster .bp { left: 42%; }
      .cap-notebook .bp { left: 38%; }
      .cap-editorial .bp { left: 50%; }
      .cap-tag .bp { right: 42%; }
      .bcredit { position: absolute; top: 1cqmin; right: 1cqmin; background: rgba(255,255,255,.72); color: #111; padding: .3cqmin 1cqmin; max-width: 60%; }
      .bd { position: absolute; left: 0; right: 0; bottom: 0; height: 16cqmin; display: flex; background: #fff; color: #111; border-top: calc(var(--rule) * 1.2) solid #111; }
      .bd-t { display: flex; flex-direction: column; justify-content: center; }
      .bd-t { flex: 1; min-width: 0; padding: 1.8cqmin 3cqmin; }
      .bd-n { font-weight: 900; font-size: 7.4cqmin; line-height: 1.05; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .bd-c { padding: 0 3.4cqmin; display: flex; flex-direction: column; justify-content: center; align-items: flex-end; }
      .bd-c b { font-weight: 900; font-size: 6.4cqmin; line-height: 1; }
      .bd-c span { font-size: 2.2cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
      .bd-c.a { background: #f2c230; } .bd-c.k { background: #111; color: #fff; }
      .ed-fade { position: absolute; top: 0; bottom: 0; left: 0; width: 50%; background: #f4ecd8; }
      .on-paper .ed-fade { background: transparent; }
      .ed { position: absolute; left: 5cqmin; top: 0; bottom: 0; width: calc(50% - 7cqmin); display: flex; flex-direction: column; justify-content: center; font-family: 'Playfair Display', Georgia, serif; }
      .ed-k { font-family: 'Archivo Narrow', sans-serif; font-weight: 600; font-size: 2.8cqmin; letter-spacing: .32em; text-transform: uppercase; }
      .ed-n { font-style: italic; font-weight: 900; font-size: 26cqmin; line-height: .86; letter-spacing: -.02em; margin-top: 2cqmin; }
      .ed-s { font-style: italic; font-size: 4.6cqmin; margin-top: 2.6cqmin; }
      .ed-st { display: flex; gap: 5cqmin; margin-top: 4cqmin; font-style: italic; font-size: 3.6cqmin; }
      .ed-st b { font-style: italic; font-weight: 900; font-size: 9.6cqmin; margin-right: 1cqmin; }
      .nb { position: absolute; left: 0; top: 0; bottom: 0; width: 38%; padding: 4cqmin 4cqmin; font-family: Caveat, cursive; color: #23324a;
            background: #f6efdb repeating-linear-gradient(#f6efdb 0 6.6cqmin, #c9d6e3 6.6cqmin calc(6.6cqmin + 1px)); border-right: 1px dashed #9c8a66; }
      .nb-d { font-size: 4.2cqmin; opacity: .8; }
      .nb-n { font-size: 10.4cqmin; font-weight: 700; line-height: 1; margin-top: 1.6cqmin; }
      .nb-s { font-size: 5cqmin; font-style: italic; margin-top: .8cqmin; }
      .nb-t { font-size: 5.6cqmin; line-height: 1.25; margin-top: 2.6cqmin; }
      .nb-c { font-size: 4cqmin; margin-top: 4cqmin; opacity: .75; }
      /* the tag's outline is an SVG polygon: a clip-path would cut the border
         off the clipped corners */
      .tg { position: absolute; right: 3cqmin; top: 50%; width: 38%; transform: translateY(-50%) rotate(-4deg); isolation: isolate;
            padding: 3cqmin 3.4cqmin 2.6cqmin 5.4cqmin; font-family: 'Special Elite', 'Courier New', monospace; color: #262018; }
      .tg-bg { position: absolute; inset: 0; width: 100%; height: 100%; z-index: -1; overflow: visible; }
      .tg-bg polygon { fill: #efe4c6; stroke: #8f7c58; stroke-width: 1.6px; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .tg::before { content: ''; position: absolute; left: 2.2cqmin; top: 50%; width: 2.4cqmin; height: 2.4cqmin; border-radius: 50%; border: 2px solid #8f7c58; transform: translateY(-50%); background: #f4ecd8; }
      .tg-k { font-size: 2.2cqmin; letter-spacing: .12em; }
      .tg-n { font-size: 5.6cqmin; margin-top: 1.6cqmin; line-height: 1.05; }
      .tg-s { font-size: 3.2cqmin; font-style: italic; margin-top: .4cqmin; }
      .tg-f { border-top: 1px solid #8f7c58; margin-top: 2cqmin; padding-top: 1.4cqmin; font-size: 2.8cqmin; line-height: 1.55; white-space: nowrap; }
      .po { position: absolute; left: 0; top: 0; bottom: 0; width: 42%; background: #c8281e; color: #fff; padding: 5cqmin 4.4cqmin; display: flex; flex-direction: column; }
      .po-n { font-family: 'Archivo Black', sans-serif; font-size: 15cqmin; line-height: .9; letter-spacing: -.03em; text-transform: uppercase; }
      .po-s { font-family: 'Archivo Narrow', sans-serif; font-weight: 600; font-size: 3.2cqmin; letter-spacing: .08em; text-transform: uppercase; margin-top: 2.4cqmin; }
      .po-st { margin-top: auto; display: grid; grid-template-columns: 1fr 1fr; border-top: 0.9cqmin solid #fff; }
      .po-st > div { padding-top: 1.6cqmin; }
      .po-st > div + div { border-left: 0.9cqmin solid #fff; padding-left: 2.4cqmin; }
      .po-st b { display: block; font-family: 'Archivo Black', sans-serif; font-weight: 400; font-size: 11cqmin; line-height: 1; }
      .po-st span { font-family: 'Archivo Narrow', sans-serif; font-weight: 600; font-size: 2.6cqmin; letter-spacing: .08em; text-transform: uppercase; }
      @container (max-aspect-ratio: 1) {
        .cap-poster .bp, .cap-notebook .bp, .cap-editorial .bp { left: 0; bottom: 46%; }
        .cap-tag .bp { right: 0; bottom: 40%; }
        .ed-fade { width: auto; left: 0; right: 0; top: 54%; bottom: 0; }
        .ed { top: 54%; bottom: 0; width: auto; right: 5cqmin; }
        .tg { top: auto; bottom: 7cqmin; right: 12%; width: 72%; transform: rotate(-3deg); }
        .nb, .po { width: auto; left: 0; right: 0; top: auto; bottom: 0; height: 46%; }
        .nb { border-right: 0; border-top: 1px dashed #9c8a66; }
        .po-n { font-size: min(11cqmin, 9cqw) !important; }
        .bd-n { white-space: normal; font-size: var(--two) !important; line-height: 1; }
        .bd { height: 26cqmin; }
        .cap-band .bp { bottom: 26cqmin; }
        .ed-n { font-size: min(17cqmin, 13cqw) !important; }
      }

      /* folio: a page from a Victorian folio, double-ruled, the plate number
         being which bird of the day this was */
      .cap-folio { color: #2a2116; }
      .cap-folio .bp { top: 10cqmin; left: 7cqmin; right: 7cqmin; bottom: 25cqmin; }
      .cap-folio .bp.fit { padding: 0; }
      .fo-frame { position: absolute; inset: 2.6cqmin; border: max(2px, .55cqmin) solid currentColor; }
      .fo-frame::after { content: ''; position: absolute; inset: .9cqmin; border: 1px solid currentColor; }
      .cap-folio .bcredit { top: 0; left: 0; right: 0; max-width: none; text-align: center; background: transparent; color: inherit; padding: .45cqmin 3cqmin 0; font-size: 1.5cqmin; }
      .fo-head { position: absolute; top: 5.2cqmin; left: 6.4cqmin; right: 6.4cqmin; display: flex; justify-content: space-between; font-family: var(--bb-sc); font-size: 2.8cqmin; letter-spacing: .16em; }
      .fo-head span:first-child { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; padding-right: 3cqmin; }
      .fo-cap { position: absolute; left: 6.4cqmin; right: 6.4cqmin; bottom: 5.4cqmin; height: 18cqmin; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; text-align: center; font-family: var(--bb-serif); }
      .fo-n { font-family: var(--bb-sc); letter-spacing: .08em; line-height: 1.05; white-space: nowrap; }
      .fo-s { font-style: italic; font-size: 3.6cqmin; margin-top: .4cqmin; }
      .fo-l { font-style: italic; font-size: 2.6cqmin; margin-top: 1.2cqmin; padding-top: 1.1cqmin; border-top: 1px solid currentColor; opacity: .85; }

      /* museum: the plate as a specimen, with a collection label beside it */
      .cap-museum { color: #1d1a16; --mz: 1; }
      .cap-museum .bp { right: 38%; }
      .mu { position: absolute; right: 4cqmin; top: 50%; width: calc(38% - 7cqmin); transform: translateY(-50%); box-sizing: border-box; background: #fbf8ef; color: #1d1a16;
            border: max(2px, .45cqmin) solid #1d1a16; box-shadow: inset 0 0 0 .7cqmin #fbf8ef, inset 0 0 0 calc(.7cqmin + 1px) #1d1a16; padding: calc(3cqmin * var(--mz)) calc(3.2cqmin * var(--mz)) calc(2.6cqmin * var(--mz)); font-family: var(--bb-serif); }
      .mu-h { font-family: var(--bb-sc); font-size: calc(3.6cqmin * var(--mz)); letter-spacing: .1em; text-align: center; line-height: 1.05; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .mu-h2 { font-size: calc(2.2cqmin * var(--mz)); font-style: italic; text-align: center; margin-top: .6cqmin; padding-bottom: 1.4cqmin; border-bottom: 1px solid #1d1a16; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .mu-f { display: grid; grid-template-columns: auto 1fr; column-gap: 2cqmin; margin: 1.2cqmin 0 0; align-items: baseline; }
      .mu-f dt { font-family: var(--bb-sc); font-size: calc(2.5cqmin * var(--mz)); letter-spacing: .06em; }
      .mu-f dd { margin: 0; font-family: Caveat, cursive; font-size: calc(4.3cqmin * var(--mz)); line-height: 1.25; color: #23324a; border-bottom: 1px dotted #6d6457; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .mu-f dd.it { font-style: italic; white-space: normal; line-height: 1.05; padding: .5cqmin 0 .4cqmin; }

      /* figure: a plate with a figure from a paper, the bird's calls per hour
         round a 24-hour dial */
      .cap-figure { color: #1f1a14; }
      .cap-figure .bp { right: 47%; }
      .fg { position: absolute; left: 55%; right: 4cqmin; top: 4cqmin; bottom: 4cqmin; display: flex; flex-direction: column; justify-content: center; gap: 2cqmin; font-family: var(--bb-serif); }
      .fg-k { font-family: var(--bb-sc); font-size: 2.8cqmin; letter-spacing: .16em; }
      .fg-n { font-family: var(--bb-sc); line-height: 1; letter-spacing: .03em; margin-top: .6cqmin; }
      .fg-s { font-style: italic; font-size: 3.4cqmin; margin-top: .6cqmin; }
      .fg-dial { width: 100%; flex: 1 1 0; min-height: 0; max-height: 52cqh; overflow: visible; }
      .fg-dial .g { fill: none; stroke: currentColor; stroke-width: .5; stroke-dasharray: 1.5 2.5; opacity: .55; }
      .fg-dial .ax { fill: none; stroke: currentColor; stroke-width: .9; }
      .fg-dial .tk line { stroke: currentColor; stroke-width: .9; }
      .fg-dial .br line { stroke: currentColor; stroke-width: 6.4; }
      .fg-dial .br line.pk { stroke: #9b2c1c; }
      .fg-dial .now { stroke: #9b2c1c; stroke-width: 2.6; }
      .fg-dial .lb text { font-family: var(--bb-serif); font-size: 11px; fill: currentColor; text-anchor: middle; dominant-baseline: middle; }
      .fg-c { font-size: 2.6cqmin; line-height: 1.35; border-top: 1px solid currentColor; padding-top: 1.4cqmin; }
      .fg-c b { font-weight: 400; font-style: italic; }

      /* cyanotype: a photogram in Prussian blue after Anna Atkins, the picture
         printed as its own negative, white on blue */
      .bl.cap-cyanotype { background: #1d4a7c !important; color: #f3f6f9; }
      .cap-cyanotype .bp { top: 4cqmin; left: 6cqmin; right: 6cqmin; bottom: 17cqmin; background: transparent !important; }
      .cap-cyanotype .bp.fit { padding: 0; }
      .cap-cyanotype .bp img { filter: grayscale(1) invert(1) contrast(1.55) brightness(1.08); mix-blend-mode: screen; }
      .cap-cyanotype .bp.nopic { color: #f3f6f9; }
      .cy-edge { position: absolute; inset: 0; pointer-events: none; box-shadow: inset 0 0 7cqmin 1.5cqmin rgba(9, 28, 54, .7); }
      .cy { position: absolute; left: 5cqmin; right: 5cqmin; bottom: 3.6cqmin; height: 12cqmin; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; text-align: center; font-family: var(--bb-serif); }
      .cy-n { font-style: italic; line-height: 1.1; white-space: nowrap; }
      .cy-s { font-family: var(--bb-sc); font-size: 2.6cqmin; letter-spacing: .14em; margin-top: 1cqmin; opacity: .9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      .cap-cyanotype .bcredit { background: rgba(9, 28, 54, .6); color: #f3f6f9; }

      @container (max-aspect-ratio: 1) {
        .cap-folio .bp { top: 9cqmin; bottom: 27cqmin; left: 6cqmin; right: 6cqmin; }
        .fo-cap { height: 20cqmin; }
        .cap-museum .bp { right: 0; bottom: 38%; }
        .mu { top: 81%; transform: translateY(-50%); left: 5cqmin; right: 5cqmin; width: auto; }
        .cap-figure .bp { right: 0; bottom: 46%; }
        .fg { left: 5cqmin; right: 5cqmin; top: 55%; bottom: 3.4cqmin; display: grid; grid-template-columns: 46% 1fr; grid-template-rows: auto minmax(0, 1fr); column-gap: 4cqmin; row-gap: 1.6cqmin; align-content: stretch; }
        .fg-t { grid-column: 1 / -1; }
        .fg-dial { max-height: none; height: 100%; min-height: 0; width: 100%; flex: none; }
        .fg-c { align-self: center; border-top: 0; border-left: 1px solid currentColor; padding: 0 0 0 2.6cqmin; font-size: 3.2cqmin; }
        .cap-cyanotype .bp { bottom: 21cqmin; top: 5cqmin; }
        .cy { height: 15cqmin; bottom: 5cqmin; }
      }

      /* near-square cells keep the museum label beside the picture: below it
         there is not room for a label sized to the cell's width */
      @container (min-aspect-ratio: 0.8) and (max-aspect-ratio: 1) {
        .cap-museum { --mz: .72; }
        .cap-museum .bp { right: 42%; bottom: 0; }
        .mu { top: 50%; transform: translateY(-50%); left: auto; right: 3cqmin; width: calc(42% - 5cqmin); }
      }

      /* panels with fewer tones: the new looks drop their paper tints */
      .lv-gray16 .cap-folio, .lv-gray4 .cap-folio, .lv-mono .cap-folio,
      .lv-gray16 .cap-museum, .lv-gray4 .cap-museum, .lv-mono .cap-museum,
      .lv-gray16 .cap-figure, .lv-gray4 .cap-figure, .lv-mono .cap-figure { color: #000; }
      .lv-gray16 .mu, .lv-gray4 .mu, .lv-mono .mu { background: #fff; box-shadow: inset 0 0 0 .7cqmin #fff, inset 0 0 0 calc(.7cqmin + 1px) #000; border-color: #000; }
      .lv-gray16 .mu-f dd, .lv-gray4 .mu-f dd, .lv-mono .mu-f dd { color: #000; border-bottom-color: #000; }
      .lv-gray16 .fg-dial .br line.pk, .lv-gray4 .fg-dial .br line.pk, .lv-mono .fg-dial .br line.pk { stroke: currentColor; }
      .lv-gray16 .fg-dial .now, .lv-gray4 .fg-dial .now, .lv-mono .fg-dial .now { stroke: currentColor; stroke-width: 4; }
      .lv-gray16 .bl.cap-cyanotype, .lv-gray4 .bl.cap-cyanotype, .lv-mono .bl.cap-cyanotype { background: #000 !important; color: #fff; }
      .lv-gray16 .cy-edge, .lv-gray4 .cy-edge, .lv-mono .cy-edge { display: none; }
      .lv-mono .tg-bg polygon { stroke: #000; stroke-width: 2px; }
      .lv-gray16 .tg-bg polygon, .lv-gray4 .tg-bg polygon { fill: #fff; stroke: #000; }
      .lv-mono .tg-bg polygon { fill: #fff; }
      .lv-mono .fo-head, .lv-mono .fo-cap, .lv-mono .mu, .lv-mono .fg, .lv-mono .cy { -webkit-text-stroke: .35px currentColor; }

      /* cluster: one picture of today's birds with a key beside or below it */
      .lay-cluster { background: #f3ead6; color: #2a2116; }
      .cl { flex: 1; min-height: 0; display: flex; gap: 3.4cqmin; padding: 3.4cqmin; background: #f3ead6; }
      .cl-pic { flex: 1 1 0; min-width: 0; min-height: 0; display: flex; container-type: size; }
      .cl-sheet { position: relative; margin: auto; aspect-ratio: var(--r); width: min(100cqw, calc(100cqh * var(--r))); }
      .cl-sheet img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; }
      .cl-mont { flex: 1; min-height: 0; display: grid; gap: 1.6cqmin; grid-template-columns: repeat(2, 1fr); grid-auto-rows: 1fr; }
      .cl-mont.n3, .cl-mont.n5, .cl-mont.n6 { grid-template-columns: repeat(3, 1fr); }
      .cl-mont figure { margin: 0; position: relative; min-height: 0; display: flex; background: #fffdf6; box-shadow: 0 0 0 1px #c9b98f; }
      .cl-tile { flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
      .cl-tile.fit { padding: 1cqmin; }
      .cl-mont figcaption { position: absolute; left: 1cqmin; top: .6cqmin; font-family: var(--bb-serif); font-size: calc(3.4cqmin * var(--ts, 1)); line-height: 1; background: #fffdf6; padding: .2cqmin .8cqmin; }
      .cl-key { flex: 0 0 34%; min-width: 0; min-height: 0; display: flex; flex-direction: column; justify-content: center; font-family: var(--bb-serif); }
      .cl.nokey { padding: 2cqmin; }
      .cl.nokey .cl-mont figcaption { left: 50%; top: auto; bottom: .6cqmin; transform: translateX(-50%); max-width: 92%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cl-key ol.fitrows { flex: 0 1 auto; max-height: 100%; }
      /* the key's header lines never give up height to the list */
      .cl-k, .cl-t, .cl-f { flex: 0 0 auto; }
      .cl-k { font-family: var(--bb-sc); font-size: calc(2.6cqmin * var(--ts, 1)); letter-spacing: .14em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cl-t { font-family: var(--bb-sc); font-size: calc(6cqmin * var(--ts, 1)); letter-spacing: .04em; line-height: 1.05; margin-top: 1cqmin; padding-bottom: 1.6cqmin; border-bottom: 1px solid currentColor; box-shadow: 0 2px 0 #f3ead6, 0 3px 0 currentColor; }
      .cl ol { list-style: none; margin: 2cqmin 0 0; padding: 0; }
      .cl li { display: flex; align-items: baseline; gap: 1.4cqmin; padding: .7cqmin 0; border-bottom: 1px solid #b7a67c; font-size: calc(3.4cqmin * var(--ts, 1)); }
      .cl li em { font-style: normal; min-width: 3.4cqmin; }
      .cl li span { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cl li span i { margin-left: 1.2cqmin; font-size: calc(2.6cqmin * var(--ts, 1)); opacity: .75; }
      .cl li b { font-weight: 400; }
      .cl-f { margin-top: 1.6cqmin; font-style: italic; font-size: calc(2.6cqmin * var(--ts, 1)); opacity: .8; }
      .lay-cluster .credit { font-family: var(--bb-serif); font-style: italic; }
      @container (max-aspect-ratio: 1) {
        .cl { flex-direction: column; gap: 2.6cqmin; }
        .cl-key { flex: 0 0 auto; justify-content: flex-start; }
        .cl-t { font-size: calc(7cqmin * var(--ts, 1)); }
        .cl li { font-size: calc(3.8cqmin * var(--ts, 1)); padding: .9cqmin 0; }
        .cl-mont.n3 { grid-template-columns: repeat(2, 1fr); }
        .cl-mont.n3 figure:last-child { grid-column: 1 / -1; }
      }
      .lv-gray16 .lay-cluster, .lv-gray4 .lay-cluster, .lv-mono .lay-cluster,
      .lv-gray16 .cl, .lv-gray4 .cl, .lv-mono .cl { background: #fff !important; color: #000; }
      .lv-gray16 .cl li, .lv-gray4 .cl li, .lv-mono .cl li { border-bottom-color: #000; }
      .lv-gray16 .cl-t, .lv-gray4 .cl-t, .lv-mono .cl-t { box-shadow: 0 2px 0 #fff, 0 3px 0 #000; }
      .lv-gray16 .cl-mont figure, .lv-gray4 .cl-mont figure, .lv-mono .cl-mont figure { background: #fff; box-shadow: 0 0 0 1px #000; }
      .lv-mono .cl-key { -webkit-text-stroke: .35px currentColor; }

      /* stamp: the bird on a postage stamp, the garden as its country and
         the day's calls as its value; the perforations are bites of the
         ground colour along the stamp's edges */
      .bl.cap-stamp { background: #d9cdb0 !important; color: #2a2116; }
      .st-card { position: absolute; inset: 5cqmin; background: #fbf7ec; }
      .st-card::before, .st-card::after { content: ''; position: absolute; }
      .st-card::before { left: 0; right: 0; top: -1.1cqmin; bottom: -1.1cqmin;
        background: radial-gradient(circle, #d9cdb0 0.85cqmin, transparent 0.95cqmin) 0 0 / 2.6cqmin 2.2cqmin repeat-x,
                    radial-gradient(circle, #d9cdb0 0.85cqmin, transparent 0.95cqmin) 0 100% / 2.6cqmin 2.2cqmin repeat-x; }
      .st-card::after { top: 0; bottom: 0; left: -1.1cqmin; right: -1.1cqmin;
        background: radial-gradient(circle, #d9cdb0 0.85cqmin, transparent 0.95cqmin) 0 0 / 2.2cqmin 2.6cqmin repeat-y,
                    radial-gradient(circle, #d9cdb0 0.85cqmin, transparent 0.95cqmin) 100% 0 / 2.2cqmin 2.6cqmin repeat-y; }
      .st-face { position: absolute; inset: 8cqmin; border: max(2px, .4cqmin) solid #2a2116; }
      .cap-stamp .bp { top: 16cqmin; left: 11cqmin; right: 11cqmin; bottom: 23cqmin; z-index: 1; }
      .st-top, .st-val, .st-bot { z-index: 2; }
      .cap-stamp .bp.fit { padding: 0; }
      .st-top { position: absolute; top: 9.6cqmin; left: 10cqmin; right: 22cqmin; font-family: var(--bb-sc); font-size: 4cqmin; letter-spacing: .2em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .st-val { position: absolute; top: 9cqmin; right: 10cqmin; text-align: right; font-family: var(--bb-serif); line-height: .9; }
      .st-val b { display: block; font-weight: 400; font-size: 7cqmin; }
      .st-val span { font-style: italic; font-size: 2.4cqmin; }
      .st-bot { position: absolute; left: 10cqmin; right: 10cqmin; bottom: 9.6cqmin; height: 12cqmin; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; font-family: var(--bb-serif); border-top: 1px solid #2a2116; }
      .st-n { font-family: var(--bb-sc); letter-spacing: .06em; white-space: nowrap; line-height: 1.05; }
      .st-s { font-style: italic; font-size: 3cqmin; }

      /* woodblock: a kacho-e print, the title in a tall cartouche down the
         right edge and the artist's seal in red */
      .cap-woodblock { color: #22201b; }
      .cap-woodblock .bp { right: 16cqmin; }
      .wb-col { position: absolute; top: 5cqmin; right: 4cqmin; bottom: 5cqmin; width: 9cqmin; display: flex; flex-direction: column; align-items: center; gap: 2cqmin; }
      .wb-title, .wb-sub { writing-mode: vertical-rl; background: #f6efdc; border: 1px solid currentColor; box-shadow: inset 0 0 0 .6cqmin #f6efdc, inset 0 0 0 calc(.6cqmin + 1px) currentColor; padding: 2.4cqmin 1.6cqmin; font-family: var(--bb-sc); max-height: 64%; overflow: hidden; }
      .wb-title { font-size: 5cqmin; letter-spacing: .12em; white-space: nowrap; }
      .wb-sub { font-family: var(--bb-serif); font-style: italic; font-size: 3cqmin; max-height: 30%; white-space: nowrap; }
      .wb-note { position: absolute; left: 3.6cqmin; top: 5cqmin; writing-mode: vertical-rl; font-family: var(--bb-serif); font-style: italic; font-size: 2.6cqmin; opacity: .8; }
      .wb-seal { position: absolute; left: 3cqmin; bottom: 5cqmin; width: 7.4cqmin; height: 7.4cqmin; display: flex; align-items: center; justify-content: center; background: #b5302a; color: #f6efdc; font-family: var(--bb-sc); font-size: 4cqmin; letter-spacing: .02em; box-shadow: inset 0 0 0 .5cqmin #b5302a, inset 0 0 0 .7cqmin #f6efdc; }

      /* cameo: the bird in an oval window cut into a dark mat, as on a
         Victorian cabinet card */
      .bl.cap-cameo { background: #2e3a32 !important; color: #eadfbf; }
      .cap-cameo .bp { top: 5%; bottom: 21%; left: 20%; right: 20%; border-radius: 50%; overflow: hidden; }
      .cap-cameo .bp.fit { padding: 7%; }
      .cm-ring { position: absolute; top: 5%; bottom: 21%; left: 20%; right: 20%; border-radius: 50%; pointer-events: none;
        box-shadow: 0 0 0 .5cqmin #c9b07a, 0 0 0 1.5cqmin #2e3a32, 0 0 0 1.7cqmin #c9b07a; }
      .cm-cap { position: absolute; left: 5cqmin; right: 5cqmin; bottom: 3%; height: 14%; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; font-family: var(--bb-serif); }
      .cm-n { font-family: var(--bb-sc); letter-spacing: .1em; white-space: nowrap; line-height: 1.05; }
      .cm-s { font-style: italic; font-size: 3cqmin; margin-top: .6cqmin; opacity: .85; }

      /* nouveau: an arched frame with a title panel, after the posters of
         the 1890s */
      .bl.cap-nouveau { background: #e7dcc3 !important; color: #3d4a33; }
      .cap-nouveau .bp { top: 6cqmin; left: 9cqmin; right: 9cqmin; bottom: 22cqmin; border-radius: 50% 50% 0 0 / 22% 22% 0 0; overflow: hidden; }
      .cap-nouveau .bp.fit { padding: 6% 4% 2%; }
      .nv-arch { position: absolute; top: 4cqmin; left: 7cqmin; right: 7cqmin; bottom: 20cqmin; border: max(2px, .6cqmin) solid currentColor; border-bottom: 0; border-radius: 50% 50% 0 0 / 22% 22% 0 0; pointer-events: none;
        box-shadow: inset 0 0 0 1cqmin #e7dcc3, inset 0 0 0 calc(1cqmin + 1px) currentColor; }
      .nv-band { position: absolute; left: 4cqmin; right: 4cqmin; bottom: 4cqmin; height: 16cqmin; background: currentColor; border-radius: 2cqmin; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
      .nv-n { font-family: 'Playfair Display', Georgia, serif; font-style: italic; font-weight: 900; color: #efe5cc; white-space: nowrap; line-height: 1.05; }
      .nv-s { font-family: 'Archivo Narrow', sans-serif; font-weight: 600; font-size: 2.6cqmin; letter-spacing: .2em; text-transform: uppercase; color: #efe5cc; margin-top: .6cqmin; }

      @container (max-aspect-ratio: 1) {
        .cap-cameo .bp, .cm-ring { left: 9%; right: 9%; top: 6%; bottom: 24%; }
        .cm-cap { height: 16%; }
        .cap-woodblock .bp { right: 15cqmin; }
      }
      .lv-gray16 .bl.cap-stamp, .lv-gray4 .bl.cap-stamp, .lv-mono .bl.cap-stamp { background: #fff !important; color: #000; }
      .lv-gray16 .st-card, .lv-gray4 .st-card, .lv-mono .st-card { box-shadow: 0 0 0 1px #000; background: #fff; }
      .lv-gray16 .st-card::before, .lv-gray4 .st-card::before, .lv-mono .st-card::before,
      .lv-gray16 .st-card::after, .lv-gray4 .st-card::after, .lv-mono .st-card::after { display: none; }
      .lv-gray16 .wb-seal, .lv-gray4 .wb-seal, .lv-mono .wb-seal { background: #000; color: #fff; box-shadow: inset 0 0 0 .5cqmin #000, inset 0 0 0 .7cqmin #fff; }
      .lv-gray16 .wb-title, .lv-gray4 .wb-title, .lv-mono .wb-title, .lv-gray16 .wb-sub, .lv-gray4 .wb-sub, .lv-mono .wb-sub { background: #fff; box-shadow: inset 0 0 0 .6cqmin #fff, inset 0 0 0 calc(.6cqmin + 1px) #000; }
      .lv-gray16 .bl.cap-cameo, .lv-gray4 .bl.cap-cameo, .lv-mono .bl.cap-cameo { background: #000 !important; color: #fff; }
      .lv-gray16 .cm-ring, .lv-gray4 .cm-ring, .lv-mono .cm-ring { box-shadow: 0 0 0 .5cqmin #fff, 0 0 0 1.5cqmin #000, 0 0 0 1.7cqmin #fff; }
      .lv-gray16 .bl.cap-nouveau, .lv-gray4 .bl.cap-nouveau, .lv-mono .bl.cap-nouveau { background: #fff !important; color: #000; }
      .lv-gray16 .nv-arch, .lv-gray4 .nv-arch, .lv-mono .nv-arch { box-shadow: inset 0 0 0 1cqmin #fff, inset 0 0 0 calc(1cqmin + 1px) #000; }
      .lv-gray16 .nv-n, .lv-gray4 .nv-n, .lv-mono .nv-n, .lv-gray16 .nv-s, .lv-gray4 .nv-s, .lv-mono .nv-s { color: #fff; }
      .lv-mono .st-bot, .lv-mono .st-top, .lv-mono .wb-col, .lv-mono .cm-cap { -webkit-text-stroke: .35px currentColor; }

      /* specimen sheet: the whole cell is the page */
      .sh { flex: 1; min-height: 0; display: flex; container-type: size; }
      .sh-s { container-type: size; color: #2a2116; }
      .sh-ln { display: block; }
      .sh-sci { font-family: var(--bb-serif); font-style: italic; letter-spacing: 0; }
      .sh-sm { font-family: var(--bb-serif); font-style: italic; letter-spacing: 0; font-size: .85em; opacity: .85; }
      .sh-new { display: inline-block; background: var(--alert, #c8281e); color: #fff; font-family: var(--bb-sc); padding: .05em .4em .1em; margin-top: .15em; font-size: .85em; }
      .lv-gray16 .sh-new, .lv-gray4 .sh-new, .lv-mono .sh-new { background: #000; }
      .sh-l { position: absolute; transform: translate(-50%, 15%); white-space: nowrap; text-align: center; line-height: 1.15; font-family: var(--bb-sc); font-size: calc(var(--lf) * 100cqh); letter-spacing: .04em; }
      .sh-c { position: absolute; left: 0; right: 0; bottom: 1.6%; text-align: center; font-family: var(--bb-serif); font-style: italic; font-size: calc(var(--lf) * 100cqh); opacity: .8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .sh-numbered .sh-l { font-family: var(--bb-serif); font-size: calc(var(--lf) * 170cqh); }
      @container (max-aspect-ratio: 1) {
        /* the plate keeps most of a tall cell; the key takes two columns */
        .cl-numbered .cl-key { max-height: 38%; flex: 0 0 auto; justify-content: flex-start; }
        .cl-numbered li { font-size: calc(2.8cqmin * var(--ts, 1)); padding: .4cqmin 0; }
        .cl-numbered .cl-pic { overflow: hidden; }
        .cl-numbered .cl-t { padding-bottom: 1cqmin; }
        .cl-numbered ol.fitrows { margin-top: 1.2cqmin; }
        .cl-numbered .cl-key ol.fitrows { flex: 1 1 auto; min-height: 0; column-gap: 4cqmin; }
        .cl-numbered .cl-key ol.fitrows > li { width: calc(50% - 2cqmin); margin-right: 4cqmin; }
        .cl-numbered .cl-t { font-size: calc(5cqmin * var(--ts, 1)); }
      }
      .sh-w { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 34%; text-align: center; font-family: var(--bb-serif); }
      .sh-wk { font-family: var(--bb-sc); font-size: calc(var(--lf) * 110cqh); letter-spacing: .16em; }
      .sh-wt { font-family: var(--bb-sc); font-size: calc(var(--lf) * 300cqh); line-height: 1.05; margin: .4em 0 .2em; }
      .sh-wd { font-style: italic; font-size: calc(var(--lf) * 110cqh); }
      .sh-notebook { color: #23324a; }
      .sh-notebook .sh-l { font-family: Caveat, cursive; font-size: calc(var(--lf) * 150cqh); letter-spacing: 0; }
      .sh-nb { position: absolute; left: 12%; right: 3%; top: 2.2%; font-family: Caveat, cursive; font-size: calc(var(--lf) * 190cqh); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .sh-drawer .sh-l { background: #fbf8ef; color: #1d1a16; padding: .25em .6em .2em; box-shadow: 0 0 0 1px #6d6457; transform: translate(-50%, 0); max-width: 30%; overflow: hidden; text-overflow: ellipsis; }
      .sh-plaque { position: absolute; left: 50%; top: 0; height: 5%; transform: translateX(-50%); display: flex; align-items: center; padding: 0 1.2em; max-width: 70%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        background: #c9a95f; color: #2a2116; box-shadow: inset 0 0 0 1px #8a6f33; font-family: var(--bb-sc); font-size: calc(var(--lf) * 110cqh); letter-spacing: .12em; }
      .lv-gray16 .sh, .lv-gray4 .sh, .lv-mono .sh { background: #fff !important; }
      .lv-gray16 .sh-plaque, .lv-gray4 .sh-plaque, .lv-mono .sh-plaque { background: #fff; color: #000; box-shadow: inset 0 0 0 1px #000; }
      .lv-gray16 .sh-notebook, .lv-gray4 .sh-notebook, .lv-mono .sh-notebook { color: #000; }
      .lv-gray16 .sh-s, .lv-gray4 .sh-s, .lv-mono .sh-s { color: #000; }
      .lv-mono .sh-l, .lv-mono .sh-c { -webkit-text-stroke: .35px currentColor; }

      /* returning birds wear the same badge as new ones */
      .new.back { background: var(--ink); }
      .lv-gray16 .new, .lv-gray4 .new, .lv-mono .new { background: #000; }

      /* the song line */
      .song { display: block; width: 100%; height: 5cqmin; overflow: visible; }
      .song polyline { fill: none; stroke: currentColor; stroke-width: 1.4; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
      .p-song { width: 60%; margin: 1cqmin 0 0; }
      .ed-song { width: 70%; margin-top: 2cqmin; }
      .fo-song { width: 36%; margin: .6cqmin 0 0; }
      .l-song { width: 50%; height: 4cqmin; margin-top: .8cqmin; opacity: .75; }
      .lv-mono .song polyline { stroke-width: 2; }

      /* sonogram: the bird above, its latest call below as a scientific strip */
      .cap-sonogram { color: #2a2116; }
      .cap-sonogram .bp { bottom: 40%; }
      .sn { position: absolute; left: 0; right: 0; bottom: 0; height: 40%; padding: 1.6cqmin 4cqmin 2.4cqmin; display: flex; flex-direction: column; font-family: var(--bb-serif); border-top: 1px solid currentColor; }
      .sn-h { display: flex; align-items: baseline; gap: 2.4cqmin; flex: 0 0 auto; }
      .sn-n { font-family: var(--bb-sc); letter-spacing: .06em; white-space: nowrap; }
      .sn-s { font-style: italic; font-size: 3cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .sn-img { flex: 1 1 0; min-height: 0; margin: 1.2cqmin 0; border: 1px solid currentColor; background: #141c24; position: relative; }
      .sn-img img { width: 100%; height: 100%; object-fit: fill; display: block; }
      .sn-none { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #cbd3dc; font-style: italic; font-size: 3cqmin; }
      .sn-c { display: flex; justify-content: space-between; font-size: 2.4cqmin; font-style: italic; opacity: .85; flex: 0 0 auto; }
      @container (max-aspect-ratio: 1) { .cap-sonogram .bp { bottom: 36%; } .sn { height: 36%; } }
      .lv-gray16 .cap-sonogram, .lv-gray4 .cap-sonogram, .lv-mono .cap-sonogram { color: #000; }
      .lv-mono .sn-img { background: #000; }

      /* chorus: a timeline of first calls */
      .lay-chorus { background: #f3ead6; color: #2a2116; }
      .ch { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 2.6cqmin 3cqmin 1cqmin; font-family: var(--bb-serif); }
      .ch-head { display: flex; align-items: baseline; gap: 2cqmin; flex: 0 0 auto; flex-wrap: wrap; }
      .ch-k { font-family: var(--bb-sc); font-size: 2.6cqmin; letter-spacing: .14em; }
      .ch-title { font-family: var(--bb-sc); font-size: 5cqmin; letter-spacing: .04em; line-height: 1; margin-left: auto; }
      .ch-sub { font-style: italic; font-size: 2.6cqmin; width: 100%; }
      .ch-strip { flex: 1 1 0; min-height: 0; position: relative; margin-top: 2cqmin; }
      .ch-line { position: absolute; left: 0; right: 0; top: 50%; height: 1px; background: currentColor; box-shadow: 0 2px 0 #f3ead6, 0 3px 0 currentColor; }
      .ch-tick { position: absolute; left: var(--p); top: 50%; width: 1px; height: 1.6cqmin; background: currentColor; transform: translateY(-50%); }
      .ch-tick.maj { height: 3cqmin; }
      .ch-tick em { position: absolute; top: 100%; left: 50%; transform: translateX(-50%); margin-top: .4cqmin; font-style: normal; font-size: 2.2cqmin; font-family: var(--bb-sc); }
      .ch-now { position: absolute; left: var(--p); top: 50%; width: 0; height: 0; transform: translate(-50%, -50%); border: 1.4cqmin solid transparent; border-left-color: #9b2c1c; border-right: 0; }
      .ch-b { position: absolute; left: var(--p); width: 15%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; text-align: center; }
      .ch-x { display: flex; flex-direction: column; align-items: center; max-width: 100%; }
      .ch-b.l0, .ch-b.l2, .ch-b.l4 { bottom: calc(50% + 3cqmin); }
      .ch-b.l1, .ch-b.l3, .ch-b.l5 { top: calc(50% + 3cqmin); flex-direction: column-reverse; }
      .ch-b.l1 .ch-x, .ch-b.l3 .ch-x, .ch-b.l5 .ch-x { flex-direction: column-reverse; }
      .ch-b.l2 { bottom: calc(50% + 22cqmin); } .ch-b.l3 { top: calc(50% + 22cqmin); }
      .ch-b.l4 { bottom: calc(50% + 41cqmin); } .ch-b.l5 { top: calc(50% + 41cqmin); }
      /* a crowded chorus: smaller cards, lanes closer together */
      .dense .ch-pic { width: 8cqmin; height: 8cqmin; }
      .dense .ch-t { font-size: 1.9cqmin; } .dense .ch-n { font-size: 2.2cqmin; } .dense .ch-s { display: none; }
      .dense .ch-b.l2 { bottom: calc(50% + 17cqmin); } .dense .ch-b.l3 { top: calc(50% + 17cqmin); }
      .dense .ch-b.l4 { bottom: calc(50% + 31cqmin); } .dense .ch-b.l5 { top: calc(50% + 31cqmin); }
      .ch-b::after { content: ''; position: absolute; left: 50%; width: 1px; height: 3cqmin; background: currentColor; opacity: .5; }
      .ch-b.l0::after, .ch-b.l2::after, .ch-b.l4::after { top: 100%; } .ch-b.l1::after, .ch-b.l3::after, .ch-b.l5::after { bottom: 100%; }
      .ch-pic { width: 11cqmin; height: 11cqmin; overflow: hidden; background: #fffdf6; box-shadow: 0 0 0 1px #c9b98f; flex: 0 0 auto; }
      .ch-pic.fit { padding: .5cqmin; }
      .ch-pic.nopic { padding: 2.6cqmin; }
      .ch-t { font-family: var(--bb-sc); font-size: 2.2cqmin; letter-spacing: .1em; margin: .3cqmin 0 0; }
      .ch-n { font-size: 2.5cqmin; line-height: 1.1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      .ch-n .new { font-size: 1.6cqmin; margin-left: .8cqmin; padding: .2cqmin .6cqmin; }
      .ch-s { font-style: italic; font-size: 2cqmin; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      @container (max-aspect-ratio: 1) {
        .ch-line { left: 50%; right: auto; top: 0; bottom: 0; width: 1px; height: auto; box-shadow: 2px 0 0 #f3ead6, 3px 0 0 currentColor; }
        .ch-tick { left: 50%; top: var(--p); width: 1.6cqmin; height: 1px; transform: translateX(-50%); }
        .ch-tick.maj { width: 3cqmin; height: 1px; }
        .ch-tick em { top: 50%; left: 100%; transform: translateY(-50%); margin: 0 0 0 .6cqmin; }
        .ch-now { left: 50%; top: var(--p); border: 1.4cqmin solid transparent; border-top-color: #9b2c1c; border-bottom: 0; }
        .ch-b { left: auto; top: var(--p); bottom: auto; width: 44%; transform: translateY(-50%); flex-direction: row; text-align: left; align-items: center; gap: 1.4cqmin; }
        .ch-b .ch-x { align-items: flex-start; flex-direction: column; }
        .ch-b.l0, .ch-b.l2, .ch-b.l4 { right: calc(50% + 3cqmin); flex-direction: row-reverse; text-align: right; bottom: auto; }
        .ch-b.l0 .ch-x, .ch-b.l2 .ch-x, .ch-b.l4 .ch-x { align-items: flex-end; }
        .ch-b.l1, .ch-b.l3, .ch-b.l5 { left: calc(50% + 3cqmin); top: var(--p); flex-direction: row; }
        .ch-b.l2, .ch-b.l4 { right: calc(50% + 3cqmin); } .ch-b.l3, .ch-b.l5 { left: calc(50% + 3cqmin); }
        .ch-b.l2, .ch-b.l3 { transform: translateY(-50%) translateY(6cqmin); } .ch-b.l4, .ch-b.l5 { transform: translateY(-50%) translateY(12cqmin); }
        .ch-b::after { display: none; }
      }
      .lv-gray16 .lay-chorus, .lv-gray4 .lay-chorus, .lv-mono .lay-chorus { background: #fff; color: #000; }
      .lv-gray16 .ch-line, .lv-gray4 .ch-line, .lv-mono .ch-line { box-shadow: 0 2px 0 #fff, 0 3px 0 #000; }
      .lv-gray16 .ch-now, .lv-gray4 .ch-now, .lv-mono .ch-now { border-left-color: #000; }
      .lv-mono .ch-pic { box-shadow: 0 0 0 1px #000; }

      /* night */
      .lay-night { background: #10192a; color: #e8e1cf; }
      .nt { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 6cqmin; font-family: var(--bb-serif); gap: 1.6cqmin; }
      .nt-moon { width: 26cqmin; height: 26cqmin; margin-bottom: 2cqmin; }
      .nt-moon .lit { fill: #e8e1cf; } .nt-moon .dark { fill: #10192a; }
      .nt-t { font-family: var(--bb-sc); font-size: 7cqmin; letter-spacing: .08em; line-height: 1.05; }
      .nt-s { font-style: italic; font-size: 3.4cqmin; max-width: 70%; }
      .nt-f { font-family: var(--bb-sc); font-size: 2.6cqmin; letter-spacing: .14em; opacity: .8; margin-top: 2cqmin; }
      .lv-gray16 .lay-night, .lv-gray4 .lay-night, .lv-mono .lay-night { background: #000; color: #fff; }
      .lv-gray16 .nt-moon .lit, .lv-gray4 .nt-moon .lit, .lv-mono .nt-moon .lit { fill: #fff; }
      .lv-gray16 .nt-moon .dark, .lv-gray4 .nt-moon .dark, .lv-mono .nt-moon .dark { fill: #000; }

      /* chart labels */
      .cells i { position: relative; }
      .cells i b { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 2.1cqmin; font-weight: 800; line-height: 1; color: var(--ink); }
      .cells i.l2 b, .cells i.l3 b { color: var(--paper); }
      .labelled .crow { max-height: 9cqmin; }
      .strip.labelled { height: 11cqmin; }
      .sb b { font-size: 1.8cqmin; font-weight: 800; line-height: 1; text-align: center; margin-bottom: .3cqmin; }

      /* panels with fewer tones. The page is composed once and quantised per
         device afterwards, so the cell says what the panel can show, and the
         palette, the heat scale and the picture treatment follow. */
      .lv-gray16, .lv-gray4, .lv-mono { --alert: var(--ink); --hi: #d4d4d4; }
      .lv-gray16 .toned img, .lv-gray4 .toned img, .lv-mono .toned img { filter: none; mix-blend-mode: normal; }
      .lv-gray16 .toned, .lv-gray4 .toned, .lv-mono .toned { background: var(--paper); }
      .lv-gray16 .lay-plate, .lv-gray16 .page, .lv-gray4 .page, .lv-mono .page, .lv-gray16 .bl, .lv-gray4 .bl, .lv-mono .bl,
      .lv-gray16 .tile, .lv-gray4 .tile, .lv-mono .tile { background: #fff; color: #000; }
      .lv-gray16 .pframe, .lv-gray4 .pframe, .lv-mono .pframe { background: #fff; box-shadow: 0 0 0 1px #000; }
      .lv-gray16 .nb, .lv-gray4 .nb, .lv-mono .nb { color: #000; background-color: #fff; border-right-color: #000; }
      .lv-gray16 .tg, .lv-gray4 .tg, .lv-mono .tg { color: #000; }
      .lv-gray16 .tg::before, .lv-gray4 .tg::before, .lv-mono .tg::before { border-color: #000; background: #fff; }
      .lv-gray16 .tg-f, .lv-gray4 .tg-f, .lv-mono .tg-f { border-top-color: #000; }
      .lv-gray16 .po, .lv-gray4 .po, .lv-mono .po { background: #000; color: #fff; }
      .lv-gray16 .ed-fade, .lv-gray4 .ed-fade, .lv-mono .ed-fade { background: #fff; }
      .lv-gray16 .ed, .lv-gray4 .ed, .lv-mono .ed { color: #000; }
      .lv-gray16 .bd-c.a, .lv-gray4 .bd-c.a, .lv-mono .bd-c.a { background: var(--hi); color: #000; }
      .lv-gray16 .prule, .lv-gray4 .prule, .lv-mono .prule { background: #000; box-shadow: 0 2px 0 #fff, 0 3px 0 #000; }
      .lv-gray16 .pcols div, .lv-gray4 .pcols div, .lv-mono .pcols div { border-bottom-color: #000; }
      /* 4 greys: the heat scale snaps to the panel's own steps */
      .lv-gray4 { --hi: #aaa; --muted: #fff; }
      .lv-gray4 .cells i, .lv-gray4 .sb i { box-shadow: inset 0 0 0 1px #aaa; }
      .lv-gray4 .cells i.l1 { background: #aaa; }
      .lv-gray4 .cells i.l2 { background: #555; }
      .lv-gray4 .cells i.l3 { background: #000; }
      .lv-gray4 .cells i.fut { background: repeating-linear-gradient(45deg, #fff 0 3px, #aaa 3px 4px); }
      .lv-gray4 .nb { background-image: repeating-linear-gradient(#fff 0 6.6cqmin, #aaa 6.6cqmin calc(6.6cqmin + 1px)); }
      /* black and white: no tones at all, so tones become patterns and
         highlights become frames; thin serif and script strokes are thickened */
      .lv-mono { --hi: #fff; --muted: #fff; }
      .lv-mono .hstat, .lv-mono .lstats > div, .lv-mono .bd-c { box-shadow: inset 0 0 0 1px #000; }
      .lv-mono .hstat.a, .lv-mono .lstats > .a, .lv-mono .bd-c.a { box-shadow: inset 0 0 0 .7cqmin #000; }
      .lv-mono .cells i, .lv-mono .sb i { box-shadow: inset 0 0 0 1px #000; }
      .lv-mono .cells i.l1 { background: repeating-linear-gradient(45deg, #fff 0 2px, #000 2px 3px); }
      .lv-mono .cells i.l2 { background: repeating-linear-gradient(45deg, #000 0 2px, #fff 2px 3px); }
      .lv-mono .cells i.l3 { background: #000; }
      .lv-mono .cells i.fut { background: #fff; box-shadow: inset 0 0 0 1px #000; opacity: .35; }
      .lv-mono .cells i b { background: #fff; color: #000; inset: 12% 8%; box-shadow: inset 0 0 0 1px #000; }
      .lv-mono .cells i.l3 b { background: #000; color: #fff; box-shadow: none; inset: 0; }
      .lv-mono .sb i.now { background: #000; box-shadow: inset 0 0 0 1px #000, inset 0 0 0 .5cqmin #fff; }
      /* ruled lines dashed: a solid black rule through handwriting reads as a strike-out */
      .lv-mono .nb { background-image: repeating-linear-gradient(90deg, transparent 0 4px, #fff 4px 7px), repeating-linear-gradient(#fff 0 6.6cqmin, #000 6.6cqmin calc(6.6cqmin + 1px)); }
      .lv-mono .ptext, .lv-mono .ed, .lv-mono .nb, .lv-mono .tg { -webkit-text-stroke: .35px currentColor; }
      .lv-mono .row:nth-child(odd), .lv-mono .arow { background: transparent; }

      .msg { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 1.4cqmin; padding: 4cqmin; }
      .msg-t { font-weight: 900; font-size: 5cqmin; }
      .msg-b { font-size: 3.4cqmin; font-weight: 700; }
      .msg-h { font-size: 2.6cqmin; opacity: .7; }
    </style>`;
}
