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
import { schema, ROLE, VERZE_SCHEMATU, znamaVerze, dokumentZeZdroje, poradiVrstev, nastavVrstvy, zNahore, prazdnyDokument, prazdnaStrana, odstavceZTextu, textZUzlu } from './schema.js';
import { AkordView, OvladacAkordu, pluginAkordu, akordyPoMazani } from './akord-uzel.js';
import { PrvekView, nactiObrazek, vlozObrazek } from './ramecky.js';
import { pluginSlok, klicSlok, znackySlok } from './sloky.js';
import { slokyDokumentu, vzorProSloku, doplnAkordy, smazAkordy } from './navrhy-akordu.js';
import { CisloStrany } from './cislo-strany.js';
import { FORMATY, cssStylu, nastavAkordy, vykresliStranu, dokonciStranu, obrazekStrany, formatStrany } from './vykresleni.js';
import { prepocitej } from './kolize.js';
import { NastrojNaplasti, NastrojTextu } from './naplast.js';
import { transponuj } from './akordy.js';

const N = schema.nodes;
const M = schema.marks;
// --- dvojstrana k úpravě (pomocné funkce; stav je `spoj`) ---------------------------------
function plocha() { const f = format(); return spoj ? { ...f, sirka: 2 * f.sirka } : f; }
function posunStrany(lp) { return spoj && lp === 'P' ? format().sirka : 0; }
function posunAktualni() { return KONTEXT ? posunStrany(KONTEXT.lp) : 0; }
// Na které straně prvek leží (podle středu). Mimo dvojstranu vždy na upravované.
function stranaUzlu(n) {
  if (!spoj) return KONTEXT ? KONTEXT.lp : 'P';
  return n.attrs.x + (n.attrs.w || 0) / 2 >= format().sirka ? 'P' : 'L';
}
// Obrázek nebo náplast přes hřbet patří oběma stranám: každá si z něj ořízne svou část
// (levá s přesahem doprava, pravá se záporným x). Textový rámeček se dělit nedá - ten
// patří straně podle středu.
// orezatPresahy: autor při uložení zvolil Oříznout - prvek přes hřbet zůstane jen na
// straně podle středu (druhá část se ořízne). Platí, dokud se při dalším uložení
// nerozhodne jinak.
let orezatPresahy = false;
function prekracujeHrbet(n) {
  const s = format().sirka;
  return !!spoj && n.attrs.x < s - 0.05 && n.attrs.x + n.attrs.w > s + 0.05;
}
function presHrbet(n) {
  if (!spoj || orezatPresahy || (n.type !== N.obrazek && n.type !== N.tvar && n.type !== N.ramecek)) return false;
  const s = format().sirka;
  return n.attrs.x < s - 0.05 && n.attrs.x + n.attrs.w > s + 0.05;
}
// Tentýž prvek přes hřbet uložený na obou stranách: na plátně jen jednou. U rámečku
// stačí stejný text (kopie má v odstavcích navíc pevné značky slok).
function stejnyPresHrbet(a, b) {
  if (a.type !== b.type) return false;
  const bez = ({ x, presah, ...r }) => JSON.stringify(r);
  if (Math.abs(a.attrs.x - b.attrs.x) >= 0.05 || bez(a.attrs) !== bez(b.attrs)) return false;
  if (a.type === N.ramecek) return a.childCount === b.childCount && a.textContent === b.textContent;
  return a.type === N.obrazek || a.type === N.tvar;
}

// Kopie rámečku přes hřbet pro stranu, kde nemá střed: presah a značky slok z plátna
// (na téhle straně by se jinak číslovaly jinak - viz znackySlok).
function kopiePresahu(u, off) {
  const znacky = new Map(znackySlok(view.state.doc, role, stranaUzlu).znacky.map((z) => [z.pos, z.znacka]));
  const odstavce = [];
  u.forEach((p, offP) => {
    const z = znacky.get(off + 1 + offP);
    odstavce.push(z != null ? p.type.create({ ...p.attrs, znacka: z }, p.content, p.marks) : p);
  });
  return u.type.create({ ...u.attrs, presah: true }, odstavce, u.marks);
}
function posunUzel(n, dx) {
  return dx ? n.type.create({ ...n.attrs, x: Math.round((n.attrs.x + dx) * 100) / 100 }, n.content, n.marks) : n;
}

// Strana zpěvníku (/editor-strany/<id strany> nebo /nova?zpevnik=&za=): kde strana je
// a kam se uloží - { zpevnik, nazev, strana, za, navrh, cislo, lp, zpet }. Bez něj je to
// samostatná pokusná stránka se zálohou v prohlížeči.
const KONTEXT = (() => {
  try { return JSON.parse(document.getElementById('kontext-strany')?.textContent || 'null'); } catch (_) { return null; }
})();
// Režim plochy u strany zpěvníku: jedna strana, nebo dvojstrana s protější stranou.
// Tady nahoře - nastavMeritko ho čte už při startu.
// Ukázka pro účet bez prémia (navrh_strany.html, ukazka): editor nad ukázkovou stranou,
// bez ukládání, obrázků a PDF.
const UKAZKA = document.documentElement.classList.contains('navrh-ukazka');
const KLIC_REZIMU = 'navrh-rezim-stran';
let naplast = null;          // nástroj Náplast (naplast.js), vznikne po editoru
let vybranoAltem = null;     // pozice prvku vybraného Alt+klikem (tažení pod jiným prvkem)
let nastrojTextu = null;     // nástroj Text (naplast.js)
// Výběr mimo ProseMirror: 'nic' (nic nevybráno - po otevření, klik na prázdné místo, Esc)
// nebo 'cislo' (vybrané číslo strany). ProseMirror nějaký výběr mít musí (jinak by po
// otevření byla vybraná první náplast) - editor je pak bez fokusu a výběr se neukazuje.
let zvlastniVyber = 'nic';
// Dvojstrana k úpravě: { druha: { st, cislo, lp, navrh, nastaveni, koncept, otisk } }.
// Na plátně je jeden dokument přes obě strany, prvky pravé strany mají x posunuté
// o šířku strany; při uložení (a v konceptech) se rozdělí podle středu prvku.
let spoj = null;
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
let casovacPripravy = null;     // krátká zpráva ve stavovém řádku (připravuje se, zamčeno)


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
  if (UKAZKA) return;            // ukázka nic neukládá (server by stejně odmítl)
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
// Strana zpěvníku s navrženou verzí: zdroj verze ze serveru. Bez verze nová strana.
async function nactiVerzi(id) {
  const r = await fetch(`/api/navrh/strana/${id}`, { credentials: 'same-origin' });
  const d = await r.json();
  if (!d.ok) throw new Error(d.error || 'Stranu se nepodařilo načíst.');
  return { nastaveni: sluc(VYCHOZI, d.zdroj.nastaveni || {}), doc: dokumentZeZdroje(d.zdroj) };
}

// Každá úprava: rozdělaná strana do prohlížeče (koncept, viz ulozKoncept).
function zalohuj() { naplanujKoncept(); }

// Zdroj strany v tom tvaru, v jakém se uloží na server. `zlomy` jsou místa, kde řádky
// zalomil prohlížeč autora - čtečka a PDF je převezmou (vykresleni.js), protože jiný
// prohlížeč by asi 1 % odstavců zalomil o slovo jinak (Safari a Chrome měří text o pixel
// jinak). Editor sám zalamuje dál živě.
// Část dokumentu jedné strany (ve dvojstraně): prvky té strany posunuté zpátky na její
// souřadnice a pro každý odstavec jeho pořadí v celém dokumentu (kvůli zlomům).
function castStrany(lp) {
  const doc = view.state.doc;
  if (!spoj) return { doc, indexy: null };
  const uzly = [], indexy = [];
  let odst = 0;
  doc.forEach((n, off) => {
    let pocet = 0;
    n.descendants((d) => { if (d.type === N.odstavec) pocet += 1; return true; });
    const moje = stranaUzlu(n) === lp;
    if (moje || presHrbet(n)) {
      const u = posunUzel(n, -posunStrany(lp));
      uzly.push(!moje && n.type === N.ramecek ? kopiePresahu(u, off) : u);
      for (let i = 0; i < pocet; i++) indexy.push(odst + i);
    }
    odst += pocet;
  });
  return { doc: N.doc.create(null, uzly), indexy };
}

// Nastavení strany: ve dvojstraně má protější strana vlastní číslo (poloha, skrytí),
// ostatní (styly, akordy, formát) je společné - na plátně vypadají obě stejně.
function nastaveniStrany(lp) {
  if (!spoj || !KONTEXT || lp === KONTEXT.lp) return nastaveni;
  const d = spoj.druha;
  return { ...nastaveni, cislo: d.nastaveni.cislo, cisloSkryte: d.nastaveni.cisloSkryte, nahledCisla: d.cislo, nahledStrany: d.lp };
}

function zdrojStrany(lp = KONTEXT ? KONTEXT.lp : 'P') {
  const { doc, indexy } = castStrany(lp);
  // Jen kopie stylů, které strana opravdu používá.
  const pouzite = new Set();
  doc.descendants((n) => { if (n.type === N.odstavec) pouzite.add(n.attrs.styl); return true; });
  const n = nastaveniStrany(lp);
  const styly = Object.fromEntries(Object.entries(n.styly).filter(([k]) => pouzite.has(k)));
  let zlomy = zmerZlomy(view.dom);
  if (indexy) {
    const nove = {};
    indexy.forEach((g, i) => { if (g >= 0 && zlomy[g]) nove[i] = zlomy[g]; });
    zlomy = nove;
  }
  return { verze: VERZE_SCHEMATU, nastaveni: { ...n, styly }, obsah: doc.toJSON(), zlomy };
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
  // Pořadí vrstev u vybraného obrázku nebo náplasti (v textu mají šipky s Ctrl svůj
  // význam - rámeček jde přes tlačítka v panelu).
  'Mod-d': () => { duplikuj(); return true; },
  'Mod-s': () => { if (KONTEXT && !UKAZKA) ulozit(); return true; },
  'Mod-ArrowUp': () => vybranyAtom(view.state) && (zmenVrstvu('dopredu'), true),
  'Mod-ArrowDown': () => vybranyAtom(view.state) && (zmenVrstvu('dozadu'), true),
  'Mod-Shift-ArrowUp': () => vybranyAtom(view.state) && (zmenVrstvu('popredi'), true),
  'Mod-Shift-ArrowDown': () => vybranyAtom(view.state) && (zmenVrstvu('pozadi'), true),
  Escape: () => { if (rezimAkordu) prepniRezimAkordu(false); else zrusVyber(); return true; },
  // Zamčený prvek klávesou nesmazat (odemyká zámek v liště nebo na prvku).
  Delete: (state) => state.selection instanceof NodeSelection && jeZamceny(state.selection.node),
  Backspace: (state) => state.selection instanceof NodeSelection && jeZamceny(state.selection.node),
});

