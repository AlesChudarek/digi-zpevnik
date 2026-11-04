// Akord nad textem: vykreslení, výběr, tažení a pole pro zápis.
//
// Kotva je prázdný inline <span> nulové šířky těsně před znakem. Akord v ní visí
// absolutně, takže neovlivní tok řádku. Výchozí poloha je ze šablony (CSS proměnné
// --akord-x/--akord-y na straně), vlastní posun dx/dy je v em písma textu.
//
// Tažení mění jen styl a teprve při puštění pošle jednu transakci - celý přesun jde
// vrátit jedním „Zpět“. Puštěný akord se přichytí ke znaku, nad kterým leží (kotva se
// přesune), a zbytkový posun si nechá, takže zůstane přesně tam, kam ho autor dal.
// S klávesou Alt se kotva nemění, akord se jen posune.

import {
  Plugin, PluginKey, NodeSelection, TextSelection, Decoration, DecorationSet, closeHistory,
} from 'prosemirror';
import { schema } from './schema.js';

const instance = new WeakMap();          // DOM kotvy -> AkordView

export const klicAkordu = new PluginKey('akordy');

export class AkordView {
  constructor(node, view, getPos) {
    this.node = node;
    this.view = view;
    this.getPos = getPos;
    this.tazeni = false;
    this.dom = document.createElement('span');
    this.dom.className = 'akord-kotva';
    this.dom.contentEditable = 'false';
    // Vzpěra: prázdný inline prvek s vlastní výškou řádku zarovnaný ke spodku textu.
    // Zvětší jen ten vizuální řádek, ve kterém akord je (i zalomený), a nevytváří místo
    // pro zalomení - akord se od svého znaku nikdy neoddělí.
    this.vzpera = document.createElement('span');
    this.vzpera.className = 'akord-vzpera';
    this.posun = document.createElement('span');
    this.posun.className = 'akord-posun';
    this.akord = document.createElement('span');
    this.akord.className = 'akord';
    this.posun.appendChild(this.akord);
    this.dom.append(this.vzpera, this.posun);
    instance.set(this.dom, this);
    this.vykresli();
  }

  vykresli() {
    const { n, dx, dy, vel } = this.node.attrs;
    // Název jen jako atribut, kreslí ho CSS (::before). Textový uzel s „#“ vedle kurzoru
    // zasekl WebKit (Safari) v nekonečné smyčce; navíc tak text akordu nepatří do výběru
    // a kopírování textu písně.
    this.akord.dataset.n = n || '\u00a0';
    this.dom.dataset.akord = n;
    this.dom.classList.toggle('prazdny', !n);
    // Ručně posunutý akord text nikdy neodsouvá (viz kolize.js).
    this.dom.classList.toggle('rucne', !!(dx || dy));
    if (this.tazeni) return;            // během tažení drží polohu ukazatel
    this.posun.style.setProperty('--dx', dx);
    this.posun.style.setProperty('--dy', dy);
    if (vel) this.akord.style.setProperty('--vel', vel / 100);
    else this.akord.style.removeProperty('--vel');
  }

  update(node) {
    if (node.type !== this.node.type) return false;
    this.node = node;
    this.vykresli();
    return true;
  }

  selectNode() { this.dom.classList.add('vybrany'); }
  deselectNode() { this.dom.classList.remove('vybrany'); }
  // Myš na akordu obsluhuje ovladač níž, ProseMirror by z kliku udělal výběr textu.
  stopEvent(e) { return /^(mouse|pointer|click|dblclick|drag)/.test(e.type); }
  ignoreMutation() { return true; }
}

// --- pomocné funkce nad dokumentem -----------------------------------------------------

function jeAkord(n) { return n && n.type === schema.nodes.akord; }

// Pozice za řadou akordů začínající na `pos` (akordy může stát víc na jednom znaku).
function zaAkordy(doc, pos) {
  let p = pos;
  while (jeAkord(doc.nodeAt(p))) p += 1;
  return p;
}

// Pozice před řadou akordů končící na `pos`.
function predAkordy(doc, pos) {
  let p = pos;
  while (p > 0 && jeAkord(doc.resolve(p).nodeBefore)) p -= 1;
  return p;
}

