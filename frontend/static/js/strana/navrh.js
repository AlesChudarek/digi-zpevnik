// Editor navržené strany - pokusná stránka (krok 1 bodu 11 v TODO).
//
// Strana jako v Canvě (rámečky s polohou), text v rámečku jako ve Wordu (styly odstavců
// a přímé formátování) a k tomu nástroj na akordy ukotvené ke znaku.
//
// Zatím nic neukládá do zpěvníku: rozpracovaná strana se drží jen v tomto prohlížeči
// (localStorage), aby se po obnovení stránky neztratila. Zdroj strany má od začátku
// tvar, který se později uloží na server: { verze, nastaveni, obsah }.

import {
  EditorState, EditorView, NodeSelection, TextSelection, Plugin, keymap, baseKeymap, history,
  undo, redo, undoDepth, redoDepth, toggleMark, Slice, Fragment, closeHistory,
} from 'prosemirror';
import { schema, ROLE, VERZE_SCHEMATU, prazdnyDokument, odstavceZTextu, textZUzlu } from './schema.js';
import { AkordView, OvladacAkordu, pluginAkordu, akordyPoMazani } from './akord-uzel.js';
import { PrvekView, pridejRamecek, nactiObrazek, vlozObrazek } from './ramecky.js';
import { pluginSlok, klicSlok } from './sloky.js';
import { slokyDokumentu, vzorProSloku, doplnAkordy, smazAkordy } from './navrhy-akordu.js';
import { CisloStrany, vykresliDvojstranu } from './cislo-strany.js';
import { FORMATY, cssStylu, nastavAkordy, vykresliStranu, dokonciStranu, obrazekStrany, formatStrany } from './vykresleni.js';
import { prepocitej } from './kolize.js';
import { NastrojNaplasti } from './naplast.js';
import { transponuj } from './akordy.js';

const N = schema.nodes;
const M = schema.marks;
// Strana zpěvníku (/editor-strany/<id strany> nebo /nova?zpevnik=&za=): kde strana je
// a kam se uloží - { zpevnik, nazev, strana, za, navrh, cislo, lp, zpet }. Bez něj je to
// samostatná pokusná stránka se zálohou v prohlížeči.
const KONTEXT = (() => {
  try { return JSON.parse(document.getElementById('kontext-strany')?.textContent || 'null'); } catch (_) { return null; }
})();
const KLIC_ZALOHY = 'navrh-strany-pokus';
// Režim plochy u strany zpěvníku: jedna strana, nebo dvojstrana s protější stranou.
// Tady nahoře - nastavMeritko ho čte už při startu.
const KLIC_REZIMU = 'navrh-rezim-stran';
let naplast = null;          // nástroj Náplast (naplast.js), vznikne po editoru
let protejsekVerze = 0;      // novější vykreslení protějšku přebije starší
let dvojstrana = (() => { try { return localStorage.getItem(KLIC_REZIMU) === 'dvojstrana'; } catch (_) { return false; } })();

// Koncepty: rozdělané (neuložené) strany zpěvníku v prohlížeči, jeden na stranu -
// listování zpěvníkem se tak nikoho neptá a nic se neztratí. Klíč podle zpěvníku a
// strany (nová strana podle toho, za kterou se vloží).
const PREDPONA_KONCEPTU = KONTEXT ? `navrh-koncept:${KONTEXT.zpevnik}:` : null;
let casovacKonceptu = null;   // zápis konceptu se odkládá (zalohuj volá při každé změně)
const klicKonceptu = (k) => PREDPONA_KONCEPTU + (k.strana ? `s${k.strana}` : `n${k.za || 0}`);
function prectiKoncept(klic) {
  try { return JSON.parse(localStorage.getItem(klic) || 'null'); } catch (_) { return null; }
}
function smazKoncept(klic) {
  try { localStorage.removeItem(klic); } catch (_) { /* nedostupné úložiště */ }
}
function koncepty() {
  const out = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const klic = localStorage.key(i);
      if (!klic || !klic.startsWith(PREDPONA_KONCEPTU)) continue;
      const k = prectiKoncept(klic);
      if (k && k.zdroj) out.push({ ...k, klic });
    }
  } catch (_) { /* nedostupné úložiště */ }
  return out.sort((a, b) => a.cislo - b.cislo);
}
const MM_NA_PX = 96 / 25.4;


const PISMA = ['Noto Sans', 'Noto Serif', 'Roboto Condensed', 'Caveat'];
// Kroky tlačítek −/+ u velikosti písma (jako v textových editorech).
const VELIKOSTI = [6, 7, 8, 9, 9.5, 10, 10.5, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48, 60, 72];

// Výchozí velikosti počítají se zpěvníkem A5: tři sloky a refrén s akordy na stranu.
const zakladStylu = {
  pismo: 'Noto Sans', velikost: 9.5, tucne: false, kurziva: false, podtrzeni: false, barva: '#000000',
  zarovnani: 'left', odsazeni: 0, pred: 0, po: 0, radkovani: 1.2,
};
// Styly jsou štětce (rozhodnutí 9. 11.): knihovna stylů patří k účtu (zatím v prohlížeči,
// na server s krokem 3) a strana si nese KOPII každé verze stylu, kterou je namalovaná.
// Změna štětce tak nezmění už hotové strany - na této straně se změna projeví hned
// (autor vidí, co upravuje), jinde ne. Smazaný styl na straně vypadá dál stejně, jen
// v panelu nebude označený.
//
// Styl = jméno, role (jak se odstavec chová, viz ROLE), vzhled a verze. Vestavěné mají
// id rovné roli, vlastní v1, v2… Odstavec nese klíč „id-verze“ (sloka-1, v2-3).
const VYCHOZI_STYLY = {
  normalni: { ...zakladStylu, nazev: 'Normální', role: 'normalni' },
  nadpis: { ...zakladStylu, nazev: 'Název', role: 'nadpis', pismo: 'Noto Serif', velikost: 15, tucne: true, radkovani: 1.15, po: 1 },
  autor: { ...zakladStylu, nazev: 'Autor', role: 'autor', velikost: 8.5, kurziva: true, po: 8, barva: '#444444' },
  sloka: { ...zakladStylu, nazev: 'Sloka', role: 'sloka', odsazeni: 6 },
  refren: { ...zakladStylu, nazev: 'Refrén', role: 'refren', odsazeni: 11 },
};
for (const st of Object.values(VYCHOZI_STYLY)) st.ver = 1;

const VYCHOZI = {
  format: 'A5',
  styly: {},                // kopie stylů použitých na straně: klíč -> vzhled
  pismoAkordu: 'Roboto Condensed', velikostAkordu: 8.5, barvaAkordu: '#b00020', tucneAkordy: true,
  automatickaMezera: false,
  notace: 'cz',
  // Číslo strany (vrstva, viz cislo-strany.js): poloha středu na PRAVÉ straně v mm.
  // Výchozí dole u vnějšího okraje, v dolním okraji strany (A5).
  cislo: {
    zobrazit: true, zrcadlit: true, x: 138, y: 205,
    pismo: 'Noto Sans', velikost: 9, tucne: false, barva: '#000000',
  },
  cisloSkryte: false,       // jen na této straně (titulní apod.)
  // Náhled, dokud strana není ve zpěvníku: číslo a levá/pravá.
  nahledCisla: 12, nahledStrany: 'P',
};

// --- stav -----------------------------------------------------------------------------

let nastaveni = structuredClone(VYCHOZI);
let meritko = 1;
let meritkoRucne = false;
let rezimAkordu = false;
let rozbalenyStyl = null;     // styl, jehož úprava je v panelu rozbalená

// Vygenerovaná pravidla stylů strany (.stranka .ps-<klíč>) - vzniká hned, používá ji
// už úvodní převod stylů.
const stylyStrany = document.createElement('style');
document.head.appendChild(stylyStrany);

const el = (id) => document.getElementById(id);
const koren = el('navrh');
const obal = el('stranka-obal');
const stranka = el('stranka');
const format = () => FORMATY[nastaveni.format] || FORMATY.A5;

function sluc(vychozi, ulozene) {
  const v = { ...structuredClone(vychozi), ...ulozene };
  v.styly = { ...(ulozene.styly || {}) };
  v.cislo = { ...vychozi.cislo, ...(ulozene.cislo || {}) };
  return v;
}

// --- knihovna stylů (štětce) ----------------------------------------------------------

// Knihovna je u účtu na serveru (/api/navrh/styly). Načte se před startem editoru.
let knihovna = await (async () => {
  const k = structuredClone(VYCHOZI_STYLY);
  try {
    const ulozena = (await (await fetch('/api/navrh/styly', { credentials: 'same-origin' })).json()).styly;
    if (ulozena) {
      for (const id of Object.keys(k)) if (ulozena[id]) k[id] = { ...k[id], ...ulozena[id] };
      for (const [id, st] of Object.entries(ulozena)) if (!k[id] && ROLE[st.role]) k[id] = { ...zakladStylu, ...st };
    }
  } catch (_) { /* nedostupná - výchozí styly */ }
  return k;
})();

let casovacKnihovny = null;
function ulozKnihovnu() {
  clearTimeout(casovacKnihovny);
  casovacKnihovny = setTimeout(() => {
    fetch('/api/navrh/styly', {
      method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ styly: knihovna }),
    }).catch(() => window.oznam('Styly se nepodařilo uložit, zkusí se to při další změně.', { nadpis: 'Styly neuložené', druh: 'varovani' }));
  }, 600);
}

const klicStylu = (id) => `${id}-${knihovna[id].ver}`;
const idZKlice = (klic) => klic.replace(/-\d+$/, '');
// Který štětec z knihovny odstavec „je“: ten, se kterým má stejný vzhled i roli (rozhodnuto
// 9. 11.: strana si pamatuje formátování, styl se pozná podle vzhledu). Když vypadá jako
// víc stylů, vyhraje ten, kterým byl namalovaný. Jinak null - formátovaný text bez stylu.
const VLASTNOSTI_STYLU = ['role', 'pismo', 'velikost', 'tucne', 'kurziva', 'podtrzeni', 'barva',
  'zarovnani', 'odsazeni', 'pred', 'po', 'radkovani'];
const stejnyVzhled = (a, b) => VLASTNOSTI_STYLU.every((k) => (a[k] ?? null) === (b[k] ?? null));
function aktualniId(klic) {
  if (!klic) return null;
  const vzhled = styl(klic);
  const vlastni = idZKlice(klic);
  if (knihovna[vlastni] && stejnyVzhled(vzhled, knihovna[vlastni])) return vlastni;
  return Object.keys(knihovna).find((id) => stejnyVzhled(vzhled, knihovna[id])) || null;
}

