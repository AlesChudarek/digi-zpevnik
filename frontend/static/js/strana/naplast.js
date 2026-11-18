// Nástroj Náplast (jako Malování): zapne se tlačítkem v liště a zůstane zapnutý, dokud
// se nevypne (znovu tlačítko, nebo Esc).
//
// - Obdélník: klik vloží malý obdélník, tažení obdélník podle tahu.
// - Volný tvar: každý klik přidá bod; tvar uzavře klik na první bod, dvojklik nebo
//   Enter, Esc rozkreslený tvar zahodí.
//
// Velikost není omezená - i malá ploška je potřeba. Klik na existující náplast ji vybere
// a táhne (PrvekView). Při zapnutém nástroji je vidět obrys všech náplastí (třída
// rezim-naplasti na kořeni editoru).

import { closeHistory, NodeSelection, TextSelection } from 'prosemirror';

const MM_NA_PX = 96 / 25.4;
const NS = 'http://www.w3.org/2000/svg';
const KLIK_PX = 4;              // menší pohyb je klik, ne tah
const MALY = { w: 8, h: 4 };    // obdélník z kliku (mm)
const PRVNI_BOD_PX = 8;         // klik tak blízko prvního bodu tvar uzavře

const r1 = (x) => Math.round(x * 10) / 10;
const r4 = (x) => Math.round(x * 10000) / 10000;

export class NastrojNaplasti {
  // volby: view, stranka (prvek strany), koren (editor), meritko(), poZmene()
  constructor(volby) {
    this.v = volby;
    this.aktivni = false;
    this.druh = 'obdelnik';     // obdelnik | volny
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
    this.druh = druh === 'volny' ? 'volny' : 'obdelnik';
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
    let tazeno = false;
    const pohyb = (ev) => {
      if (!tazeno && Math.hypot(ev.clientX - sx, ev.clientY - sy) < KLIK_PX) return;
      tazeno = true;
      const [x1, y1] = this.mm(ev);
      this.ukazNahled([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], true);
    };
    const konec = (ev) => {
      window.removeEventListener('pointermove', pohyb);
      window.removeEventListener('pointerup', konec);
      this.skryjNahled();
      if (!tazeno) {
        this.vloz({ x: r1(x0 - MALY.w / 2), y: r1(y0 - MALY.h / 2), w: MALY.w, h: MALY.h, body: null });
        return;
      }
      const [x1, y1] = this.mm(ev);
      const x = Math.min(x0, x1), y = Math.min(y0, y1);
      this.vloz({ x: r1(x), y: r1(y), w: Math.max(r1(Math.abs(x1 - x0)), 0.2), h: Math.max(r1(Math.abs(y1 - y0)), 0.2), body: null });
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
    // Na konec dokumentu: nad sken (ten je první) i nad dřív vložené náplasti.
    const pos = view.state.doc.content.size;
    const tr = closeHistory(view.state.tr).insert(pos, n);
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
    const n = schema.nodes.ramecek.create(attrs, schema.nodes.odstavec.create({ styl: 'normalni' }));
    const pos = view.state.doc.content.size;
    const tr = closeHistory(view.state.tr).insert(pos, n);
    tr.setSelection(TextSelection.create(tr.doc, pos + 2));
    view.dispatch(tr.scrollIntoView());
    this.prepni(false);
    view.focus();
  }
}
