/* backyard_birds: what BirdNET-Go has heard today.

   Six layouts off one payload. list, latest, gallery and chart follow the
   theme; plate and bleed are printed matter, so they carry their own paper
   and ink and ignore it. Pictures and the caption fonts arrive inlined from
   the server: the render sandbox has no network.

   A photo shown among classic plates is toned to a warm monotone so a garden
   where only some birds have a plate still reads as one set. */

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
  const tone = toneAll && p.kind === 'photo' ? ' toned' : '';
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

const tag = (s) => (s.new ? '<span class="new">new</span>' : '');

function head(d) {
  return `
    <div class="head">
      <div class="hmark">${BIRD}</div>
      <div class="htxt">
        <div class="ht">${esc(d.label)}</div>
        <div class="hs">${esc(d.date)} · ${d.demo ? 'demo, no BirdNET-Go set' : 'heard by BirdNET-Go'}</div>
      </div>
      <div class="hstat a"><b>${d.species_today}</b><span>species</span></div>
      <div class="hstat"><b>${d.detections_today}</b><span>calls</span></div>
    </div>`;
}

function strip(d) {
  const mx = Math.max(1, ...d.hourly_total);
  return `<div class="strip">${d.hourly_total.map((v, h) => `
    <div class="sb"><i style="height:${Math.max(4, (v / mx) * 100)}%" class="${h === d.now_hour ? 'now' : v ? 'on' : ''}"></i>
    <em>${h % 6 === 0 ? String(h).padStart(2, '0') : ''}</em></div>`).join('')}</div>`;
}

function foot(d) {
  const bits = [...(d.notices || []), ...(d.credits || [])];
  return bits.length ? `<div class="credit">${bits.map(esc).join(' · ')}</div>` : '';
}

/* ---------------- layouts ---------------- */

function list(d, showSci, tone) {
  const rows = d.species.map((s) => `
    <div class="row">
      ${d.pictures === 'none' ? `<div class="rpic nopic">${BIRD}</div>` : pic(s.pic, 'rpic', tone)}
      <div class="rnm"><div class="rcn">${esc(s.name)}${tag(s)}</div>${showSci && s.sci ? `<div class="rsn">${esc(s.sci)}</div>` : ''}</div>
      <div class="rago">${esc(ago(s.ago_min))}</div>
      <div class="rct">${s.count}</div>
    </div>`).join('');
  return `${head(d)}<div class="rows">${rows}</div><div class="lfoot">${strip(d)}${foot(d)}</div>`;
}

function latest(d, showSci, tone) {
  const L = d.latest;
  const others = d.species.filter((s) => s.name !== L.name).sort((a, b) => b.count - a.count);
  return `
    <div class="lat">
      <div class="lpic">${pic(L.pic, 'lp', tone)}
        <div class="lcap">
          <div class="kick">Just heard · ${esc(L.last)}</div>
          <div class="lname">${esc(L.name)}${tag(L)}</div>
          <div class="lmeta">${showSci && L.sci ? `<i>${esc(L.sci)}</i>` : ''}<span>${Math.round(L.conf * 100)}% sure</span><span>${L.count} calls today</span></div>
        </div>
      </div>
      <div class="lside">
        <div class="lstats"><div class="a"><b>${d.species_today}</b><span>species today</span></div><div><b>${d.detections_today}</b><span>calls</span></div></div>
        <div class="lalso"><div class="cap">Also heard today</div>${others.map((s) => `
          <div class="arow"><span>${esc(s.name)}${tag(s)}</span><b>${s.count}</b></div>`).join('')}</div>
        <div class="lfoot2">${esc(d.label)} · ${esc(d.date)}</div>
      </div>
    </div>${foot(d)}`;
}

function gallery(d, _showSci, tone) {
  const tiles = d.species.map((s) => `
    <div class="tile${s.new ? ' isnew' : ''}">
      ${pic(s.pic, 'tpic', tone)}
      <div class="tcap"><span>${esc(s.name)}</span><b>${s.count}</b></div>
      ${s.new ? '<div class="tnew">new today</div>' : ''}
    </div>`).join('');
  return `${head(d)}<div class="grid n${Math.min(d.species.length, 8)}">${tiles}</div>${foot(d)}`;
}