// Kopie stylu na straně (pro vykreslení); nový klíč se do strany zkopíruje z knihovny.
function zajistiVeStrane(klic) {
  if (nastaveni.styly[klic]) return;
  const id = idZKlice(klic);
  if (!knihovna[id]) return;
  nastaveni = { ...nastaveni, styly: { ...nastaveni.styly, [klic]: { ...knihovna[id] } } };
  obnovCss();
}
// Vzhled odstavce: kopie na straně, jinak (ještě nezkopírovaný klíč nebo holé id)
// z knihovny, jinak Normální.
const styl = (klic) => nastaveni.styly[klic] || knihovna[klic] || knihovna[idZKlice(klic)] || knihovna.normalni;
const role = (klic) => styl(klic).role;

// Po Enteru na konci odstavce: po Názvu Autor, po Autorovi Sloka, jinak stejný styl.
function dalsiStyl(klic) {
  const r = role(klic);
  const dalsi = r === 'nadpis' ? klicStylu('autor') : r === 'autor' ? klicStylu('sloka') : klic;
  zajistiVeStrane(dalsi);
  return dalsi;
}

// Odstavce s holým id stylu (ukázka, vložený text, starší strana) nebo se stylem, který
// strana nezná, dostanou klíč aktuální verze z knihovny a kopii do strany.
function normalizujStyly(state) {
  let tr = null;
  state.doc.descendants((n, pos) => {
    if (n.type !== N.odstavec) return true;
    const k = n.attrs.styl;
    if (nastaveni.styly[k]) return false;
    const id = idZKlice(k);
    if (!knihovna[id] && !knihovna[k]) return false;
    const novy = klicStylu(knihovna[k] ? k : id);
    zajistiVeStrane(novy);
    tr = (tr || state.tr).setNodeMarkup(pos, null, { ...n.attrs, styl: novy });
    return false;
  });
  if (tr) tr.setMeta('addToHistory', false);
  return tr;
}
// Strana zpěvníku s navrženou verzí: zdroj verze ze serveru. Bez verze
// nová strana. Zálohu v prohlížeči má jen samostatná pokusná stránka.
async function nactiVerzi(id) {
  const r = await fetch(`/api/navrh/strana/${id}`, { credentials: 'same-origin' });
  const d = await r.json();
  if (!d.ok) throw new Error(d.error || 'Stranu se nepodařilo načíst.');
  return { nastaveni: sluc(VYCHOZI, d.zdroj.nastaveni || {}), doc: schema.nodeFromJSON(d.zdroj.obsah) };
}

function nactiZalohu() {
  if (KONTEXT) return null;
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
  if (KONTEXT) { naplanujKoncept(); return; }
  clearTimeout(casovacZalohy);
  casovacZalohy = setTimeout(() => {
    try {
      localStorage.setItem(KLIC_ZALOHY, JSON.stringify(zdrojStrany()));
    } catch (_) { /* soukromé okno apod. - záloha není nutná */ }
  }, 400);
}

// Zdroj strany v tom tvaru, v jakém se uloží na server. `zlomy` jsou místa, kde řádky
// zalomil prohlížeč autora - čtečka a PDF je převezmou (vykresleni.js), protože jiný
// prohlížeč by asi 1 % odstavců zalomil o slovo jinak (Safari a Chrome měří text o pixel
// jinak). Editor sám zalamuje dál živě.
function zdrojStrany() {
  // Jen kopie stylů, které strana opravdu používá.
  const pouzite = new Set();
  view.state.doc.descendants((n) => { if (n.type === N.odstavec) pouzite.add(n.attrs.styl); return true; });
  const styly = Object.fromEntries(Object.entries(nastaveni.styly).filter(([k]) => pouzite.has(k)));
  return { verze: VERZE_SCHEMATU, nastaveni: { ...nastaveni, styly }, obsah: view.state.doc.toJSON(), zlomy: zmerZlomy(view.dom) };
}

// { indexOdstavce: [počet znaků textu před začátkem každého dalšího řádku] } - jen
// odstavce, které se zalomily samy (ne ručním zalomením Shift+Enter).
function zmerZlomy(koren) {
  const zlomy = {};
  const g = document.createRange();
  [...koren.querySelectorAll('.ps-p')].forEach((p, index) => {
    const starty = [];
    let predchozi = null, znak = 0, poBr = false;
    const chodec = document.createTreeWalker(p, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = chodec.nextNode())) {
      // Ruční zalomení (Shift+Enter) není zlom - zalomení převzaté ze zdroje (ps-zlom) ano.
      if (n.nodeType === 1) {
        if (n.nodeName === 'BR' && !n.classList.contains('ProseMirror-trailingBreak') && !n.classList.contains('ps-zlom')) poBr = true;
        continue;
      }
      if (n.parentElement.closest('.akord-kotva, .ps-nabidka')) continue;
      for (let i = 0; i < n.length; i++, znak++) {
        g.setStart(n, i);
        g.setEnd(n, i + 1);
        const r = [...g.getClientRects()].find((x) => x.width > 0);
        if (!r) continue;
        // Nový řádek: střed znaku je pod spodkem předchozího. Porovnávat jen horní hrany
        // nejde - menší písmo na témže řádku má horní hranu níž.
        if (predchozi && (r.top + r.bottom) / 2 > predchozi.bottom && !poBr) starty.push(znak);
        predchozi = r;
        poBr = false;
      }
    }
    if (starty.length) zlomy[index] = starty;
  });
  return zlomy;
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
    const novy = naKonci ? dalsiStyl(p.attrs.styl) : p.attrs.styl;
    const stejny = novy === p.attrs.styl;
    tr = tr.split($p.pos, 1, [{
      type: N.odstavec,
      attrs: { styl: novy, zarovnani: stejny ? p.attrs.zarovnani : null, odsazeni: stejny ? p.attrs.odsazeni : null, znacka: null },
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

let zaloha = nactiZalohu();
if (KONTEXT && KONTEXT.navrh) {
  try {
    zaloha = await nactiVerzi(KONTEXT.navrh);
  } catch (chyba) {
    window.oznam(String(chyba.message || chyba), { nadpis: 'Strana se nenačetla', druh: 'chyba' });
  }
}
// Rozdělaná (neuložená) úprava této strany z prohlížeče má přednost před uloženou
// verzí. Když se strana mezitím uložila jinde (jiná verze), zeptá se.
let pouzitKoncept = false;
if (KONTEXT) {
  const k = prectiKoncept(klicKonceptu(KONTEXT));
  if (k && k.zdroj && k.zdroj.verze === VERZE_SCHEMATU) {
    pouzitKoncept = (k.zaklad || null) === (KONTEXT.navrh || null)
      || await window.showConfirm('Strana má rozdělanou úpravu, ale mezitím byla uložena jinak. '
        + 'Pokračovat v rozdělané úpravě? Jinak se zahodí.', 'Pokračovat v úpravě');
    if (pouzitKoncept) zaloha = { nastaveni: sluc(VYCHOZI, k.zdroj.nastaveni || {}), doc: schema.nodeFromJSON(k.zdroj.obsah) };
    else smazKoncept(klicKonceptu(KONTEXT));
  }
}
// Strana s nahraným obrázkem (sken), dosud nenavržená: obrázek je podkladem přes celou
// stranu (za textem), aby šlo dopsat text a akordy. Plus malý textový rámeček nahoře -
// strana ho mít musí a přes celou stranu by zakrýval sken pro klikání.
if (KONTEXT && KONTEXT.obrazek && !KONTEXT.navrh && !pouzitKoncept) {
  try {
    zaloha = { nastaveni, doc: await dokumentSeSkenem(KONTEXT.obrazek) };
  } catch (chyba) {
    window.oznam('Obrázek strany se nenačetl.', { nadpis: 'Strana se nenačetla', druh: 'chyba' });
  }
}
if (zaloha) nastaveni = zaloha.nastaveni;
if (KONTEXT) {
  // Číslo a levá/pravá podle skutečné pozice ve zpěvníku.
  nastaveni = { ...nastaveni, nahledCisla: KONTEXT.cislo, nahledStrany: KONTEXT.lp === 'L' ? 'L' : 'P' };
}

const prvek = (n, v, g) => new PrvekView(n, v, g, { meritko: () => meritko, rozmer: format });

async function dokumentSeSkenem(src) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const f = format();
  const k = Math.min(f.sirka / img.naturalWidth, f.vyska / img.naturalHeight);
  const w = Math.round(img.naturalWidth * k * 10) / 10, h = Math.round(img.naturalHeight * k * 10) / 10;
  return N.doc.create(null, [
    N.obrazek.create({ src, x: Math.round((f.sirka - w) * 5) / 10, y: Math.round((f.vyska - h) * 5) / 10, w, h, vrstva: 'za' }),
    N.ramecek.create({ x: f.okraj, y: f.okraj, w: 60, h: 20 }, N.odstavec.create()),
  ]);
}

const view = new EditorView(stranka.querySelector('.stranka-mount'), {
  state: EditorState.create({
    doc: zaloha ? zaloha.doc : prazdnyDokument(format()),
    plugins: [
      pluginAkordu(ovladac), pluginSlok({ role }), history(), klavesy, keymap(baseKeymap),
      new Plugin({ appendTransaction: (trs, _, novy) => (trs.some((t) => t.docChanged) ? normalizujStyly(novy) : null) }),
    ],
  }),
  attributes: { class: 'stranka-obsah', spellcheck: 'false' },
  nodeViews: {
    akord: (n, v, g) => new AkordView(n, v, g),
    ramecek: prvek,
    obrazek: prvek,
    tvar: prvek,
  },
  clipboardTextSerializer: (slice) => textZUzlu(slice.content),
  clipboardTextParser: vlozenyText,
  // Obrázek ze schránky nebo přetažený soubor se vloží jako obrázek na stranu.
  handlePaste: (v, e) => vlozSoubory(e.clipboardData && e.clipboardData.files),
  handleDrop: (v, e) => vlozSoubory(e.dataTransfer && e.dataTransfer.files, e),
  dispatchTransaction(tr) {
    const blik = akordyPoMazani(tr);
    view.updateState(view.state.apply(tr));
    if (tr.docChanged) zalohuj();
    if (blik.length) ovladac.blikni(blik);
    aktualizujListu();
  },
});
ovladac.pripoj(view);


const cisloStrany = new CisloStrany(stranka, el('navrh-plocha'), {
  format, nastaveni: () => nastaveni, meritko: () => meritko,
  zmen: (zmena) => { nastaveni = { ...nastaveni, ...zmena }; pouzijNastaveni(); zalohuj(); },
});

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



function obnovCss() {
  stylyStrany.textContent = Object.entries(nastaveni.styly).map(([k, v]) => cssStylu('.stranka', k, v)).join('\n');
}

function pouzijNastaveni() {
  const f = format();
  const s = stranka.style;
  s.width = f.sirka + 'mm';
  s.height = f.vyska + 'mm';
  s.setProperty('--okraj', f.okraj + 'mm');
  for (const cil of [s, el('ukazka-akordu').style]) nastavAkordy(cil, nastaveni);
  obnovCss();
  naplnVyberStylu();
  vykresliStyly();
  if (typeof cisloStrany !== 'undefined') cisloStrany.vykresli();
  vykresliDvojstranu(el('dvojstrana-panel'), f, nastaveni.cislo, String(nastaveni.nahledCisla), nastaveni.nahledStrany);
  // Role stylu mohla změnit číslování slok.
  if (typeof view !== 'undefined') view.dispatch(view.state.tr.setMeta(klicSlok, true));
  nastavMeritko();
  document.fonts.ready.then(naplanujPrepocet);
  naplanujPrepocet();
}

// Výška pracovní plochy: do spodku okna (lišta a panel se pak neposouvají se stránkou).
function nastavVysku() {
  if (window.innerWidth <= 900) { koren.style.height = ''; return; }
  const horni = koren.getBoundingClientRect().top + window.scrollY;
  koren.style.height = Math.max(420, window.innerHeight - horni) + 'px';
}

// Bez ručního přiblížení se ukazuje celá strana.
function nastavMeritko(nove) {
  const f = format();
  const sirka = f.sirka * MM_NA_PX, vyska = f.vyska * MM_NA_PX;
  if (nove != null) { meritko = nove; meritkoRucne = true; }
  else if (!meritkoRucne) {
    const plocha = el('navrh-plocha');
    // Okraj plochy (24 px kolem) a spodní pruh se stavem a přiblížením.
    const volnoX = plocha.clientWidth - 48, volnoY = plocha.clientHeight - 48 - 44;
    const stran = dvojstrana && KONTEXT ? 2 : 1;      // ve dvojstraně se vejdou obě
    meritko = Math.max(0.3, Math.min(3, (volnoX - (stran - 1) * 6) / (sirka * stran), volnoY / vyska));
  }
  meritko = Math.round(meritko * 100) / 100;
  stranka.style.transform = `scale(${meritko})`;
  stranka.style.setProperty('--meritko', meritko);
  obal.style.width = sirka * meritko + 'px';
  obal.style.height = vyska * meritko + 'px';
  el('meritko-text').textContent = Math.round(meritko * 100) + ' %';
  naplanujPrepocet();
  if (KONTEXT) vykresliProtejsek();
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
      tr = tr.setNodeMarkup(pos, null, { ...a, x: z(a.x), y: z(a.y), w: z(a.w), h: z(a.h) });
    });
    view.dispatch(tr);
    const c = nastaveni.cislo;
    nastaveni = { ...nastaveni, cislo: { ...c, x: Math.round(c.x * pomer * 2) / 2, y: Math.round(c.y * pomer * 2) / 2 } };
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
  for (const prvek of koren.querySelectorAll('select[data-role]')) {
    for (const [k, nazev] of Object.entries(ROLE)) prvek.add(new Option(nazev, k));
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
  for (const prvek of koren.querySelectorAll('[data-styl-pole]')) {
    const klic = prvek.dataset.stylPole;
    prvek.addEventListener(udalostPrvku(prvek), () => {
      const h = hodnotaPrvku(prvek);
      if (Number.isNaN(h) || !rozbalenyStyl || (klic === 'nazev' && !String(h).trim())) return;
      zmenStyl(rozbalenyStyl, { [klic]: klic === 'nazev' ? String(h).trim() : h });
    });
  }
  // Řez a zarovnání jako přepínače (stejně jako v liště).
  for (const b of koren.querySelectorAll('[data-styl-prepinac]')) {
    b.addEventListener('click', () => {
      if (!rozbalenyStyl) return;
      const k = b.dataset.stylPrepinac;
      zmenStyl(rozbalenyStyl, { [k]: !knihovna[rozbalenyStyl][k] });
    });
  }
  for (const b of koren.querySelectorAll('[data-styl-zarovnani]')) {
    b.addEventListener('click', () => { if (rozbalenyStyl) zmenStyl(rozbalenyStyl, { zarovnani: b.dataset.stylZarovnani }); });
  }
  // Číslo strany: data-cislo-pole (vnořené nastaveni.cislo), přepínač tučně, levá/pravá.
  const zmenCislo = (zmena) => {
    nastaveni = { ...nastaveni, cislo: { ...nastaveni.cislo, ...zmena } };
    pouzijNastaveni();
    zalohuj();
  };
  for (const prvek of koren.querySelectorAll('[data-cislo-pole]')) {
    prvek.addEventListener(udalostPrvku(prvek), () => {
      const h = hodnotaPrvku(prvek);
      if (!Number.isNaN(h)) zmenCislo({ [prvek.dataset.cisloPole]: h });
    });
  }
  for (const b of koren.querySelectorAll('[data-cislo-prepinac]')) {
    b.addEventListener('click', () => zmenCislo({ [b.dataset.cisloPrepinac]: !nastaveni.cislo[b.dataset.cisloPrepinac] }));
  }
  for (const b of koren.querySelectorAll('[data-nahled-strany]')) {
    b.addEventListener('click', () => {
      nastaveni = { ...nastaveni, nahledStrany: b.dataset.nahledStrany };
      pouzijNastaveni();
      zalohuj();
    });
  }
  el('smazat-styl').addEventListener('click', () => smazStyl(rozbalenyStyl));
  el('novy-styl').addEventListener('click', () => {
    const formular = el('novy-styl-formular');
    formular.hidden = !formular.hidden;
    if (!formular.hidden) {
      const p = view.state.selection.$from.parent;
      el('novy-styl-role').value = p.type === N.odstavec ? role(p.attrs.styl) : 'normalni';
      el('novy-styl-nazev').value = '';
      el('novy-styl-nazev').focus();
    }
  });
  el('novy-styl-zrusit').addEventListener('click', () => { el('novy-styl-formular').hidden = true; });
  const vytvor = () => {
    const nazev = el('novy-styl-nazev').value.trim();
    if (!nazev) { el('novy-styl-nazev').focus(); return; }
    novyStyl(nazev, el('novy-styl-role').value);
    el('novy-styl-formular').hidden = true;
  };
  el('novy-styl-vytvorit').addEventListener('click', vytvor);
  el('novy-styl-nazev').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); vytvor(); } });
  nactiPanel();
}

