// Prvky na straně (jako v Canvě): textové rámečky, obrázky a náplasti - přesun, změna
// velikosti, otočení, zámek, smazání.
//
// Prvek je blokový uzel s polohou v mm. Lišta s úchytem nad horním okrajem a úchyty
// vybraného prvku (8 na velikost, kolečko pod ním na otočení) nejsou součástí textu
// (contenteditable=false) a obsluhuje je tenhle pohled; obsah textového rámečku spravuje
// ProseMirror. Obrázek jde táhnout za celou plochu a jeho rohy drží poměr stran (se
// Shiftem volně). Zamčený prvek úchyty nemá a tah ho jen vybere. Během tažení se mění
// jen styl a teprve při puštění jde jedna transakce - celý přesun vrátí jedno „Zpět“.

import { closeHistory, TextSelection, NodeSelection } from 'prosemirror';
import { bodyTvaru, zNahore } from './schema.js';

const MM_NA_PX = 96 / 25.4;
const MIN_MM = 8;
const KROK_MM = 0.5;

const zaokrouhliMm = (x) => Math.round(x / KROK_MM) * KROK_MM;
const r1 = (x) => Math.round(x * 10) / 10;
const r4 = (x) => Math.round(x * 10000) / 10000;
const MAX_BODU = 24;      // víc bodů (elipsa má 36) se jako táhnutelné body neukazuje

export class PrvekView {
  // volby: meritko() a rozmer() -> { sirka, vyska } strany v mm
  constructor(node, view, getPos, volby) {
    this.node = node;
    this.view = view;
    this.getPos = getPos;
    this.volby = volby;
    this.tah = null;
    this.jeObrazek = node.type.name === 'obrazek';
    // Tvar (náplast): barevný obdélník - chová se jako obrázek (vybírá se klikem, smí
    // přesahovat okraj), jen bez obrázku a s volným poměrem stran.
    this.jeTvar = node.type.name === 'tvar';
    this.jeAtom = this.jeObrazek || this.jeTvar;
    const nazev = this.jeObrazek ? 'obrázek' : this.jeTvar ? 'náplast' : 'rámeček';

    this.dom = document.createElement('div');
    this.dom.className = this.jeObrazek ? 'ps-obrazek' : this.jeTvar ? 'ps-tvar' : 'ps-ramecek';
    this.dom.prvekView = this;    // tažení prvku pod jiným (Alt+klik, navrh.js)

    this.lista = document.createElement('div');
    this.lista.className = 'ps-prvek-lista';
    this.lista.contentEditable = 'false';
    this.lista.title = `Přesunout ${nazev}`;
    this.smazat = document.createElement('button');
    this.smazat.type = 'button';
    this.smazat.className = 'ps-prvek-smazat';
    this.smazat.textContent = '×';
    this.smazat.title = `Smazat ${nazev}`;
    this.lista.appendChild(this.smazat);

    // Úchyty vybraného prvku (jako v Canvě): 8 na velikost (obrázek jen rohy - strany by
    // ho deformovaly), kolečko pod ním na otočení, u mnohoúhelníku náplasti i body.
    // Jsou uvnitř prvku, takže se otáčejí s ním.
    this.uchyty = document.createElement('div');
    this.uchyty.className = 'ps-uchyty';
    this.uchyty.contentEditable = 'false';
    const stopka = document.createElement('div');
    stopka.className = 'ps-otoc-stopka';
    this.uchyty.appendChild(stopka);
    const rohyAStrany = this.jeObrazek ? ['nw', 'ne', 'se', 'sw'] : ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
    for (const u of [...rohyAStrany, 'otoc']) {
      const d = document.createElement('div');
      d.className = u === 'otoc' ? 'ps-uchyt ps-uchyt-otoc' : `ps-uchyt ps-uchyt-${u}`;
      d.dataset.uchyt = u;
      d.title = u === 'otoc' ? 'Otočit (se Shiftem po 15°)'
        : this.jeObrazek ? 'Změnit velikost (se Shiftem i poměr stran)' : 'Změnit velikost (u rohu se Shiftem ve stejném poměru)';
      this.uchyty.appendChild(d);
    }
    this.uchyty.addEventListener('pointerdown', (e) => this.chytni(e, e.target));
    // Zámek: u zamčeného prvku místo úchytů, klik ho odemkne.
    this.zamek = document.createElement('button');
    this.zamek.type = 'button';
    this.zamek.className = 'ps-zamek';
    this.zamek.contentEditable = 'false';
    this.zamek.title = 'Zamčeno - klik odemkne';
    this.zamek.setAttribute('aria-label', 'Odemknout');
    this.zamek.innerHTML = '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
    this.zamek.addEventListener('pointerdown', (e) => { e.preventDefault(); e.stopPropagation(); });
    this.zamek.addEventListener('click', (e) => { e.stopPropagation(); this.nastavZamek(false); });

    if (this.jeObrazek) {
      this.img = document.createElement('img');
      this.img.alt = '';
      this.img.draggable = false;
      this.dom.contentEditable = 'false';
      this.dom.append(this.lista, this.img, this.uchyty, this.zamek);
      this.img.addEventListener('pointerdown', (e) => this.zacni(e, 'presun'));
    } else if (this.jeTvar) {
      // Náplast bez lišty: táhne se za samotný tvar (klikat jde jen na jeho plochu, ne na
      // prázdné rohy obdélníku kolem volného tvaru), obrys kreslí SVG.
      this.dom.contentEditable = 'false';
      const NS = 'http://www.w3.org/2000/svg';
      this.svg = document.createElementNS(NS, 'svg');
      this.svg.setAttribute('viewBox', '0 0 100 100');
      this.svg.setAttribute('preserveAspectRatio', 'none');
      this.polygon = document.createElementNS(NS, 'polygon');
      this.svg.appendChild(this.polygon);
      this.bodyEl = document.createElement('div');
      this.bodyEl.className = 'ps-body';
      this.uchyty.appendChild(this.bodyEl);
      this.srafy = document.createElement('div');
      this.srafy.className = 'ps-srafy';
      this.dom.append(this.svg, this.srafy, this.uchyty, this.zamek);
      this.polygon.addEventListener('pointerdown', (e) => this.zacni(e, 'presun'));
      this.polygon.addEventListener('dblclick', (e) => this.pridejBod(e));
    } else {
      this.contentDOM = document.createElement('div');
      this.contentDOM.className = 'ps-ramecek-obsah';
      this.dom.append(this.lista, this.contentDOM, this.uchyty, this.zamek);
    }
    this.nastav();
    this.oznacPresah();

    this.lista.addEventListener('pointerdown', (e) => {
      if (e.target === this.smazat) return;
      this.zacni(e, 'presun');
    });
    this.smazat.addEventListener('click', () => this.odstran());
  }