// Délka znaku (1, nebo 2 u znaků mimo základní rovinu - emoji) před/za pozicí.
function delkaZnaku(doc, pos, smer) {
  const text = smer < 0 ? doc.textBetween(Math.max(0, pos - 2), pos) : doc.textBetween(pos, pos + 2);
  if (text.length < 2) return 1;
  const k = smer < 0 ? text.charCodeAt(1) : text.charCodeAt(0);
  return (smer < 0 ? (k >= 0xdc00 && k <= 0xdfff) : (k >= 0xd800 && k <= 0xdbff)) ? 2 : 1;
}

// Kotevní znak akordu na pozici `pos`: [od, do) nebo null (akord na konci řádku).
export function kotevniZnak(doc, pos) {
  const za = zaAkordy(doc, pos);
  const $za = doc.resolve(za);
  const dalsi = $za.nodeAfter;
  if (dalsi && dalsi.isText) return [za, za + 1];
  return null;
}

// Znak pod bodem obrazovky -> pozice těsně před ním (tam patří akord), nebo null.
// posAtCoords vrací nejbližší hranici mezi znaky; kliknutí do pravé půlky znaku by tak
// akord dalo až za něj.
function poziceZnakuPodBodem(view, x, y) {
  const r = view.posAtCoords({ left: x, top: y });
  if (!r) return null;
  const doc = view.state.doc;
  let pos = r.pos;
  const $p = doc.resolve(pos);
  if (!$p.parent.isTextblock) return null;
  // Hranice před akordy patří ke znaku za nimi.
  pos = predAkordy(doc, pos);
  const zaK = zaAkordy(doc, pos);
  try {
    const vlevo = view.coordsAtPos(zaK, 1).left;
    if (x < vlevo - 1) {
      // Bod je vlevo od hranice: patří ke znaku před ní (přeskoč akordy).
      const $z = doc.resolve(pos);
      if ($z.nodeBefore && $z.nodeBefore.isText) pos = predAkordy(doc, pos - 1);
    }
  } catch (_) { /* pozice mimo zobrazení */ }
  return pos;
}

// --- plugin: zvýraznění kotvy, psaní a šipky u akordů -------------------------------

