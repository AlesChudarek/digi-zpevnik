// Editor navržené strany - pokusná stránka (krok 1 bodu 11 v TODO).
//
// Strana jako v Canvě (rámečky s polohou), text v rámečku jako ve Wordu (styly odstavců
// a přímé formátování) a k tomu nástroj na akordy ukotvené ke znaku.
//
// Zatím nic neukládá do zpěvníku: rozpracovaná strana se drží jen v tomto prohlížeči
// (localStorage), aby se po obnovení stránky neztratila. Zdroj strany má od začátku
// tvar, který se později uloží na server: { verze, nastaveni, obsah }.

import {
  EditorState, EditorView, NodeSelection, TextSelection, keymap, baseKeymap, history,
  undo, redo, undoDepth, redoDepth, toggleMark, Slice, Fragment, closeHistory,
} from 'prosemirror';
import { schema, STYLY, DALSI_STYL, VERZE_SCHEMATU, prazdnyDokument, odstavceZTextu, textZUzlu } from './schema.js';
import { AkordView, OvladacAkordu, pluginAkordu, akordyPoMazani } from './akord-uzel.js';
import { RamecekView, pridejRamecek } from './ramecky.js';
import { pluginSlok } from './sloky.js';
import { prepocitej } from './kolize.js';
import { transponuj } from './akordy.js';

const N = schema.nodes;
const M = schema.marks;
const KLIC_ZALOHY = 'navrh-strany-pokus';
const MM_NA_PX = 96 / 25.4;

const FORMATY = {
  A5: { sirka: 148, vyska: 210, okraj: 12 },
  A4: { sirka: 210, vyska: 297, okraj: 18 },
};

const PISMA = ['Noto Sans', 'Noto Serif', 'Roboto Condensed', 'Caveat'];
const VELIKOSTI = [7, 8, 9, 10, 10.5, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40];

const zakladStylu = {
  pismo: 'Noto Sans', velikost: 11, tucne: false, kurziva: false, barva: '#000000',
  zarovnani: 'left', odsazeni: 0, pred: 0, po: 0, radkovani: 1.25,
};
const VYCHOZI = {
  format: 'A5',
  styly: {
    normalni: { ...zakladStylu },
    nadpis: { ...zakladStylu, pismo: 'Noto Serif', velikost: 17, tucne: true, radkovani: 1.2, po: 1 },
    autor: { ...zakladStylu, velikost: 10, kurziva: true, po: 10 },
    sloka: { ...zakladStylu, odsazeni: 7 },
    refren: { ...zakladStylu, odsazeni: 13 },
  },
  pismoAkordu: 'Roboto Condensed', velikostAkordu: 10, barvaAkordu: '#b00020', tucneAkordy: true,
  automatickaMezera: false,
  notace: 'cz',
};

// --- stav -----------------------------------------------------------------------------

let nastaveni = structuredClone(VYCHOZI);
let meritko = 1;
let meritkoRucne = false;
let rezimAkordu = false;

const el = (id) => document.getElementById(id);
const koren = el('navrh');
const obal = el('stranka-obal');
const stranka = el('stranka');
const format = () => FORMATY[nastaveni.format] || FORMATY.A5;

function sluc(vychozi, ulozene) {
  const v = { ...structuredClone(vychozi), ...ulozene };
  v.styly = {};
  for (const k of Object.keys(STYLY)) v.styly[k] = { ...vychozi.styly[k], ...(ulozene.styly || {})[k] };
  return v;
}