  nastav(a = this.node.attrs) {
    const s = this.dom.style;
    s.left = a.x + 'mm';
    s.top = a.y + 'mm';
    s.width = a.w + 'mm';
    s.height = a.h + 'mm';
    s.zIndex = a.podklad ? 0 : (a.z || 0) + 1;    // vrstva, viz stylPrvku ve schema.js
    if (this.jeObrazek) {
      if (this.img.getAttribute('src') !== a.src) this.img.src = a.src;
      this.dom.classList.toggle('ps-podklad', !!a.podklad);
    }
    s.transform = a.otoceni ? `rotate(${a.otoceni}deg)` : '';
    if (a.otoceni) this.dom.dataset.otoceni = a.otoceni; else delete this.dom.dataset.otoceni;
    this.dom.classList.toggle('zamceny', !!a.zamceno);
    // Kurzor úchytu podle toho, kam po otočení míří.
    const KURZORY = ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize'];
    const SMERY = { n: 0, ne: 1, e: 2, se: 3, s: 0, sw: 1, w: 2, nw: 3 };
    for (const d of this.uchyty.querySelectorAll('[data-uchyt]')) {
      if (d.dataset.uchyt === 'otoc') continue;
      d.style.cursor = KURZORY[(SMERY[d.dataset.uchyt] + Math.round((a.otoceni || 0) / 45) + 8) % 4];
    }
    if (this.jeTvar) {
      this.polygon.setAttribute('points', bodyTvaru(a.body));
      this.polygon.setAttribute('fill', a.barva);
      const body = (a.body || '0,0 1,0 1,1 0,1').trim().split(/\s+/).map((b) => b.split(',').map((x) => +x * 100 + '%').join(' '));
      this.srafy.style.clipPath = `polygon(${body.join(',')})`;
      this.nastavBody(a);
    }
  }