function jeZamceny(node) {
  if (!node.attrs.zamceno) return false;
  // Krátce ve stavovém řádku (okno by zdržovalo) - stejně jako připravované funkce.
  clearTimeout(casovacPripravy);
  const popis = el('navrh-popis');
  napisPopis(popis, 'Zamčeno', 'smazat jde po odemčení (zámek v liště nebo na prvku)');
  popis.classList.add('pripravuje-zprava');
  casovacPripravy = setTimeout(aktualizujListu, 3500);
  return true;
}

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

let zaloha = null;
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
  if (k && k.zdroj && znamaVerze(k.zdroj.verze)) {
    pouzitKoncept = (k.zaklad || null) === (KONTEXT.navrh || null)
      || await window.showConfirm('Strana má rozdělanou úpravu, ale mezitím byla uložena jinak. '
        + 'Pokračovat v rozdělané úpravě? Jinak se zahodí.', 'Pokračovat v úpravě');
    if (pouzitKoncept) zaloha = { nastaveni: sluc(VYCHOZI, k.zdroj.nastaveni || {}), doc: dokumentZeZdroje(k.zdroj) };
    else smazKoncept(klicKonceptu(KONTEXT));
  }
}
// Strana s nahraným obrázkem (sken), dosud nenavržená: obrázek je podkladem přes celou
// stranu (za textem); text a akordy se dopíšou do rámečku přidaného tlačítkem Text.
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

// presah: jak daleko za plátno (mm) smí prvek - zhruba šířka strany na každou stranu.
const prvek = (n, v, g) => new PrvekView(n, v, g, { meritko: () => meritko, rozmer: plocha, presah: () => format().sirka });

// Protější strana pro úpravu ve dvojstraně: rozdělaná z prohlížeče (ke stejné verzi),
// uložená verze, sken jako podklad, nebo prázdná strana - stejně jako při otevření samotné.
async function nactiProtejsek(p) {
  const k = prectiKoncept(klicKonceptu({ strana: p.st.id }));
  if (k && k.zdroj && znamaVerze(k.zdroj.verze) && (k.zaklad || null) === (p.st.navrh || null)) {
    return { nastaveni: sluc(VYCHOZI, k.zdroj.nastaveni || {}), doc: dokumentZeZdroje(k.zdroj), koncept: true };
  }
  try {
    if (p.st.navrh) return { ...(await nactiVerzi(p.st.navrh)), koncept: false };
    if (p.st.obrazek) return { nastaveni: { ...nastaveni }, doc: await dokumentSeSkenem(p.st.obrazek), koncept: false };
  } catch (_) {
    return null;
  }
  return { nastaveni: { ...nastaveni }, doc: prazdnaStrana(format()), koncept: false };
}

async function dokumentSeSkenem(src) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const f = format();
  const k = Math.min(f.sirka / img.naturalWidth, f.vyska / img.naturalHeight);
  const w = Math.round(img.naturalWidth * k * 10) / 10, h = Math.round(img.naturalHeight * k * 10) / 10;
  return N.doc.create(null, [
    N.obrazek.create({ src, x: Math.round((f.sirka - w) * 5) / 10, y: Math.round((f.vyska - h) * 5) / 10, w, h, podklad: true }),
  ]);
}

function vybranyAtom(state) {
  const sel = state.selection;
  return sel instanceof NodeSelection && (sel.node.type === N.obrazek || sel.node.type === N.tvar);
}

// --- vrstvy ---------------------------------------------------------------------------
// Vrstva prvku je jeho `z` (schema.js, poradiVrstev) - pořadí uzlů je pořadí čtení.

// Prvek s výběrem: vybraný obrázek nebo náplast, nebo rámeček s kurzorem.
// { index, pos, node } nebo null.
function prvekVyberu(state) {
  const sel = state.selection;
  if (sel instanceof NodeSelection && sel.$from.depth === 0) return { index: sel.$from.index(0), pos: sel.from, node: sel.node };
  if (sel.$from.depth < 1 || sel.$from.index(0) !== sel.$to.index(0)) return null;
  return { index: sel.$from.index(0), pos: sel.$from.before(1), node: sel.$from.node(1) };
}

const prekryva = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

// Nové pořadí vrstev (indexy odspodu nahoru) po příkazu, nebo null, když by se nic
// nezměnilo. O vrstvu výš/níž: přes nejbližší prvek, který se s tímhle překrývá -
// přeskočit prvek jinde na straně by na pohled nic neudělalo.
function poradiPoPrikazu(doc, k, kam) {
  if (doc.child(k).attrs.podklad) return null;
  const poradi = poradiVrstev(doc).filter((i) => !doc.child(i).attrs.podklad);
  const i = poradi.indexOf(k);
  const a = doc.child(k).attrs;
  let cil = null;
  if (kam === 'popredi') cil = i < poradi.length - 1 ? poradi.length - 1 : null;
  else if (kam === 'pozadi') cil = i > 0 ? 0 : null;
  else if (kam === 'dopredu') {
    for (let j = i + 1; j < poradi.length && cil == null; j++) if (prekryva(a, doc.child(poradi[j]).attrs)) cil = j;
  } else if (kam === 'dozadu') {
    for (let j = i - 1; j >= 0 && cil == null; j--) if (prekryva(a, doc.child(poradi[j]).attrs)) cil = j;
  }
  if (cil == null) return null;
  poradi.splice(i, 1);
  poradi.splice(cil, 0, k);
  return poradi;
}

function zmenVrstvu(kam) {
  const { state } = view;
  const p = prvekVyberu(state);
  const poradi = p && poradiPoPrikazu(state.doc, p.index, kam);
  if (!poradi) return false;
  const tr = nastavVrstvy(closeHistory(state.tr), poradi);
  if (state.selection instanceof NodeSelection) tr.setSelection(NodeSelection.create(tr.doc, state.selection.from));
  view.dispatch(tr);
  view.focus();
  return true;
}

// Prvky pod bodem obrazovky, odshora dolů (i podklad, i prvek pod jiným). Náplast jen
// svou plochou, ne prázdnými rohy obdélníku kolem.
function prvkyPodBodem(x, y) {
  const doc = view.state.doc;
  const pozice = [];
  doc.forEach((_, pos) => pozice.push(pos));
  return poradiVrstev(doc).reverse().map((k) => ({ index: k, pos: pozice[k], node: doc.child(k), dom: view.nodeDOM(pozice[k]) }))
    .filter(({ node, dom }) => {
      if (!dom) return false;
      const r = dom.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) return false;
      if (node.attrs.otoceni && node.type !== N.tvar) {
        // Otočený prvek: bod v jeho osách (kolem středu) proti jeho rozměru.
        const k = MM_NA_PX * meritko, u = -node.attrs.otoceni * Math.PI / 180;
        const dx = x - (r.left + r.right) / 2, dy = y - (r.top + r.bottom) / 2;
        const lx = dx * Math.cos(u) - dy * Math.sin(u), ly = dx * Math.sin(u) + dy * Math.cos(u);
        return Math.abs(lx) <= node.attrs.w * k / 2 && Math.abs(ly) <= node.attrs.h * k / 2;
      }
      if (node.type !== N.tvar) return true;
      // Přes matici SVG - platí i pro otočenou náplast.
      const poly = dom.querySelector('polygon');
      const svg = poly.ownerSVGElement;
      const bod = svg.createSVGPoint();
      bod.x = x;
      bod.y = y;
      return poly.isPointInFill(bod.matrixTransform(svg.getScreenCTM().inverse()));
    });
}

// Ukázka pro účet bez prémia: strana, která předvede, co editor umí - styly a formátování,
// víc textových rámečků, obrázek a náplasti (i neobdélníkové a přes sebe).
function ukazkovyDokument() {
  const f = format();
  const pisen = prazdnyDokument(f).firstChild;
  const poznamka = N.odstavec.create({ styl: 'normalni' }, schema.text('Capo 2, hrát volně a potichu',
    [M.pismo.create({ f: 'Caveat' }), M.velikost.create({ v: 14 }), M.barva.create({ c: '#b00020' })]));
  // Náplasti nad logem: obrázek náplasti ze tří polygonů přes sebe (později vložená leží
  // výš) - pásek se zkosenými rohy, polštářek a srdce, celé natočené. Body jsou v mm
  // vůči středu náplasti.
  const tvar = (body, barva) => {
    const xs = body.map((b) => b[0]), ys = body.map((b) => b[1]);
    const x = Math.min(...xs), y = Math.min(...ys), w = Math.max(...xs) - x, h = Math.max(...ys) - y;
    return N.tvar.create({ x, y, w, h, barva,
      body: body.map(([bx, by]) => `${((bx - x) / w).toFixed(4)},${((by - y) / h).toFixed(4)}`).join(' ') });
  };
  const sx = 115, sy = 166, uhel = -18 * Math.PI / 180;
  const natoc = (body) => body.map(([bx, by]) => [sx + bx * Math.cos(uhel) - by * Math.sin(uhel),
    sy + bx * Math.sin(uhel) + by * Math.cos(uhel)]);
  return N.doc.create(null, [
    tvar(natoc([[-19, -7], [19, -7], [22, -4], [22, 4], [19, 7], [-19, 7], [-22, 4], [-22, -4]]), '#e8c9a0'),
    tvar(natoc([[-6, -6], [6, -6], [6, 6], [-6, 6]]), '#ffffff'),
    tvar(natoc([[0, -2], [1.2, -3.4], [2.8, -3.4], [4, -2.2], [4, -0.6], [0, 3.6],
      [-4, -0.6], [-4, -2.2], [-2.8, -3.4], [-1.2, -3.4]]), '#d6453d'),
    // Rámeček na výšku textu (končí ~119 mm) - prázdná plocha by kryla prvky pod ním.
    N.ramecek.create({ x: f.okraj, y: 8, w: f.sirka - 2 * f.okraj, h: 116 }, pisen.content),
    N.ramecek.create({ x: f.okraj, y: 170, w: 70, h: 14 }, poznamka),
    N.obrazek.create({ src: document.getElementById('logo-ukazky').dataset.src, x: f.sirka - f.okraj - 48, y: 182, w: 48, h: 13.9 }),
  ]);
}