function nactiZalohu() {
  try {
    const data = JSON.parse(localStorage.getItem(KLIC_ZALOHY) || 'null');
    if (data && data.verze === VERZE_SCHEMATU && data.obsah) {
      return { nastaveni: sluc(VYCHOZI, data.nastaveni || {}), doc: schema.nodeFromJSON(data.obsah) };
    }
  } catch (_) { /* poškozená, stará nebo nedostupná záloha - začne se znovu */ }
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

// --- klávesy --------------------------------------------------------------------------

// Enter: nový odstavec. Na konci odstavce pokračuje „dalším stylem“ (po Názvu Autor, po
// Autorovi Sloka), uprostřed se odstavec jen rozdělí. Formátování textu pokračuje.
function enter(state, dispatch) {
  const { $from } = state.selection;
  if ($from.parent.type !== N.odstavec) return false;
  if (dispatch) {
    const znacky = state.storedMarks || ($from.parentOffset && $from.marks());
    let tr = state.tr;
    if (!state.selection.empty) tr = tr.deleteSelection();
    const $p = tr.selection.$from;
    const p = $p.parent;
    const naKonci = $p.parentOffset === p.content.size;
    const styl = naKonci ? DALSI_STYL[p.attrs.styl] || p.attrs.styl : p.attrs.styl;
    const stejny = styl === p.attrs.styl;
    tr = tr.split($p.pos, 1, [{
      type: N.odstavec,
      attrs: { styl, zarovnani: stejny ? p.attrs.zarovnani : null, odsazeni: stejny ? p.attrs.odsazeni : null, znacka: null },
    }]);
    if (znacky && stejny) tr.ensureMarks(znacky);
    dispatch(tr.scrollIntoView());
  }
  return true;
}

function zalomeni(state, dispatch) {
  if (dispatch) dispatch(state.tr.replaceSelectionWith(N.zalomeni.create()).scrollIntoView());
  return true;
}

const klavesy = keymap({
  'Mod-z': undo, 'Mod-y': redo, 'Mod-Shift-z': redo,
  'Mod-b': toggleMark(M.tucne), 'Mod-i': toggleMark(M.kurziva), 'Mod-u': toggleMark(M.podtrzeni),
  Enter: enter,
  'Shift-Enter': zalomeni,
  Escape: () => { if (rezimAkordu) { prepniRezimAkordu(false); return true; } return false; },
});

// Vkládání textu: každý řádek je odstavec, prázdný řádek dělí sloky, [Ami] je ukotvený
// akord. První řádek se připojí k odstavci s kurzorem (ProseMirror kus otevře naplno);
// text, který začíná prázdným řádkem, začne novým odstavcem.
function vlozenyText(text, $kontext) {
  const odstavce = odstavceZTextu(text);
  if (!odstavce.length) return Slice.empty;
  if (/^[ \t]*\r?\n[ \t]*\r?\n/.test(text) && $kontext.parent.content.size) {
    odstavce.unshift(N.odstavec.create({ styl: odstavce[0].attrs.styl }));
  }
  return new Slice(Fragment.from(odstavce), 1, 1);
}

// --- editor ---------------------------------------------------------------------------

const ovladac = new OvladacAkordu(obal, {
  meritko: () => meritko,
  rezimAkordu: () => rezimAkordu,
  priZmene: () => naplanujPrepocet(),
});

const zaloha = nactiZalohu();
if (zaloha) nastaveni = zaloha.nastaveni;

const view = new EditorView(stranka.querySelector('.stranka-mount'), {
  state: EditorState.create({
    doc: zaloha ? zaloha.doc : prazdnyDokument(format()),
    plugins: [pluginAkordu(ovladac), pluginSlok(), history(), klavesy, keymap(baseKeymap)],
  }),
  attributes: { class: 'stranka-obsah', spellcheck: 'false' },
  nodeViews: {
    akord: (n, v, g) => new AkordView(n, v, g),
    ramecek: (n, v, g) => new RamecekView(n, v, g, { meritko: () => meritko, rozmer: format }),
  },
  clipboardTextSerializer: (slice) => textZUzlu(slice.content),
  clipboardTextParser: vlozenyText,
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
    ukazStav(prepocitej(view.dom, { automatickaMezera: nastaveni.automatickaMezera, meritko }));
    ovladac.prekresliSpojnici();
  });
}

