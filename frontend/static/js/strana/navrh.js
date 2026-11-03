// Editor navržené strany - pokusná stránka (krok 1 bodu 11 v TODO).
//
// Zatím nic neukládá do zpěvníku: rozpracovaná strana se drží jen v tomto prohlížeči
// (localStorage), aby se po obnovení stránky neztratila. Zdroj strany má od začátku
// tvar, který se později uloží na server: { verze, nastaveni, obsah }.

import {
  EditorState, EditorView, NodeSelection, TextSelection, keymap, baseKeymap, history,
  undo, redo, undoDepth, redoDepth, toggleMark, setBlockType, chainCommands, Slice, Fragment,
  closeHistory,
} from 'prosemirror';
import { schema, VERZE_SCHEMATU, prazdnyDokument, slokyZTextu, textZUzlu } from './schema.js';
import { AkordView, OvladacAkordu, pluginAkordu, akordyPoMazani } from './akord-uzel.js';
import { prepocitej } from './kolize.js';
import { transponuj } from './akordy.js';

const KLIC_ZALOHY = 'navrh-strany-pokus';

const FORMATY = {
  A5: { sirka: 148, vyska: 210, okraj: 12 },
  A4: { sirka: 210, vyska: 297, okraj: 18 },
};

const PISMA = ['Noto Sans', 'Noto Serif', 'Roboto Condensed', 'Caveat'];

const VYCHOZI = {
  format: 'A5',
  pismo: 'Noto Sans', velikost: 11, radkovani: 1.25,
  pismoNadpisu: 'Noto Serif', velikostNadpisu: 17,
  pismoAkordu: 'Roboto Condensed', velikostAkordu: 10, barvaAkordu: '#b00020', tucneAkordy: true,
  odsazeniRefrenu: 6,
  automatickaMezera: true,
  notace: 'cz',
};

const MM_NA_PX = 96 / 25.4;

// --- stav -----------------------------------------------------------------------------

let nastaveni = { ...VYCHOZI };
let meritko = 1;
let meritkoRucne = false;
let rezimAkordu = false;

const el = (id) => document.getElementById(id);
const koren = el('navrh');
const obal = el('stranka-obal');
const stranka = el('stranka');
const hranice = el('stranka-hranice');

function nactiZalohu() {
  try {
    const data = JSON.parse(localStorage.getItem(KLIC_ZALOHY) || 'null');
    if (data && data.verze === VERZE_SCHEMATU && data.obsah) {
      return { nastaveni: { ...VYCHOZI, ...data.nastaveni }, doc: schema.nodeFromJSON(data.obsah) };
    }
  } catch (_) { /* poškozená nebo nedostupná záloha - začne se znovu */ }
  return null;
}

let casovacZalohy = null;
function zalohuj() {
  clearTimeout(casovacZalohy);
  casovacZalohy = setTimeout(() => {
    try {
      localStorage.setItem(KLIC_ZALOHY, JSON.stringify(zdrojStrany()));
    } catch (_) { /* soukromé okno apod. - záloha není nutná */ }
  }, 400);
}

// Zdroj strany v tom tvaru, v jakém se později uloží k obrázku.
function zdrojStrany() {
  return { verze: VERZE_SCHEMATU, nastaveni, obsah: view.state.doc.toJSON() };
}
window.zdrojStrany = () => zdrojStrany();

// --- editor ---------------------------------------------------------------------------

const ovladac = new OvladacAkordu(obal, {
  meritko: () => meritko,
  rezimAkordu: () => rezimAkordu,
  priZmene: () => naplanujPrepocet(),
});