export function pluginAkordu(ovladac) {
  return new Plugin({
    key: klicAkordu,
    state: {
      init: () => ({ kandidat: null }),
      apply(tr, stary) {
        const meta = tr.getMeta(klicAkordu);
        if (meta && 'kandidat' in meta) return { kandidat: meta.kandidat };
        return stary;
      },
    },
    props: {
      decorations(state) {
        const pos = ovladac.aktivniPozice(state);
        const znaky = [];
        const kand = klicAkordu.getState(state).kandidat;
        if (kand) znaky.push(kand);
        else if (pos != null) {
          const z = kotevniZnak(state.doc, pos);
          if (z) znaky.push(z);
        }
        if (!znaky.length) return null;
        return DecorationSet.create(state.doc,
          znaky.map(([od, d]) => Decoration.inline(od, d, { class: 'kotva-znak' })));
      },
      handleTextInput(view, from, to, text) {
        const doc = view.state.doc;
        // „[“ vloží akord u kurzoru - zvyk z ChordPro.
        if (text === '[') {
          ovladac.vlozAkord(predAkordy(doc, from), from !== to ? [from, to] : null);
          return true;
        }
        // Kurzor stojí před akordem i za ním na stejném místě. Psaní za akordem by
        // akord přesunulo na nový text - vloží se proto před něj a akord zůstane
        // u původního písmene (jako by kurzor stál před ním).
        if (from === to && jeAkord(doc.resolve(from).nodeBefore)) {
          const pred = predAkordy(doc, from);
          const tr = view.state.tr.insertText(text, pred);
          tr.setSelection(TextSelection.create(tr.doc, pred + text.length));
          view.dispatch(tr);
          return true;
        }
        return false;
      },
      handleKeyDown(view, e) {
        const sel = view.state.selection;
        // Šipky přes akord jedním krokem: pozice před akordem a za ním jsou na obrazovce
        // totéž místo, takže by jinak jeden stisk „nic neudělal“. Nastavuje se jen stav,
        // kurzor v DOM si prohlížeč umístí sám - WebKit ho vedle needitovatelného prvku
        // přesouvá a vynucená poloha by se s ním přetahovala donekonečna.
        if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && !e.shiftKey && !e.altKey
            && !e.metaKey && !e.ctrlKey && sel.empty) {
          const doc = view.state.doc;
          const $p = sel.$from;
          const vedleAkordu = jeAkord($p.nodeBefore) || jeAkord(doc.nodeAt(sel.from));
          if (vedleAkordu) {
            let cil;
            if (e.key === 'ArrowRight') {
              const za = zaAkordy(doc, sel.from);
              if (za >= $p.end()) return false;
              cil = za + 1;
            } else {
              const pred = predAkordy(doc, sel.from);
              if (pred <= $p.start()) return false;
              cil = pred - 1;
            }
            view.dispatch(view.state.tr.setSelection(TextSelection.create(doc, cil)));
            return true;
          }
        }
        // Backspace a Delete vedle akordu: prohlížeč by kurzor v DOM posunul za akord
        // a smazal akord místo písmene. Maže se znak, který je vidět vedle kurzoru;
        // akord, jehož znak zmizel, pak patří k dalšímu.
        if ((e.key === 'Backspace' || e.key === 'Delete') && sel.empty
            && !e.altKey && !e.metaKey && !e.ctrlKey) {
          const doc = view.state.doc;
          const $p = sel.$from;
          if (jeAkord($p.nodeBefore) || jeAkord(doc.nodeAt(sel.from))) {
            let od, d;
            if (e.key === 'Backspace') {
              d = predAkordy(doc, sel.from);
              if (d <= $p.start()) return false;
              od = d - delkaZnaku(doc, d, -1);
            } else {
              od = zaAkordy(doc, sel.from);
              if (od >= $p.end()) return false;
              d = od + delkaZnaku(doc, od, 1);
            }
            const tr = view.state.tr.delete(od, d);
            tr.setSelection(TextSelection.create(tr.doc, od));
            view.dispatch(tr.scrollIntoView());
            return true;
          }
        }
        if (sel instanceof NodeSelection && jeAkord(sel.node)) {
          if (e.key === 'Enter' || e.key === 'F2') { ovladac.otevriPole(sel.from); return true; }
          // Písmeno nad vybraným akordem by akord nahradilo textem - místo toho se
          // otevře pole s tím písmenem.
          if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            ovladac.otevriPole(sel.from, e.key);
            return true;
          }
        }
        return false;
      },
    },
    view: () => ({
      update(view, predchozi) {
        if (!view.state.doc.eq(predchozi.doc)) ovladac.poZmeneDokumentu(predchozi);
        ovladac.prekresliSpojnici();
      },
    }),
  });
}

// --- ovladač: klikání, tažení, pole pro zápis, spojnice ----------------------------

export class OvladacAkordu {
  // obal: prvek, uvnitř kterého je strana (s měřítkem) a vrstva pro pole a spojnici.
  constructor(obal, { meritko, rezimAkordu, priZmene }) {
    this.obal = obal;
    this.meritko = meritko;          // () => číslo
    this.rezimAkordu = rezimAkordu;  // () => true, když je zapnutý nástroj Akordy
    this.priZmene = priZmene || (() => {});
    this.view = null;
    this.upravovany = null;          // pozice akordu s otevřeným polem
    this.novy = false;               // pole patří právě vloženému akordu
    this.tah = null;

    this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this.svg.classList.add('spojnice');
    this.cara = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    this.voditko = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    this.voditko.classList.add('voditko');
    this.svg.append(this.cara, this.voditko);
    obal.appendChild(this.svg);

    this.pole = document.createElement('input');
    this.pole.className = 'akord-pole';
    this.pole.type = 'text';
    this.pole.hidden = true;
    this.pole.spellcheck = false;
    this.pole.autocomplete = 'off';
    this.pole.setAttribute('aria-label', 'Akord');
    obal.appendChild(this.pole);
    this.pole.addEventListener('keydown', (e) => this.klavesaPole(e));
    this.pole.addEventListener('input', () => this.rozmerPole());
    this.pole.addEventListener('blur', () => { if (!this.pole.hidden) this.potvrdPole(); });
  }

  pripoj(view) {
    this.view = view;
    view.dom.addEventListener('pointerdown', (e) => this.stisk(e));
  }