function ukazStav({ pretece, kolize }) {
  const zpravy = [];
  if (pretece) zpravy.push(pretece > 1 ? `Text se nevejde do rámečků (${pretece}).` : 'Text se nevejde do rámečku.');
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

// --- šablona: styly odstavců a akordy -------------------------------------------------

const stylyStrany = document.createElement('style');
document.head.appendChild(stylyStrany);

function cssStylu(k, s) {
  return `.stranka .ps-${k} {
    font-family: '${s.pismo}', sans-serif; font-size: ${s.velikost}pt; line-height: ${s.radkovani};
    font-weight: ${s.tucne ? 700 : 400}; font-style: ${s.kurziva ? 'italic' : 'normal'};
    color: ${s.barva}; text-align: ${s.zarovnani}; padding-left: ${s.odsazeni}mm;
    margin: ${s.pred}pt 0 ${s.po}pt; }`;
}

function pouzijNastaveni() {
  const f = format();
  const s = stranka.style;
  s.width = f.sirka + 'mm';
  s.height = f.vyska + 'mm';
  s.setProperty('--okraj', f.okraj + 'mm');
  s.setProperty('--pismo-akordu', `'${nastaveni.pismoAkordu}'`);
  s.setProperty('--velikost-akordu', nastaveni.velikostAkordu + 'pt');
  s.setProperty('--barva-akordu', nastaveni.barvaAkordu);
  s.setProperty('--vaha-akordu', nastaveni.tucneAkordy ? 700 : 400);
  stylyStrany.textContent = Object.entries(nastaveni.styly).map(([k, v]) => cssStylu(k, v)).join('\n');
  nastavMeritko();
  document.fonts.ready.then(naplanujPrepocet);
  naplanujPrepocet();
}

function nastavMeritko(nove) {
  const f = format();
  const sirka = f.sirka * MM_NA_PX, vyska = f.vyska * MM_NA_PX;
  if (nove != null) { meritko = nove; meritkoRucne = true; }
  else if (!meritkoRucne) {
    const volno = el('navrh-plocha').clientWidth - 32;
    meritko = Math.max(0.5, Math.min(2.5, volno / sirka));
  }
  meritko = Math.round(meritko * 100) / 100;
  stranka.style.transform = `scale(${meritko})`;
  stranka.style.setProperty('--meritko', meritko);
  obal.style.width = sirka * meritko + 'px';
  obal.style.height = vyska * meritko + 'px';
  el('meritko-text').textContent = Math.round(meritko * 100) + ' %';
  naplanujPrepocet();
}

// Změna formátu: rámečky se zvětší/zmenší ve stejném poměru (A5 a A4 mají stejný tvar).
function zmenFormat(novy) {
  const stary = format();
  nastaveni = { ...nastaveni, format: novy };
  const pomer = format().sirka / stary.sirka;
  if (pomer !== 1) {
    let tr = closeHistory(view.state.tr);
    view.state.doc.forEach((r, pos) => {
      const a = r.attrs;
      const z = (x) => Math.round(x * pomer * 2) / 2;
      tr = tr.setNodeMarkup(pos, null, { x: z(a.x), y: z(a.y), w: z(a.w), h: z(a.h) });
    });
    view.dispatch(tr);
  }
  meritkoRucne = false;
  pouzijNastaveni();
  zalohuj();
}

function hodnotaPrvku(prvek) {
  if (prvek.type === 'checkbox') return prvek.checked;
  if (prvek.type === 'number') return parseFloat(String(prvek.value).replace(',', '.'));
  return prvek.value;
}

const udalostPrvku = (prvek) => (prvek.type === 'checkbox' || prvek.tagName === 'SELECT' ? 'change' : 'input');

// Panel: data-nastaveni="klic" (formát, akordy) a data-styl-pole="klic" (upravovaný styl).
function napojPanel() {
  for (const prvek of koren.querySelectorAll('select[data-pisma]')) {
    for (const p of PISMA) prvek.add(new Option(p, p));
  }
  for (const prvek of koren.querySelectorAll('[data-nastaveni]')) {
    const klic = prvek.dataset.nastaveni;
    prvek.addEventListener(udalostPrvku(prvek), () => {
      if (klic === 'format') { zmenFormat(prvek.value); return; }
      const h = hodnotaPrvku(prvek);
      if (Number.isNaN(h)) return;
      nastaveni = { ...nastaveni, [klic]: h };
      pouzijNastaveni();
      zalohuj();
    });
  }
  const vyberStylu = el('upravovany-styl');
  for (const [k, nazev] of Object.entries(STYLY)) vyberStylu.add(new Option(nazev, k));
  vyberStylu.addEventListener('change', nactiPanel);
  for (const prvek of koren.querySelectorAll('[data-styl-pole]')) {
    const klic = prvek.dataset.stylPole;
    prvek.addEventListener(udalostPrvku(prvek), () => {
      const h = hodnotaPrvku(prvek);
      if (Number.isNaN(h)) return;
      const styl = vyberStylu.value;
      nastaveni = { ...nastaveni, styly: { ...nastaveni.styly, [styl]: { ...nastaveni.styly[styl], [klic]: h } } };
      pouzijNastaveni();
      zalohuj();
      aktualizujListu();
    });
  }
  nactiPanel();
}

function nactiPanel() {
  for (const prvek of koren.querySelectorAll('[data-nastaveni]')) {
    const h = nastaveni[prvek.dataset.nastaveni];
    if (prvek.type === 'checkbox') prvek.checked = !!h; else prvek.value = h;
  }
  const styl = nastaveni.styly[el('upravovany-styl').value];
  for (const prvek of koren.querySelectorAll('[data-styl-pole]')) {
    const h = styl[prvek.dataset.stylPole];
    if (prvek.type === 'checkbox') prvek.checked = !!h; else prvek.value = h;
  }
}

// --- příkazy lišty --------------------------------------------------------------------

// Odstavce ve výběru: [{ node, pos }].
function vybraneOdstavce(state) {
  const { from, to } = state.selection;
  const out = [];
  state.doc.nodesBetween(from, to, (n, pos) => {
    if (n.type === N.odstavec) { out.push({ node: n, pos }); return false; }
    return true;
  });
  return out;
}

function upravOdstavce(zmena) {
  const odstavce = vybraneOdstavce(view.state);
  if (!odstavce.length) return;
  let tr = closeHistory(view.state.tr);
  for (const { node, pos } of odstavce) tr = tr.setNodeMarkup(pos, null, { ...node.attrs, ...zmena(node) });
  view.dispatch(tr);
}

// Styl odstavce: ruční zarovnání, odsazení a značka se při změně stylu zahodí (jako ve
// Wordu „použít styl“).
function nastavStyl(styl) {
  upravOdstavce(() => ({ styl, zarovnani: null, odsazeni: null, znacka: null }));
  view.focus();
}

// Přímé formátování výběru (písmo, velikost, barva). Bez výběru platí pro další psaní.
function nastavZnacku(typ, attrs) {
  const { state } = view;
  const { from, to, empty } = state.selection;
  const tr = empty ? state.tr.addStoredMark(typ.create(attrs))
    : closeHistory(state.tr).addMark(from, to, typ.create(attrs));
  view.dispatch(tr);
  view.focus();
}

function vymazFormatovani() {
  const { from, to, empty } = view.state.selection;
  let tr = closeHistory(view.state.tr);
  if (!empty) for (const t of [M.tucne, M.kurziva, M.podtrzeni, M.pismo, M.velikost, M.barva]) tr = tr.removeMark(from, to, t);
  tr.setStoredMarks([]);
  for (const { node, pos } of vybraneOdstavce(view.state)) {
    tr = tr.setNodeMarkup(pos, null, { ...node.attrs, zarovnani: null, odsazeni: null });
  }
  view.dispatch(tr);
}

function odsad(o) {
  upravOdstavce((n) => {
    const aktualni = n.attrs.odsazeni ?? nastaveni.styly[n.attrs.styl].odsazeni;
    return { odsazeni: Math.max(0, Math.round((aktualni + o) * 2) / 2) };
  });
}

function transponujVse(pultony) {
  const { state } = view;
  let tr = closeHistory(state.tr);
  state.doc.descendants((n, pos) => {
    if (n.type === N.akord) {
      const novy = transponuj(n.attrs.n, pultony, nastaveni.notace);
      if (novy !== n.attrs.n) tr = tr.setNodeMarkup(pos, null, { ...n.attrs, n: novy });
    }
  });
  if (tr.docChanged) view.dispatch(tr);
}

function prepniRezimAkordu(zapnout = !rezimAkordu) {
  rezimAkordu = zapnout;
  koren.classList.toggle('rezim-akordu', rezimAkordu);
  aktualizujListu();
}

const PRIKAZY = {
  tucne: () => toggleMark(M.tucne)(view.state, view.dispatch),
  kurziva: () => toggleMark(M.kurziva)(view.state, view.dispatch),
  podtrzeni: () => toggleMark(M.podtrzeni)(view.state, view.dispatch),
  vymazat: vymazFormatovani,
  'zarovnat-left': () => upravOdstavce(() => ({ zarovnani: 'left' })),
  'zarovnat-center': () => upravOdstavce(() => ({ zarovnani: 'center' })),
  'zarovnat-right': () => upravOdstavce(() => ({ zarovnani: 'right' })),
  'zarovnat-justify': () => upravOdstavce(() => ({ zarovnani: 'justify' })),
  'odsadit-mene': () => odsad(-5),
  'odsadit-vice': () => odsad(5),
  akordy: () => prepniRezimAkordu(),
  'transp-dolu': () => transponujVse(-1),
  'transp-nahoru': () => transponujVse(1),
  ramecek: () => pridejRamecek(view, format()),
  zpet: () => undo(view.state, view.dispatch),
  znovu: () => redo(view.state, view.dispatch),
  mensi: () => nastavMeritko(Math.max(0.4, meritko - 0.1)),
  vetsi: () => nastavMeritko(Math.min(3, meritko + 0.1)),
  'na-sirku': () => { meritkoRucne = false; nastavMeritko(); },
};

function napojListu() {
  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    // mousedown: tlačítko nesmí vzít editoru fokus a výběr.
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      PRIKAZY[b.dataset.prikaz]();
      if (!['mensi', 'vetsi', 'na-sirku', 'ramecek'].includes(b.dataset.prikaz)) view.focus();
    });
  }
  const styl = el('lista-styl');
  for (const [k, nazev] of Object.entries(STYLY)) styl.add(new Option(nazev, k));
  styl.addEventListener('change', () => nastavStyl(styl.value));
  const pismo = el('lista-pismo');
  for (const p of PISMA) pismo.add(new Option(p, p));
  pismo.addEventListener('change', () => nastavZnacku(M.pismo, { f: pismo.value }));
  const velikost = el('lista-velikost');
  for (const v of VELIKOSTI) velikost.add(new Option(String(v).replace('.', ','), v));
  velikost.addEventListener('change', () => nastavZnacku(M.velikost, { v: Number(velikost.value) }));
  const barva = el('lista-barva');
  barva.addEventListener('change', () => nastavZnacku(M.barva, { c: barva.value }));
}