// Dvojstrana k úpravě: jen u uložené strany s protější stranou stejného formátu (jinak
// zůstane protější strana jen k náhledu).
let docStart = zaloha ? zaloha.doc : (UKAZKA ? ukazkovyDokument() : KONTEXT ? prazdnaStrana(format()) : prazdnyDokument(format()));
if (dvojstrana && KONTEXT && KONTEXT.strana) {
  const p = protejsiStrana();
  const druha = p && p.st ? await nactiProtejsek(p) : null;
  if (druha && (druha.nastaveni.format || 'A5') === (nastaveni.format || 'A5')) {
    spoj = { druha: { st: p.st, cislo: p.st.cislo, lp: p.vlevo ? 'L' : 'P', navrh: p.st.navrh || null,
                      nastaveni: druha.nastaveni, koncept: druha.koncept, otisk: null } };
    // Styly obou stran na plátně (klíč „id-verze“ je u stejné verze stejný).
    nastaveni = { ...nastaveni, styly: { ...druha.nastaveni.styly, ...nastaveni.styly } };
    const uzly = [];
    const pridej = (d, lp) => d.forEach((n) => {
      const u = posunUzel(n, lp === 'P' ? format().sirka : 0);
      // Prvek přes hřbet je uložený na obou stranách - na plátně jen jednou (originál,
      // ne kopie s pevnými značkami).
      const i = uzly.findIndex((v) => stejnyPresHrbet(v, u));
      if (i < 0) uzly.push(u);
      else if (uzly[i].attrs.presah && !u.attrs.presah) uzly[i] = u;
    });
    if (spoj.druha.lp === 'L') { pridej(druha.doc, 'L'); pridej(docStart, 'P'); }
    else { pridej(docStart, 'L'); pridej(druha.doc, 'P'); }
    // Na plátně je každý rámeček celý a upravitelný (i kopie, ke které se originál nenašel).
    docStart = N.doc.create(null, uzly.map((u) => (u.type === N.ramecek && u.attrs.presah
      ? u.type.create({ ...u.attrs, presah: false }, u.content, u.marks) : u)));
  }
}