  // Pozice akordu, ke kterému se má kreslit kotva a spojnice.
  aktivniPozice(state) {
    if (this.tah) return this.tah.pos;
    if (this.upravovany != null) return this.upravovany;
    const sel = state.selection;
    if (sel instanceof NodeSelection && jeAkord(sel.node)) return sel.from;
    return null;
  }

  domAkordu(pos) {
    const dom = this.view.nodeDOM(pos);
    return dom && dom.classList && dom.classList.contains('akord-kotva') ? dom : null;
  }

  // --- myš ---

  stisk(e) {
    if (e.button !== 0) return;
    // Rozepsané pole napřed zapsat: prázdný akord se tím smaže a pozice za ním by jinak
    // byly o jednu vedle.
    if (this.upravovany != null) this.potvrdPole({ vratFokus: false });
    const kotva = e.target.closest && e.target.closest('.akord-kotva');
    if (kotva && kotva.isConnected && this.view.dom.contains(kotva)) {
      const inst = instance.get(kotva);
      if (!inst) return;
      e.preventDefault();
      this.zacniTah(inst, e);
      return;
    }
    if (!this.rezimAkordu()) return;
    // Nástroj Akordy: klik na znak vloží akord (nebo vybere ten, který už na znaku je).
    const pos = poziceZnakuPodBodem(this.view, e.clientX, e.clientY);
    if (pos == null) return;
    e.preventDefault();
    this.vlozAkord(pos);
  }

  vlozAkord(pos, nahradit = null) {
    const { state } = this.view;
    if (this.upravovany != null) this.potvrdPole();
    if (jeAkord(state.doc.nodeAt(pos)) && !nahradit) {
      // Na znaku už akord je: upravit ten.
      this.view.focus();
      this.view.dispatch(state.tr.setSelection(NodeSelection.create(state.doc, pos)));
      this.otevriPole(pos);
      return;
    }
    let tr = state.tr;
    if (nahradit) { tr = tr.delete(nahradit[0], nahradit[1]); pos = tr.mapping.map(pos); }
    tr = tr.insert(pos, schema.nodes.akord.create({ n: '' }));
    tr.setSelection(NodeSelection.create(tr.doc, pos));
    this.view.focus();
    this.view.dispatch(tr);
    this.novy = true;
    this.otevriPole(pos);
  }