function nactiPanel() {
  for (const prvek of koren.querySelectorAll('[data-nastaveni]')) {
    const h = nastaveni[prvek.dataset.nastaveni];
    if (prvek.type === 'checkbox') prvek.checked = !!h; else prvek.value = h;
  }
  for (const prvek of koren.querySelectorAll('[data-cislo-pole]')) {
    const h = nastaveni.cislo[prvek.dataset.cisloPole];
    if (prvek.type === 'checkbox') prvek.checked = !!h; else prvek.value = h;
  }
  for (const b of koren.querySelectorAll('[data-cislo-prepinac]')) b.classList.toggle('aktivni', !!nastaveni.cislo[b.dataset.cisloPrepinac]);
  for (const b of koren.querySelectorAll('[data-nahled-strany]')) b.classList.toggle('aktivni', b.dataset.nahledStrany === nastaveni.nahledStrany);
  if (!rozbalenyStyl) return;
  const s = knihovna[rozbalenyStyl];
  for (const prvek of koren.querySelectorAll('[data-styl-pole]')) {
    if (document.activeElement === prvek) continue;
    const h = s[prvek.dataset.stylPole];
    if (prvek.type === 'checkbox') prvek.checked = !!h; else prvek.value = h;
  }
  for (const b of koren.querySelectorAll('[data-styl-prepinac]')) b.classList.toggle('aktivni', !!s[b.dataset.stylPrepinac]);
  for (const b of koren.querySelectorAll('[data-styl-zarovnani]')) b.classList.toggle('aktivni', s.zarovnani === b.dataset.stylZarovnani);
  // Jméno, role a smazání jen u vlastních stylů - vestavěné nesou číslování a Enter.
  const vlastni = !VYCHOZI_STYLY[rozbalenyStyl];
  for (const e of koren.querySelectorAll('.jen-vlastni')) e.hidden = !vlastni;
}

// Úprava štětce: při první změně od rozbalení dostane styl novou verzi a odstavce TÉTO
// strany s předchozí verzí přejdou na ni (autor vidí, co upravuje). Ostatní strany mají
// svou kopii staré verze a nezmění se. Další změny téže úpravy mění už novou verzi.
const upravovane = new Set();
function zmenStyl(id, zmena) {
  const stary = klicStylu(id);
  if (!upravovane.has(id)) {
    upravovane.add(id);
    // Odstavce strany, které tento styl „jsou“ (stejný vzhled), než se změní.
    const jeho = klicyStylu(id);
    knihovna = { ...knihovna, [id]: { ...knihovna[id], ...zmena, ver: knihovna[id].ver + 1 } };
    const novy = klicStylu(id);
    zajistiVeStrane(novy);
    prevedOdstavce(jeho, novy);
  } else {
    knihovna = { ...knihovna, [id]: { ...knihovna[id], ...zmena } };
    nastaveni = { ...nastaveni, styly: { ...nastaveni.styly, [stary]: { ...knihovna[id] } } };
  }
  ulozKnihovnu();
  pouzijNastaveni();
  zalohuj();
  aktualizujListu();
}

// Klíče odstavců strany, které vypadají jako styl `id`.
function klicyStylu(id) {
  const klice = new Set();
  view.state.doc.descendants((n) => {
    if (n.type === N.odstavec && aktualniId(n.attrs.styl) === id) klice.add(n.attrs.styl);
    return n.type !== N.odstavec;
  });
  return klice;
}

// Odstavce strany s klíčem z množiny `z` dostanou klíč `na` (bez zápisu do Zpět - jde
// o úpravu štětce, ne textu).
function prevedOdstavce(z, na, tr = null) {
  let t = tr || view.state.tr;
  view.state.doc.descendants((n, pos) => {
    if (n.type === N.odstavec && z.has(n.attrs.styl)) t = t.setNodeMarkup(pos, null, { ...n.attrs, styl: na });
    return n.type !== N.odstavec;
  });
  if (tr) return t;
  if (t.docChanged) view.dispatch(t.setMeta('addToHistory', false));
  return t;
}
function naplnVyberStylu() {
  const vyber = el('lista-styl');
  const hodnota = vyber.value;
  vyber.textContent = '';
  for (const [k, s] of Object.entries(knihovna)) vyber.add(new Option(s.nazev, k));
  if (hodnota) vyber.value = hodnota;
}