  // Body mnohoúhelníku jde táhnout (volný tvar, trojúhelník). Obdélník má na to rohy
  // velikosti a elipsa (36 bodů) by byla jen hromada teček.
  nastavBody(a) {
    const body = a.body ? a.body.trim().split(/\s+/) : [];
    const pocet = body.length >= 3 && body.length <= MAX_BODU ? body.length : 0;
    while (this.bodyEl.children.length > pocet) this.bodyEl.lastChild.remove();
    while (this.bodyEl.children.length < pocet) {
      const d = document.createElement('div');
      d.className = 'ps-bod';
      d.title = 'Posunout bod (dvojklik bod odebere, dvojklik na okraj tvaru přidá)';
      this.bodyEl.appendChild(d);
    }
    for (let i = 0; i < pocet; i++) {
      const [bx, by] = body[i].split(',').map(Number);
      const d = this.bodyEl.children[i];
      d.dataset.bod = i;
      d.style.left = bx * 100 + '%';
      d.style.top = by * 100 + '%';
    }
    this.dom.classList.toggle('ma-body', pocet > 0);
  }

  // Kopie rámečku přes hřbet (presah) jde upravit jen ve dvojstraně, kde je rámeček celý -
  // jinak by se kopie a originál rozešly. Tady je jen k vidění.
  oznacPresah() {
    if (this.jeAtom) return;
    const presah = !!this.node.attrs.presah;
    this.dom.classList.toggle('ps-presah', presah);
    if (presah) this.contentDOM.contentEditable = 'false';
    else this.contentDOM.removeAttribute('contenteditable');
    if (presah) this.dom.title = 'Část textu přes hřbet z vedlejší strany - upravit ve dvojstraně';
    else this.dom.removeAttribute('title');
  }

  update(node) {
    if (node.type !== this.node.type) return false;
    if (!this.jeAtom && !!node.attrs.presah !== !!this.node.attrs.presah) { this.node = node; this.oznacPresah(); }
    this.node = node;
    if (!this.tah) this.nastav();
    return true;
  }

  selectNode() { this.dom.classList.add('vybrany'); }
  deselectNode() { this.dom.classList.remove('vybrany'); }

  // Lišta, roh a styl prvku nejsou obsah dokumentu.
  ignoreMutation(m) {
    if (!this.contentDOM) return true;
    if (m.type === 'selection') return false;
    return !this.contentDOM.contains(m.target) || m.target === this.contentDOM && m.type === 'attributes';
  }

  stopEvent(e) { return !this.contentDOM || !this.contentDOM.contains(e.target); }

  // Úchyt náplasti (velikost, otočení, bod) podle prvku, na který se kliklo - volá ho
  // i nástroj Náplast a výběr, když úchyt leží pod textem (navrh.js).
  chytni(e, cil) {
    const u = cil.closest && cil.closest('[data-uchyt], [data-bod]');
    if (!u) return false;
    if (u.dataset.bod != null) {
      // Dvojklik bod odebere (vlastní měření - pointerdown počet kliknutí spolehlivě nenese).
      const i = +u.dataset.bod, ted = performance.now(), minule = this._klikBodu;
      this._klikBodu = { i, ted };
      if (minule && minule.i === i && ted - minule.ted < 400) {
        e.preventDefault();
        e.stopPropagation();
        this._klikBodu = null;
        this.odeberBod(i);
        return true;
      }
      this.zacni(e, 'bod', null, i);
    } else if (u.dataset.uchyt === 'otoc') this.zacni(e, 'otoc');
    else this.zacni(e, 'velikost', null, u.dataset.uchyt);
    return true;
  }

  // Střed obdélníku náplasti na obrazovce (kolem něj se otáčí).
  stredNaObrazovce() {
    const r = this.dom.getBoundingClientRect();
    return [(r.left + r.right) / 2, (r.top + r.bottom) / 2];
  }

  // Bod obrazovky v podílech obdélníku náplasti (i otočené) - podle původních atributů tahu.
  podilBodu(e, a) {
    const pxNaMm = MM_NA_PX * this.volby.meritko();
    const [sx, sy] = this.tah ? this.tah.stred : this.stredNaObrazovce();
    const dx = (e.clientX - sx) / pxNaMm, dy = (e.clientY - sy) / pxNaMm;
    const u = -(a.otoceni || 0) * Math.PI / 180;
    const lx = dx * Math.cos(u) - dy * Math.sin(u), ly = dx * Math.sin(u) + dy * Math.cos(u);
    return [lx / a.w + 0.5, ly / a.h + 0.5];
  }