const view = new EditorView(stranka.querySelector('.stranka-mount'), {
  state: EditorState.create({
    doc: docStart,
    plugins: [
      pluginAkordu(ovladac), pluginSlok({ role, stranaPrvku: (n) => stranaUzlu(n) }), history(), klavesy, keymap(baseKeymap),
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
  // Vybraný obrázek nebo náplast: psaní a vložení textu nic nedělá. Jinak by ProseMirror
  // prvek nahradil textem v novém rámečku (vlevo nahoře, výchozí poloha).
  handleTextInput: (v) => vybranyAtom(v.state),
  handlePaste: (v, e) => vlozSoubory(e.clipboardData && e.clipboardData.files) || vybranyAtom(v.state),
  handleDrop: (v, e) => vlozSoubory(e.dataTransfer && e.dataTransfer.files, e),
  dispatchTransaction(tr) {
    const blik = akordyPoMazani(tr);
    view.updateState(view.state.apply(tr));
    if (vybranoAltem != null) {
      // Prvek vybraný Alt+klikem drží přednost, dokud je vybraný (i po přesunu).
      const pos = tr.mapping.map(vybranoAltem);
      const s = view.state.selection;
      vybranoAltem = s instanceof NodeSelection && s.from === pos ? pos : null;
    }
    if (tr.docChanged) zalohuj();
    if (blik.length) ovladac.blikni(blik);
    // Nástroj Akord pracuje s textem: vybraný obrázek nebo náplast ho vypne (jedním klikem
    // z akordů na náplast, ne nejdřív do textu a pak znovu).
    if (rezimAkordu && tr.selectionSet && vybranyAtom(view.state)) { prepniRezimAkordu(false); return; }
    aktualizujListu();
  },
});
ovladac.pripoj(view);


const cisloStrany = new CisloStrany(stranka, el('navrh-plocha'), {
  format, nastaveni: () => nastaveni, meritko: () => meritko, posun: () => posunAktualni(),
  vyber: (jine) => vyberCislo(jine),
  zmen: (zmena) => { nastaveni = { ...nastaveni, ...zmena }; sjednotCislo(); pouzijNastaveni(); zalohuj(); },
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
  }
}

// --- šablona: styly odstavců a akordy -------------------------------------------------



function obnovCss() {
  stylyStrany.textContent = Object.entries(nastaveni.styly).map(([k, v]) => cssStylu('.stranka', k, v)).join('\n');
}

function pouzijNastaveni() {
  const f = format();
  const s = stranka.style;
  s.width = plocha().sirka + 'mm';
  // Dvojstrana k úpravě: hřbet uprostřed a číslo protější strany.
  stranka.classList.toggle('dvojstrana-uprava', !!spoj);
  s.setProperty('--sirka-strany', f.sirka + 'mm');
  vykresliDruheCislo();
  s.height = f.vyska + 'mm';
  s.setProperty('--okraj', f.okraj + 'mm');
  for (const cil of [s, el('ukazka-akordu').style]) nastavAkordy(cil, nastaveni);
  obnovCss();
  naplnVyberStylu();
  vykresliStyly();
  if (typeof cisloStrany !== 'undefined') cisloStrany.vykresli();
  // Role stylu mohla změnit číslování slok.
  if (typeof view !== 'undefined') view.dispatch(view.state.tr.setMeta(klicSlok, true));
  nastavMeritko();
  document.fonts.ready.then(naplanujPrepocet);
  naplanujPrepocet();
}

// Číslo protější strany ve dvojstraně k úpravě: jen k vidění (táhne se číslo upravované).
function vykresliDruheCislo() {
  let e = stranka.querySelector('.ps-cislo-druhe');
  if (!spoj) { if (e) e.remove(); return; }
  if (!e) {
    e = document.createElement('div');
    e.className = 'ps-cislo-strany ps-cislo-druhe';
    e.title = 'Číslo protější strany: klik vybere, tažením poloha';
    // Táhne se stejně jako číslo upravované strany (poloha platí pro obě).
    e.addEventListener('pointerdown', (ev) => cisloStrany.zacni(ev, { el: e, strana: spoj.druha.lp, posun: posunStrany(spoj.druha.lp) }));
    stranka.appendChild(e);
  }
  const d = spoj.druha, c = d.nastaveni.cislo;
  e.hidden = !c || !c.zobrazit;
  // Skryté na protější straně: bledě, jako u upravované (jinak by nešlo kliknout a odkrýt).
  e.classList.toggle('skryte', !!d.nastaveni.cisloSkryte);
  if (e.hidden) return;
  const { x, y } = CisloStrany.poloha(c, format(), d.lp);
  e.textContent = String(d.cislo);
  e.style.cssText = `left:${x + posunStrany(d.lp)}mm;top:${y}mm;font-family:'${c.pismo}',sans-serif;`
    + `font-size:${c.velikost}pt;font-weight:${c.tucne ? 700 : 400};color:${c.barva}`;
}

// Výška pracovní plochy: do spodku okna (lišta a panel se pak neposouvají se stránkou).
function nastavVysku() {
  if (window.innerWidth <= 900) { koren.style.height = ''; return; }
  const horni = koren.getBoundingClientRect().top + window.scrollY;
  koren.style.height = Math.max(420, window.innerHeight - horni) + 'px';
}

// Bez ručního přiblížení se ukazuje celá strana.
function nastavMeritko(nove) {
  const f = plocha();
  const sirka = f.sirka * MM_NA_PX, vyska = f.vyska * MM_NA_PX;
  if (nove != null) { meritko = nove; meritkoRucne = true; }
  else if (!meritkoRucne) {
    const plocha = el('navrh-plocha');
    // Okraj plochy (24 px kolem).
    const volnoX = plocha.clientWidth - 48, volnoY = plocha.clientHeight - 48;
    // Náhled protější strany vedle - jen když nějaká je (první a poslední strana ji nemají
    // a ukážou se jako jedna strana).
    const stran = dvojstrana && KONTEXT && !spoj && protejsiStrana()?.st ? 2 : 1;
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
      // Skrýt číslo u vybraného čísla protější strany (dvojstrana).
      if (klic === 'cisloSkryte' && spoj && cisloStrany.vybraneJine) {
        spoj.druha.nastaveni = { ...spoj.druha.nastaveni, cisloSkryte: h };
        pouzijNastaveni();
        zalohuj();
        return;
      }
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
    sjednotCislo();
    pouzijNastaveni();
    zalohuj();
    // Vypnuté číslování: vybrané číslo zmizelo.
    if (zvlastniVyber === 'cislo' && !nastaveni.cislo.zobrazit) zrusVyber();
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
  if (spoj && typeof cisloStrany !== 'undefined' && cisloStrany.vybraneJine) {
    koren.querySelector('.navrh-kontext [data-nastaveni="cisloSkryte"]').checked = !!spoj.druha.nastaveni.cisloSkryte;
  }
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
      // Ukázka obrázek nenahrává - zůstane jen v prohlížeči (data: URL ve straně), stejně
      // se nikam neuloží.
      .then(async (o) => vlozObrazek(view, { ...format(), posun: posunAktualni() }, { ...o, src: UKAZKA ? o.src : await nahrajObrazek(o.src) }, kam))
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

let otiskPriNacteni = null;   // upravovaná strana; protější má spoj.druha.otisk
const otiskStrany = (lp = KONTEXT ? KONTEXT.lp : 'P') => JSON.stringify({
  o: castStrany(lp).doc.toJSON(), n: { ...nastaveniStrany(lp), nahledCisla: 0, nahledStrany: '' } });
const neulozenoStrana = (lp) => otiskStrany(lp) !== (spoj && lp !== KONTEXT.lp ? spoj.druha.otisk : otiskPriNacteni);
const neulozeno = () => !!KONTEXT && (neulozenoStrana(KONTEXT.lp) || (!!spoj && neulozenoStrana(spoj.druha.lp)));
const klicDruhe = () => (spoj ? klicKonceptu({ strana: spoj.druha.st.id }) : null);
const jineKoncepty = () => (KONTEXT ? koncepty().filter((k) => k.klic !== klicKonceptu(KONTEXT) && k.klic !== klicDruhe()) : []);
// Rozdělané strany tady na plátně (upravovaná, ve dvojstraně i protější).
const rozdelaneZde = () => {
  if (!KONTEXT) return [];
  const out = neulozenoStrana(KONTEXT.lp) ? [{ cislo: KONTEXT.cislo }] : [];
  if (spoj && neulozenoStrana(spoj.druha.lp)) out.push({ cislo: spoj.druha.cislo });
  return out.sort((a, b) => a.cislo - b.cislo);
};
const cislaStran = (seznam) => seznam.map((k) => k.cislo).join(', ');

function ulozKoncept() {
  clearTimeout(casovacKonceptu);
  if (!KONTEXT) return;
  const zapis = (klic, rozdelana, data) => {
    if (!rozdelana) { smazKoncept(klic); return; }
    try { localStorage.setItem(klic, JSON.stringify(data)); } catch (_) { /* plné nebo nedostupné úložiště */ }
  };
  const lp = KONTEXT.lp;
  const tahle = neulozenoStrana(lp);
  zapis(klicKonceptu(KONTEXT), tahle, tahle ? { zdroj: zdrojStrany(lp), zaklad: KONTEXT.navrh || null,
    strana: KONTEXT.strana || null, za: KONTEXT.strana ? null : (KONTEXT.za || 0), cislo: KONTEXT.cislo } : null);
  if (spoj) {
    const d = spoj.druha;
    const rozdelana = neulozenoStrana(d.lp);
    zapis(klicDruhe(), rozdelana, rozdelana ? { zdroj: zdrojStrany(d.lp), zaklad: d.navrh, strana: d.st.id, za: null, cislo: d.cislo } : null);
  }
  obnovListovani();
}
function naplanujKoncept() {
  clearTimeout(casovacKonceptu);
  casovacKonceptu = setTimeout(ulozKoncept, 400);
}

// Zdroj strany na server: PDF a náhled se vyrobí ze zdroje, server verzi připojí na místo
// (cil = { strana } nebo { za }). Vrací odpověď serveru.
async function souboryStrany(zdroj) {
  const { pdfStrany } = await import(new URL('./pdf-strany.js', import.meta.url).href);
  // PDF i náhled bez čísla strany - to se dokreslí podle pozice ve zpěvníku.
  const { bytes } = await pdfStrany(zdroj, {});
  const { blob: nahled } = await obrazekStrany(zdroj, { sirkaPx: 420 });
  return { bytes, nahled };
}
// soubory: už vyrobené PDF a náhled (víc stejných nových stran naráz).
async function odesliStranu(zdroj, cil, soubory = null) {
  const { bytes, nahled } = soubory || await souboryStrany(zdroj);
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
  if (spoj && neulozenoStrana(spoj.druha.lp)) {
    // Protější strana dvojstrany (vždy uložená strana zpěvníku).
    const dr = spoj.druha;
    // Otisk z okamžiku odeslání - úprava během ukládání zůstane neuložená.
    const otisk2 = otiskStrany(dr.lp);
    const d2 = await odesliStranu(zdrojStrany(dr.lp), { strana: dr.st.id });
    dr.otisk = otisk2;
    dr.navrh = d2.id;
    smazKoncept(klicDruhe());
    const st = KONTEXT.strany.find((x) => x.id === dr.st.id);
    if (st) { st.druh = 'navrh'; st.navrh = d2.id; }
  }
  if (KONTEXT.strana && !neulozenoStrana(KONTEXT.lp)) return;
  const staryKlic = klicKonceptu(KONTEXT);
  const otisk = otiskStrany();
  const d = await odesliStranu(zdrojStrany(), { strana: KONTEXT.strana, za: KONTEXT.za });
  otiskPriNacteni = otisk;
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

// Před uložením dvojstrany: prvky přes hřbet. Obrázek a náplast jde zachovat na obou
// stranách (každá ořízne svou část), nebo oříznout; text v rámečku zůstane jen na
// straně podle středu. Vrací false, když autor uložení zrušil.
async function potvrdPresahy() {
  if (!spoj) return true;
  let prvku = 0, textu = 0;
  view.state.doc.forEach((n) => {
    if (!prekracujeHrbet(n)) return;
    prvku += 1;
    if (n.type === N.ramecek) textu += 1;
  });
  if (!prvku) return true;
  const text = textu
    ? ' Text přes hřbet se vytiskne přes obě strany, obsahem zpěvníku (hledání, číslování slok) zůstane jen na straně, kde má rámeček střed.'
    : '';
  const volba = await window.showUnsavedConfirm(
    `Přes hřbet dvojstrany přesahují prvky (${prvku}). Můžou zůstat na obou stranách (každá strana ukáže svou `
    + `část), nebo se oříznout - pak zůstanou jen na straně, kde mají střed.${text}`,
    { save: 'Zachovat na obou stranách', discard: 'Oříznout', discardStyl: 'neutralni' });
  if (volba === 'cancel') return false;
  orezatPresahy = volba === 'discard';
  return true;
}

let ulozitSe = false;
let ukladani = null;
// vse: uložit i ostatní rozdělané strany. Vrací true, když se všechno povedlo. Když se
// zrovna ukládá (třeba nová strana hned po otevření), počká a uloží znovu.
async function ulozitStranu(vse) {
  if (!KONTEXT) return false;
  while (ukladani) await ukladani;
  ukladani = ulozitStranuTed(vse);
  try { return await ukladani; } finally { ukladani = null; }
}
async function ulozitStranuTed(vse) {
  if (neulozeno() && !(await potvrdPresahy())) return false;
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
  // Jiné rozdělané strany (z listování) - zeptat se, jestli uložit i je.
  const tato = spoj ? `strany ${Math.min(KONTEXT.cislo, spoj.druha.cislo)}–${Math.max(KONTEXT.cislo, spoj.druha.cislo)}`
    : `stranu ${KONTEXT.cislo}`;
  const jedna = jine.length === 1;
  const volba = await window.showUnsavedConfirm(
    jedna ? `Neuložené změny má i strana ${cislaStran(jine)} (rozpracovaná při listování). Uložit i ji?`
      : `Neuložené změny mají i strany ${cislaStran(jine)} (rozpracované při listování). Uložit i je?`,
    { save: jedna ? `Uložit i stranu ${cislaStran(jine)}` : 'Uložit všechny', discard: `Uložit jen ${tato}`, discardStyl: 'neutralni' });
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
  // Dvojstrana: obě strany mají stejnou váhu - „Strany 6–7“ (ne „Strana 7 a 6“). Šířky
  // popisu i pole jsou pevné (CSS), ať šipky při listování neuhýbají; levá/pravá je jen
  // v nápovědě pole.
  el('listovani-popis').textContent = spoj ? 'Strany' : 'Strana';
  if (pole && document.activeElement !== pole && spoj) {
    pole.value = `${Math.min(KONTEXT.cislo, spoj.druha.cislo)}–${Math.max(KONTEXT.cislo, spoj.druha.cislo)}`;
  }
  if (pole) {
    pole.dataset.napoveda = spoj ? 'Čísla stran dvojstrany (napsat číslo a Enter)'
      : `Strana ${KONTEXT.cislo}, ${KONTEXT.lp === 'L' ? 'levá' : 'pravá'} (napsat číslo a Enter)`;
  }
  obnovStavUlozeni();
  const jine = jineKoncepty();
  const znak = el('neulozene-jinde');
  if (znak) {
    znak.hidden = !jine.length;
    znak.setAttribute('data-napoveda', `Neuložené změny i jinde: ${jine.length === 1 ? 'strana' : 'strany'} ${cislaStran(jine)}`);
  }
  for (const [prikaz, smer] of [['predchozi-strana', -1], ['dalsi-strana', 1]]) {
    const b = koren.querySelector(`[data-prikaz="${prikaz}"]`);
    if (b) b.disabled = !sousedniStrana(smer);
  }
  // Přidat stranu (nabídka, viz pridejStrany) jde jen od strany, která už ve zpěvníku je.
  const nova = el('nova-strana');
  if (nova) nova.disabled = !KONTEXT.strana;
}

// --- přidat stranu ----------------------------------------------------------------------
// Tlačítko + otevře nabídku: kam (za tuto stranu / na konec), obsah (prázdná, s ukázkovou
// písní, kopie vybrané strany) a počet. Strany vzniknou hned na serveru a editor otevře
// první z nich; rozdělaná úprava této strany zůstane v prohlížeči jako při listování.

// Strana, za kterou se přidává a která se nabídne ke kopii: upravovaná, ve dvojstraně ta
// s vyšším číslem.
function stranaProPridani() {
  if (spoj && spoj.druha.cislo > KONTEXT.cislo) return { id: spoj.druha.st.id, cislo: spoj.druha.cislo };
  return { id: KONTEXT.strana, cislo: KONTEXT.cislo };
}

function ukazNabidkuStran(ukazat) {
  const n = el('nabidka-stran');
  n.hidden = !ukazat;
  el('nova-strana').setAttribute('aria-expanded', String(ukazat));
  if (!ukazat) return;
  const ja = stranaProPridani();
  el('ns-za-popis').textContent = `Za stranu ${ja.cislo}`;
  const vyber = el('ns-kopie');
  vyber.innerHTML = '';
  for (const st of KONTEXT.strany) {
    const o = document.createElement('option');
    o.value = String(st.id);
    o.textContent = String(st.cislo);
    o.selected = st.id === ja.id;
    vyber.appendChild(o);
  }
  el('ns-chyba').hidden = true;
}

// Zdroj nové strany z dokumentu: nastavení této strany (formát, akordy, číslo), styly
// odstavců jako kopie z knihovny.
function zdrojNoveStrany(doc) {
  const styly = {};
  const obsah = doc.toJSON();
  const projdi = (u) => {
    if (u.type === 'odstavec' && u.attrs) {
      const k = u.attrs.styl;
      if (nastaveni.styly[k]) styly[k] = nastaveni.styly[k];
      else {
        const id = knihovna[k] ? k : idZKlice(k);
        if (knihovna[id]) {
          u.attrs.styl = klicStylu(id);
          styly[u.attrs.styl] = { ...knihovna[id] };
        }
      }
    }
    (u.content || []).forEach(projdi);
  };
  projdi(obsah);
  return { verze: VERZE_SCHEMATU, nastaveni: { ...nastaveniStrany(KONTEXT.lp), styly, cisloSkryte: false }, obsah, zlomy: {} };
}

// Zdroj strany ke kopii: z plátna (i s neuloženou úpravou), z rozdělané úpravy
// v prohlížeči, uložená verze, sken jako podklad, jinak prázdná.
async function zdrojKopie(id) {
  if (id === KONTEXT.strana) return zdrojStrany(KONTEXT.lp);
  if (spoj && id === spoj.druha.st.id) return zdrojStrany(spoj.druha.lp);
  const st = KONTEXT.strany.find((x) => x.id === id);
  if (!st) throw new Error('Strana ke kopii ve zpěvníku už není.');
  const k = prectiKoncept(klicKonceptu({ strana: id }));
  if (k && k.zdroj && znamaVerze(k.zdroj.verze)) return k.zdroj;
  if (st.navrh) {
    const r = await fetch(`/api/navrh/strana/${st.navrh}`, { credentials: 'same-origin' });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error(d.error || 'Stranu ke kopii se nepodařilo načíst.');
    return d.zdroj;
  }
  if (st.obrazek) return zdrojNoveStrany(await dokumentSeSkenem(st.obrazek));
  return zdrojNoveStrany(prazdnaStrana(format()));
}

let pridavaSe = false;
async function pridejStrany() {
  if (pridavaSe || !KONTEXT || !KONTEXT.strana) return;
  const kam = koren.querySelector('input[name="ns-kam"]:checked').value;
  const obsah = koren.querySelector('input[name="ns-obsah"]:checked').value;
  const pocet = Math.max(1, Math.min(20, parseInt(el('ns-pocet').value, 10) || 1));
  const tlacitko = el('ns-pridat');
  const chyba = el('ns-chyba');
  pridavaSe = true;
  tlacitko.disabled = true;
  tlacitko.textContent = pocet > 1 ? `Přidává se 0 / ${pocet}…` : 'Přidává se…';
  chyba.hidden = true;
  try {
    const zdroj = obsah === 'kopie' ? await zdrojKopie(+el('ns-kopie').value)
      : zdrojNoveStrany(obsah === 'ukazka' ? prazdnyDokument(format()) : prazdnaStrana(format()));
    if (JSON.stringify(zdroj.obsah).includes('"src":"data:')) {
      throw new Error('Strana ke kopii má obrázek, který se ještě nenahrál. Nejdřív ji otevřete a uložte.');
    }
    const soubory = await souboryStrany(zdroj);
    let za = kam === 'konec' ? KONTEXT.strany[KONTEXT.strany.length - 1].id : stranaProPridani().id;
    let prvni = null;
    for (let i = 0; i < pocet; i++) {
      const d = await odesliStranu(zdroj, { za }, soubory);
      if (prvni == null) prvni = d.strana;
      za = d.strana;
      if (pocet > 1) tlacitko.textContent = `Přidává se ${i + 1} / ${pocet}…`;
    }
    jdiNa(`/editor-strany/${prvni}`);
  } catch (e) {
    chyba.textContent = String(e.message || e);
    chyba.hidden = false;
    tlacitko.disabled = false;
    tlacitko.textContent = 'Přidat';
    pridavaSe = false;
  }
}

function napojPridaniStran() {
  const tlacitko = el('nova-strana');
  if (!tlacitko) return;
  tlacitko.addEventListener('click', () => ukazNabidkuStran(el('nabidka-stran').hidden));
  // Výběr strany ke kopii rovnou zvolí „Kopie strany“.
  el('ns-kopie').addEventListener('input', () => { koren.querySelector('input[name="ns-obsah"][value="kopie"]').checked = true; });
  el('ns-kopie').addEventListener('focus', () => { koren.querySelector('input[name="ns-obsah"][value="kopie"]').checked = true; });
  el('ns-pridat').addEventListener('click', pridejStrany);
  el('nabidka-stran').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName !== 'SELECT') { e.preventDefault(); pridejStrany(); } });
  document.addEventListener('pointerdown', (e) => {
    if (!el('nabidka-stran').hidden && !pridavaSe && !e.target.closest('.pridat-stranu')) ukazNabidkuStran(false);
  }, true);
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || el('nabidka-stran').hidden || pridavaSe) return;
    ukazNabidkuStran(false);
    tlacitko.focus();
    e.preventDefault();
    e.stopPropagation();
  }, true);
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
  if (!dvojstrana || !KONTEXT || spoj) return;     // spoj: protější strana se upravuje na plátně
  const verze = ++protejsekVerze;
  const p = protejsiStrana();
  const f = format();
  const sirka = f.sirka * MM_NA_PX, vyska = f.vyska * MM_NA_PX;
  const box = document.createElement('div');
  box.className = 'protejsek' + (p.st ? '' : ' zadna');
  box.style.width = sirka * meritko + 'px';
  box.style.height = vyska * meritko + 'px';
  if (!p.st) return;     // první nebo poslední strana - zůstane jako jedna strana
  if (p.vlevo) dvoj.insertBefore(box, dvoj.firstChild); else dvoj.appendChild(box);
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
  if (dvoj === dvojstrana) return;
  dvojstrana = dvoj;
  try { localStorage.setItem(KLIC_REZIMU, dvoj ? 'dvojstrana' : 'jedna'); } catch (_) { /* jen pohodlí */ }
  // Uložená strana: dvojstrana se upravuje na jednom plátně - postaví se znovu při
  // načtení; rozdělaná práce zůstane v prohlížeči (koncepty).
  if (KONTEXT && KONTEXT.strana) { jdiNa(location.pathname); return; }
  if (!meritkoRucne) nastavMeritko(); else vykresliProtejsek();
}