// Enter v prázdném řádku sloku rozdělí (prázdný řádek zmizí) - jako u seznamů v textových
// editorech. V nadpisu a autorovi pokračuje novou slokou.
function enter(state, dispatch) {
  const { $from, empty } = state.selection;
  if (!empty) return false;
  const rodic = $from.parent;
  if (rodic.type === schema.nodes.radek && rodic.content.size === 0) {
    const sloka = $from.node(-1);
    if (sloka.childCount < 2) return false;
    if (dispatch) {
      const zacatekRadku = $from.before();
      let tr = state.tr.delete(zacatekRadku, $from.after());
      const index = $from.index(-1);
      if (index > 0 && index < sloka.childCount - 1) {
        // Uprostřed: rozdělit sloku na dvě.
        tr = tr.split(zacatekRadku, 1);
        tr.setSelection(TextSelection.create(tr.doc, zacatekRadku + 3));
      } else if (index === sloka.childCount - 1) {
        // Na konci: nová sloka za touto.
        const za = tr.mapping.map($from.after(-1));
        tr = tr.insert(za, schema.nodes.sloka.create({ druh: 'sloka' }, schema.nodes.radek.create()));
        tr.setSelection(TextSelection.create(tr.doc, za + 2));
      } else {
        return false;
      }
      dispatch(tr.scrollIntoView());
    }
    return true;
  }
  if (rodic.type === schema.nodes.nadpis || rodic.type === schema.nodes.autor) {
    if ($from.parentOffset < rodic.content.size) return false;
    if (dispatch) {
      const za = $from.after();
      const dalsi = state.doc.resolve(za).nodeAfter;
      const novy = rodic.type === schema.nodes.nadpis && !(dalsi && dalsi.type === schema.nodes.autor)
        ? schema.nodes.autor.create()
        : schema.nodes.sloka.create(null, schema.nodes.radek.create());
      const tr = state.tr.insert(za, novy);
      tr.setSelection(TextSelection.create(tr.doc, za + (novy.type === schema.nodes.sloka ? 2 : 1)));
      dispatch(tr.scrollIntoView());
    }
    return true;
  }
  return false;
}

// Enter uvnitř řádku: nový řádek téže sloky (splitBlock z baseKeymap to dělá sám).

const klavesy = keymap({
  'Mod-z': undo, 'Mod-y': redo, 'Mod-Shift-z': redo,
  'Mod-b': toggleMark(schema.marks.tucne), 'Mod-i': toggleMark(schema.marks.kurziva),
  Enter: chainCommands(enter, baseKeymap.Enter),
  Escape: () => { if (rezimAkordu) { prepniRezimAkordu(false); return true; } return false; },
});

let zacinaNovouSlokou = false;

// Vkládání textu: prázdné řádky = sloky, [Ami] = ukotvený akord. Uvnitř řádku se
// jednořádkový text vloží normálně.
function vlozenyText(text, $kontext) {
  if (!/\n/.test(text) && $kontext.parent.isTextblock) {
    const sloky = slokyZTextu(text);
    if (sloky.length === 1 && sloky[0].childCount === 1) {
      return new Slice(sloky[0].firstChild.content, 0, 0);
    }
  }
  const sloky = slokyZTextu(text);
  if (!sloky.length) return Slice.empty;
  // První řádek se připojí k řádku s kurzorem. Text, který začíná prázdným řádkem,
  // má ale začít novou slokou - to zařídí vlozSlokySamostatne (otevřenost vráceného
  // kusu ProseMirror stejně přepíše na největší možnou).
  zacinaNovouSlokou = /^[ \t]*\r?\n[ \t]*\r?\n/.test(text);
  return new Slice(Fragment.from(sloky), 2, 2);
}

// Vložené sloky, které mají začít novou slokou (text začínal prázdným řádkem): sloka
// s kurzorem se rozdělí a vložené sloky přijdou mezi její části (na začátku a na konci
// sloky před ni / za ni).
function vlozSlokySamostatne(view, event, slice) {
  const prvni = slice.content.firstChild;
  const novou = zacinaNovouSlokou;
  zacinaNovouSlokou = false;
  if (!novou || !prvni || prvni.type !== schema.nodes.sloka) return false;
  let tr = view.state.tr.deleteSelection();
  const $p = tr.selection.$from;
  if ($p.parent.type !== schema.nodes.radek) return false;
  const vSloce = $p.index(-1), radku = $p.node(-1).childCount;
  let kam;
  if (vSloce === 0 && $p.parentOffset === 0) kam = $p.before(-1);
  else if (vSloce === radku - 1 && $p.parentOffset === $p.parent.content.size) kam = $p.after(-1);
  else { tr = tr.split($p.pos, 2); kam = $p.pos + 2; }
  tr = tr.insert(kam, slice.content);
  tr.setSelection(TextSelection.near(tr.doc.resolve(kam + slice.content.size)));
  view.dispatch(tr.scrollIntoView());
  return true;
}

const zaloha = nactiZalohu();
if (zaloha) nastaveni = zaloha.nastaveni;