  // Body v podílech (smí přesahovat 0-1) -> nové x, y, w, h, body: obdélník se natáhne
  // přesně kolem bodů a střed se posune s ohledem na otočení.
  normalizujBody(a, body) {
    const xs = body.map((b) => b[0] * a.w), ys = body.map((b) => b[1] * a.h);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const w = Math.max(maxX - minX, 0.2), h = Math.max(maxY - minY, 0.2);
    const ox = (minX + maxX) / 2 - a.w / 2, oy = (minY + maxY) / 2 - a.h / 2;
    const u = (a.otoceni || 0) * Math.PI / 180;
    const cx = a.x + a.w / 2 + ox * Math.cos(u) - oy * Math.sin(u);
    const cy = a.y + a.h / 2 + ox * Math.sin(u) + oy * Math.cos(u);
    return { ...a, x: r1(cx - w / 2), y: r1(cy - h / 2), w: r1(w), h: r1(h),
      body: xs.map((x, i) => `${r4((x - minX) / w)},${r4((ys[i] - minY) / h)}`).join(' ') };
  }

  bodyPole(a) {
    return (a.body || '0,0 1,0 1,1 0,1').trim().split(/\s+/).map((b) => b.split(',').map(Number));
  }

  odeberBod(i) {
    const a = this.node.attrs;
    const body = this.bodyPole(a);
    if (body.length <= 3) return;
    body.splice(i, 1);
    this.zapis(this.normalizujBody(a, body));
  }