// Nejbližší strana před (-1) / za (1) touto. Nová strana ještě v seznamu
// není a její číslo má teď strana, která bude za ní.
function sousedniStrana(smer) {
  // Ve dvojstraně k úpravě se listuje přes celou dvojstranu.
  const ja = KONTEXT.cislo;
  const od = spoj ? Math.min(ja, spoj.druha.cislo) : ja, po = spoj ? Math.max(ja, spoj.druha.cislo) : ja;
  const kandidati = KONTEXT.strany.filter((st) => (smer < 0 ? st.cislo < od : (KONTEXT.strana ? st.cislo > po : st.cislo >= ja)));
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
  // Odkaz uvnitř webu (zpět do zpěvníku, lišta webu…) s rozdělanými stranami: stejné
  // okno jako editor zpěvníku. Zavření záložky nebo jiná adresa umí jen upozornění
  // prohlížeče (beforeunload výš).
  document.addEventListener('click', async (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || a.target === '_blank') return;
    const href = a.getAttribute('href') || '';
    if (href.startsWith('#') || href.startsWith('javascript:')) return;
    const cil = new URL(a.href, location.href);
    if (cil.origin !== location.origin) return;
    const jine = jineKoncepty();
    if (!neulozeno() && !jine.length) return;
    e.preventDefault();
    const strany = rozdelaneZde().concat(jine).sort((x, y) => x.cislo - y.cislo);
    const volba = await window.showUnsavedConfirm(
      strany.length === 1 ? `Strana ${strany[0].cislo} má neuložené změny.` : `Neuložené změny jsou na stranách ${cislaStran(strany)}.`,
      { save: strany.length === 1 ? 'Uložit a odejít' : 'Uložit vše a odejít',
        discard: 'Odejít bez uložení' });
    if (volba === 'cancel') return;
    if (volba === 'save' && !(await ulozitStranu(true))) return;
    if (volba === 'discard') for (const k of koncepty()) smazKoncept(k.klic);
    odchazi = true;
    location.href = cil.href;
  }, true);
  // Číslo strany jako ve čtečce: napsat a Enter (nebo odejít z pole).
  const pole = el('cislo-strany-zpevniku');
  const skoc = () => {
    const n = parseInt(pole.value, 10);
    const st = KONTEXT.strany.find((x) => x.cislo === n);
    // Strana z právě otevřené dvojstrany: nikam se nejde.
    if (st && st.id !== KONTEXT.strana && !(spoj && st.id === spoj.druha.st.id)) jdiNaStranu(st);
    else { pole.blur(); obnovListovani(); }
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
  if (zapnout && nastrojTextu?.aktivni) nastrojTextu.prepni(false);
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

// Rozepsaný akord (pole nad akordem) se před přepnutím nástroje zapíše - tlačítka
// nástrojů fokus neberou, takže by pole zůstalo otevřené.
function zapisAkord() {
  if (ovladac.upravovany != null) ovladac.potvrdPole({ vratFokus: false });
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
  // Nástroje (Akord, Text, Náplast) se navzájem vypínají - zapnutý je vždy nejvýš jeden.
  ramecek: () => {
    zapisAkord();
    if (rezimAkordu) prepniRezimAkordu(false);
    if (naplast.aktivni) naplast.prepni(false);
    nastrojTextu.prepni();
  },
  obrazek: () => { zapisAkord(); el('soubor-obrazku').click(); },
  naplast: () => {
    zapisAkord();
    if (rezimAkordu) prepniRezimAkordu(false);
    if (nastrojTextu.aktivni) nastrojTextu.prepni(false);
    naplast.prepni();
  },
  // Výběr = žádný nástroj (i × ve štítku nástroje).
  vybrat: () => {
    zapisAkord();
    if (rezimAkordu) prepniRezimAkordu(false);
    if (nastrojTextu.aktivni) nastrojTextu.prepni(false);
    if (naplast.aktivni) naplast.prepni(false);
  },
  duplikovat: () => duplikuj(),
  cislo: () => {
    zapisAkord();
    zapniCislovani(true);
    vyberCislo();
    stranka.querySelector('.ps-cislo-strany').scrollIntoView({ block: 'nearest', inline: 'nearest' });
  },
  zkratky: () => { el('okno-zkratek').hidden = false; el('zkratky-zavrit').focus(); },
  panel: () => prepniPanel(),
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
  'naplast-elipsa': () => naplast.nastavDruh('elipsa'),
  'naplast-trojuhelnik': () => naplast.nastavDruh('trojuhelnik'),
};

const BEZ_FOKUSU = ['cislo', 'zkratky', 'panel', 'mensi', 'vetsi', 'cela-strana', 'ramecek', 'obrazek', 'naplast', 'naplast-obdelnik', 'naplast-volny', 'naplast-elipsa', 'naplast-trojuhelnik', 'nahled', 'ulozit', 'predchozi-strana', 'dalsi-strana', 'jedna-strana', 'dvojstrana'];

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
  el('nahled-png')?.addEventListener('click', obrazekNahledu);
  el('nahled-pdf')?.addEventListener('click', pdfNahledu);
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
    if (c === 'cislo') b.classList.toggle('aktivni', zvlastniVyber === 'cislo');
    else if (c === 'vybrat') b.classList.toggle('aktivni', b.classList.contains('nastroj') && !rezimAkordu && !naplast?.aktivni && !nastrojTextu?.aktivni && zvlastniVyber !== 'cislo');
    else if (c === 'akordy') b.classList.toggle('aktivni', rezimAkordu);
    else if (c === 'naplast') b.classList.toggle('aktivni', !!naplast?.aktivni);
    else if (c === 'ramecek') b.classList.toggle('aktivni', !!nastrojTextu?.aktivni);
    else if (c.startsWith('naplast-')) b.classList.toggle('aktivni', naplast?.druh === c.slice(8));
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

  // Kontextová lišta: vybraný akord, obrázek, náplast.
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
  let naplasti = 0;
  state.doc.forEach((n) => { if (n.type === N.tvar) naplasti += 1; });
  el('tvar-smazat-vse').hidden = naplasti < 1 || (!!tvar && naplasti < 2);
  el('tvar-smazat-vse').textContent = `Smazat všechny (${naplasti})`;
  if (tvar && el('tvar-barva').value !== tvar.attrs.barva) { el('tvar-barva').value = tvar.attrs.barva; if (naplast) naplast.barva = tvar.attrs.barva; }
  // Pořadí vrstev (nabídka v liště): položka, která by nic nezměnila, je vypnutá, bez
  // takové položky i celé tlačítko. Podklad ve vrstvách není.
  el('obrazek-podklad').hidden = !(obrazek && obrazek.attrs.podklad);
  const prvek = prvekVyberu(state);
  let nejakePoradi = false;
  for (const b of el('poradi-nabidka').querySelectorAll('[data-poradi]')) {
    b.disabled = !prvek || prvek.node.attrs.presah || !poradiPoPrikazu(state.doc, prvek.index, b.dataset.poradi);
    nejakePoradi ||= !b.disabled;
  }
  el('poradi-tlacitko').disabled = !nejakePoradi;
  if (!nejakePoradi) ukazPoradi(false);
  // Otočení a zámek vybraného prvku.
  if (prvek) {
    if (document.activeElement !== el('prvek-otoceni')) el('prvek-otoceni').value = String(Math.round(prvek.node.attrs.otoceni || 0));
    el('prvek-otoceni').disabled = !!prvek.node.attrs.zamceno;
    const z = !!prvek.node.attrs.zamceno;
    el('prvek-zamek').setAttribute('aria-pressed', String(z));
    el('prvek-zamek').setAttribute('aria-label', z ? 'Odemknout' : 'Zamknout');
    el('prvek-zamek').dataset.napoveda = z ? 'Zamčeno: klik odemkne' : 'Zamknout polohu: prvek se nedá posunout, zvětšit, otočit ani smazat (text v rámečku jde psát dál)';
  }
  el('tvar-smazat').disabled = !!tvar?.attrs.zamceno;
  el('obrazek-smazat').disabled = !!obrazek?.attrs.zamceno;

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
  obnovKontext(state);
}