function chart(d) {
  /* hours with any calls, padded to a sensible day; owls widen it to the night */
  const used = d.hourly_total.map((v, h) => (v ? h : null)).filter((h) => h != null);
  const from = Math.min(5, ...used), to = 23;
  const hours = [];
  for (let h = from; h <= to; h++) hours.push(h);
  const rows = [...d.species].sort((a, b) => b.count - a.count);
  const mx = Math.max(1, ...rows.flatMap((s) => hours.map((h) => s.hourly[h] || 0)));
  const cell = (v, h) => {
    if (h > d.now_hour) return '<i class="fut"></i>';
    if (!v) return '<i></i>';
    const l = v / mx;
    return `<i class="${l > 0.55 ? 'l3' : l > 0.25 ? 'l2' : 'l1'}"></i>`;
  };
  return `${head(d)}
    <div class="chart">
      ${rows.map((s) => `<div class="crow"><div class="cn">${esc(s.name)}${tag(s)}</div>
        <div class="cells">${hours.map((h) => cell(s.hourly[h] || 0, h)).join('')}</div><div class="cc">${s.count}</div></div>`).join('')}
      <div class="crow axis"><div class="cn"></div><div class="cells">${hours.map((h) => `<em>${h % 3 === 0 ? h : ''}</em>`).join('')}</div><div class="cc"></div></div>
    </div>`;
}