// Lišta ukazuje skutečný vzhled u kurzoru: přímé formátování, jinak styl odstavce.
function aktualizujListu() {
  const { state } = view;
  const { $from } = state.selection;
  const p = $from.parent.type === N.odstavec ? $from.parent : null;
  const styl = p ? nastaveni.styly[p.attrs.styl] : nastaveni.styly.normalni;
  const znacky = state.storedMarks || $from.marks();
  const znacka = (t) => znacky.find((m) => m.type === t);
  const nastavVyber = (prvek, hodnota) => {
    if (![...prvek.options].some((o) => o.value === String(hodnota))) {
      prvek.add(new Option(String(hodnota).replace('.', ','), hodnota));
    }
    prvek.value = String(hodnota);
  };
  if (p) el('lista-styl').value = p.attrs.styl;
  nastavVyber(el('lista-pismo'), znacka(M.pismo)?.attrs.f || styl.pismo);
  nastavVyber(el('lista-velikost'), znacka(M.velikost)?.attrs.v || styl.velikost);
  el('lista-barva').value = znacka(M.barva)?.attrs.c || styl.barva;
  const zarovnani = p ? p.attrs.zarovnani || styl.zarovnani : null;

  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    const c = b.dataset.prikaz;
    if (c === 'akordy') b.classList.toggle('aktivni', rezimAkordu);
    else if (c === 'tucne') b.classList.toggle('aktivni', !!znacka(M.tucne) || (styl.tucne && !!p));
    else if (c === 'kurziva') b.classList.toggle('aktivni', !!znacka(M.kurziva) || (styl.kurziva && !!p));
    else if (c === 'podtrzeni') b.classList.toggle('aktivni', !!znacka(M.podtrzeni));
    else if (c.startsWith('zarovnat-')) b.classList.toggle('aktivni', c === 'zarovnat-' + zarovnani);
    else if (c === 'zpet') b.disabled = undoDepth(state) === 0;
    else if (c === 'znovu') b.disabled = redoDepth(state) === 0;
  }

  // Aktivní rámeček (s kurzorem) ukazuje lištu a roh.
  const aktivni = $from.depth >= 1 ? view.nodeDOM($from.before(1)) : null;
  for (const r of view.dom.querySelectorAll('.ps-ramecek')) r.classList.toggle('aktivni', r === aktivni);

  // Panel vybraného akordu.
  const sel = state.selection;
  const vybrany = sel instanceof NodeSelection && sel.node.type === N.akord ? sel : null;
  el('vybrany-akord').hidden = !vybrany;
  if (vybrany) el('akord-velikost').value = vybrany.node.attrs.vel || 100;

  // Značka sloky: u sloky nebo refrénu, ve kterém je kurzor.
  const zacatek = zacatekSloky(state);
  el('panel-znacka').hidden = !zacatek;
  if (zacatek) {
    const pole = el('znacka-sloky');
    if (document.activeElement !== pole) pole.value = zacatek.node.attrs.znacka ?? '';
    requestAnimationFrame(() => {
      const dom = view.nodeDOM(zacatek.pos);
      pole.placeholder = (dom && dom.dataset.znacka) || '';
    });
  }
}