// --- kontextová lišta a stavový řádek -------------------------------------------------
// Co je vybrané (štítek vlevo v kontextové liště, popis ve stavovém řádku) a jaký nástroj
// je zapnutý (štítek nad stranou, rámeček plochy). Skupiny lišty s data-pro se ukazují
// podle výběru; karty vybraného akordu, obrázku a náplasti řídí aktualizujListu výš.

function druhVyberu(state) {
  if (zvlastniVyber) return zvlastniVyber;
  const sel = state.selection;
  if (sel instanceof NodeSelection) {
    const t = sel.node.type;
    if (t === N.akord) return 'akord';
    if (t === N.obrazek) return sel.node.attrs.podklad ? 'podklad' : 'obrazek';
    if (t === N.tvar) return 'naplast';
    if (t === N.ramecek) return 'ramecek';
  }
  return sel.$from.parent.type === N.odstavec ? 'text' : 'nic';
}

const NAZVY_TVARU = { obdelnik: 'Obdélník', elipsa: 'Elipsa', trojuhelnik: 'Trojúhelník', volny: 'Volný tvar' };
const zapnutyNastroj = () => (naplast?.aktivni ? 'naplast' : nastrojTextu?.aktivni ? 'text' : rezimAkordu ? 'akord' : null);
const cisloMm = (x) => String(Math.round(x * 10) / 10).replace('.', ',');
const POPISY_VYBERU = {
  text: ['i-ramecek', 'Text'], ramecek: ['i-ramecek', 'Rámeček'], akord: ['i-akord', 'Akord'],
  obrazek: ['i-obrazek', 'Obrázek'], podklad: ['i-obrazek', 'Podklad strany'], naplast: ['i-naplast', 'Náplast'],
  nic: ['i-jedna-strana', 'Strana'], cislo: ['i-cislo', 'Číslo strany'], 'nova-naplast': ['i-naplast', 'Nová náplast'], 'novy-ramecek': ['i-ramecek', 'Nový rámeček'],
};

// Text s tučným začátkem (název výběru) - bez innerHTML, název akordu píše uživatel.
function napisPopis(prvek, nazev, zbytek) {
  prvek.textContent = '';
  const b = document.createElement('b');
  b.textContent = nazev;
  prvek.append(b, zbytek ? ' · ' + zbytek : '');
}

function obnovKontext(state) {
  if (typeof naplast === 'undefined' || !naplast || !nastrojTextu) return;
  const druh = druhVyberu(state);
  const nastroj = zapnutyNastroj();
  koren.dataset.vyber = druh;
  koren.dataset.nastroj = nastroj || '';
  koren.classList.toggle('s-nastrojem', !!nastroj);

  const ukaz = new Set();
  if (druh === 'text' && nastroj !== 'naplast' && nastroj !== 'text') ukaz.add('text');
  if (nastroj === 'akord' || druh === 'akord') ukaz.add('akordy');
  if (druh === 'nic' && !nastroj) ukaz.add('strana');
  const p = zvlastniVyber ? null : prvekVyberu(state);
  // S nástrojem Akord jen akordy a text (pořadí, otočení a zámek by lištu zalomily).
  if (p && !p.node.attrs.podklad && !p.node.attrs.presah && druh !== 'akord' && nastroj !== 'text' && nastroj !== 'akord'
      && !(nastroj === 'naplast' && druh !== 'naplast')) ukaz.add('prvek');
  if (zvlastniVyber) {
    el('vybrany-akord').hidden = true;
    el('vybrany-obrazek').hidden = true;
    if (nastroj !== 'naplast') el('vybrany-tvar').hidden = true;
    else el('tvar-smazat').hidden = true;
  }
  el('vybrane-cislo').hidden = druh !== 'cislo';
  const skupiny = [...koren.querySelectorAll('.navrh-kontext .skupina')];
  for (const g of skupiny) if (g.dataset.pro) g.hidden = !g.dataset.pro.split(' ').some((t) => ukaz.has(t));
  // Duplikát rámečku s textem jde Ctrl+D; v liště jen u obrázku a náplasti (místo pro text).
  koren.querySelector('.navrh-kontext [data-prikaz="duplikovat"]').hidden = druh === 'text';
  let prvni = true;
  for (const g of skupiny) { g.classList.toggle('prvni', prvni && !g.hidden); if (!g.hidden) prvni = false; }

  // Štítek: co se upravuje.
  const klic = nastroj === 'naplast' && druh !== 'naplast' ? 'nova-naplast' : nastroj === 'text' ? 'novy-ramecek' : druh;
  const [ikona, nazev] = POPISY_VYBERU[klic];
  const stitek = el('kontext-stitek');
  stitek.querySelector('use').setAttribute('href', '#' + ikona);
  const sel = state.selection;
  stitek.querySelector('span').textContent = druh === 'akord' && klic === 'akord' && sel.node.attrs.n ? `Akord ${sel.node.attrs.n}` : nazev;
  el('kontext-napoveda').textContent = klic === 'novy-ramecek' ? 'Klik na stranu vloží rámeček, tažením jde nakreslit libovolně velký.'
    : klic === 'nic' ? 'Klik do textu ho upraví, na prvek ho vybere.' : '';

  // Stavový řádek: popis výběru, u nástroje jak s ním pracovat.
  const popis = el('navrh-popis');
  popis.classList.remove('pripravuje-zprava');
  const rozmer = (a) => `${cisloMm(a.w)} × ${cisloMm(a.h)} mm${a.otoceni ? ` · otočeno o ${Math.round(a.otoceni)}°` : ''}${a.zamceno ? ' · zamčeno' : ''}`;
  if (nastroj === 'akord') napisPopis(popis, 'Nástroj Akord', 'klik na znak vloží akord, tažením se akord posune · Esc ukončí');
  else if (nastroj === 'naplast' && druh !== 'naplast') {
    napisPopis(popis, 'Nástroj Náplast', naplast.druh === 'volny'
      ? 'klikáním body, tvar uzavře klik na první bod, dvojklik nebo Enter · Esc ukončí'
      : `klik vloží ${{ obdelnik: 'malý obdélník', elipsa: 'malou elipsu', trojuhelnik: 'malý trojúhelník' }[naplast.druh]}, tažení větší${naplast.druh === 'trojuhelnik' ? '' : ` (se Shiftem ${naplast.druh === 'elipsa' ? 'kruh' : 'čtverec'})`} · Esc ukončí`);
  } else if (nastroj === 'text') napisPopis(popis, 'Nástroj Text', 'klik nebo tažení na straně vloží rámeček · Esc ukončí');
  else if (druh === 'akord') napisPopis(popis, `Akord ${sel.node.attrs.n || ''}`.trim(), 'tažením posunout (Alt drží znak, Shift jen vodorovně) · Delete smaže');
  else if (druh === 'obrazek') napisPopis(popis, 'Obrázek', `${rozmer(sel.node.attrs)}${sel.node.attrs.zamceno ? '' : ' · přesun tažením, velikost za rohy, otočení kolečkem pod ním'} · pravé tlačítko nabídne další`);
  else if (druh === 'podklad') napisPopis(popis, 'Podklad strany', 'leží vždy vespod, klik ho nevybere (jen Alt+klik) · Delete smaže');
  else if (druh === 'naplast') napisPopis(popis, 'Náplast', `${rozmer(sel.node.attrs)}${sel.node.attrs.zamceno ? '' : ' · přesun tažením, velikost za úchyty, otočení kolečkem pod ní'} · pravé tlačítko nabídne další`);
  else if (druh === 'ramecek') napisPopis(popis, 'Rámeček', rozmer(sel.node.attrs));
  else if (druh === 'text') {
    const id = souhrnVyberu(state).styl;
    const casti = [];
    if (id && knihovna[id]) casti.push(`styl ${knihovna[id].nazev}`);
    if (p && p.node.type === N.ramecek) casti.push(`rámeček ${rozmer(p.node.attrs)}`);
    if (!sel.empty) casti.push(`označeno znaků: ${state.doc.textBetween(sel.from, sel.to, '').length}`);
    napisPopis(popis, 'Text', casti.join(' · '));
  } else if (druh === 'cislo') napisPopis(popis, 'Číslo strany', 'tažením posunout, přichytí se ke středu a k okrajům · platí pro všechny strany · Esc zruší výběr');
  else napisPopis(popis, 'Nic nevybráno', `strana ${nastaveni.format || 'A5'} · klik do textu ho upraví, klik na obrázek, náplast nebo číslo strany je vybere`);

  // Štítek nástroje nad stranou.
  const rezim = el('navrh-rezim');
  rezim.hidden = !nastroj;
  if (nastroj) {
    rezim.querySelector('use').setAttribute('href', nastroj === 'akord' ? '#i-akord' : nastroj === 'naplast' ? '#i-naplast' : '#i-ramecek');
    rezim.querySelector('.rezim-text').textContent = nastroj === 'akord' ? 'Nástroj Akord: klik na znak vloží akord'
      : nastroj === 'text' ? 'Nástroj Text: klik nebo tažení vloží rámeček'
      : `Nástroj Náplast: ${NAZVY_TVARU[naplast.druh].toLowerCase()}`;
  }
}