// Seznam stylů v panelu: ukázka každého stylu jeho písmem a krátký popis. Klik na styl ho
// použije na odstavce ve výběru, tužka rozbalí jeho úpravu. U stylu odstavce s kurzorem,
// od kterého se odstavec liší přímým formátováním, je „Uložit“ (převezme to formátování).
function vykresliStyly() {
  const seznam = el('seznam-stylu');
  const uprava = el('uprava-stylu');
  seznam.textContent = '';
  for (const [k, s] of Object.entries(knihovna)) {
    const li = document.createElement('li');
    li.dataset.styl = k;
    const nahled = document.createElement('button');
    nahled.type = 'button';
    nahled.className = 'nahled';
    nahled.title = `Použít styl ${s.nazev}`;
    nahled.style.fontFamily = `'${s.pismo}', sans-serif`;
    nahled.style.fontSize = Math.min(Math.max(s.velikost * 1.25, 12), 19) + 'px';
    nahled.style.fontWeight = s.tucne ? 700 : 400;
    nahled.style.fontStyle = s.kurziva ? 'italic' : 'normal';
    nahled.style.textDecoration = s.podtrzeni ? 'underline' : 'none';
    nahled.style.color = s.barva;
    nahled.textContent = s.nazev;
    const popis = document.createElement('span');
    popis.className = 'popis';
    const casti = [`${s.pismo} ${String(s.velikost).replace('.', ',')} pt`];
    if (!VYCHOZI_STYLY[k]) casti.push(ROLE[s.role].replace(/ \(.*\)/, '').toLowerCase());
    popis.textContent = casti.join(' · ');
    nahled.appendChild(popis);
    nahled.addEventListener('mousedown', (e) => e.preventDefault());
    nahled.addEventListener('click', () => nastavStyl(k));
    const ulozit = document.createElement('button');
    ulozit.type = 'button';
    ulozit.className = 'ulozit-styl';
    ulozit.textContent = 'Uložit';
    ulozit.title = 'Uložit formátování odstavce do stylu (změní všechny odstavce s tímto stylem)';
    ulozit.hidden = true;
    ulozit.addEventListener('mousedown', (e) => e.preventDefault());
    ulozit.addEventListener('click', () => ulozDoStylu());
    const upravit = document.createElement('button');
    upravit.type = 'button';
    upravit.className = 'upravit';
    upravit.title = `Upravit styl ${s.nazev}`;
    upravit.setAttribute('aria-expanded', String(rozbalenyStyl === k));
    upravit.innerHTML = '<svg viewBox="0 0 24 24"><use href="#i-tuzka"/></svg>';
    upravit.addEventListener('click', () => {
      rozbalenyStyl = rozbalenyStyl === k ? null : k;
      upravovane.clear();
      vykresliStyly();
    });
    li.append(nahled, ulozit, upravit);
    li.classList.toggle('rozbaleny', rozbalenyStyl === k);
    seznam.appendChild(li);
    if (rozbalenyStyl === k) li.after(uprava);
  }
  uprava.hidden = !rozbalenyStyl;
  if (!rozbalenyStyl) seznam.after(uprava);
  nactiPanel();
  if (typeof view !== 'undefined') oznacAktivniStyl();
}

// id: styl výběru, nebo null u výběru přes odstavce s různými styly (nic se neoznačí).
function oznacAktivniStyl(id = souhrnVyberu(view.state).styl) {
  const p = view.state.selection.$from.parent;
  const lisi = id && p.type === N.odstavec && aktualniId(p.attrs.styl) === id && Object.keys(odchylky(p)).length > 0;
  // Jen položky stylů - mezi nimi může být vložený formulář úpravy.
  for (const li of el('seznam-stylu').querySelectorAll(':scope > li')) {
    const tento = li.dataset.styl === id;
    li.classList.toggle('aktivni', tento);
    li.querySelector('.ulozit-styl').hidden = !(tento && lisi);
  }
}

// --- vlastní styly ---------------------------------------------------------------------

const ZNACKY_STYLU = [
  ['pismo', (m) => m.attrs.f], ['velikost', (m) => m.attrs.v], ['barva', (m) => m.attrs.c],
  ['tucne', () => true], ['kurziva', () => true], ['podtrzeni', () => true],
];

// Čím se odstavec liší od svého stylu: přímé zarovnání a odsazení a formátování, které
// má CELÝ text odstavce (tučné slovo uprostřed do stylu nepatří). { klic: hodnota }.
function odchylky(p) {
  const z = {};
  if (p.attrs.zarovnani && p.attrs.zarovnani !== styl(p.attrs.styl).zarovnani) z.zarovnani = p.attrs.zarovnani;
  if (p.attrs.odsazeni != null && p.attrs.odsazeni !== styl(p.attrs.styl).odsazeni) z.odsazeni = p.attrs.odsazeni;
  const texty = [];
  p.forEach((n) => { if (n.isText && n.text.trim()) texty.push(n); });
  if (!texty.length) return z;
  for (const [klic, hodnota] of ZNACKY_STYLU) {
    const hodnoty = texty.map((t) => { const m = t.marks.find((x) => x.type === M[klic]); return m ? hodnota(m) : undefined; });
    if (hodnoty[0] !== undefined && hodnoty.every((h) => h === hodnoty[0]) && hodnoty[0] !== styl(p.attrs.styl)[klic]) {
      z[klic] = hodnoty[0];
    }
  }
  return z;
}

// Odstavec s kurzorem: { node, pos } nebo null.
function odstavecSKurzorem() {
  const { $from } = view.state.selection;
  if ($from.parent.type !== N.odstavec) return null;
  return { node: $from.parent, pos: $from.before() };
}

// Odebere z odstavce přímé formátování, které teď nese styl.
function odeberPrevzate(tr, { node, pos }, klice) {
  const od = pos + 1, d = pos + node.nodeSize - 1;
  for (const k of klice) if (M[k]) tr = tr.removeMark(od, d, M[k]);
  const attrs = { ...node.attrs };
  if (klice.includes('zarovnani')) attrs.zarovnani = null;
  if (klice.includes('odsazeni')) attrs.odsazeni = null;
  return tr.setNodeMarkup(pos, null, attrs);
}

function ulozDoStylu() {
  const o = odstavecSKurzorem();
  const id = o && aktualniId(o.node.attrs.styl);
  if (!id) return;
  const z = odchylky(o.node);
  if (!Object.keys(z).length) return;
  // Nová verze štětce s převzatým formátováním; odstavce strany s ním přejdou na ni.
  const jeho = klicyStylu(id);
  jeho.add(o.node.attrs.styl);
  knihovna = { ...knihovna, [id]: { ...knihovna[id], ...z, ver: knihovna[id].ver + 1 } };
  const novy = klicStylu(id);
  zajistiVeStrane(novy);
  let tr = odeberPrevzate(closeHistory(view.state.tr), o, Object.keys(z));
  tr = prevedOdstavce(jeho, novy, tr);
  view.dispatch(tr);
  ulozKnihovnu();
  pouzijNastaveni();
  zalohuj();
  view.focus();
}
function novyStyl(nazev, r) {
  const o = odstavecSKurzorem();
  let i = 1;
  while (knihovna['v' + i]) i += 1;
  const id = 'v' + i;
  const zaklad = o ? styl(o.node.attrs.styl) : knihovna.normalni;
  const z = o ? odchylky(o.node) : {};
  knihovna = { ...knihovna, [id]: { ...zaklad, ...z, nazev, role: r, ver: 1 } };
  ulozKnihovnu();
  zajistiVeStrane(klicStylu(id));
  pouzijNastaveni();
  if (o) {
    let tr = odeberPrevzate(closeHistory(view.state.tr), o, Object.keys(z));
    tr = tr.setNodeMarkup(o.pos, null, { ...tr.doc.nodeAt(o.pos).attrs, styl: klicStylu(id) });
    view.dispatch(tr);
  }
  zalohuj();
  view.focus();
}
// Smazání vlastního stylu: jeho odstavce dostanou vestavěný styl se stejnou rolí.
// Smazání štětce: jen z knihovny. Strany, které jsou jím namalované, mají svou kopii
// a vypadají dál stejně - jen v panelu nebude označený žádný styl.
function smazStyl(id) {
  if (!id || VYCHOZI_STYLY[id]) return;
  const knih = { ...knihovna };
  delete knih[id];
  knihovna = knih;
  ulozKnihovnu();
  rozbalenyStyl = null;
  pouzijNastaveni();
  aktualizujListu();
}
// --- obrázky --------------------------------------------------------------------------