  // Dvojklik na okraj vybraného mnohoúhelníku přidá bod na nejbližší hranu.
  pridejBod(e) {
    const a = this.node.attrs;
    if (!this.dom.classList.contains('ma-body') || !this.dom.classList.contains('vybrany')) return;
    e.preventDefault();
    e.stopPropagation();
    const body = this.bodyPole(a);
    if (body.length >= MAX_BODU) return;
    const [px, py] = this.podilBodu(e, a);
    let nej = 0, vzd = Infinity;
    for (let i = 0; i < body.length; i++) {
      const [ax, ay] = body[i], [bx, by] = body[(i + 1) % body.length];
      // Vzdálenost bodu od úsečky v mm.
      const vx = (bx - ax) * a.w, vy = (by - ay) * a.h, wx = (px - ax) * a.w, wy = (py - ay) * a.h;
      const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / (vx * vx + vy * vy || 1)));
      const d = Math.hypot(wx - t * vx, wy - t * vy);
      if (d < vzd) { vzd = d; nej = i; }
    }
    body.splice(nej + 1, 0, [px, py]);
    this.zapis(this.normalizujBody(a, body));
  }

  zapis(attrs) {
    const tr = closeHistory(this.view.state.tr).setNodeMarkup(this.getPos(), null, attrs);
    if (this.jeAtom) tr.setSelection(NodeSelection.create(tr.doc, this.getPos()));
    this.view.dispatch(tr);
    this.view.focus();
  }

  nastavZamek(zamknout) {
    this.zapis({ ...this.node.attrs, zamceno: !!zamknout });
  }

  // priKliku: co udělat místo výběru, když se nepohnulo (klik, ne tah).
  zacni(e, druh, priKliku = null, co = null) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const a = this.node.attrs;
    this.tah = { druh, co, x0: e.clientX, y0: e.clientY, puvodni: { ...a }, nove: { ...a }, posunuto: false, priKliku,
      stred: this.stredNaObrazovce(), zamceno: !!a.zamceno };
    this.dom.classList.add('tazeny');
    this._pohyb = (ev) => this.pohyb(ev);
    this._konec = () => this.konec();
    this._esc = (ev) => { if (ev.key === 'Escape') this.zrus(); };
    window.addEventListener('pointermove', this._pohyb);
    window.addEventListener('pointerup', this._konec);
    window.addEventListener('keydown', this._esc, true);
  }

  pohyb(e) {
    const t = this.tah;
    if (!t) return;
    const pxNaMm = MM_NA_PX * this.volby.meritko();
    const dx = (e.clientX - t.x0) / pxNaMm, dy = (e.clientY - t.y0) / pxNaMm;
    if (t.zamceno) return;      // zamčený prvek se nehne - tah je jen klik (výběr)
    if (!t.posunuto && Math.hypot(dx, dy) * pxNaMm < 3) return;
    t.posunuto = true;
    // Prvky smí i mimo stranu (strana je ořízne, editor je ukáže zašedlé) - jen do
    // vzdálenosti `presah` (mm) od plátna, ať se nedají ztratit z dosahu.
    const { sirka, vyska } = this.volby.rozmer();
    const M = this.volby.presah ? this.volby.presah() : 0;
    const p = t.puvodni;
    // Náplast se ladí jemněji (0,1 mm) - zakrývá i malé věci.
    const zaokrouhli = this.jeTvar ? (x) => Math.round(x * 10) / 10 : zaokrouhliMm;
    if (t.druh === 'velikost') {
      // Obrázek drží poměr stran (se Shiftem volně), ostatní naopak.
      t.nove = this.zmenVelikost(p, dx, dy, t.co, this.jeObrazek ? !e.shiftKey : e.shiftKey);
    } else if (t.druh === 'otoc') {
      // Kolečko je pod prvkem: svisle dolů od středu = 0°.
      const [sx, sy] = t.stred;
      let uhel = Math.atan2(e.clientY - sy, e.clientX - sx) * 180 / Math.PI - 90;
      if (e.shiftKey) uhel = Math.round(uhel / 15) * 15;
      else if (Math.abs(uhel - Math.round(uhel / 90) * 90) < 4) uhel = Math.round(uhel / 90) * 90;  // přichytí k pravému úhlu
      uhel = ((Math.round(uhel * 10) / 10) % 360 + 540) % 360 - 180;
      t.nove = { ...p, otoceni: uhel === -180 ? 180 : uhel };
    } else if (t.druh === 'bod') {
      const body = this.bodyPole(p);
      body[t.co] = this.podilBodu(e, p);
      t.nove = this.normalizujBody(p, body);
    } else if (t.druh === 'presun') {
      t.nove.x = Math.min(Math.max(zaokrouhli(p.x + dx), -M), sirka + M - p.w);
      t.nove.y = Math.min(Math.max(zaokrouhli(p.y + dy), -M), vyska + M - p.h);
    }
    this.nastav(t.nove);
  }

  // Velikost za úchyt `u` (n, ne, e…) v osách prvku (i otočeného): protější strana / roh
  // zůstane na místě. drzetPomer: roh mění obě strany ve stejném poměru. Náplast nemá
  // nejmenší velikost (i malá ploška je potřeba), rámeček a obrázek MIN_MM.
  zmenVelikost(p, dx, dy, u, drzetPomer) {
    const min = this.jeTvar ? 0.2 : MIN_MM;
    const zaokr = this.jeTvar ? r1 : zaokrouhliMm;
    const sx = u.includes('e') ? 1 : u.includes('w') ? -1 : 0;
    const sy = u.includes('s') ? 1 : u.includes('n') ? -1 : 0;
    const uhel = (p.otoceni || 0) * Math.PI / 180, cos = Math.cos(uhel), sin = Math.sin(uhel);
    const lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos;     // posun v osách náplasti
    let w = sx ? Math.max(p.w + sx * lx, min) : p.w;
    let h = sy ? Math.max(p.h + sy * ly, min) : p.h;
    if (drzetPomer && sx && sy) {
      // Rozhoduje větší z obou změn.
      const k = Math.abs(w / p.w - 1) >= Math.abs(h / p.h - 1) ? w / p.w : h / p.h;
      w = Math.max(p.w * k, min);
      h = Math.max(p.h * k, min);
    }
    w = zaokr(w);
    h = zaokr(h);
    // Kotva (protější strana) zůstává: nový střed = kotva + půlka nového rozměru.
    const kx = -sx * p.w / 2, ky = -sy * p.h / 2;
    const ox = kx + sx * w / 2, oy = ky + sy * h / 2;
    const cx = p.x + p.w / 2 + ox * cos - oy * sin, cy = p.y + p.h / 2 + ox * sin + oy * cos;
    return { ...p, x: r1(cx - w / 2), y: r1(cy - h / 2), w, h };
  }

  konec() {
    const t = this.tah;
    this.uklid();
    if (!t) return;
    if (t.posunuto) this._klikBodu = null;     // tažení bodu není první klik dvojkliku
    if (!t.posunuto) {
      if (t.priKliku) t.priKliku();
      else if (this.jeAtom) {
        this.view.dispatch(this.view.state.tr.setSelection(NodeSelection.create(this.view.state.doc, this.getPos())));
      } else {
        // Klik na lištu rámečku: kurzor na konec jeho textu.
        const pos = this.getPos() + this.node.nodeSize - 2;
        this.view.dispatch(this.view.state.tr.setSelection(TextSelection.near(this.view.state.doc.resolve(pos), -1)));
      }
      this.view.focus();
      return;
    }
    const tr = closeHistory(this.view.state.tr).setNodeMarkup(this.getPos(), null, t.nove);
    if (this.jeAtom) tr.setSelection(NodeSelection.create(tr.doc, this.getPos()));
    this.view.dispatch(tr);
    // Tažení za lištu nebo roh nedá fokus do editoru (pointerdown je zastavený) - bez
    // tohohle by Ctrl+Z po přesunu nešlo, dokud se nekliklo jinam.
    this.view.focus();
  }

  zrus() {
    const t = this.tah;
    this.uklid();
    if (t) this.nastav();
  }

  uklid() {
    window.removeEventListener('pointermove', this._pohyb);
    window.removeEventListener('pointerup', this._konec);
    window.removeEventListener('keydown', this._esc, true);
    this.dom.classList.remove('tazeny');
    this.tah = null;
  }

  odstran() {
    if (this.node.attrs.zamceno) return;
    const { state } = this.view;
    const pos = this.getPos();
    this.view.dispatch(closeHistory(state.tr).delete(pos, pos + this.node.nodeSize));
    this.view.focus();
  }

  destroy() { this.uklid(); }
}