const view = new EditorView(stranka.querySelector('.stranka-mount'), {
  state: EditorState.create({
    doc: zaloha ? zaloha.doc : prazdnyDokument(),
    plugins: [pluginAkordu(ovladac), history(), klavesy, keymap(baseKeymap)],
  }),
  attributes: { class: 'stranka-obsah', spellcheck: 'false' },
  nodeViews: { akord: (n, v, g) => new AkordView(n, v, g) },
  clipboardTextSerializer: (slice) => textZUzlu(slice.content),
  clipboardTextParser: vlozenyText,
  handlePaste: vlozSlokySamostatne,
  dispatchTransaction(tr) {
    const blik = akordyPoMazani(tr);
    view.updateState(view.state.apply(tr));
    if (tr.docChanged) zalohuj();
    if (blik.length) ovladac.blikni(blik);
    aktualizujListu();
  },
});
ovladac.pripoj(view);

// --- přepočet kolizí a přetečení ------------------------------------------------------

let naplanovano = false;
function naplanujPrepocet() {
  if (naplanovano) return;
  naplanovano = true;
  requestAnimationFrame(() => {
    naplanovano = false;
    const vysledek = prepocitej(view.dom, { automatickaMezera: nastaveni.automatickaMezera, meritko });
    hranice.classList.toggle('pretece', vysledek.pretece);
    ukazStav(vysledek);
    ovladac.prekresliSpojnici();
  });
}

function ukazStav({ pretece, siroke, kolize }) {
  const zpravy = [];
  if (pretece) zpravy.push('Obsah se nevejde na stranu.');
  if (siroke) zpravy.push(`Řádky širší než strana: ${siroke}.`);
  if (kolize) zpravy.push(`Akordy přes sebe: ${kolize}.`);
  const stav = el('navrh-stav');
  stav.textContent = '';
  if (zpravy.length) {
    const s = document.createElement('span');
    s.className = 'varovani';
    s.textContent = zpravy.join(' ');
    stav.appendChild(s);
  } else {
    stav.textContent = 'Rozpracovaná strana zůstává jen v tomto prohlížeči, do zpěvníku se zatím neukládá.';
  }
}

// --- šablona (nastavení strany) -------------------------------------------------------

function pouzijNastaveni() {
  const f = FORMATY[nastaveni.format] || FORMATY.A5;
  stranka.classList.remove('format-A4', 'format-A5');
  stranka.classList.add('format-' + nastaveni.format);
  const s = stranka.style;
  s.setProperty('--okraj', f.okraj + 'mm');
  s.setProperty('--pismo', `'${nastaveni.pismo}'`);
  s.setProperty('--velikost', nastaveni.velikost + 'pt');
  s.setProperty('--radkovani', nastaveni.radkovani);
  s.setProperty('--pismo-nadpisu', `'${nastaveni.pismoNadpisu}'`);
  s.setProperty('--velikost-nadpisu', nastaveni.velikostNadpisu + 'pt');
  s.setProperty('--pismo-akordu', `'${nastaveni.pismoAkordu}'`);
  s.setProperty('--velikost-akordu', nastaveni.velikostAkordu + 'pt');
  s.setProperty('--barva-akordu', nastaveni.barvaAkordu);
  s.setProperty('--vaha-akordu', nastaveni.tucneAkordy ? 700 : 400);
  s.setProperty('--odsazeni-refrenu', nastaveni.odsazeniRefrenu + 'mm');
  nastavMeritko();
  // Písmo se může dotahovat - měřit až po něm.
  document.fonts.ready.then(naplanujPrepocet);
  naplanujPrepocet();
}

function rozmerStrany() {
  const f = FORMATY[nastaveni.format] || FORMATY.A5;
  return { sirka: f.sirka * MM_NA_PX, vyska: f.vyska * MM_NA_PX };
}

function nastavMeritko(nove) {
  const { sirka, vyska } = rozmerStrany();
  if (nove != null) { meritko = nove; meritkoRucne = true; }
  else if (!meritkoRucne) {
    const plocha = el('navrh-plocha');
    const volno = plocha.clientWidth - 32;
    meritko = Math.max(0.5, Math.min(2.5, volno / sirka));
  }
  meritko = Math.round(meritko * 100) / 100;
  stranka.style.transform = `scale(${meritko})`;
  obal.style.width = sirka * meritko + 'px';
  obal.style.height = vyska * meritko + 'px';
  el('meritko-text').textContent = Math.round(meritko * 100) + ' %';
  naplanujPrepocet();
}