// Soubory ze schránky, přetažení nebo výběru: obrázky se vloží na stranu. Vrací true,
// když se něco vkládalo (pak se událost dál nezpracovává).
function vlozSoubory(soubory, udalost = null) {
  const obrazky = [...(soubory || [])].filter((f) => /^image\//.test(f.type));
  if (!obrazky.length) return false;
  let kam = null;
  if (udalost) {
    const r = stranka.getBoundingClientRect();
    const pxNaMm = MM_NA_PX * meritko;
    kam = { x: (udalost.clientX - r.left) / pxNaMm, y: (udalost.clientY - r.top) / pxNaMm };
  }
  for (const soubor of obrazky) {
    nactiObrazek(soubor)
      .then(async (o) => vlozObrazek(view, format(), { ...o, src: await nahrajObrazek(o.src) }, kam))
      .catch((chyba) => window.oznam(chyba.message, { nadpis: 'Obrázek se nevložil', druh: 'chyba' }));
  }
  return true;
}

// Obrázek (data: URL) na server - strana pak odkazuje na /navrh/obrazek/<id>.
async function nahrajObrazek(dataUrl) {
  const blob = await (await fetch(dataUrl)).blob();
  const fd = new FormData();
  fd.append('soubor', blob, blob.type === 'image/png' ? 'obrazek.png' : 'obrazek.jpg');
  const r = await fetch('/api/navrh/obrazek', { method: 'POST', body: fd, credentials: 'same-origin' });
  const d = await r.json().catch(() => ({}));
  if (!d.ok) throw new Error(d.error || 'Obrázek se nepodařilo nahrát.');
  return d.url;
}

// Obrázky, které jsou ve straně ještě jako data: URL (starší záloha), na server.
async function nahrajDataObrazky() {
  const zmeny = [];
  view.state.doc.forEach((n, pos) => {
    if (n.type === N.obrazek && n.attrs.src.startsWith('data:')) zmeny.push({ n, pos });
  });
  if (!zmeny.length) return;
  let tr = view.state.tr;
  for (const { n, pos } of zmeny) tr = tr.setNodeMarkup(pos, null, { ...n.attrs, src: await nahrajObrazek(n.attrs.src) });
  view.dispatch(tr.setMeta('addToHistory', false));
}

// --- Uložit (strana zpěvníku) ----------------------------------------------------------
// Uložit nahraje novou verzi strany (zdroj, PDF, náhled) a server ji rovnou připojí na
// místo ve zpěvníku; nová strana tím teprve ve zpěvníku vznikne. Editor zůstane otevřený.
// Rozdělané jiné strany (koncepty) jde uložit naráz - z jejich zdroje, bez otevírání.

let otiskPriNacteni = null;
const otiskStrany = () => JSON.stringify({ o: view.state.doc.toJSON(), n: { ...nastaveni, nahledCisla: 0, nahledStrany: '' } });
const neulozeno = () => !!KONTEXT && otiskStrany() !== otiskPriNacteni;
const jineKoncepty = () => (KONTEXT ? koncepty().filter((k) => k.klic !== klicKonceptu(KONTEXT)) : []);
const cislaStran = (seznam) => seznam.map((k) => k.cislo).join(', ');

function ulozKoncept() {
  clearTimeout(casovacKonceptu);
  if (!KONTEXT) return;
  const klic = klicKonceptu(KONTEXT);
  if (neulozeno()) {
    try {
      localStorage.setItem(klic, JSON.stringify({ zdroj: zdrojStrany(), zaklad: KONTEXT.navrh || null,
        strana: KONTEXT.strana || null, za: KONTEXT.strana ? null : (KONTEXT.za || 0), cislo: KONTEXT.cislo }));
    } catch (_) { /* plné nebo nedostupné úložiště - koncept se nezachová */ }
  } else {
    smazKoncept(klic);
  }
  obnovListovani();
}
function naplanujKoncept() {
  clearTimeout(casovacKonceptu);
  casovacKonceptu = setTimeout(ulozKoncept, 400);
}

// Zdroj strany na server: PDF a náhled se vyrobí ze zdroje, server verzi připojí na místo
// (cil = { strana } nebo { za }). Vrací odpověď serveru.
async function odesliStranu(zdroj, cil) {
  const { pdfStrany } = await import(new URL('./pdf-strany.js', import.meta.url).href);
  // PDF i náhled bez čísla strany - to se dokreslí podle pozice ve zpěvníku.
  const { bytes } = await pdfStrany(zdroj, {});
  const { blob: nahled } = await obrazekStrany(zdroj, { sirkaPx: 420 });
  const fd = new FormData();
  fd.append('zdroj', JSON.stringify(zdroj));
  fd.append('pdf', new Blob([bytes], { type: 'application/pdf' }), 'strana.pdf');
  fd.append('nahled', nahled, 'nahled.png');
  if (cil.strana) fd.append('strana_id', String(cil.strana));
  else { fd.append('zpevnik', KONTEXT.zpevnik); fd.append('za', String(cil.za || 0)); }
  const r = await fetch('/api/navrh/strana', { method: 'POST', body: fd, credentials: 'same-origin' });
  const d = await r.json().catch(() => ({}));
  if (!d.ok) throw new Error(d.error || 'Stranu se nepodařilo uložit.');
  return d;
}

// Nová strana je teď ve zpěvníku: zařadit ji do seznamu stran (další se posunou).
function zaradNovou(id, cislo) {
  for (const st of KONTEXT.strany) if (st.cislo >= cislo) st.cislo += 1;
  KONTEXT.strany.push({ id, cislo, druh: 'navrh' });
  KONTEXT.strany.sort((a, b) => a.cislo - b.cislo);
}

async function ulozAktualni() {
  await nahrajDataObrazky();
  const staryKlic = klicKonceptu(KONTEXT);
  const d = await odesliStranu(zdrojStrany(), { strana: KONTEXT.strana, za: KONTEXT.za });
  otiskPriNacteni = otiskStrany();
  KONTEXT.navrh = d.id;
  smazKoncept(staryKlic);
  if (!KONTEXT.strana && d.strana) {
    // Nová strana teď ve zpěvníku je: další Uložit už ji jen přepíše.
    zaradNovou(d.strana, KONTEXT.cislo);
    KONTEXT.strana = d.strana;
    KONTEXT.za = null;
    // window.: `history` je tu plugin historie úprav z ProseMirroru.
    window.history.replaceState(null, '', `/editor-strany/${d.strana}`);
  } else {
    const st = KONTEXT.strany.find((x) => x.id === KONTEXT.strana);
    if (st) st.druh = 'navrh';
  }
}

async function ulozKoncepty(seznam) {
  const chyby = [];
  for (const k of seznam) {
    try {
      if (JSON.stringify(k.zdroj.obsah).includes('"src":"data:')) throw new Error('obrázek strany se nenahrál - otevřete ji a uložte znovu');
      const d = await odesliStranu(k.zdroj, { strana: k.strana, za: k.za });
      smazKoncept(k.klic);
      if (!k.strana && d.strana) zaradNovou(d.strana, k.cislo);
      else {
        const st = KONTEXT.strany.find((x) => x.id === k.strana);
        if (st) st.druh = 'navrh';
      }
    } catch (chyba) {
      chyby.push(`strana ${k.cislo}: ${chyba.message || chyba}`);
    }
  }
  if (chyby.length) throw new Error(chyby.join('; '));
}

let ulozitSe = false;
// vse: uložit i ostatní rozdělané strany. Vrací true, když se všechno povedlo.
async function ulozitStranu(vse) {
  if (!KONTEXT || ulozitSe) return false;
  ulozitSe = true;
  clearTimeout(casovacKonceptu);
  const tlacitko = koren.querySelector('[data-prikaz="ulozit"]');
  if (tlacitko) { tlacitko.disabled = true; tlacitko.textContent = 'Ukládá se…'; }
  let povedlo = false;
  try {
    if (neulozeno() || !KONTEXT.strana) await ulozAktualni();
    if (vse) await ulozKoncepty(jineKoncepty());
    povedlo = true;
  } catch (chyba) {
    window.oznam(String(chyba.message || chyba), { nadpis: 'Strana se neuložila', druh: 'chyba' });
  } finally {
    ulozitSe = false;
    obnovListovani();
    if (tlacitko) {
      tlacitko.disabled = false;
      tlacitko.textContent = povedlo ? 'Uloženo' : 'Uložit';
      if (povedlo) setTimeout(() => { if (!ulozitSe) tlacitko.textContent = 'Uložit'; }, 1500);
    }
  }
  return povedlo;
}

// Tlačítko Uložit: když jsou rozdělané i jiné strany, zeptá se, jestli je uložit taky.
async function ulozit() {
  const jine = jineKoncepty();
  if (!jine.length) return ulozitStranu(false);
  const volba = await window.showUnsavedConfirm(
    `Neuložené změny jsou i na stranách ${cislaStran(jine)}. Tato strana: ${KONTEXT.cislo}.`,
    { save: 'Uložit všechny', discard: 'Uložit jen tuto', discardStyl: 'neutralni' });
  if (volba === 'cancel') return false;
  return ulozitStranu(volba === 'save');
}

// --- listování zpěvníkem ----------------------------------------------------------------

// Spodní lišta: ‹ číslo strany › a režim jedna strana / dvojstrana. Rozdělané strany
// ukazuje výstraha u Uložit.
function obnovListovani() {
  if (!KONTEXT) return;
  const pole = el('cislo-strany-zpevniku');
  if (pole && document.activeElement !== pole) pole.value = String(KONTEXT.cislo);
  const jine = jineKoncepty();
  const znak = el('neulozene-jinde');
  if (znak) {
    znak.hidden = !jine.length;
    znak.setAttribute('data-napoveda', `Neuložené změny i na stranách ${cislaStran(jine)}`);
  }
  for (const [prikaz, smer] of [['predchozi-strana', -1], ['dalsi-strana', 1]]) {
    const b = koren.querySelector(`[data-prikaz="${prikaz}"]`);
    if (b) b.disabled = !sousedniStrana(smer);
  }
}

// --- dvojstrana -------------------------------------------------------------------------
// Vedle upravované strany protější strana zpěvníku (levá vlevo, pravá vpravo), jen
// k náhledu: rozdělaná úprava z prohlížeče, jinak uložená verze, obrázek, nebo prázdná.
// Klik ji otevře k úpravě. Obě strany naráz upravovat zatím nejde.

function protejsiStrana() {
  if (!KONTEXT) return null;
  const vlevo = KONTEXT.lp === 'P';          // pravá strana má protějšek vlevo
  // Nová strana v seznamu ještě není: číslo, které dostane, má teď strana za ní.
  const cislo = vlevo ? KONTEXT.cislo - 1 : (KONTEXT.strana ? KONTEXT.cislo + 1 : KONTEXT.cislo);
  const st = KONTEXT.strany.find((x) => x.cislo === cislo && x.id !== KONTEXT.strana);
  return { st, vlevo, cislo: vlevo ? KONTEXT.cislo - 1 : KONTEXT.cislo + 1 };
}

async function vykresliProtejsek() {
  const dvoj = el('dvojstrana');
  if (!dvoj) return;
  dvoj.querySelector('.protejsek')?.remove();
  for (const b of koren.querySelectorAll('[data-prikaz="jedna-strana"], [data-prikaz="dvojstrana"]')) {
    b.classList.toggle('aktivni', (b.dataset.prikaz === 'dvojstrana') === dvojstrana);
  }
  if (!dvojstrana || !KONTEXT) return;
  const verze = ++protejsekVerze;
  const p = protejsiStrana();
  const f = format();
  const sirka = f.sirka * MM_NA_PX, vyska = f.vyska * MM_NA_PX;
  const box = document.createElement('div');
  box.className = 'protejsek' + (p.st ? '' : ' zadna');
  box.style.width = sirka * meritko + 'px';
  box.style.height = vyska * meritko + 'px';
  if (p.vlevo) dvoj.insertBefore(box, dvoj.firstChild); else dvoj.appendChild(box);
  if (!p.st) return;
  box.dataset.napoveda = `Upravit stranu ${p.st.cislo}`;
  box.addEventListener('click', () => jdiNaStranu(p.st));
  const koncept = prectiKoncept(klicKonceptu({ strana: p.st.id }));
  let zdroj = koncept && koncept.zdroj;
  try {
    if (!zdroj && p.st.navrh) {
      const r = await fetch(`/api/navrh/strana/${p.st.navrh}`, { credentials: 'same-origin' });
      const d = await r.json();
      if (d.ok) zdroj = d.zdroj;
    }
  } catch (_) { /* protějšek je jen náhled - bez něj se dá pracovat dál */ }
  if (verze !== protejsekVerze) return;
  if (zdroj) {
    const k = vykresliStranu(zdroj, { cislo: p.st.cislo, strana: p.vlevo ? 'L' : 'P' });
    const fz = formatStrany(zdroj.nastaveni);
    k.style.transform = `scale(${(sirka * meritko) / (fz.sirka * MM_NA_PX)})`;
    k.style.transformOrigin = '0 0';
    box.appendChild(k);
    dokonciStranu(k, zdroj, meritko);
  } else if (p.st.obrazek) {
    const img = document.createElement('img');
    img.src = p.st.obrazek;
    img.alt = '';
    box.appendChild(img);
  }
  const c = document.createElement('span');
  c.className = 'protejsek-cislo';
  c.textContent = `${p.st.cislo}${koncept ? ' • rozdělaná' : ''}`;
  box.appendChild(c);
}

function nastavRezim(dvoj) {
  dvojstrana = dvoj;
  try { localStorage.setItem(KLIC_REZIMU, dvoj ? 'dvojstrana' : 'jedna'); } catch (_) { /* jen pohodlí */ }
  if (!meritkoRucne) nastavMeritko(); else vykresliProtejsek();
}

// Nejbližší strana před (-1) / za (1) touto. Nová strana ještě v seznamu
// není a její číslo má teď strana, která bude za ní.
function sousedniStrana(smer) {
  const ja = KONTEXT.cislo;
  const kandidati = KONTEXT.strany.filter((st) => (smer < 0 ? st.cislo < ja : (KONTEXT.strana ? st.cislo > ja : st.cislo >= ja)));
  if (!kandidati.length) return null;
  return smer < 0 ? kandidati[kandidati.length - 1] : kandidati[0];
}

let odchazi = false;
function jdiNa(adresa) {
  ulozKoncept();          // rozdělaná strana zůstane v prohlížeči
  odchazi = true;
  location.href = adresa;
}
function jdiNaStranu(st) { if (st) jdiNa(`/editor-strany/${st.id}`); }

// Odchod z editoru s rozdělanými stranami: zeptá se stejně jako editor zpěvníku;
// zavření záložky nebo jiný odkaz webu ohlídá prohlížeč (beforeunload).
if (KONTEXT) {
  window.addEventListener('beforeunload', (e) => {
    if (odchazi || (!neulozeno() && !jineKoncepty().length)) return;
    ulozKoncept();
    e.preventDefault();
    e.returnValue = '';
  });
  document.getElementById('zpet-do-zpevniku')?.addEventListener('click', async (e) => {
    const jine = jineKoncepty();
    if (!neulozeno() && !jine.length) return;
    e.preventDefault();
    const strany = (neulozeno() ? [{ cislo: KONTEXT.cislo }] : []).concat(jine).sort((a, b) => a.cislo - b.cislo);
    const volba = await window.showUnsavedConfirm(
      strany.length === 1 ? `Strana ${strany[0].cislo} má neuložené změny.` : `Neuložené změny jsou na stranách ${cislaStran(strany)}.`,
      { save: strany.length === 1 ? 'Uložit a odejít' : 'Uložit vše a odejít',
        discard: 'Odejít bez uložení' });
    if (volba === 'cancel') return;
    if (volba === 'save' && !(await ulozitStranu(true))) return;
    if (volba === 'discard') for (const k of koncepty()) smazKoncept(k.klic);
    odchazi = true;
    location.href = KONTEXT.zpet;
  });
  // Číslo strany jako ve čtečce: napsat a Enter (nebo odejít z pole).
  const pole = el('cislo-strany-zpevniku');
  const skoc = () => {
    const n = parseInt(pole.value, 10);
    const st = KONTEXT.strany.find((x) => x.cislo === n);
    if (st && st.id !== KONTEXT.strana) jdiNaStranu(st);
    else pole.value = String(KONTEXT.cislo);
  };
  pole?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); skoc(); } });
  pole?.addEventListener('change', skoc);
  pole?.addEventListener('focus', () => pole.select());
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