// První odstavec sloky/refrénu, ve které je kurzor: { node, pos } nebo null.
function zacatekSloky(state) {
  const { $from } = state.selection;
  const p = $from.parent;
  if (p.type !== N.odstavec || !['sloka', 'refren'].includes(p.attrs.styl) || !p.content.size) return null;
  const ramecek = $from.node(-1);
  let i = $from.index(-1);
  while (i > 0) {
    const pred = ramecek.child(i - 1);
    if (pred.attrs.styl !== p.attrs.styl || !pred.content.size) break;
    i -= 1;
  }
  let pos = $from.start(-1);
  for (let j = 0; j < i; j++) pos += ramecek.child(j).nodeSize;
  return { node: ramecek.child(i), pos };
}

function upravVybranyAkord(zmena) {
  const sel = view.state.selection;
  if (!(sel instanceof NodeSelection) || sel.node.type !== N.akord) return;
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
  el('znacka-sloky').addEventListener('input', (e) => {
    const zacatek = zacatekSloky(view.state);
    if (!zacatek) return;
    const h = e.target.value.trim();
    view.dispatch(view.state.tr.setNodeMarkup(zacatek.pos, null, { ...zacatek.node.attrs, znacka: h === '' ? null : h }));
  });
  el('zacit-znovu').addEventListener('click', async () => {
    const ok = await window.showConfirm('Rozpracovaná strana se smaže a začne se s prázdnou.',
      'Smazat stranu', { nebezpecne: true });
    if (!ok) return;
    nastaveni = structuredClone(VYCHOZI);
    view.updateState(EditorState.create({ doc: prazdnyDokument(format()), plugins: view.state.plugins }));
    nactiPanel();
    meritkoRucne = false;
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
