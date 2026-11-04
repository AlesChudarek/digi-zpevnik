// Číslování slok a nabídka zopakovat refrén.
//
// Sloka a refrén jsou jen styly odstavců, ne struktura. Sloka je řada neprázdných
// odstavců stylu Sloka; prázdný odstavec nebo jiný styl ji ukončí. Číslo (a „R:“
// u refrénu) se kreslí jako dekorace na prvním odstavci, takže není součástí textu
// a nedá se omylem smazat. Odstavec může mít vlastní značku (atribut `znacka`), která
// číslo přepíše; číselná značka („5.“) posune i číslování dalších slok. Název písně
// číslování začíná znovu.
//
// Refrén: na prázdném odstavci stylu Refrén, který začíná nový refrén, se šedě nabídne
// kopie předchozího refrénu i s akordy; Tab ji vloží.

import { Plugin, PluginKey, Decoration, DecorationSet, TextSelection } from 'prosemirror';
import { schema } from './schema.js';

export const klicSlok = new PluginKey('sloky');

// role(styl) -> role stylu odstavce ('sloka', 'refren', 'nadpis'…); nastavuje pluginSlok.
let role = (styl) => styl;

function neprazdny(p, r) {
  return p && role(p.attrs.styl) === r && p.content.size > 0;
}

function spocitej(state) {
  const dekorace = [];
  const refreny = [];          // [{ od, odstavce: [...] }] v pořadí dokumentu
  let cislo = 0;
  state.doc.forEach((ramecek, offR) => {
    let predchozi = null;
    let rozepsanyRefren = null;
    ramecek.forEach((p, offP) => {
      const pos = offR + 1 + offP;
      if (role(p.attrs.styl) === 'nadpis') cislo = 0;
      if (neprazdny(p, 'refren')) {
        if (!neprazdny(predchozi, 'refren')) {
          rozepsanyRefren = { od: pos, odstavce: [] };
          refreny.push(rozepsanyRefren);
          dekorace.push(Decoration.node(pos, pos + p.nodeSize,
            { class: 'ps-zacatek', 'data-znacka': p.attrs.znacka ?? 'R:' }));
        }
        rozepsanyRefren.odstavce.push(p);
      } else {
        rozepsanyRefren = null;
      }
      if (neprazdny(p, 'sloka') && !neprazdny(predchozi, 'sloka')) {
        cislo += 1;
        let znacka = `${cislo}.`;
        if (p.attrs.znacka != null) {
          znacka = p.attrs.znacka;
          const n = parseInt(znacka, 10);
          if (Number.isFinite(n)) cislo = n;
        }
        dekorace.push(Decoration.node(pos, pos + p.nodeSize, { class: 'ps-zacatek', 'data-znacka': znacka }));
      }
      predchozi = p;
    });
  });

  // Nabídka refrénu: kurzor v prázdném odstavci Refrén, který by začínal nový refrén.
  let nabidka = null;
  const sel = state.selection;
  if (sel.empty) {
    const $p = sel.$from;
    const p = $p.parent;
    if (p.type === schema.nodes.odstavec && role(p.attrs.styl) === 'refren' && p.content.size === 0) {
      const index = $p.index(-1);
      const pred = index > 0 ? $p.node(-1).child(index - 1) : null;
      if (!neprazdny(pred, 'refren')) {
        const posP = $p.before();
        const vzor = refreny.filter((r) => r.od < posP).pop();
        if (vzor) {
          nabidka = { pos: posP, odstavce: vzor.odstavce };
          const ukazka = document.createElement('span');
          ukazka.className = 'ps-nabidka';
          ukazka.textContent = vzor.odstavce[0].textContent;
          const klavesa = document.createElement('span');
          klavesa.className = 'ps-nabidka-klavesa';
          klavesa.textContent = vzor.odstavce.length > 1 ? `… Tab vloží refrén (${vzor.odstavce.length} ř.)` : 'Tab vloží refrén';
          ukazka.appendChild(klavesa);
          dekorace.push(Decoration.widget($p.pos, ukazka, { side: 1, key: 'nabidka' }));
        }
      }
    }
  }
  return { dekorace: DecorationSet.create(state.doc, dekorace), nabidka };
}

export function pluginSlok(volby = {}) {
  if (volby.role) role = volby.role;
  return new Plugin({
    key: klicSlok,
    state: {
      init: (_, state) => spocitej(state),
      // Meta „prepocitat“: změnil se styl (jeho role) mimo dokument.
      apply: (tr, stary, _, novy) => (tr.docChanged || tr.selectionSet || tr.getMeta(klicSlok) ? spocitej(novy) : stary),
    },
    props: {
      decorations: (state) => klicSlok.getState(state).dekorace,
      handleKeyDown(view, e) {
        if (e.key !== 'Tab' || e.shiftKey) return false;
        const { nabidka } = klicSlok.getState(view.state);
        if (!nabidka) return false;
        const prazdny = view.state.doc.nodeAt(nabidka.pos);
        const kopie = nabidka.odstavce.map((p) => p.copy(p.content));
        const tr = view.state.tr.replaceWith(nabidka.pos, nabidka.pos + prazdny.nodeSize, kopie);
        const konec = nabidka.pos + kopie.reduce((s, p) => s + p.nodeSize, 0);
        tr.setSelection(TextSelection.near(tr.doc.resolve(konec - 1), -1));
        view.dispatch(tr.scrollIntoView());
        return true;
      },
    },
  });
}