// --- obrázky --------------------------------------------------------------------------

const MAX_PX = 2000;      // delší strana; víc tisk při 300 dpi na A5 nepotřebuje

// Soubor -> data: URL, větší obrázky zmenšené. Průhledné zůstanou PNG, ostatní JPEG.
export function nactiObrazek(soubor) {
  return new Promise((ok, chyba) => {
    if (!soubor || !/^image\//.test(soubor.type)) { chyba(new Error('Soubor není obrázek.')); return; }
    const url = URL.createObjectURL(soubor);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const k = Math.min(1, MAX_PX / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k);
      const platno = document.createElement('canvas');
      platno.width = w;
      platno.height = h;
      const c = platno.getContext('2d');
      c.drawImage(img, 0, 0, w, h);
      const pruhledny = /png|gif|webp|svg/.test(soubor.type) && maPruhlednost(c, w, h);
      ok({ src: platno.toDataURL(pruhledny ? 'image/png' : 'image/jpeg', 0.9), w, h });
    };
    img.onerror = () => { URL.revokeObjectURL(url); chyba(new Error('Obrázek se nepodařilo načíst.')); };
    img.src = url;
  });
}

function maPruhlednost(c, w, h) {
  const data = c.getImageData(0, 0, w, h).data;
  for (let i = 3; i < data.length; i += 4 * 7) if (data[i] < 250) return true;
  return false;
}

// Vloží obrázek na stranu: na šířku poloviny sazby (nejvýš na výšku sazby), uprostřed.
export function vlozObrazek(view, format, { src, w, h }, kam = null) {
  const sirkaSazby = format.sirka - 2 * format.okraj, vyskaSazby = format.vyska - 2 * format.okraj;
  let sirka = sirkaSazby / 2, vyska = sirka * h / w;
  if (vyska > vyskaSazby) { vyska = vyskaSazby; sirka = vyska * w / h; }
  sirka = zaokrouhliMm(sirka);
  vyska = zaokrouhliMm(vyska);
  // format.posun: ve dvojstraně levý okraj upravované strany (mm).
  const x = kam ? zaokrouhliMm(kam.x - sirka / 2) : zaokrouhliMm((format.posun || 0) + (format.sirka - sirka) / 2);
  const y = kam ? zaokrouhliMm(kam.y - vyska / 2) : zaokrouhliMm((format.vyska - vyska) / 2);
  const uzel = view.state.schema.nodes.obrazek.create({ src, x, y, w: sirka, h: vyska, z: zNahore(view.state.doc) });
  const konec = view.state.doc.content.size;
  const tr = closeHistory(view.state.tr).insert(konec, uzel);
  tr.setSelection(NodeSelection.create(tr.doc, konec));
  view.dispatch(tr);
  view.focus();
}