// Ovládací prvky panelu: data-nastaveni="klic" a typ podle prvku.
function napojPanel() {
  for (const prvek of koren.querySelectorAll('[data-nastaveni]')) {
    const klic = prvek.dataset.nastaveni;
    if (prvek.tagName === 'SELECT' && /^pismo/.test(klic) && !prvek.options.length) {
      for (const p of PISMA) prvek.add(new Option(p, p));
    }
    const nacti = () => {
      if (prvek.type === 'checkbox') prvek.checked = !!nastaveni[klic];
      else prvek.value = nastaveni[klic];
    };
    nacti();
    prvek.addEventListener(prvek.type === 'checkbox' || prvek.tagName === 'SELECT' ? 'change' : 'input', () => {
      let hodnota = prvek.type === 'checkbox' ? prvek.checked : prvek.value;
      if (prvek.type === 'number') {
        hodnota = parseFloat(String(hodnota).replace(',', '.'));
        if (!Number.isFinite(hodnota)) return;
      }
      nastaveni = { ...nastaveni, [klic]: hodnota };
      if (klic === 'format') meritkoRucne = false;
      pouzijNastaveni();
      zalohuj();
    });
  }
}

// --- lišta ----------------------------------------------------------------------------

// Řádek sloky jako samostatný blok jiného typu: sloka se kolem něj rozdělí.
function vyjmiRadek(state, typ) {
  const { $from } = state.selection;
  if ($from.parent.type !== schema.nodes.radek) return null;
  const sloka = $from.node(-1);
  const index = $from.index(-1);
  const pred = [], za = [];
  sloka.forEach((r, _, i) => { if (i < index) pred.push(r); else if (i > index) za.push(r); });
  const nove = [];
  if (pred.length) nove.push(sloka.type.create(sloka.attrs, pred));
  nove.push(typ.create(null, $from.parent.content));
  if (za.length) nove.push(sloka.type.create(sloka.attrs, za));
  const zacatek = $from.before(-1);
  const tr = state.tr.replaceWith(zacatek, $from.after(-1), nove);
  const novyZacatek = zacatek + (pred.length ? nove[0].nodeSize : 0);
  tr.setSelection(TextSelection.create(tr.doc, novyZacatek + 1 + $from.parentOffset));
  return tr;
}

function nastavBlok(druh) {
  return (state, dispatch) => {
    const { $from, $to } = state.selection;
    if (druh === 'nadpis' || druh === 'autor' || druh === 'text_blok') {
      const tr = vyjmiRadek(state, schema.nodes[druh]);
      if (tr) { if (dispatch) dispatch(tr); return true; }
      return setBlockType(schema.nodes[druh])(state, dispatch);
    }
    // Sloka / refrén / bez značky: když je kurzor ve sloce, změní se její druh; jinak se
    // z bloku stane sloka.
    let tr = state.tr;
    let zmeneno = false;
    state.doc.nodesBetween($from.pos, $to.pos, (n, pos) => {
      if (n.type === schema.nodes.sloka) {
        tr = tr.setNodeMarkup(pos, null, { druh });
        zmeneno = true;
        return false;
      }
      if (n.isTextblock && n.type !== schema.nodes.radek) {
        const sloka = schema.nodes.sloka.create({ druh }, schema.nodes.radek.create(null, n.content));
        tr = tr.replaceWith(tr.mapping.map(pos), tr.mapping.map(pos + n.nodeSize), sloka);
        zmeneno = true;
        return false;
      }
      return true;
    });
    if (!zmeneno) return false;
    if (dispatch) dispatch(tr);
    return true;
  };
}

function aktivniDruh(state) {
  const { $from } = state.selection;
  for (let d = $from.depth; d > 0; d--) {
    const n = $from.node(d);
    if (n.type === schema.nodes.sloka) return n.attrs.druh;
    if (n.type !== schema.nodes.radek) return n.type.name;
  }
  if (state.selection instanceof NodeSelection) return null;
  return null;
}

function transponujVse(pultony) {
  const { state } = view;
  let tr = closeHistory(state.tr);
  state.doc.descendants((n, pos) => {
    if (n.type === schema.nodes.akord) {
      const novy = transponuj(n.attrs.n, pultony, nastaveni.notace);
      if (novy !== n.attrs.n) tr = tr.setNodeMarkup(pos, null, { ...n.attrs, n: novy });
    }
  });
  if (tr.docChanged) view.dispatch(tr);
  view.focus();
}

function prepniRezimAkordu(zapnout = !rezimAkordu) {
  rezimAkordu = zapnout;
  koren.classList.toggle('rezim-akordu', rezimAkordu);
  aktualizujListu();
}