// Zrušení výběru (klik na prázdné místo, Esc) a výběr čísla strany: editor bez fokusu,
// ať klávesy (Delete) nesahají na prvek, který tam ProseMirror drží.
function zrusVyber() {
  zvlastniVyber = 'nic';
  cisloStrany.nastavVybrane(false);
  view.dom.blur();
  aktualizujListu();
}
// Ve dvojstraně mají obě strany stejné číslování (poloha, písmo): změna u jedné platí i pro
// druhou. Skrytí čísla je dál jen pro jednu stranu.
function sjednotCislo() {
  if (spoj) spoj.druha.nastaveni = { ...spoj.druha.nastaveni, cislo: nastaveni.cislo };
}
// jine: ve dvojstraně vybrané číslo protější strany ({ el, strana }).
function vyberCislo(jine = null) {
  if (zapnutyNastroj()) PRIKAZY.vybrat();
  zvlastniVyber = 'cislo';
  cisloStrany.nastavVybrane(true, jine);
  nactiPanel();
  view.dom.blur();
  aktualizujListu();
}

// Stav uložení v hlavičce (obnovuje se se zálohou rozdělané strany).
function obnovStavUlozeni() {
  const e = el('stav-ulozeni');
  if (!e || !KONTEXT) return;
  const ne = neulozeno();
  e.classList.toggle('neulozeno', ne || !KONTEXT.strana);
  e.textContent = !KONTEXT.strana ? 'Nová strana, zatím neuložená' : ne ? 'Neuložené změny' : 'Uloženo';
}

// Panel vpravo jde schovat (víc místa pro stranu); volba zůstane v prohlížeči.
const KLIC_PANELU = 'navrh-strany-panel';
function prepniPanel(ukazat = koren.classList.contains('bez-panelu')) {
  koren.classList.toggle('bez-panelu', !ukazat);
  el('prepinac-panelu').setAttribute('aria-pressed', String(ukazat));
  try { localStorage.setItem(KLIC_PANELU, ukazat ? '1' : '0'); } catch (_) { /* jen pohodlí */ }
  if (!meritkoRucne) nastavMeritko();
}
function nactiPanelVidet() {
  try { if (localStorage.getItem(KLIC_PANELU) === '0') prepniPanel(false); } catch (_) { /* jen pohodlí */ }
}

// Duplikát prvku (obrázek, náplast, rámeček s kurzorem): o 5 mm vedle, nahoru ve vrstvách,
// v pořadí čtení na konec. Podklad ani kopie přes hřbet se neduplikují.
// Změna atributů prvku (otočení, zámek) jedním krokem Zpět; výběr zůstane.
function zmenPrvek(p, zmena) {
  const tr = closeHistory(view.state.tr).setNodeMarkup(p.pos, null, { ...p.node.attrs, ...zmena });
  if (view.state.selection instanceof NodeSelection && view.state.selection.from === p.pos) tr.setSelection(NodeSelection.create(tr.doc, p.pos));
  view.dispatch(tr);
}

function duplikuj() {
  const { state } = view;
  const p = prvekVyberu(state);
  if (!p || p.node.attrs.podklad || p.node.attrs.presah) return false;
  const a = p.node.attrs;
  // Kopie je odemčená - vzniká proto, aby se s ní hýbalo.
  const kopie = p.node.type.create({ ...a, x: a.x + 5, y: a.y + 5, z: zNahore(state.doc), zamceno: false }, p.node.content, p.node.marks);
  const konec = state.doc.content.size;
  const tr = closeHistory(state.tr).insert(konec, kopie);
  tr.setSelection(kopie.type === N.ramecek ? TextSelection.near(tr.doc.resolve(konec + 1)) : NodeSelection.create(tr.doc, konec));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
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

function ukazPoradi(ukazat) {
  el('poradi-nabidka').hidden = !ukazat;
  el('poradi-tlacitko').setAttribute('aria-expanded', String(ukazat));
}

function napojPanelAkordu() {
  el('akord-velikost').addEventListener('change', (e) => {
    const v = parseInt(e.target.value, 10);
    upravVybranyAkord({ vel: !v || v === 100 ? null : Math.max(40, Math.min(300, v)) });
  });
  el('akord-vychozi').addEventListener('click', () => upravVybranyAkord({ dx: 0, dy: 0 }));
  // Nabídka Pořadí: klik nemá brát fokus z editoru (kurzor v rámečku = rámeček, se
  // kterým se hýbe). Zavře ji výběr, klik jinam a Esc.
  el('poradi-tlacitko').addEventListener('pointerdown', (e) => e.preventDefault());
  el('poradi-tlacitko').addEventListener('click', () => ukazPoradi(el('poradi-nabidka').hidden));
  for (const b of el('poradi-nabidka').querySelectorAll('[data-poradi]')) {
    b.addEventListener('pointerdown', (e) => e.preventDefault());
    b.addEventListener('click', () => { ukazPoradi(false); zmenVrstvu(b.dataset.poradi); });
  }
  document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.navrh-poradi')) ukazPoradi(false); }, true);
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || el('poradi-nabidka').hidden) return;
    ukazPoradi(false);
    e.preventDefault();
    e.stopPropagation();
  }, true);
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
  // Všechny náplasti strany jedním krokem (Ctrl+Z je vrátí).
  // Otočení a zámek vybraného prvku (rámeček i s kurzorem v textu).
  el('prvek-otoceni').addEventListener('change', (e) => {
    const p = prvekVyberu(view.state);
    if (!p) return;
    let uhel = parseFloat(String(e.target.value).replace(',', '.'));
    if (!Number.isFinite(uhel)) uhel = 0;
    uhel = ((uhel % 360) + 540) % 360 - 180;
    if (uhel === -180) uhel = 180;
    zmenPrvek(p, { otoceni: Math.round(uhel * 10) / 10 });
  });
  el('prvek-otoceni').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.target.blur(); view.focus(); } });
  el('prvek-zamek').addEventListener('click', () => {
    const p = prvekVyberu(view.state);
    if (p) zmenPrvek(p, { zamceno: !p.node.attrs.zamceno });
    view.focus();
  });
  el('tvar-smazat-vse').addEventListener('click', () => {
    const pozice = [];
    view.state.doc.forEach((n, pos) => { if (n.type === N.tvar && !n.attrs.zamceno) pozice.push([pos, n.nodeSize]); });
    let tr = closeHistory(view.state.tr);
    for (const [pos, size] of pozice.reverse()) tr = tr.delete(pos, pos + size);
    if (pozice.length) view.dispatch(tr);
    view.focus();
  });
  el('tvar-smazat').addEventListener('click', () => {
    const sel = view.state.selection;
    if (sel instanceof NodeSelection && sel.node.type === N.tvar && !jeZamceny(sel.node)) view.dispatch(view.state.tr.deleteSelection());
    view.focus();
  });
  el('obrazek-smazat').addEventListener('click', () => {
    const sel = view.state.selection;
    if (sel instanceof NodeSelection && sel.node.type === N.obrazek && !jeZamceny(sel.node)) view.dispatch(view.state.tr.deleteSelection());
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
// --- start ----------------------------------------------------------------------------

// Start: styly strany na verze z knihovny; kopie stylů, které strana nepoužívá, pryč.
{
  const tr = normalizujStyly(view.state);
  if (tr) view.updateState(view.state.apply(tr));
  const pouzite = new Set();
  view.state.doc.descendants((n) => { if (n.type === N.odstavec) pouzite.add(n.attrs.styl); return true; });
  nastaveni = { ...nastaveni, styly: Object.fromEntries(Object.entries(nastaveni.styly).filter(([k]) => pouzite.has(k))) };
}

// Prvek pod jiným (registrováno před nástroji, ať má přednost):
// - Alt+klik vybere prvek pod tím vybraným (opakovaně dál dolů, pak znovu shora), i podklad,
//   a hned ho jde táhnout.
// - Obrázek nebo náplast vybraná Alt+klikem jde pak táhnout, i když ji jiný prvek
//   zakrývá; klik bez tahu je obyčejný klik na to, co je nahoře. Jen po Alt+kliku -
//   jinak by tažení přes text (označení) posunulo náhodou vybraný prvek pod ním.
stranka.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || (naplast && naplast.body)) return;
  const t = e.target;
  // Úchyt vybraného prvku má přednost před vším, co leží nad ním (text, lišta jiného
  // prvku) - náplasti leží pod textem a jinak by na úchyty nešlo dosáhnout.
  if (t.closest('.ps-uchyty, .ps-zamek')) return;
  const uchyt = document.elementsFromPoint(e.clientX, e.clientY)
    .find((x) => x.matches('.vybrany > .ps-uchyty [data-uchyt], .aktivni > .ps-uchyty [data-uchyt], .vybrany > .ps-uchyty [data-bod]')
      && getComputedStyle(x.closest('.ps-uchyty')).display !== 'none');
  if (uchyt) {
    e.stopImmediatePropagation();
    uchyt.closest('.ps-tvar, .ps-obrazek, .ps-ramecek').prvekView.chytni(e, uchyt);
    return;
  }
  // Vlastní ovládání (lišta, akord, číslo strany) a místa mimo prvky si klik nechají.
  if (t.closest('.ps-prvek-lista, .akord-kotva, .ps-cislo-strany')) return;
  if (!t.closest('.ps-ramecek, .ps-obrazek, .ps-tvar') && !t.closest('.stranka-obsah')) return;
  const pod = prvkyPodBodem(e.clientX, e.clientY);
  if (!pod.length) return;
  // Nástroj Náplast: klik na náplast ji vybere (a táhne), i když leží pod textem -
  // nekreslí přes ni novou.
  if (naplast && naplast.aktivni && !e.altKey) {
    const tvar = pod.find((h) => h.node.type === N.tvar);
    if (tvar && tvar.dom.prvekView) {
      e.stopImmediatePropagation();
      tvar.dom.prvekView.zacni(e, 'presun');
    }
    return;
  }
  const vybrany = prvekVyberu(view.state);
  const i = vybrany ? pod.findIndex((h) => h.index === vybrany.index) : -1;
  let volba = null, priKliku = null;
  if (e.altKey) {
    volba = pod[(i < 0 ? 1 : i + 1) % pod.length];
    if (volba && volba.node.type !== N.ramecek) vybranoAltem = volba.pos;
  } else if (i > 0 && vybranyAtom(view.state) && vybranoAltem === vybrany.pos) {
    volba = pod[i];
    if (!naplast || !naplast.aktivni) priKliku = () => klikNaPrvek(pod[0], e);
  }
  if (!volba || !volba.dom.prvekView) return;
  e.stopImmediatePropagation();
  volba.dom.prvekView.zacni(e, 'presun', priKliku);
}, true);

// Klik na prvek bez tahu: obrázek a náplast vybere, do rámečku dá kurzor na místo kliku.
function klikNaPrvek(h, e) {
  const { state } = view;
  let sel;
  if (h.node.type !== N.ramecek) sel = NodeSelection.create(state.doc, h.pos);
  else {
    const p = view.posAtCoords({ left: e.clientX, top: e.clientY });
    const uvnitr = p && p.pos > h.pos && p.pos < h.pos + h.node.nodeSize;
    sel = TextSelection.near(state.doc.resolve(uvnitr ? p.pos : h.pos + h.node.nodeSize - 2), -1);
  }
  view.dispatch(state.tr.setSelection(sel));
}