function plate(d, showSci, tone) {
  const L = d.latest;
  const others = d.species.filter((s) => s.name !== L.name).slice(0, 8);
  const nth = ordinalWords(L.count);
  return `
    <div class="page">
      <div class="pframe">${pic(L.pic, 'pp', tone)}</div>
      <div class="ptext">
        <div class="pkick">Last heard ${esc(L.last)}${L.first ? ` · first at ${esc(L.first)}` : ''}</div>
        <div class="pname">${esc(L.name)}.</div>
        ${showSci && L.sci ? `<div class="psci">${esc(L.sci)}.</div>` : ''}
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
      <div class="ed-k">Heard ${esc(when)}</div>
      <div class="ed-n" style="${fit(L.name, 24, 46, 0.5)}">${esc(L.name)}</div>
      ${sci ? `<div class="ed-s">${sci}</div>` : ''}
      <div class="ed-st"><span><b>${L.count}</b> calls today</span><span><b>${d.species_today}</b> species</span></div></div>`;
  } else if (caption === 'notebook') {
    cap = `<div class="nb">
      <div class="nb-d">${esc(d.date)} · ${esc(L.last)}</div>
      <div class="nb-n">${esc(L.name)}!</div>
      ${sci ? `<div class="nb-s">${sci}</div>` : ''}
      <div class="nb-t">heard ${esc(when)} &mdash; <u>${L.count}</u> times today</div>
      <div class="nb-t">${d.species_today} species so far &#10003;</div>
      <div class="nb-c">BirdNET ${Math.round(L.conf * 100)}% sure</div></div>`;
  } else if (caption === 'tag') {
    cap = `<div class="tg">
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
      <div class="bd-t"><div class="kick">Just heard · ${esc(when)}</div><div class="bd-n" style="${fit(L.name, 7.4, 62, 0.56, true)};--two:${twoLines(L.name, 7.4, 56, 0.56)}">${esc(L.name)}${tag(L)}</div></div>
      <div class="bd-c a"><b>${L.count}</b><span>calls today</span></div>
      <div class="bd-c k"><b>${d.species_today}</b><span>species</span></div></div>`;
  }
  const credit = L.pic && L.pic.credit ? `<div class="bcredit">${esc(L.pic.credit)}</div>` : '';
  const paper = L.pic && L.pic.paper ? ` style="background:${esc(L.pic.paper)}"` : '';
  return `<div class="bl cap-${caption}${L.pic && L.pic.paper ? ' on-paper' : ''}"${paper}>${pic(L.pic, 'bp', tone)}${cap}${credit}</div>`;
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
  const tone = String(d.pictures || '').startsWith('plate');

  let body;
  if (d.error) {
    body = `<div class="msg"><div class="msg-t">Backyard birds</div><div class="msg-b">${esc(d.error)}</div></div>`;
  } else if (d.empty || !d.latest) {
    body = `${head(d)}<div class="msg"><div class="msg-b">Nothing heard yet today.</div>
            <div class="msg-h">BirdNET-Go lists a bird once it is heard above the confidence you set.</div></div>`;
  } else if (layout === 'latest') body = latest(d, showSci, tone);
  else if (layout === 'gallery') body = gallery(d, showSci, tone);
  else if (layout === 'chart') body = chart(d);
  else if (layout === 'plate') body = plate(d, showSci, tone);
  else if (layout === 'bleed') body = bleed(d, showSci, tone, d.caption || 'band');
  else body = list(d, showSci, tone);

  installFonts(shadow, d.font_css);
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="bb lay-${esc(layout)} size-${esc(size)}">${body}</div>
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
      }
      .ico { width: 100%; height: 100%; fill: currentColor; display: block; }
      img { display: block; width: 100%; height: 100%; object-fit: cover; }
      .nopic { background: var(--muted); color: var(--text-secondary, #666); padding: 18%; }
      .toned { background: #f3ead6; }
      .bb .fit img { object-fit: contain; }
      .toned img { filter: grayscale(1) sepia(.5) contrast(1.12) brightness(1.04); mix-blend-mode: multiply; }
      .new {
        display: inline-block; margin-left: 1.4cqmin; padding: 0.3cqmin 1cqmin; vertical-align: 0.4cqmin;
        background: var(--alert); color: #fff; font-size: 2.2cqmin; font-weight: 800;
        letter-spacing: .08em; text-transform: uppercase; line-height: 1.2;
      }
      .kick { font-size: 2.6cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--alert); }
      .credit, .bcredit { font-size: 1.9cqmin; opacity: .6; padding: 0.8cqmin 2.4cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

      /* header shared by list, gallery, chart */
      .head { display: flex; align-items: stretch; flex: 0 0 auto; border-bottom: var(--rule) solid var(--ink); min-height: 11cqmin; }
      .hmark { background: var(--ink); color: var(--paper); width: 11cqmin; padding: 2.2cqmin; flex: 0 0 auto; }
      .htxt { flex: 1; min-width: 0; padding: 1.4cqmin 2.6cqmin; display: flex; flex-direction: column; justify-content: center; }
      .ht { font-weight: 900; font-size: 4.4cqmin; line-height: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .hs { font-size: 2.3cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; opacity: .6; margin-top: .6cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .hstat { padding: 1cqmin 2.6cqmin; display: flex; flex-direction: column; justify-content: center; align-items: flex-end; background: var(--muted); }
      .hstat.a { background: var(--hi); }
      .hstat b { font-weight: 900; font-size: 5.6cqmin; line-height: 1; }
      .hstat span { font-size: 2.1cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }

      /* list */
      .rows { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
      .row { flex: 1 1 0; min-height: 0; max-height: 14cqmin; display: flex; align-items: center; gap: 2.4cqmin; padding: 0 2.6cqmin; border-top: calc(var(--rule) * .6) solid var(--ink); }
      .row:first-child { border-top: 0; }
      .rpic { height: 78%; aspect-ratio: 1; flex: 0 0 auto; overflow: hidden; }
      .rnm { flex: 1; min-width: 0; }
      .rcn { font-weight: 800; font-size: 3.6cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .rsn { font-size: 2.3cqmin; font-style: italic; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .rago { font-size: 2.7cqmin; font-weight: 600; white-space: nowrap; }
      .rct { font-size: 4.6cqmin; font-weight: 900; min-width: 9cqmin; text-align: right; }
      .lfoot { flex: 0 0 auto; border-top: var(--rule) solid var(--ink); padding-top: 1cqmin; }
      .strip { display: flex; gap: .5cqmin; height: 9cqmin; padding: 0 2.6cqmin; }
      .sb { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; }
      .sb i { display: block; background: var(--muted); }
      .sb i.on { background: var(--ink); }
      .sb i.now { background: var(--alert); }
      .sb em { font-style: normal; font-size: 1.8cqmin; font-weight: 700; height: 2.4cqmin; text-align: center; }
      @container (max-height: 260px) { .rsn, .lfoot { display: none; } }

      /* latest */
      .lat { flex: 1; min-height: 0; display: flex; }
      .lpic { flex: 0 0 58%; display: flex; flex-direction: column; min-width: 0; }
      .lp { flex: 1; min-height: 0; overflow: hidden; }
      .lp img { object-fit: cover; }
      .lp.fit { padding: 2cqmin; }
      .lcap { border-top: var(--rule) solid var(--ink); padding: 1.8cqmin 2.6cqmin 2cqmin; }
      .lname { font-weight: 900; font-size: 7.4cqmin; line-height: 1.04; }
      .lmeta { display: flex; gap: 2.6cqmin; font-size: 2.6cqmin; font-weight: 600; margin-top: .8cqmin; flex-wrap: wrap; }
      .lmeta i { opacity: .7; }
      .lside { flex: 1; min-width: 0; border-left: var(--rule) solid var(--ink); display: flex; flex-direction: column; }
      .lstats { display: flex; border-bottom: var(--rule) solid var(--ink); }
      .lstats > div { flex: 1; padding: 1.8cqmin 2.4cqmin; background: var(--muted); }
      .lstats > .a { background: var(--hi); }
      .lstats b { display: block; font-weight: 900; font-size: 6.4cqmin; line-height: 1; }
      .lstats span { font-size: 2.1cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
      .lalso { flex: 1; min-height: 0; overflow: hidden; padding: 1cqmin 2.4cqmin 0; }
      .cap { font-size: 2.2cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; padding: .6cqmin 0 1cqmin; }
      .arow { display: flex; gap: 1.4cqmin; align-items: baseline; padding: 1.2cqmin 0; border-top: calc(var(--rule) * .6) solid var(--ink); font-size: 3cqmin; font-weight: 700; }
      .arow span { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .arow b { font-weight: 900; font-size: 3.3cqmin; }
      .lfoot2 { border-top: var(--rule) solid var(--ink); padding: 1.4cqmin 2.4cqmin; font-size: 2.1cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; opacity: .6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      @container (max-aspect-ratio: 1) {
        .lat { flex-direction: column; }
        .lpic { flex: 0 0 64%; }
        .lside { border-left: 0; border-top: var(--rule) solid var(--ink); }
      }

      /* gallery */
      .grid { flex: 1; min-height: 0; display: grid; gap: 1.6cqmin; padding: 1.8cqmin 2cqmin 1cqmin; grid-template-columns: repeat(4, 1fr); grid-auto-rows: 1fr; }
      .grid.n1 { grid-template-columns: 1fr; } .grid.n2 { grid-template-columns: repeat(2, 1fr); }
      .grid.n3 { grid-template-columns: repeat(3, 1fr); } .grid.n4 { grid-template-columns: repeat(4, 1fr); }
      .grid.n5, .grid.n6 { grid-template-columns: repeat(3, 1fr); }
      @container (max-aspect-ratio: 1) { .grid { grid-template-columns: repeat(2, 1fr) !important; } }
      .tile { position: relative; border: calc(var(--rule) * .7) solid var(--ink); display: flex; flex-direction: column; min-height: 0; overflow: hidden; background: #f6efdc; }
      .tpic { flex: 1; min-height: 0; overflow: hidden; }
      .tpic img { object-fit: cover; }
      .tpic.fit { padding: 1cqmin; }
      .tcap { display: flex; gap: 1cqmin; align-items: baseline; padding: .8cqmin 1.4cqmin; border-top: calc(var(--rule) * .7) solid var(--ink); background: var(--paper); font-size: 2.5cqmin; font-weight: 800; }
      .tcap span { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tcap b { font-size: 3cqmin; font-weight: 900; }
      .isnew .tcap { background: var(--alert); color: #fff; }
      .tnew { position: absolute; top: 0; left: 0; background: var(--alert); color: #fff; font-size: 2cqmin; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; padding: .5cqmin 1.2cqmin; }

      /* chart */
      .chart { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 1.6cqmin 2.6cqmin 1cqmin; gap: .8cqmin; }
      .crow { flex: 1 1 0; min-height: 0; max-height: 8cqmin; display: flex; align-items: center; gap: 2cqmin; }
      .crow .cn { width: 30%; font-weight: 800; font-size: 3cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cells { flex: 1; display: flex; gap: .35cqmin; height: 72%; }
      .cells i { flex: 1; background: var(--muted); }
      .cells i.l1 { background: color-mix(in srgb, var(--ink) 30%, var(--paper)); }
      .cells i.l2 { background: color-mix(in srgb, var(--ink) 62%, var(--paper)); }
      .cells i.l3 { background: var(--ink); }
      .cells i.fut { background: repeating-linear-gradient(45deg, var(--paper) 0 3px, var(--muted) 3px 5px); }
      .crow .cc { width: 8cqmin; text-align: right; font-weight: 900; font-size: 3.6cqmin; }
      .crow.axis { flex: 0 0 auto; height: 3cqmin; }
      .axis em { flex: 1; font-style: normal; font-size: 2cqmin; font-weight: 700; }

      /* plate: a page from the book */
      .lay-plate { background: #f3ead6; color: #2a2116; }
      .page { flex: 1; min-height: 0; display: flex; gap: 4cqmin; padding: 3.6cqmin; }
      .pframe { flex: 0 0 42%; background: #fffdf6; padding: 1.4cqmin; box-shadow: 0 0 0 1px #c9b98f; }
      .pp { width: 100%; height: 100%; overflow: hidden; }
      .pp img { object-fit: contain; }
      .ptext { flex: 1; min-width: 0; display: flex; flex-direction: column; font-family: 'IM Fell English', Georgia, serif; }
      .pkick { font-family: 'IM Fell English SC', Georgia, serif; font-size: 2.8cqmin; letter-spacing: .14em; }
      .pkick.sp { margin-top: 3.6cqmin; }
      .pname { font-family: 'IM Fell English SC', Georgia, serif; font-size: 8.8cqmin; letter-spacing: .06em; line-height: 1; margin-top: 1.4cqmin; }
      .psci { font-style: italic; font-size: 4.2cqmin; margin-top: .6cqmin; }
      .prule { height: 1px; background: #2a2116; margin: 2.6cqmin 0 2cqmin; box-shadow: 0 2px 0 #f3ead6, 0 3px 0 #2a2116; }
      .pline { font-style: italic; font-size: 3.6cqmin; }
      .pcols { columns: 2; column-gap: 4cqmin; margin-top: 1.2cqmin; font-size: 3.4cqmin; }
      .pcols div { display: flex; justify-content: space-between; border-bottom: 1px solid #b7a67c; padding: .5cqmin 0; break-inside: avoid; }
      .pcols span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 1cqmin; }
      .pcredit { margin-top: auto; font-style: italic; font-size: 2.2cqmin; opacity: .75; }
      @container (max-aspect-ratio: 1) {
        .page { flex-direction: column; }
        .pframe { flex: 0 0 52%; }
      }

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
      .tg { position: absolute; right: 3cqmin; top: 50%; width: 38%; transform: translateY(-50%) rotate(-4deg); background: #efe4c6; border: 1px solid #8f7c58;
            padding: 3cqmin 3.4cqmin 2.6cqmin 5.4cqmin; font-family: 'Special Elite', 'Courier New', monospace; color: #262018;
            clip-path: polygon(0 12%, 8% 0, 100% 0, 100% 100%, 8% 100%, 0 88%); }
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

      .msg { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 1.4cqmin; padding: 4cqmin; }
      .msg-t { font-weight: 900; font-size: 5cqmin; }
      .msg-b { font-size: 3.4cqmin; font-weight: 700; }
      .msg-h { font-size: 2.6cqmin; opacity: .7; }
    </style>`;
}
