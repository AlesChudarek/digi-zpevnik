// Prvky na straně (jako v Canvě): textové rámečky a obrázky - přesun, změna velikosti,
// smazání.
//
// Prvek je blokový uzel s polohou v mm. Lišta s úchytem nad horním okrajem a roh vpravo
// dole nejsou součástí textu (contenteditable=false) a obsluhuje je tenhle pohled; obsah
// textového rámečku spravuje ProseMirror. Obrázek jde táhnout za celou plochu a jeho roh
// drží poměr stran (se Shiftem volně). Během tažení se mění jen styl a teprve při
// puštění jde jedna transakce - celý přesun vrátí jedno „Zpět“.

import { closeHistory, TextSelection, NodeSelection } from 'prosemirror';
import { bodyTvaru } from './schema.js';

const MM_NA_PX = 96 / 25.4;
const MIN_MM = 8;
const KROK_MM = 0.5;

const zaokrouhliMm = (x) => Math.round(x / KROK_MM) * KROK_MM;

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

    this.roh = document.createElement('div');
    this.roh.className = 'ps-prvek-roh';
    this.roh.contentEditable = 'false';
    this.roh.title = 'Změnit velikost';

    if (this.jeObrazek) {
      this.img = document.createElement('img');
      this.img.alt = '';
      this.img.draggable = false;
      this.dom.contentEditable = 'false';
      this.dom.append(this.lista, this.img, this.roh);
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
      this.dom.append(this.svg, this.roh);
      this.polygon.addEventListener('pointerdown', (e) => this.zacni(e, 'presun'));
    } else {
      this.contentDOM = document.createElement('div');
      this.contentDOM.className = 'ps-ramecek-obsah';
      this.dom.append(this.lista, this.contentDOM, this.roh);
    }
    this.nastav();
    this.oznacPresah();

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
    if (this.jeObrazek) {
      if (this.img.getAttribute('src') !== a.src) this.img.src = a.src;
      this.dom.classList.toggle('ps-vrstva-za', a.vrstva === 'za');
      this.dom.classList.toggle('ps-vrstva-pred', a.vrstva !== 'za');
    }
    if (this.jeTvar) {
      this.polygon.setAttribute('points', bodyTvaru(a.body));
      this.polygon.setAttribute('fill', a.barva);
    }
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
    // Náplast se ladí jemněji (0,1 mm) - zakrývá i malé věci.
    const zaokrouhli = this.jeTvar ? (x) => Math.round(x * 10) / 10 : zaokrouhliMm;
    if (t.druh === 'presun') {
      // Obrázek smí přesahovat přes okraj strany (ořízne se), rámeček ne.
      const presah = this.jeAtom ? Math.min(p.w, p.h) * 0.8 : 0;
      t.nove.x = Math.min(Math.max(zaokrouhli(p.x + dx), -presah), sirka - p.w + presah);
      t.nove.y = Math.min(Math.max(zaokrouhli(p.y + dy), -presah), vyska - p.h + presah);
    } else {
      let w = Math.max(zaokrouhli(p.w + dx), MIN_MM);
      let h = Math.max(zaokrouhli(p.h + dy), MIN_MM);
      if (this.jeTvar) {
        // Náplast nemá nejmenší velikost - i malá ploška je potřeba.
        w = Math.max(zaokrouhli(p.w + dx), 0.2);
        h = Math.max(zaokrouhli(p.h + dy), 0.2);
      } else if (this.jeObrazek && !e.shiftKey) {
        // Poměr stran: rozhoduje větší z obou změn.
        const pomer = p.w / p.h;
        if (Math.abs(dx) * p.h >= Math.abs(dy) * p.w) h = zaokrouhli(w / pomer);
        else w = zaokrouhli(h * pomer);
      }
      t.nove.w = this.jeAtom ? w : Math.min(w, sirka - p.x);
      t.nove.h = this.jeAtom ? h : Math.min(h, vyska - p.y);
    }
    this.nastav(t.nove);
  }

  konec() {
    const t = this.tah;
    this.uklid();
    if (!t) return;
    if (!t.posunuto) {
      if (this.jeAtom) {
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
    if (!this.jeAtom) {
      let ramecku = 0;
      state.doc.forEach((n) => { if (n.type === this.node.type) ramecku += 1; });
      if (ramecku < 2) {
        window.oznam('Strana musí mít aspoň jeden textový rámeček.', { nadpis: 'Rámeček nejde smazat', druh: 'info' });
        return;
      }
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
    { x: (format.posun || 0) + format.okraj + 6 * n, y: format.okraj + 6 * n, w: Math.round((format.sirka - 2 * format.okraj) / 2), h: 30 },
    schema.nodes.odstavec.create({ styl: 'normalni' }),
  );
  const konec = view.state.doc.content.size;
  const tr = closeHistory(view.state.tr).insert(konec, ramecek);
  tr.setSelection(TextSelection.create(tr.doc, konec + 2));
  view.dispatch(tr.scrollIntoView());
  view.focus();
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
  const uzel = view.state.schema.nodes.obrazek.create({ src, x, y, w: sirka, h: vyska });
  const konec = view.state.doc.content.size;
  const tr = closeHistory(view.state.tr).insert(konec, uzel);
  tr.setSelection(NodeSelection.create(tr.doc, konec));
  view.dispatch(tr);
  view.focus();
}