  zacniTah(inst, e) {
    const pos = inst.getPos();
    const view = this.view;
    view.focus();
    view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, pos)));
    const pismo = parseFloat(getComputedStyle(inst.dom).fontSize) || 16;
    this.tah = {
      inst, pos, x0: e.clientX, y0: e.clientY, posunuto: false, alt: false,
      dx0: inst.node.attrs.dx, dy0: inst.node.attrs.dy, dx: inst.node.attrs.dx,
      dy: inst.node.attrs.dy, pismo, kandidat: null,
    };
    // Pohyb se poslouchá na window: tažený prvek může ProseMirror mezitím překreslit
    // a pointer capture by se ztratil.
    this._pohyb = (ev) => this.pohyb(ev);
    this._konec = (ev) => this.konecTahu(ev);
    this._esc = (ev) => { if (ev.key === 'Escape') this.zrusTah(); };
    window.addEventListener('pointermove', this._pohyb);
    window.addEventListener('pointerup', this._konec);
    window.addEventListener('keydown', this._esc, true);
  }

  pohyb(e) {
    const t = this.tah;
    if (!t) return;
    const m = this.meritko();
    const mx = (e.clientX - t.x0) / m, my = (e.clientY - t.y0) / m;
    if (!t.posunuto && Math.hypot(mx, my) * m < 4) return;
    if (!t.posunuto) { t.posunuto = true; t.inst.tazeni = true; t.inst.dom.classList.add('tazeny'); }
    t.alt = e.altKey;
    t.dx = zaokrouhli(t.dx0 + mx / t.pismo);
    // Shift: jen vodorovně (výška zůstane).
    t.dy = e.shiftKey ? t.dy0 : zaokrouhli(t.dy0 + my / t.pismo);
    t.inst.posun.style.setProperty('--dx', t.dx);
    t.inst.posun.style.setProperty('--dy', t.dy);
    t.kandidat = t.alt ? null : this.najdiKandidata(t);
    t.uroven = e.shiftKey ? null : this.prichytVysku(t);
    const kandZnak = t.kandidat != null ? kotevniZnak(this.view.state.doc, t.kandidat) : null;
    const stary = klicAkordu.getState(this.view.state).kandidat;
    if (JSON.stringify(stary) !== JSON.stringify(kandZnak)) {
      this.view.dispatch(this.view.state.tr.setMeta(klicAkordu, { kandidat: kandZnak }));
    }
    this.prekresliSpojnici();
  }

  // Vodicí čára: když je spodek akordu blízko spodku jiného akordu ve stejném řádku
  // (nebo výchozí výšky), přichytí se na ni. Vrací svislou polohu čáry, nebo null.
  prichytVysku(t) {
    const m = this.meritko();
    const em = t.pismo * m;
    const r = t.inst.akord.getBoundingClientRect();
    // Řádek, do kterého akord patří: kandidát, jinak vlastní kotva.
    let horniKotvy;
    if (t.kandidat != null) {
      try { horniKotvy = this.view.coordsAtPos(t.kandidat, 1).top; } catch (_) { return null; }
    } else horniKotvy = t.inst.dom.getBoundingClientRect().top;
    const urovne = [];
    // Výchozí výška vlastního akordu (dy = 0), když zůstává na svém řádku.
    if (t.kandidat == null) urovne.push(r.bottom - t.dy * em);
    for (const k of this.view.dom.querySelectorAll('.akord-kotva')) {
      if (k === t.inst.dom) continue;
      if (Math.abs(k.getBoundingClientRect().top - horniKotvy) > 3) continue;
      urovne.push(k.querySelector('.akord').getBoundingClientRect().bottom);
    }
    let nejblizsi = null;
    for (const u of urovne) {
      if (Math.abs(u - r.bottom) < 6 && (nejblizsi == null || Math.abs(u - r.bottom) < Math.abs(nejblizsi - r.bottom))) nejblizsi = u;
    }
    if (nejblizsi == null) return null;
    t.dy = zaokrouhli(t.dy + (nejblizsi - r.bottom) / em);
    t.inst.posun.style.setProperty('--dy', t.dy);
    const odstavec = t.inst.dom.closest('.ps-p') || this.view.dom;
    const o = odstavec.getBoundingClientRect();
    return { y: nejblizsi, x1: o.left, x2: o.right };
  }

  // Znak, ke kterému by se akord po puštění přichytil: nejbližší hranice znaku pod
  // levým dolním rohem akordu (o kus níž, v řádku textu pod ním).
  najdiKandidata(t) {
    const r = t.inst.akord.getBoundingClientRect();
    const y = r.bottom + t.pismo * this.meritko() * 0.6;
    const res = this.view.posAtCoords({ left: r.left + 1, top: y });
    if (!res) return null;
    const doc = this.view.state.doc;
    if (!doc.resolve(res.pos).parent.isTextblock) return null;
    let pos = predAkordy(doc, res.pos);
    // Vlastní pozice akordu i hranice hned za ním znamenají „beze změny“.
    if (pos === t.pos || pos === t.pos + 1) return null;
    return pos;
  }

  konecTahu() {
    const t = this.tah;
    this.uklidTah();
    if (!t) return;
    const view = this.view;
    if (!t.posunuto) {          // obyčejné kliknutí = otevřít pole
      this.otevriPole(t.pos);
      return;
    }
    t.inst.tazeni = false;
    t.inst.dom.classList.remove('tazeny');
    // Styl z tažení vrátit podle uzlu: ProseMirror přesunutý akord se stejnými atributy
    // použije znovu bez update(), takže by mu tažený posun zůstal.
    t.inst.vykresli();
    // Každé tažení je samostatný krok Zpět (jinak by se slilo s tím, co bylo před ním).
    let tr = closeHistory(view.state.tr).setMeta(klicAkordu, { kandidat: null });
    const attrs = { ...t.inst.node.attrs, dx: t.dx, dy: t.dy };
    if (t.kandidat != null) {
      // Přesun kotvy: akord zůstane vizuálně tam, kam ho autor pustil, takže se
      // zbytkový posun přepočte proti novému znaku.
      const stare = t.inst.dom.getBoundingClientRect();
      let nova = null;
      try { nova = view.coordsAtPos(t.kandidat, 1); } catch (_) { /* nic */ }
      if (nova) {
        const m = this.meritko();
        attrs.dx = zaokrouhli(t.dx + (stare.left - nova.left) / m / t.pismo);
        attrs.dy = zaokrouhli(t.dy + (stare.top - nova.top) / m / t.pismo);
      }
      tr = tr.delete(t.pos, t.pos + 1);
      const cil = tr.mapping.map(t.kandidat);
      tr = tr.insert(cil, schema.nodes.akord.create(attrs));
      tr.setSelection(NodeSelection.create(tr.doc, cil));
    } else {
      tr = tr.setNodeMarkup(t.pos, null, attrs);
      tr.setSelection(NodeSelection.create(tr.doc, t.pos));
    }
    view.dispatch(tr);
  }

  zrusTah() {
    const t = this.tah;
    this.uklidTah();
    if (!t) return;
    t.inst.tazeni = false;
    t.inst.dom.classList.remove('tazeny');
    t.inst.vykresli();
    this.view.dispatch(this.view.state.tr.setMeta(klicAkordu, { kandidat: null }));
  }

  uklidTah() {
    window.removeEventListener('pointermove', this._pohyb);
    window.removeEventListener('pointerup', this._konec);
    window.removeEventListener('keydown', this._esc, true);
    this.tah = null;
  }

  // --- pole pro zápis akordu ---

  otevriPole(pos, prvniZnak = null) {
    const node = this.view.state.doc.nodeAt(pos);
    if (!jeAkord(node)) return;
    this.upravovany = pos;
    this.pole.value = prvniZnak != null ? prvniZnak : node.attrs.n;
    this.pole.hidden = false;
    this.umistiPole();
    this.pole.focus();
    if (prvniZnak == null) this.pole.select();
    // Překreslit dekorace (kotva se zvýrazní i bez výběru uzlu).
    this.view.dispatch(this.view.state.tr.setMeta(klicAkordu, { kandidat: null }));
  }

  umistiPole() {
    if (this.upravovany == null) return;
    const dom = this.domAkordu(this.upravovany);
    if (!dom) return;
    const r = dom.querySelector('.akord').getBoundingClientRect();
    const o = this.obal.getBoundingClientRect();
    const styl = getComputedStyle(dom.querySelector('.akord'));
    const m = this.meritko();
    this.pole.style.left = (r.left - o.left - 4) + 'px';
    this.pole.style.top = (r.top - o.top - 3) + 'px';
    this.pole.style.font = `${styl.fontWeight} ${parseFloat(styl.fontSize) * m}px ${styl.fontFamily}`;
    this.pole.style.color = styl.color;
    this.rozmerPole();
  }

  rozmerPole() {
    this.pole.style.width = `calc(${Math.max(3, this.pole.value.length + 1)}ch + 10px)`;
  }

  klavesaPole(e) {
    if (e.key === 'Enter') { e.preventDefault(); this.potvrdPole(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); this.zrusPole(); }
    else if (e.key === 'Tab') {
      e.preventDefault();
      const pos = this.potvrdPole({ vratFokus: false });
      this.skocNaAkord(pos, e.shiftKey ? -1 : 1);
    }
  }

  // Zapíše pole do akordu. Prázdné pole akord smaže. Vrací pozici, kde akord byl.
  potvrdPole({ vratFokus = true } = {}) {
    const pos = this.upravovany;
    if (pos == null) return null;
    const hodnota = this.pole.value.trim();
    this.zavriPole();
    const { state } = this.view;
    const node = state.doc.nodeAt(pos);
    if (!jeAkord(node)) return pos;
    let tr = closeHistory(state.tr);
    if (!hodnota) {
      tr = tr.delete(pos, pos + 1);
      tr.setSelection(TextSelection.create(tr.doc, pos));
    } else {
      if (hodnota !== node.attrs.n) tr = tr.setNodeMarkup(pos, null, { ...node.attrs, n: hodnota });
      // Kurzor před akord, ne výběr akordu: písmeno napsané do vybraného uzlu by ho
      // nahradilo.
      tr.setSelection(TextSelection.create(tr.doc, pos));
    }
    this.view.dispatch(tr);
    if (vratFokus) this.view.focus();
    return pos;
  }

  zrusPole() {
    const pos = this.upravovany;
    const novy = this.novy;
    this.zavriPole();
    const { state } = this.view;
    const node = pos != null ? state.doc.nodeAt(pos) : null;
    if (novy && jeAkord(node) && !node.attrs.n) {
      const tr = state.tr.delete(pos, pos + 1);
      tr.setSelection(TextSelection.create(tr.doc, pos));
      this.view.dispatch(tr);
    } else if (pos != null) {
      this.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, pos)));
    }
    this.view.focus();
  }

  zavriPole() {
    this.upravovany = null;
    this.novy = false;
    this.pole.hidden = true;
  }

  // Tab v poli: na další (Shift+Tab předchozí) akord v dokumentu.
  skocNaAkord(pos, smer) {
    const pozice = [];
    this.view.state.doc.descendants((n, p) => { if (jeAkord(n)) pozice.push(p); });
    if (!pozice.length) { this.view.focus(); return; }
    let i = pozice.findIndex((p) => p >= pos);
    if (i < 0) i = pozice.length;
    const cil = smer > 0
      ? pozice[pozice[i] === pos ? i + 1 : i]
      : pozice[i - 1];
    if (cil == null) { this.view.focus(); return; }
    this.view.dispatch(this.view.state.tr.setSelection(NodeSelection.create(this.view.state.doc, cil)));
    this.otevriPole(cil);
  }

  // --- po změně dokumentu ---

  poZmeneDokumentu(predchozi) {
    // Pole drží pozici - po změně dokumentu ji přepočítat by šlo, ale pole se otevírá
    // jen nad akordem a psaní do dokumentu ho zavře (blur), takže stačí kontrola.
    if (this.upravovany != null && !jeAkord(this.view.state.doc.nodeAt(this.upravovany))) {
      this.zavriPole();
    }
    this.priZmene();
  }

  // Akordy, jejichž kotevní znak zmizel (smazání), krátce bliknou - autor vidí, že
  // akord přešel na další znak.
  blikni(pozice) {
    for (const p of pozice) {
      const dom = this.domAkordu(p);
      if (!dom) continue;
      dom.classList.remove('blik');
      void dom.offsetWidth;
      dom.classList.add('blik');
      setTimeout(() => dom.classList.remove('blik'), 900);
    }
  }

  prekresliSpojnici() {
    const pos = this.view ? this.aktivniPozice(this.view.state) : null;
    const dom = pos != null ? this.domAkordu(pos) : null;
    if (!dom) { this.svg.classList.remove('viditelna'); return; }
    const o = this.obal.getBoundingClientRect();
    const a = dom.querySelector('.akord').getBoundingClientRect();
    // Kotva: levý horní roh kotevního znaku (prázdná kotva má nulovou šířku a sedí
    // přesně na jeho začátku).
    const k = dom.getBoundingClientRect();
    this.cara.setAttribute('x1', k.left - o.left);
    this.cara.setAttribute('y1', k.top - o.top);
    this.cara.setAttribute('x2', a.left - o.left);
    this.cara.setAttribute('y2', a.bottom - o.top);
    const u = this.tah && this.tah.uroven;
    this.voditko.classList.toggle('viditelne', !!u);
    if (u) {
      this.voditko.setAttribute('x1', u.x1 - o.left);
      this.voditko.setAttribute('x2', u.x2 - o.left);
      this.voditko.setAttribute('y1', u.y - o.top);
      this.voditko.setAttribute('y2', u.y - o.top);
    }
    this.svg.classList.add('viditelna');
    if (this.upravovany != null) this.umistiPole();
  }
}

// Po transakci: akordy, před kterými se mazalo (jejich znak zmizel), k bliknutí.
export function akordyPoMazani(tr) {
  const vysledek = [];
  if (!tr.docChanged) return vysledek;
  tr.steps.forEach((krok, i) => {
    const mapa = krok.getMap();
    mapa.forEach((odStare, doStare, odNove, doNove) => {
      if (doStare - odStare <= doNove - odNove) return;   // nic se neubralo
      let p = odNove;
      for (let j = i + 1; j < tr.steps.length; j++) p = tr.steps[j].getMap().map(p);
      // Akord(y) těsně před místem mazání teď patří k jinému znaku.
      let q = p;
      const doc = tr.doc;
      if (q > doc.content.size) return;
      while (q > 0 && jeAkord(doc.resolve(q).nodeBefore)) { q -= 1; vysledek.push(q); }
    });
  });
  return vysledek;
}

function zaokrouhli(x) { return Math.round(x * 100) / 100; }