// Styl odstavce: všechny odstavce ve výběru dostanou styl celý - ruční zarovnání,
// odsazení, značka i přímé písmo, velikost a barva se zahodí, ať výběr s různým
// formátováním vypadá potom jednotně. Zvýraznění slov (tučné, kurzíva) zůstane.
function nastavStyl(id) {
  const odstavce = vybraneOdstavce(view.state);
  if (!odstavce.length || !knihovna[id]) return;
  const klic = klicStylu(id);
  zajistiVeStrane(klic);
  let tr = closeHistory(view.state.tr);
  for (const { node, pos } of odstavce) {
    tr = tr.setNodeMarkup(pos, null, { ...node.attrs, styl: klic, zarovnani: null, odsazeni: null, znacka: null });
    for (const t of [M.pismo, M.velikost, M.barva]) tr = tr.removeMark(pos + 1, pos + node.nodeSize - 1, t);
  }
  view.dispatch(tr);
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

// Formátování u kurzoru, nebo prvního textu výběru. $from.marks() by u výběru vrátilo
// formátování znaku PŘED výběrem - po prvním +/- by se velikost už neměnila.
function znackyVyberu(state) {
  const { empty, from, to, $from } = state.selection;
  if (empty) return state.storedMarks || $from.marks();
  let nalezene = null;
  state.doc.nodesBetween(from, to, (n) => {
    if (!nalezene && n.isText) nalezene = n.marks;
    return !nalezene;
  });
  return nalezene || $from.marks();
}

// Souhrn formátování výběru, jak se má ukázat v liště: hodnota, když je v celém výběru
// stejná, jinak null (lišta pak neukáže nic). Počítá se skutečný vzhled - přímé
// formátování, jinak styl odstavce.
function souhrnVyberu(state) {
  const { empty, from, to, $from } = state.selection;
  const vlastnosti = {
    pismo: [M.pismo, (m) => m.attrs.f], velikost: [M.velikost, (m) => m.attrs.v], barva: [M.barva, (m) => m.attrs.c],
    tucne: [M.tucne, () => true], kurziva: [M.kurziva, () => true], podtrzeni: [M.podtrzeni, () => true],
  };
  const hodnota = (klic, znacky, idStylu) => {
    const [typ, cti] = vlastnosti[klic];
    const m = znacky.find((z) => z.type === typ);
    return m ? cti(m) : styl(idStylu)[klic] || (['tucne', 'kurziva', 'podtrzeni'].includes(klic) ? false : styl(idStylu)[klic]);
  };
  const vysledek = {};
  const odstavce = vybraneOdstavce(state);
  const styly = new Set(odstavce.map((o) => o.node.attrs.styl));
  vysledek.styl = styly.size === 1 ? aktualniId([...styly][0]) : null;
  if (empty) {
    const p = $from.parent;
    const id = p.type === N.odstavec ? p.attrs.styl : 'normalni';
    const znacky = state.storedMarks || $from.marks();
    for (const k of Object.keys(vlastnosti)) vysledek[k] = hodnota(k, znacky, id);
    return vysledek;
  }
  const texty = [];
  state.doc.nodesBetween(from, to, (n, pos, rodic) => {
    if (n.isText) texty.push({ znacky: n.marks, styl: rodic.attrs.styl });
  });
  for (const k of Object.keys(vlastnosti)) {
    const hodnoty = new Set(texty.map((t) => hodnota(k, t.znacky, t.styl)));
    vysledek[k] = hodnoty.size === 1 ? [...hodnoty][0] : null;
  }
  return vysledek;
}

// Velikost písma u kurzoru (přímé formátování, jinak styl odstavce), v pt.
function velikostUKurzoru() {
  const { state } = view;
  const m = znackyVyberu(state).find((z) => z.type === M.velikost);
  if (m) return m.attrs.v;
  const p = state.selection.$from.parent;
  return styl(p.type === N.odstavec ? p.attrs.styl : 'normalni').velikost;
}

function krokVelikosti(smer) {
  const v = velikostUKurzoru();
  const dalsi = smer > 0 ? VELIKOSTI.find((x) => x > v) : [...VELIKOSTI].reverse().find((x) => x < v);
  if (dalsi) nastavZnacku(M.velikost, { v: dalsi });
}

function odsad(o) {
  upravOdstavce((n) => {
    const aktualni = n.attrs.odsazeni ?? styl(n.attrs.styl).odsazeni;
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
  if (zapnout && naplast?.aktivni) naplast.prepni(false);
  koren.classList.toggle('rezim-akordu', rezimAkordu);
  if (!rezimAkordu) skryjNabidkuSloky();
  aktualizujListu();
  ovladac.prekresliSpojnici();
}

// --- akordy podle předchozí sloky ------------------------------------------------------
// S nástrojem Akord se nad slokou (pod myší, jinak s kurzorem) objeví tlačítko: sloka bez
// akordů je dostane z nejbližší sloky s akordy (navrhy-akordu.js), sloka s akordy je
// může smazat. Celé jde vrátit jedním Zpět.

const nabidkaSloky = document.createElement('div');
nabidkaSloky.className = 'nabidka-sloky';
nabidkaSloky.hidden = true;
obal.appendChild(nabidkaSloky);
let slokaNabidky = null;

function skryjNabidkuSloky() {
  nabidkaSloky.hidden = true;
  slokaNabidky = null;
}

function slokaNaPozici(pos) {
  return slokyDokumentu(view.state.doc, role).find((s) => pos >= s.od && pos <= s.do) || null;
}

function ukazNabidkuSloky(sloka) {
  if (!rezimAkordu || !sloka) { skryjNabidkuSloky(); return; }
  const sloky = slokyDokumentu(view.state.doc, role);
  const tato = sloky.find((s) => s.od === sloka.od);
  if (!tato) { skryjNabidkuSloky(); return; }
  const vzor = tato.akordu ? null : vzorProSloku(sloky, tato);
  if (!tato.akordu && !vzor) { skryjNabidkuSloky(); return; }
  const klic = `${tato.od}:${tato.akordu}`;
  if (slokaNabidky === klic && !nabidkaSloky.hidden) return;
  slokaNabidky = klic;
  nabidkaSloky.textContent = '';
  const b = document.createElement('button');
  b.type = 'button';
  if (vzor) {
    const znacka = view.nodeDOM(vzor.od)?.dataset.znacka;
    b.textContent = vzor.role === 'refren' ? '♪ Doplnit akordy jako předchozí refrén'
      : `♪ Doplnit akordy jako ${znacka ? znacka + ' ' : 'předchozí '}sloka`;
    // Nabídka se schová před akcí: akce ji po změně dokumentu ukáže znovu (Smazat).
    b.addEventListener('click', () => {
      skryjNabidkuSloky();
      ovladac.blikni(doplnAkordy(view, tato, vzor));
      view.focus();
    });
  } else {
    b.textContent = 'Smazat akordy sloky';
    b.className = 'smazat';
    b.addEventListener('click', () => { skryjNabidkuSloky(); smazAkordy(view, tato); view.focus(); });
  }
  b.addEventListener('mousedown', (e) => e.preventDefault());
  nabidkaSloky.appendChild(b);
  // Nad pravým horním rohem prvního řádku sloky.
  const dom = view.nodeDOM(tato.od);
  const r = dom.getBoundingClientRect();
  const o = obal.getBoundingClientRect();
  nabidkaSloky.hidden = false;
  nabidkaSloky.style.left = Math.max(0, r.right - o.left - nabidkaSloky.offsetWidth) + 'px';
  nabidkaSloky.style.top = Math.max(0, r.top - o.top - nabidkaSloky.offsetHeight - 2) + 'px';
}

obal.addEventListener('pointermove', (e) => {
  if (!rezimAkordu || e.buttons) return;
  if (nabidkaSloky.contains(e.target)) return;
  const res = view.posAtCoords({ left: e.clientX, top: e.clientY });
  ukazNabidkuSloky(res ? slokaNaPozici(res.pos) : null);
});

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
  obrazek: () => el('soubor-obrazku').click(),
  naplast: () => {
    if (rezimAkordu) prepniRezimAkordu(false);
    naplast.prepni();
    if (naplast.aktivni) prepniZalozku('strana');
  },
  'pismo-mensi': () => krokVelikosti(-1),
  'pismo-vetsi': () => krokVelikosti(1),
  zpet: () => undo(view.state, view.dispatch),
  znovu: () => redo(view.state, view.dispatch),
  mensi: () => nastavMeritko(Math.max(0.3, Math.round((meritko - 0.1) * 10) / 10)),
  vetsi: () => nastavMeritko(Math.min(3, Math.round((meritko + 0.1) * 10) / 10)),
  'cela-strana': () => { meritkoRucne = false; nastavMeritko(); },
  nahled: otevriNahled,
  ulozit,
  'predchozi-strana': () => jdiNaStranu(sousedniStrana(-1)),
  'dalsi-strana': () => jdiNaStranu(sousedniStrana(1)),
  'jedna-strana': () => nastavRezim(false),
  dvojstrana: () => nastavRezim(true),
  'naplast-obdelnik': () => naplast.nastavDruh('obdelnik'),
  'naplast-volny': () => naplast.nastavDruh('volny'),
};

const BEZ_FOKUSU = ['mensi', 'vetsi', 'cela-strana', 'ramecek', 'obrazek', 'naplast', 'naplast-obdelnik', 'naplast-volny', 'nahled', 'ulozit', 'predchozi-strana', 'dalsi-strana', 'jedna-strana', 'dvojstrana'];

function napojListu() {
  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    // mousedown: tlačítko nesmí vzít editoru fokus a výběr.
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      PRIKAZY[b.dataset.prikaz]();
      if (!BEZ_FOKUSU.includes(b.dataset.prikaz)) view.focus();
    });
  }
  el('lista-styl').addEventListener('change', () => nastavStyl(el('lista-styl').value));
  const pismo = el('lista-pismo');
  for (const p of PISMA) pismo.add(new Option(p, p));
  pismo.addEventListener('change', () => nastavZnacku(M.pismo, { f: pismo.value }));
  // Velikost: napsat číslo a Enter (nebo opustit pole).
  const velikost = el('lista-velikost');
  const potvrdVelikost = () => {
    const v = parseFloat(velikost.value.replace(',', '.'));
    if (Number.isFinite(v) && v >= 4 && v <= 200 && v !== velikostUKurzoru()) nastavZnacku(M.velikost, { v });
    else aktualizujListu();
  };
  velikost.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); potvrdVelikost(); view.focus(); }
    if (e.key === 'Escape') { aktualizujListu(); view.focus(); }
  });
  velikost.addEventListener('change', potvrdVelikost);
  // Barva: výběr barvy pošle „change“ až při zavření - a když se zavře kliknutím do textu,
  // je výběr mezitím pryč. Barva se proto mění průběžně („input“) na výběr, který byl
  // označený při otevření.
  const barva = el('lista-barva');
  let vyberProBarvu = null;
  barva.closest('label').addEventListener('pointerdown', () => {
    const { from, to } = view.state.selection;
    vyberProBarvu = { from, to };
  });
  barva.addEventListener('input', () => {
    el('lista-barva-pruh').style.background = barva.value;
    const v = vyberProBarvu || view.state.selection;
    let tr = view.state.tr;
    if (v.from === v.to) tr = tr.addStoredMark(M.barva.create({ c: barva.value }));
    else tr = tr.addMark(v.from, v.to, M.barva.create({ c: barva.value }));
    view.dispatch(tr);
  });
  barva.addEventListener('change', () => { vyberProBarvu = null; view.focus(); });
  el('nahled-zavrit').addEventListener('click', () => { el('nahled-cteni').hidden = true; view.focus(); });
  el('nahled-png').addEventListener('click', obrazekNahledu);
  el('nahled-pdf').addEventListener('click', pdfNahledu);
  el('nahled-cteni').addEventListener('click', (e) => { if (e.target === el('nahled-cteni')) el('nahled-zavrit').click(); });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el('nahled-cteni').hidden) { e.stopPropagation(); el('nahled-zavrit').click(); }
  }, true);
  el('soubor-obrazku').addEventListener('change', (e) => {
    vlozSoubory(e.target.files);
    e.target.value = '';
  });
  // Přetahovaný soubor nad plochou: zvýraznit stranu.
  const plocha = el('navrh-plocha');
  plocha.addEventListener('dragover', (e) => {
    if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); plocha.classList.add('pretahovani'); }
  });
  plocha.addEventListener('dragleave', () => plocha.classList.remove('pretahovani'));
  plocha.addEventListener('drop', (e) => {
    plocha.classList.remove('pretahovani');
    if (e.defaultPrevented) return;
    // Pád vedle strany (mimo editor): vložit doprostřed.
    if (vlozSoubory(e.dataTransfer && e.dataTransfer.files)) e.preventDefault();
  });
}

