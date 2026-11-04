// Rámečky na straně (jako v Canvě): přesun, změna velikosti, smazání.
//
// Rámeček je blokový uzel s polohou v mm. Lišta s úchytem nad horním okrajem a roh
// vpravo dole nejsou součástí textu (contenteditable=false) a obsluhuje je tenhle
// pohled; obsah rámečku spravuje ProseMirror. Přesun mění během tažení jen styl
// a teprve při puštění pošle jednu transakci - jde vrátit jedním „Zpět“.

import { closeHistory, TextSelection } from 'prosemirror';

const MM_NA_PX = 96 / 25.4;
const MIN_MM = 10;
const KROK_MM = 0.5;

const zaokrouhli = (x) => Math.round(x / KROK_MM) * KROK_MM;

export class RamecekView {
  // volby: meritko() a rozmer() -> { sirka, vyska } strany v mm
  constructor(node, view, getPos, volby) {
    this.node = node;
    this.view = view;
    this.getPos = getPos;
    this.volby = volby;
    this.tah = null;

    this.dom = document.createElement('div');
    this.dom.className = 'ps-ramecek';

    this.lista = document.createElement('div');
    this.lista.className = 'ps-ramecek-lista';
    this.lista.contentEditable = 'false';
    this.lista.title = 'Přesunout rámeček';
    this.smazat = document.createElement('button');
    this.smazat.type = 'button';
    this.smazat.className = 'ps-ramecek-smazat';
    this.smazat.textContent = '×';
    this.smazat.title = 'Smazat rámeček';
    this.lista.appendChild(this.smazat);

    this.roh = document.createElement('div');
    this.roh.className = 'ps-ramecek-roh';
    this.roh.contentEditable = 'false';
    this.roh.title = 'Změnit velikost';

    this.contentDOM = document.createElement('div');
    this.contentDOM.className = 'ps-ramecek-obsah';

    this.dom.append(this.lista, this.contentDOM, this.roh);
    this.nastav();

    this.lista.addEventListener('pointerdown', (e) => {
      if (e.target === this.smazat) return;
      this.zacni(e, 'presun');
    });
    this.roh.addEventListener('pointerdown', (e) => this.zacni(e, 'rozmer'));
    this.smazat.addEventListener('click', () => this.odstran());
  }

  nastav(a = this.node.attrs) {
    const s = this.dom.style;
    s.left = a.x + 'mm';
    s.top = a.y + 'mm';
    s.width = a.w + 'mm';
    s.height = a.h + 'mm';
  }

  update(node) {
    if (node.type !== this.node.type) return false;
    this.node = node;
    if (!this.tah) this.nastav();
    return true;
  }

  // Lišta, roh a styl rámečku nejsou obsah dokumentu.
  ignoreMutation(m) {
    if (m.type === 'selection') return false;
    return !this.contentDOM.contains(m.target) || m.target === this.contentDOM && m.type === 'attributes';
  }

  stopEvent(e) { return !this.contentDOM.contains(e.target); }

  zacni(e, druh) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const a = this.node.attrs;
    this.tah = { druh, x0: e.clientX, y0: e.clientY, puvodni: { ...a }, nove: { ...a }, posunuto: false };
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
    if (!t.posunuto && Math.hypot(dx, dy) * pxNaMm < 3) return;
    t.posunuto = true;
    const { sirka, vyska } = this.volby.rozmer();
    const p = t.puvodni;
    if (t.druh === 'presun') {
      t.nove.x = Math.min(Math.max(zaokrouhli(p.x + dx), 0), sirka - p.w);
      t.nove.y = Math.min(Math.max(zaokrouhli(p.y + dy), 0), vyska - p.h);
    } else {
      t.nove.w = Math.min(Math.max(zaokrouhli(p.w + dx), MIN_MM), sirka - p.x);
      t.nove.h = Math.min(Math.max(zaokrouhli(p.h + dy), MIN_MM), vyska - p.y);
    }
    this.nastav(t.nove);
  }

  konec() {
    const t = this.tah;
    this.uklid();
    if (!t) return;
    if (!t.posunuto) {
      // Klik na lištu: kurzor na konec rámečku.
      const pos = this.getPos() + this.node.nodeSize - 2;
      this.view.dispatch(this.view.state.tr.setSelection(TextSelection.near(this.view.state.doc.resolve(pos), -1)));
      this.view.focus();
      return;
    }
    const tr = closeHistory(this.view.state.tr).setNodeMarkup(this.getPos(), null, t.nove);
    this.view.dispatch(tr);
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
    const { state } = this.view;
    if (state.doc.childCount < 2) {
      if (window.oznam) window.oznam('Strana musí mít aspoň jeden textový rámeček.', { nadpis: 'Rámeček nejde smazat', druh: 'info' });
      return;
    }
    const pos = this.getPos();
    this.view.dispatch(closeHistory(state.tr).delete(pos, pos + this.node.nodeSize));
    this.view.focus();
  }

  destroy() { this.uklid(); }
}

// Nový textový rámeček: posunutý kousek od levého horního rohu sazby, aby byl vidět.
export function pridejRamecek(view, format) {
  const { schema } = view.state;
  const n = view.state.doc.childCount;
  const ramecek = schema.nodes.ramecek.create(
    { x: format.okraj + 6 * n, y: format.okraj + 6 * n, w: Math.round((format.sirka - 2 * format.okraj) / 2), h: 30 },
    schema.nodes.odstavec.create({ styl: 'normalni' }),
  );
  const konec = view.state.doc.content.size;
  const tr = closeHistory(view.state.tr).insert(konec, ramecek);
  tr.setSelection(TextSelection.create(tr.doc, konec + 2));
  view.dispatch(tr.scrollIntoView());
  view.focus();
}