const PRIKAZY = {
  nadpis: () => nastavBlok('nadpis'),
  autor: () => nastavBlok('autor'),
  sloka: () => nastavBlok('sloka'),
  refren: () => nastavBlok('refren'),
  bez: () => nastavBlok('bez'),
  text: () => nastavBlok('text_blok'),
  tucne: () => toggleMark(schema.marks.tucne),
  kurziva: () => toggleMark(schema.marks.kurziva),
};

function napojListu() {
  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    // mousedown: tlačítko nesmí vzít editoru fokus a výběr.
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      const p = b.dataset.prikaz;
      if (PRIKAZY[p]) { PRIKAZY[p]()(view.state, view.dispatch); view.focus(); }
      else if (p === 'akordy') prepniRezimAkordu();
      else if (p === 'zpet') { undo(view.state, view.dispatch); view.focus(); }
      else if (p === 'znovu') { redo(view.state, view.dispatch); view.focus(); }
      else if (p === 'transp-dolu') transponujVse(-1);
      else if (p === 'transp-nahoru') transponujVse(1);
      else if (p === 'mensi') nastavMeritko(Math.max(0.4, meritko - 0.1));
      else if (p === 'vetsi') nastavMeritko(Math.min(3, meritko + 0.1));
      else if (p === 'na-sirku') { meritkoRucne = false; nastavMeritko(); }
    });
  }
}

function aktualizujListu() {
  const { state } = view;
  const druh = aktivniDruh(state);
  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    const p = b.dataset.prikaz;
    if (p === 'akordy') b.classList.toggle('aktivni', rezimAkordu);
    else if (['nadpis', 'autor', 'sloka', 'refren', 'bez'].includes(p)) b.classList.toggle('aktivni', druh === p);
    else if (p === 'text') b.classList.toggle('aktivni', druh === 'text_blok');
    else if (p === 'zpet') b.disabled = undoDepth(state) === 0;
    else if (p === 'znovu') b.disabled = redoDepth(state) === 0;
  }
  // Panel vybraného akordu.
  const sel = state.selection;
  const vybrany = sel instanceof NodeSelection && sel.node.type === schema.nodes.akord ? sel : null;
  const panel = el('vybrany-akord');
  panel.hidden = !vybrany;
  if (vybrany) {
    el('akord-velikost').value = vybrany.node.attrs.vel || 100;
  }
}

function upravVybranyAkord(zmena) {
  const sel = view.state.selection;
  if (!(sel instanceof NodeSelection) || sel.node.type !== schema.nodes.akord) return;
  const tr = view.state.tr.setNodeMarkup(sel.from, null, { ...sel.node.attrs, ...zmena });
  tr.setSelection(NodeSelection.create(tr.doc, sel.from));
  view.dispatch(tr);
}

function napojPanelAkordu() {
  el('akord-velikost').addEventListener('change', (e) => {
    const v = parseInt(e.target.value, 10);
    upravVybranyAkord({ vel: !v || v === 100 ? null : Math.max(40, Math.min(300, v)) });
  });
  el('akord-vychozi').addEventListener('click', () => upravVybranyAkord({ dx: 0, dy: 0 }));
  el('akord-smazat').addEventListener('click', () => {
    const sel = view.state.selection;
    if (sel instanceof NodeSelection) view.dispatch(view.state.tr.deleteSelection());
    view.focus();
  });
  el('zacit-znovu').addEventListener('click', async () => {
    const ok = await window.showConfirm('Rozpracovaná strana se smaže a začne se s prázdnou.',
      'Smazat stranu', { nebezpecne: true });
    if (!ok) return;
    nastaveni = { ...VYCHOZI };
    view.updateState(EditorState.create({ doc: prazdnyDokument(), plugins: view.state.plugins }));
    for (const prvek of koren.querySelectorAll('[data-nastaveni]')) {
      if (prvek.type === 'checkbox') prvek.checked = !!nastaveni[prvek.dataset.nastaveni];
      else prvek.value = nastaveni[prvek.dataset.nastaveni];
    }
    pouzijNastaveni();
    zalohuj();
    aktualizujListu();
  });
}

// --- start ----------------------------------------------------------------------------

napojPanel();
napojListu();
napojPanelAkordu();
pouzijNastaveni();
aktualizujListu();
window.addEventListener('resize', () => { if (!meritkoRucne) nastavMeritko(); });
view.focus();

// Pro testy a ladění.
window.navrhStrany = {
  view, schema, TextSelection, nastaveni: () => nastaveni, meritko: () => meritko, prepniRezimAkordu,
};
