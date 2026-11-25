// Nástroj Náplast (jako Malování): zapne se tlačítkem v liště a zůstane zapnutý, dokud
// se nevypne (znovu tlačítko, nebo Esc).
//
// - Obdélník, elipsa, trojúhelník: klik vloží malý tvar, tažení tvar podle tahu (se
//   Shiftem čtverec / kruh). Elipsa je mnohoúhelník z 36 bodů - schéma i PDF zůstávají
//   u mnohoúhelníku.
// - Volný tvar: každý klik přidá bod; tvar uzavře klik na první bod, dvojklik nebo
//   Enter, Esc rozkreslený tvar zahodí.
//
// Velikost není omezená - i malá ploška je potřeba. Klik na existující náplast ji vybere
// a táhne (PrvekView). Při zapnutém nástroji je vidět obrys všech náplastí (třída
// rezim-naplasti na kořeni editoru).

import { closeHistory, NodeSelection, TextSelection } from 'prosemirror';
import { vrstvaPodTextem, zNahore } from './schema.js';

const MM_NA_PX = 96 / 25.4;
const NS = 'http://www.w3.org/2000/svg';
const KLIK_PX = 4;              // menší pohyb je klik, ne tah
const MALY = { w: 8, h: 4 };    // obdélník z kliku (mm)
const PRVNI_BOD_PX = 8;         // klik tak blízko prvního bodu tvar uzavře

const r1 = (x) => Math.round(x * 10) / 10;
const r4 = (x) => Math.round(x * 10000) / 10000;

// Základní tvary: body v podílech obdélníku kolem (jako uzel tvar), null = obdélník.
const BODY_ELIPSY = Array.from({ length: 36 }, (_, i) => {
  const a = (i / 36) * 2 * Math.PI;
  return `${r4(0.5 + 0.5 * Math.cos(a))},${r4(0.5 + 0.5 * Math.sin(a))}`;
}).join(' ');
export const TVARY = { obdelnik: null, elipsa: BODY_ELIPSY, trojuhelnik: '0.5,0 1,1 0,1' };
const bodyVObdelniku = (body, x, y, w, h) => (body || '0,0 1,0 1,1 0,1').split(' ')
  .map((b) => { const [bx, by] = b.split(',').map(Number); return [x + bx * w, y + by * h]; });

export class NastrojNaplasti {
  // volby: view, stranka (prvek strany), koren (editor), meritko(), poZmene()
  constructor(volby) {
    this.v = volby;
    this.aktivni = false;
    this.druh = 'obdelnik';     // obdelnik | elipsa | trojuhelnik | volny
    this.barva = '#ffffff';
    this.body = null;           // rozkreslený volný tvar: [[x, y] mm]
    this.nahled = document.createElementNS(NS, 'svg');
    this.nahled.setAttribute('class', 'ps-naplast-nahled');
    this.nahled.style.display = 'none';
    volby.stranka.appendChild(this.nahled);
    volby.stranka.addEventListener('pointerdown', (e) => this.dolu(e), true);
    volby.stranka.addEventListener('pointermove', (e) => this.pohybVolneho(e));
    volby.stranka.addEventListener('dblclick', (e) => { if (this.aktivni && this.body) { e.preventDefault(); this.dokonciVolny(); } }, true);
  }

  prepni(zapnout = !this.aktivni) {
    this.aktivni = zapnout;
    this.zrusVolny();
    this.v.koren.classList.toggle('rezim-naplasti', zapnout);
    this.v.poZmene();
  }

  nastavDruh(druh) {
    this.druh = druh === 'volny' || druh in TVARY ? druh : 'obdelnik';
    this.zrusVolny();
    this.v.poZmene();
  }

  // Esc: nejdřív zahodí rozkreslený tvar, pak vypne nástroj. Vrací, jestli něco udělal.
  esc() {
    if (!this.aktivni) return false;
    if (this.body) { this.zrusVolny(); return true; }
    this.prepni(false);
    return true;
  }

  enter() {
    if (!this.aktivni || !this.body) return false;
    this.dokonciVolny();
    return true;
  }

  mm(e) {
    const r = this.v.stranka.getBoundingClientRect();
    const k = MM_NA_PX * this.v.meritko();
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  }

  dolu(e) {
    if (!this.aktivni || e.button !== 0) return;
    // Na existující náplast (mimo rozkreslený tvar): výběr a přesun jako vždy.
    if (!this.body && e.target.closest && e.target.closest('.ps-tvar')) return;
    e.preventDefault();
    e.stopPropagation();
    if (this.druh === 'volny') this.bodVolneho(e);
    else this.tahObdelniku(e);
  }