// Lišta ukazuje skutečný vzhled u kurzoru: přímé formátování, jinak styl odstavce.
function aktualizujListu() {
  const { state } = view;
  const { $from } = state.selection;
  const p = $from.parent.type === N.odstavec ? $from.parent : null;
  const st = styl(p ? p.attrs.styl : 'normalni');
  const souhrn = souhrnVyberu(state);
  // Smíšený výběr: výběry zůstanou prázdné (prázdná volba), aby šla zvolit i hodnota,
  // kterou už část výběru má - a použila se na celý výběr.
  const nastavVyber = (prvek, hodnota) => {
    if (hodnota == null) hodnota = '';
    if (![...prvek.options].some((o) => o.value === String(hodnota))) {
      const o = new Option(String(hodnota).replace('.', ','), hodnota);
      if (hodnota === '') o.hidden = true;
      prvek.add(o);
    }
    prvek.value = String(hodnota);
  };
  nastavVyber(el('lista-styl'), souhrn.styl);
  nastavVyber(el('lista-pismo'), souhrn.pismo);
  if (document.activeElement !== el('lista-velikost')) {
    el('lista-velikost').value = souhrn.velikost == null ? '' : String(souhrn.velikost).replace('.', ',');
  }
  if (document.activeElement !== el('lista-barva') && souhrn.barva) el('lista-barva').value = souhrn.barva;
  el('lista-barva-pruh').style.background = souhrn.barva || 'repeating-linear-gradient(90deg, #d33 0 6px, #3a3 6px 12px, #36c 12px 18px)';
  oznacAktivniStyl(souhrn.styl);
  const zarovnani = p ? p.attrs.zarovnani || st.zarovnani : null;

  for (const b of koren.querySelectorAll('[data-prikaz]')) {
    const c = b.dataset.prikaz;
    if (c === 'akordy') b.classList.toggle('aktivni', rezimAkordu);
    else if (c === 'naplast') b.classList.toggle('aktivni', !!naplast?.aktivni);
    else if (c === 'naplast-obdelnik' || c === 'naplast-volny') b.classList.toggle('aktivni', naplast?.druh === c.slice(8));
    else if (c === 'tucne') b.classList.toggle('aktivni', souhrn.tucne === true);
    else if (c === 'kurziva') b.classList.toggle('aktivni', souhrn.kurziva === true);
    else if (c === 'podtrzeni') b.classList.toggle('aktivni', souhrn.podtrzeni === true);
    else if (c.startsWith('zarovnat-')) b.classList.toggle('aktivni', c === 'zarovnat-' + zarovnani);
    else if (c === 'zpet') b.disabled = undoDepth(state) === 0;
    else if (c === 'znovu') b.disabled = redoDepth(state) === 0;
  }

  // Aktivní rámeček (s kurzorem) ukazuje lištu a roh.
  const aktivni = $from.depth >= 1 ? view.nodeDOM($from.before(1)) : null;
  for (const r of view.dom.querySelectorAll('.ps-ramecek')) r.classList.toggle('aktivni', r === aktivni);

  // Kontextové karty: vybraný akord, vybraný obrázek.
  if (typeof prepniZalozku === 'function') zalozkaPodleVyberu(state);
  const sel = state.selection;
  const vybrany = sel instanceof NodeSelection && sel.node.type === N.akord ? sel : null;
  el('vybrany-akord').hidden = !vybrany;
  if (vybrany) el('akord-velikost').value = vybrany.node.attrs.vel || 100;
  const obrazek = sel instanceof NodeSelection && sel.node.type === N.obrazek ? sel.node : null;
  el('vybrany-obrazek').hidden = !obrazek;
  const tvar = sel instanceof NodeSelection && sel.node.type === N.tvar ? sel.node : null;
  // Karta náplasti: u vybrané náplasti, nebo když je nástroj zapnutý (druh a barva
  // nových náplastí).
  const nastroj = !!naplast?.aktivni;
  el('vybrany-tvar').hidden = !tvar && !nastroj;
  el('tvar-druh').hidden = !nastroj;
  el('tvar-smazat').hidden = !tvar;
  if (tvar && el('tvar-barva').value !== tvar.attrs.barva) { el('tvar-barva').value = tvar.attrs.barva; if (naplast) naplast.barva = tvar.attrs.barva; }
  if (obrazek) {
    for (const b of el('vybrany-obrazek').querySelectorAll('[data-vrstva]')) {
      b.classList.toggle('aktivni', b.dataset.vrstva === obrazek.attrs.vrstva);
    }
  }

  // Nabídka akordů u sloky s kurzorem (bez myši nad stranou).
  if (rezimAkordu && nabidkaSloky.hidden && state.selection.empty) ukazNabidkuSloky(slokaNaPozici(state.selection.from));
  else if (rezimAkordu && !nabidkaSloky.hidden && slokaNabidky) {
    // Po změně dokumentu (doplnění, smazání) se nabídka přepočítá.
    const [od] = slokaNabidky.split(':').map(Number);
    const s2 = slokaNaPozici(od + 1);
    slokaNabidky = null;
    ukazNabidkuSloky(s2);
  }

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
  if (p.type !== N.odstavec || !['sloka', 'refren'].includes(role(p.attrs.styl)) || !p.content.size) return null;
  const ramecek = $from.node(-1);
  let i = $from.index(-1);
  while (i > 0) {
    const pred = ramecek.child(i - 1);
    if (role(pred.attrs.styl) !== role(p.attrs.styl) || !pred.content.size) break;
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
  for (const b of el('vybrany-obrazek').querySelectorAll('[data-vrstva]')) {
    b.addEventListener('click', () => {
      const sel = view.state.selection;
      if (!(sel instanceof NodeSelection) || sel.node.type !== N.obrazek) return;
      const tr = closeHistory(view.state.tr).setNodeMarkup(sel.from, null, { ...sel.node.attrs, vrstva: b.dataset.vrstva });
      tr.setSelection(NodeSelection.create(tr.doc, sel.from));
      view.dispatch(tr);
    });
  }
  // Náplast: barva (input při tahu jen ukazuje, change uloží jedním krokem zpět).
  const upravTvar = (barva, zapsat) => {
    const sel = view.state.selection;
    if (!(sel instanceof NodeSelection) || sel.node.type !== N.tvar) return;
    if (!zapsat) { view.nodeDOM(sel.from).querySelector('polygon')?.setAttribute('fill', barva); return; }
    const tr = closeHistory(view.state.tr).setNodeMarkup(sel.from, null, { ...sel.node.attrs, barva });
    tr.setSelection(NodeSelection.create(tr.doc, sel.from));
    view.dispatch(tr);
  };
  el('tvar-barva').addEventListener('input', (e) => { naplast.barva = e.target.value; upravTvar(e.target.value, false); });
  el('tvar-barva').addEventListener('change', (e) => { naplast.barva = e.target.value; upravTvar(e.target.value, true); });
  el('tvar-smazat').addEventListener('click', () => {
    const sel = view.state.selection;
    if (sel instanceof NodeSelection && sel.node.type === N.tvar) view.dispatch(view.state.tr.deleteSelection());
    view.focus();
  });
  el('obrazek-smazat').addEventListener('click', () => {
    const sel = view.state.selection;
    if (sel instanceof NodeSelection && sel.node.type === N.obrazek) view.dispatch(view.state.tr.deleteSelection());
    view.focus();
  });
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
  el('zacit-znovu')?.addEventListener('click', async () => {
    const ok = await window.showConfirm('Rozpracovaná strana se smaže a začne se s prázdnou.',
      'Smazat stranu', { nebezpecne: true });
    if (!ok) return;
    nastaveni = structuredClone(VYCHOZI);
    view.updateState(EditorState.create({ doc: prazdnyDokument(format()), plugins: view.state.plugins }));
    const tr = normalizujStyly(view.state);
    if (tr) view.updateState(view.state.apply(tr));
    nactiPanel();
    meritkoRucne = false;
    pouzijNastaveni();
    zalohuj();
    aktualizujListu();
  });
}

// --- náhled čtení -------------------------------------------------------------------
// Strana tak, jak ji uvidí čtečka: vykreslená mimo editor (vykresleni.js) ze zdroje,
// s číslem strany podle náhledu. Tlačítko PNG vyrobí obrázek pro PDF a změří čas.

function otevriNahled() {
  const okno = el('nahled-cteni');
  const misto = el('nahled-cteni-strana');
  misto.textContent = '';
  const zdroj = zdrojStrany();
  const koren = vykresliStranu(zdroj, { cislo: nastaveni.nahledCisla, strana: nastaveni.nahledStrany });
  okno.hidden = false;
  const f = format();
  const k = Math.min((misto.clientWidth - 8) / (f.sirka * MM_NA_PX), (misto.clientHeight - 8) / (f.vyska * MM_NA_PX));
  const obal2 = document.createElement('div');
  obal2.className = 'nahled-cteni-obal';
  obal2.style.width = f.sirka * MM_NA_PX * k + 'px';
  obal2.style.height = f.vyska * MM_NA_PX * k + 'px';
  koren.style.transform = `scale(${k})`;
  obal2.appendChild(koren);
  misto.appendChild(obal2);
  dokonciStranu(koren, zdroj, k);
  el('nahled-cteni-info').textContent = '';
}

async function pdfNahledu() {
  const info = el('nahled-cteni-info');
  info.textContent = 'Vyrábí se PDF…';
  try {
    const { pdfStrany } = await import(new URL('./pdf-strany.js', import.meta.url).href);
    const { bytes, ms } = await pdfStrany(zdrojStrany(), { cislo: nastaveni.nahledCisla, strana: nastaveni.nahledStrany });
    info.textContent = `PDF: ${ms} ms, ${Math.round(bytes.length / 1024)} kB`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    a.download = 'strana.pdf';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (chyba) {
    info.textContent = '';
    window.oznam(String(chyba.message || chyba), { nadpis: 'PDF strany se nevytvořilo', druh: 'chyba' });
  }
}

async function obrazekNahledu() {
  const info = el('nahled-cteni-info');
  info.textContent = 'Vyrábí se obrázek…';
  try {
    const { blob, ms } = await obrazekStrany(zdrojStrany(), { cislo: nastaveni.nahledCisla, strana: nastaveni.nahledStrany });
    info.textContent = `Obrázek 1748 px: ${ms} ms, ${Math.round(blob.size / 1024)} kB`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'strana.png';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (chyba) {
    info.textContent = '';
    window.oznam(String(chyba.message || chyba), { nadpis: 'Obrázek strany se nevytvořil', druh: 'chyba' });
  }
}

// --- záložky panelu -------------------------------------------------------------------

const KLIC_ZALOZKY = 'navrh-strany-zalozka';
function prepniZalozku(k) {
  for (const b of koren.querySelectorAll('[data-zalozka]')) {
    b.classList.toggle('aktivni', b.dataset.zalozka === k);
    b.setAttribute('aria-selected', String(b.dataset.zalozka === k));
  }
  for (const p of koren.querySelectorAll('[data-panel]')) p.hidden = p.dataset.panel !== k;
  try { localStorage.setItem(KLIC_ZALOZKY, k); } catch (_) { /* jen pohodlí */ }
}
for (const b of koren.querySelectorAll('[data-zalozka]')) {
  b.addEventListener('mousedown', (e) => e.preventDefault());
  b.addEventListener('click', () => prepniZalozku(b.dataset.zalozka));
}
// Výběr akordu nebo obrázku přepne na jeho záložku (jen při nově vybraném uzlu, ne při
// každé změně - jinak by panel přeskakoval pod rukama).
let posledniVybrany = null;
function zalozkaPodleVyberu(state) {
  const sel = state.selection;
  const uzel = sel instanceof NodeSelection ? sel.node : null;
  if (uzel === posledniVybrany) return;
  posledniVybrany = uzel;
  if (uzel && uzel.type === N.akord) prepniZalozku('akordy');
  else if (uzel && (uzel.type === N.obrazek || uzel.type === N.tvar)) prepniZalozku('strana');
}

// --- start ----------------------------------------------------------------------------

// Start: styly strany na verze z knihovny; kopie stylů, které strana nepoužívá, pryč.
{
  const tr = normalizujStyly(view.state);
  if (tr) view.updateState(view.state.apply(tr));
  const pouzite = new Set();
  view.state.doc.descendants((n) => { if (n.type === N.odstavec) pouzite.add(n.attrs.styl); return true; });
  nastaveni = { ...nastaveni, styly: Object.fromEntries(Object.entries(nastaveni.styly).filter(([k]) => pouzite.has(k))) };
}

// Nástroj Náplast (naplast.js): kreslí do strany, dokud je zapnutý.
naplast = new NastrojNaplasti({ view, stranka, koren, meritko: () => meritko, poZmene: () => aktualizujListu() });
// Esc a Enter i mimo editor textu (po kliknutí do strany fokus v textu být nemusí).
window.addEventListener('keydown', (e) => {
  if (!naplast.aktivni) return;
  if ((e.key === 'Escape' && naplast.esc()) || (e.key === 'Enter' && naplast.enter())) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);
napojPanel();
napojListu();
napojPanelAkordu();
nastavVysku();
prepniZalozku((() => { try { return localStorage.getItem(KLIC_ZALOZKY) || 'text'; } catch (_) { return 'text'; } })());
pouzijNastaveni();
aktualizujListu();
window.addEventListener('resize', () => { nastavVysku(); if (!meritkoRucne) nastavMeritko(); });
view.focus();
// Načtený koncept je rozdělaná úprava: strana je od začátku neuložená.
otiskPriNacteni = pouzitKoncept ? null : otiskStrany();
obnovListovani();

// Pro testy a ladění.
// nahradTextem: test začíná od známého textu, ne od ukázkové písně.
window.navrhStrany = {
  view, schema, TextSelection, nastaveni: () => nastaveni, knihovna: () => knihovna, meritko: () => meritko, prepniRezimAkordu,
  cislo: () => cisloStrany,
  zdroj: () => zdrojStrany(),
  zmerZlomy,
  vykresliStranu, obrazekStrany,
  nahradTextem(nazev, autor, text) {
    const f = format();
    const doc = N.doc.create(null, N.ramecek.create(
      { x: f.okraj, y: f.okraj, w: f.sirka - 2 * f.okraj, h: f.vyska - 2 * f.okraj },
      [N.odstavec.create({ styl: 'nadpis' }, schema.text(nazev)),
        N.odstavec.create({ styl: 'autor' }, schema.text(autor)), ...odstavceZTextu(text)],
    ));
    view.updateState(EditorState.create({ doc, plugins: view.state.plugins }));
    const tr = normalizujStyly(view.state);
    if (tr) view.updateState(view.state.apply(tr));
    pouzijNastaveni();
    aktualizujListu();
    naplanujPrepocet();
    zalohuj();            // jako každá úprava (záloha / koncept strany zpěvníku)
  },
};