// Nástroj Náplast (naplast.js): kreslí do strany, dokud je zapnutý.
naplast = new NastrojNaplasti({ view, stranka, koren, meritko: () => meritko, poZmene: () => aktualizujListu(), zrusVyber: () => zrusVyber() });
nastrojTextu = new NastrojTextu({ view, stranka, koren, meritko: () => meritko, poZmene: () => aktualizujListu() });
// Esc a Enter i mimo editor textu (po kliknutí do strany fokus v textu být nemusí).
window.addEventListener('keydown', (e) => {
  if (nastrojTextu.aktivni && e.key === 'Escape') { nastrojTextu.prepni(false); e.preventDefault(); e.stopPropagation(); return; }
  if (!naplast.aktivni) return;
  if ((e.key === 'Escape' && naplast.esc()) || (e.key === 'Enter' && naplast.enter())) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);
// Připravované funkce: tlačítko jen na chvíli napíše do stavového řádku, že funkce zatím
// není (okno by zbytečně zdržovalo).
for (const b of koren.querySelectorAll('[data-pripravuje]')) {
  b.addEventListener('mousedown', (e) => e.preventDefault());
  b.addEventListener('click', () => {
    clearTimeout(casovacPripravy);
    const popis = el('navrh-popis');
    napisPopis(popis, 'Připravuje se', b.dataset.pripravuje);
    popis.classList.add('pripravuje-zprava');
    casovacPripravy = setTimeout(aktualizujListu, 3500);
  });
}

// Okno zkratek: zavře ×, klik vedle a Esc.
{
  const okno = el('okno-zkratek');
  const zavri = () => { okno.hidden = true; view.focus(); };
  el('zkratky-zavrit').addEventListener('click', zavri);
  okno.addEventListener('click', (e) => { if (e.target === okno) zavri(); });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !okno.hidden) { e.stopPropagation(); e.preventDefault(); zavri(); }
  }, true);
}

// Nabídka prvku na pravé tlačítko (obrázek, náplast, podklad): pořadí, duplikát, smazání.
// V textu zůstává nabídka prohlížeče (kopírovat, vložit).
{
  const nabidka = el('nabidka-prvku');
  const zavri = () => { nabidka.hidden = true; };
  stranka.addEventListener('contextmenu', (e) => {
    if (naplast.body) return;
    const pod = prvkyPodBodem(e.clientX, e.clientY);
    const vybrany = prvekVyberu(view.state);
    const h = pod.find((x) => vybrany && x.index === vybrany.index && x.node.type !== N.ramecek) || pod[0];
    if (!h || h.node.type === N.ramecek) { zavri(); return; }
    e.preventDefault();
    const sel = view.state.selection;
    if (!(sel instanceof NodeSelection && sel.from === h.pos)) view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, h.pos)));
    for (const b of nabidka.querySelectorAll('[data-poradi]')) b.disabled = !poradiPoPrikazu(view.state.doc, h.index, b.dataset.poradi);
    nabidka.querySelector('[data-akce="duplikovat"]').disabled = !!h.node.attrs.podklad || !!h.node.attrs.presah;
    nabidka.querySelector('[data-akce="smazat"]').disabled = !!h.node.attrs.zamceno;
    nabidka.querySelector('[data-akce="zamek"]').hidden = !!h.node.attrs.podklad;
    nabidka.querySelector('.zamek-popis').textContent = h.node.attrs.zamceno ? 'Odemknout' : 'Zamknout';
    nabidka.hidden = false;
    const r = nabidka.getBoundingClientRect();
    nabidka.style.left = Math.max(4, Math.min(e.clientX, window.innerWidth - r.width - 8)) + 'px';
    nabidka.style.top = Math.max(4, Math.min(e.clientY, window.innerHeight - r.height - 8)) + 'px';
  });
  for (const b of nabidka.querySelectorAll('button')) {
    b.addEventListener('pointerdown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      zavri();
      if (b.dataset.poradi) zmenVrstvu(b.dataset.poradi);
      else if (b.dataset.akce === 'duplikovat') duplikuj();
      else if (b.dataset.akce === 'zamek') {
        const p = prvekVyberu(view.state);
        if (p) zmenPrvek(p, { zamceno: !p.node.attrs.zamceno });
        view.focus();
      } else if (b.dataset.akce === 'smazat' && view.state.selection instanceof NodeSelection && !view.state.selection.node.attrs.zamceno) {
        view.dispatch(view.state.tr.deleteSelection());
        view.focus();
      }
    });
  }
  document.addEventListener('pointerdown', (e) => { if (!nabidka.contains(e.target)) zavri(); }, true);
  el('navrh-plocha').addEventListener('scroll', zavri);
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !nabidka.hidden) { zavri(); e.preventDefault(); e.stopPropagation(); }
  }, true);
}

napojPanel();
napojListu();
napojPanelAkordu();
nastavVysku();
prepniZalozku((() => {
  try { const z = localStorage.getItem(KLIC_ZALOZKY); return ['text', 'akordy'].includes(z) ? z : 'text'; } catch (_) { return 'text'; }
})());
nactiPanelVidet();
pouzijNastaveni();
aktualizujListu();
window.addEventListener('resize', () => { nastavVysku(); if (!meritkoRucne) nastavMeritko(); });
// Po otevření není nic vybrané (ProseMirror by jinak držel první prvek - často náplast).
// Klik do textu nebo na prvek dá editoru fokus a tím i běžný výběr.
view.dom.addEventListener('focus', () => {
  if (!zvlastniVyber) return;
  zvlastniVyber = null;
  cisloStrany.nastavVybrane(false);
  aktualizujListu();
});
// Klik na prázdné místo (papír mimo prvky, šedá plocha kolem): nic nevybráno.
el('navrh-plocha').addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || zapnutyNastroj()) return;
  const t = e.target;
  if (t !== view.dom && !['stranka', 'navrh-plocha', 'dvojstrana', 'stranka-obal'].includes(t.id) && !t.classList.contains('ps-mimo')) return;
  e.preventDefault();
  zrusVyber();
});
// Bez fokusu v editoru (nic nevybráno, vybrané číslo): Esc, Zpět/Znovu a Uložit fungují dál.
window.addEventListener('keydown', (e) => {
  // Psaní do polí (velikost, název) nechat být; zaškrtávátko a tlačítko v liště Esc nebrání.
  const pole = e.target.closest?.('input:not([type="checkbox"]):not([type="color"]), select, textarea, [contenteditable="true"]');
  if (!zvlastniVyber || view.hasFocus() || pole) return;
  if (document.querySelector('.oznameni-zaclona, #app-confirm-overlay[aria-hidden="false"]')) return;
  const mod = e.metaKey || e.ctrlKey;
  const k = e.key.toLowerCase();
  if (e.key === 'Escape' && zvlastniVyber === 'cislo') { e.target.blur?.(); zrusVyber(); nactiPanel(); }
  else if (mod && k === 'z' && !e.shiftKey) undo(view.state, view.dispatch);
  else if (mod && (k === 'y' || (k === 'z' && e.shiftKey))) redo(view.state, view.dispatch);
  else if (mod && k === 's') { if (KONTEXT && !UKAZKA) ulozit(); }
  else return;
  e.preventDefault();
});
// Zaškrtávátko v kontextové liště po změně fokus pustí - jinak by u něj zůstala viset
// nápověda (napoveda.js ji ukazuje i u fokusu) a první Esc by patřil jí.
for (const z of koren.querySelectorAll('.navrh-kontext input[type="checkbox"]')) z.addEventListener('change', () => z.blur());
// Číslo v levé liště: zapne číslování (když je vypnuté) a číslo vybere; Vypnout číslování
// v kontextové liště u vybraného čísla.
const zapniCislovani = (zapnout) => {
  if (!!nastaveni.cislo.zobrazit === zapnout) return;
  nastaveni = { ...nastaveni, cislo: { ...nastaveni.cislo, zobrazit: zapnout } };
  pouzijNastaveni();
  nactiPanel();
  zalohuj();
};
el('vypnout-cislovani').addEventListener('click', () => { zapniCislovani(false); zrusVyber(); });
// Načtený koncept je rozdělaná úprava: strana je od začátku neuložená.
otiskPriNacteni = pouzitKoncept ? null : otiskStrany();
if (spoj) spoj.druha.otisk = spoj.druha.koncept ? null : otiskStrany(spoj.druha.lp);
obnovListovani();

// Pro testy a ladění.
// nahradTextem: test začíná od známého textu, ne od ukázkové písně.
if (KONTEXT) napojPridaniStran();
// Nová strana (/editor-strany/nova - z výběru strany nebo z editoru zpěvníku) se ve
// zpěvníku založí hned při otevření, ne až prvním Uložit.
if (KONTEXT && !KONTEXT.strana && !UKAZKA) ulozitStranu(false);

window.navrhStrany = {
  view, schema, TextSelection, NodeSelection, nastaveni: () => nastaveni, knihovna: () => knihovna, meritko: () => meritko, prepniRezimAkordu,
  cislo: () => cisloStrany,
  zdroj: (lp) => zdrojStrany(lp),
  dvojstrana: () => (spoj ? { lp: KONTEXT.lp, druha: { cislo: spoj.druha.cislo, lp: spoj.druha.lp, id: spoj.druha.st.id } } : null),
  zmerZlomy, dokumentZeZdroje,
  vykresliStranu, obrazekStrany,
  // Ve dvojstraně nahradí jen upravovanou stranu.
  nahradTextem(nazev, autor, text) {
    const f = format();
    const novy = posunUzel(N.ramecek.create(
      { x: f.okraj, y: f.okraj, w: f.sirka - 2 * f.okraj, h: f.vyska - 2 * f.okraj, z: zNahore(view.state.doc) },
      [N.odstavec.create({ styl: 'nadpis' }, schema.text(nazev)),
        N.odstavec.create({ styl: 'autor' }, schema.text(autor)), ...odstavceZTextu(text)],
    ), posunAktualni());
    const ostatni = [];
    if (spoj) view.state.doc.forEach((n) => { if (stranaUzlu(n) !== KONTEXT.lp) ostatni.push(n); });
    const doc = N.doc.create(null, [...ostatni, novy]);
    view.updateState(EditorState.create({ doc, plugins: view.state.plugins }));
    const tr = normalizujStyly(view.state);
    if (tr) view.updateState(view.state.apply(tr));
    pouzijNastaveni();
    aktualizujListu();
    naplanujPrepocet();
    zalohuj();            // jako každá úprava (záloha / koncept strany zpěvníku)
  },
};