  // --- obdélník ---

  tahObdelniku(e) {
    const [x0, y0] = this.mm(e);
    const sx = e.clientX, sy = e.clientY;
    const body = TVARY[this.druh] ?? null;
    let tazeno = false;
    // Obdélník tahu; se Shiftem stejná šířka a výška (čtverec, kruh).
    const obdelnik = (ev) => {
      let [x1, y1] = this.mm(ev);
      if (ev.shiftKey) {
        const d = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
        x1 = x0 + Math.sign(x1 - x0 || 1) * d;
        y1 = y0 + Math.sign(y1 - y0 || 1) * d;
      }
      return { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) };
    };
    const pohyb = (ev) => {
      if (!tazeno && Math.hypot(ev.clientX - sx, ev.clientY - sy) < KLIK_PX) return;
      tazeno = true;
      const o = obdelnik(ev);
      this.ukazNahled(bodyVObdelniku(body, o.x, o.y, o.w, o.h), true);
    };
    const konec = (ev) => {
      window.removeEventListener('pointermove', pohyb);
      window.removeEventListener('pointerup', konec);
      this.skryjNahled();
      if (!tazeno) {
        // Klik: malý tvar (kruh a trojúhelník stejně široké jako vysoké).
        const m = body ? { w: MALY.w, h: MALY.w } : MALY;
        this.vloz({ x: r1(x0 - m.w / 2), y: r1(y0 - m.h / 2), w: m.w, h: m.h, body });
        return;
      }
      const o = obdelnik(ev);
      this.vloz({ x: r1(o.x), y: r1(o.y), w: Math.max(r1(o.w), 0.2), h: Math.max(r1(o.h), 0.2), body });
    };
    window.addEventListener('pointermove', pohyb);
    window.addEventListener('pointerup', konec);
  }

  // --- volný tvar ---

  bodVolneho(e) {
    const bod = this.mm(e);
    if (this.body && this.body.length >= 3) {
      const [px, py] = this.body[0];
      const k = MM_NA_PX * this.v.meritko();
      if (Math.hypot((bod[0] - px) * k, (bod[1] - py) * k) <= PRVNI_BOD_PX) { this.dokonciVolny(); return; }
    }
    if (!this.body) this.body = [];
    this.body.push(bod);
    this.ukazNahled([...this.body], false);
  }

  pohybVolneho(e) {
    if (!this.aktivni || !this.body) return;
    this.ukazNahled([...this.body, this.mm(e)], false);
  }

  dokonciVolny() {
    // Dvojklik přidal stejný bod dvakrát - duplicity pryč.
    const body = (this.body || []).filter((b, i, a) => i === 0 || Math.hypot(b[0] - a[i - 1][0], b[1] - a[i - 1][1]) > 0.05);
    this.zrusVolny();
    if (body.length < 3) return;
    const xs = body.map((b) => b[0]), ys = body.map((b) => b[1]);
    const x = Math.min(...xs), y = Math.min(...ys);
    const w = Math.max(Math.max(...xs) - x, 0.2), h = Math.max(Math.max(...ys) - y, 0.2);
    this.vloz({ x: r1(x), y: r1(y), w: r1(w) || 0.2, h: r1(h) || 0.2,
      body: body.map(([bx, by]) => `${r4((bx - x) / w)},${r4((by - y) / h)}`).join(' ') });
  }

  zrusVolny() {
    this.body = null;
    this.skryjNahled();
  }

  // --- náhled kreslení a vložení ---

  ukazNahled(body, uzavreny) {
    const k = MM_NA_PX;    // náhled je uvnitř strany, v jejích (nezvětšených) px
    this.nahled.style.display = '';
    this.nahled.innerHTML = '';
    const tvar = document.createElementNS(NS, uzavreny ? 'polygon' : 'polyline');
    tvar.setAttribute('points', body.map(([x, y]) => `${x * k},${y * k}`).join(' '));
    tvar.setAttribute('fill', uzavreny ? this.barva : 'none');
    this.nahled.appendChild(tvar);
    if (!uzavreny && body.length) {
      const prvni = document.createElementNS(NS, 'circle');
      prvni.setAttribute('cx', body[0][0] * k);
      prvni.setAttribute('cy', body[0][1] * k);
      prvni.setAttribute('r', PRVNI_BOD_PX / this.v.meritko());
      this.nahled.appendChild(prvni);
    }
  }

  skryjNahled() {
    this.nahled.style.display = 'none';
    this.nahled.innerHTML = '';
  }

  vloz(attrs) {
    const { view } = this.v;
    const n = view.state.schema.nodes.tvar.create({ ...attrs, barva: this.barva });
    // Na konec dokumentu, ve vrstvách nad dřív vložené náplasti, ale pod text.
    const pos = view.state.doc.content.size;
    const tr = closeHistory(view.state.tr).insert(pos, n);
    vrstvaPodTextem(tr, tr.doc.childCount - 1);
    tr.setSelection(NodeSelection.create(tr.doc, pos));
    view.dispatch(tr);
    // Klik nástrojem fokus do editoru nedal (pointerdown je zastavený) - bez něj by
    // nová vybraná náplast nešla smazat Backspacem.
    view.focus();
  }
}

// Nástroj Text: stejně jako Náplast se zapne tlačítkem a pak se na stranu klikne (rámeček
// výchozí velikosti) nebo táhne (rámeček podle tahu). Po vložení nástroj skončí a kurzor
// je v novém rámečku - další klik do textu má psát, ne zakládat další rámeček. Klik do
// existujícího textu nástroj jen vypne.
const RAMECEK = { w: 60, h: 20 };   // rámeček z kliku (mm)
const MIN_RAMECEK = { w: 10, h: 6 };

export class NastrojTextu {
  // volby: view, stranka, koren, meritko(), poZmene()
  constructor(volby) {
    this.v = volby;
    this.aktivni = false;
    this.nahled = document.createElementNS(NS, 'svg');
    this.nahled.setAttribute('class', 'ps-naplast-nahled');
    this.nahled.style.display = 'none';
    volby.stranka.appendChild(this.nahled);
    volby.stranka.addEventListener('pointerdown', (e) => this.dolu(e), true);
  }

  prepni(zapnout = !this.aktivni) {
    this.aktivni = zapnout;
    this.v.koren.classList.toggle('rezim-textu', zapnout);
    this.v.poZmene();
  }

  mm(e) {
    const r = this.v.stranka.getBoundingClientRect();
    const k = MM_NA_PX * this.v.meritko();
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  }

  dolu(e) {
    if (!this.aktivni || e.button !== 0) return;
    if (e.target.closest && e.target.closest('.ps-ramecek')) { this.prepni(false); return; }
    e.preventDefault();
    e.stopPropagation();
    const [x0, y0] = this.mm(e);
    const sx = e.clientX, sy = e.clientY;
    let tazeno = false;
    const pohyb = (ev) => {
      if (!tazeno && Math.hypot(ev.clientX - sx, ev.clientY - sy) < KLIK_PX) return;
      tazeno = true;
      const [x1, y1] = this.mm(ev);
      const k = MM_NA_PX;
      this.nahled.style.display = '';
      this.nahled.innerHTML = '';
      const r = document.createElementNS(NS, 'polygon');
      r.setAttribute('points', [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([x, y]) => `${x * k},${y * k}`).join(' '));
      r.setAttribute('fill', 'none');
      this.nahled.appendChild(r);
    };
    const konec = (ev) => {
      window.removeEventListener('pointermove', pohyb);
      window.removeEventListener('pointerup', konec);
      this.nahled.style.display = 'none';
      this.nahled.innerHTML = '';
      if (!tazeno) { this.vloz({ x: r1(x0), y: r1(y0), w: RAMECEK.w, h: RAMECEK.h }); return; }
      const [x1, y1] = this.mm(ev);
      this.vloz({ x: r1(Math.min(x0, x1)), y: r1(Math.min(y0, y1)),
        w: Math.max(r1(Math.abs(x1 - x0)), MIN_RAMECEK.w), h: Math.max(r1(Math.abs(y1 - y0)), MIN_RAMECEK.h) });
    };
    window.addEventListener('pointermove', pohyb);
    window.addEventListener('pointerup', konec);
  }

  vloz(attrs) {
    const { view } = this.v;
    const { schema } = view.state;
    const n = schema.nodes.ramecek.create({ ...attrs, z: zNahore(view.state.doc) }, schema.nodes.odstavec.create({ styl: 'normalni' }));
    const pos = view.state.doc.content.size;
    const tr = closeHistory(view.state.tr).insert(pos, n);
    tr.setSelection(TextSelection.create(tr.doc, pos + 2));
    view.dispatch(tr.scrollIntoView());
    this.prepni(false);
    view.focus();
  }
}
