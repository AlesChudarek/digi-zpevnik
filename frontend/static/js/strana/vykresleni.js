// Vykreslení navržené strany mimo editor: pro čtečku a pro obrázek do PDF.
//
// Ze zdroje strany ({ verze, nastaveni, obsah }) postaví hotovou stránku se stejnými
// třídami a stejnou stavbou akordů jako editor, takže ji kreslí stejné CSS (strana.css)
// a vypadá stejně. Číslo strany je vrstva podle toho, kde strana ve zpěvníku je - do
// obsahu se nezapéká.
//
// obrazekStrany() z toho udělá PNG: stránka se přes SVG <foreignObject> nakreslí na
// plátno. Písma a obrázky musí být uvnitř SVG jako data: URL - SVG obrázek si nic
// nestahuje.

import { DOMSerializer } from 'prosemirror';
import { schema } from './schema.js';
import { znackySlok } from './sloky.js';
import { CisloStrany } from './cislo-strany.js';
import { prepocitej } from './kolize.js';

export const MM_NA_PX = 96 / 25.4;

export const FORMATY = {
  A5: { sirka: 148, vyska: 210, okraj: 10 },
  A4: { sirka: 210, vyska: 297, okraj: 15 },
};

export const formatStrany = (nastaveni) => FORMATY[nastaveni.format] || FORMATY.A5;

// Pravidlo jednoho stylu odstavce. `pred` omezí pravidlo na jednu stranu (čtečka jich
// ukazuje víc a kopie stylů se můžou lišit).
export function cssStylu(pred, klic, s) {
  return `${pred} .ps-${klic} {
    font-family: '${s.pismo}', sans-serif; font-size: ${s.velikost}pt; line-height: ${s.radkovani};
    font-weight: ${s.tucne ? 700 : 400}; font-style: ${s.kurziva ? 'italic' : 'normal'};
    text-decoration: ${s.podtrzeni ? 'underline' : 'none'};
    color: ${s.barva}; text-align: ${s.zarovnani}; padding-left: ${s.odsazeni}mm;
    margin: ${s.pred}pt 0 ${s.po}pt; }`;
}

// Proměnné akordů (písmo, velikost, barva, řez) na prvku strany.
export function nastavAkordy(styl, n) {
  styl.setProperty('--pismo-akordu', `'${n.pismoAkordu}'`);
  styl.setProperty('--velikost-akordu', n.velikostAkordu + 'pt');
  styl.setProperty('--barva-akordu', n.barvaAkordu);
  styl.setProperty('--vaha-akordu', n.tucneAkordy ? 700 : 400);
}

// --- DOM strany -----------------------------------------------------------------------

function element(tag, trida, styl) {
  const e = document.createElement(tag);
  if (trida) e.className = trida;
  if (styl) e.style.cssText = styl;
  return e;
}

const mm = (x) => `${x}mm`;
const polohaPrvku = (a) => `left:${mm(a.x)};top:${mm(a.y)};width:${mm(a.w)};height:${mm(a.h)}`;

// Stejná stavba jako AkordView a PrvekView v editoru, bez ovládacích prvků.
const serializer = (() => {
  const zaklad = DOMSerializer.fromSchema(schema);
  const uzly = {
    ...zaklad.nodes,
    akord(n) {
      const kotva = element('span', 'akord-kotva');
      if (n.attrs.dx || n.attrs.dy) kotva.classList.add('rucne');
      const posun = element('span', 'akord-posun');
      posun.style.setProperty('--dx', n.attrs.dx);
      posun.style.setProperty('--dy', n.attrs.dy);
      const akord = element('span', 'akord');
      akord.dataset.n = n.attrs.n;
      if (n.attrs.vel) akord.style.setProperty('--vel', n.attrs.vel / 100);
      posun.appendChild(akord);
      kotva.append(element('span', 'akord-vzpera'), posun);
      return kotva;
    },
    ramecek(n) {
      const dom = element('div', 'ps-ramecek', polohaPrvku(n.attrs));
      const obsah = element('div', 'ps-ramecek-obsah');
      dom.appendChild(obsah);
      return { dom, contentDOM: obsah };
    },
    obrazek(n) {
      const dom = element('div', `ps-obrazek ps-vrstva-${n.attrs.vrstva === 'za' ? 'za' : 'pred'}`, polohaPrvku(n.attrs));
      const img = element('img');
      img.src = n.attrs.src;
      img.alt = '';
      dom.appendChild(img);
      return dom;
    },
  };
  return new DOMSerializer(uzly, zaklad.marks);
})();

let pocitadlo = 0;

// Strana jako prvek (nepřipojený do dokumentu).
//   zdroj      { nastaveni, obsah } - jak ho ukládá editor
//   cislo      číslo strany ve zpěvníku (null = bez čísla)
//   strana     'L' | 'P' - levá/pravá (poloha čísla)
export function vykresliStranu(zdroj, { cislo = null, strana = 'P' } = {}) {
  const n = zdroj.nastaveni;
  const doc = schema.nodeFromJSON(zdroj.obsah);
  const f = formatStrany(n);
  const id = `ps-s${++pocitadlo}`;

  const koren = element('div', `stranka ps-cteni ${id}`);
  koren.style.width = mm(f.sirka);
  koren.style.height = mm(f.vyska);
  koren.style.setProperty('--okraj', mm(f.okraj));
  nastavAkordy(koren.style, n);

  const styly = element('style');
  styly.textContent = Object.entries(n.styly || {}).map(([k, s]) => cssStylu(`.${id}`, k, s)).join('\n');
  koren.appendChild(styly);

  const obsah = element('div', 'stranka-obsah');
  obsah.appendChild(serializer.serializeFragment(doc.content));
  koren.appendChild(obsah);

  // Prázdný odstavec (mezera mezi slokami) má mít výšku řádku: editor do něj dává
  // zalomení (ProseMirror-trailingBreak), serializace ne.
  for (const p of obsah.querySelectorAll('.ps-p')) {
    if (!p.firstChild || p.lastChild.nodeName === 'BR') p.appendChild(element('br'));
  }

  // Značky slok: odstavce v DOM jsou ve stejném pořadí jako v dokumentu.
  const role = (k) => (n.styly && n.styly[k] ? n.styly[k].role : k.replace(/-\d+$/, ''));
  const { znacky } = znackySlok(doc, role);
  const poradi = new Map();
  let i = 0;
  doc.descendants((uzel, pos) => {
    if (uzel.type === schema.nodes.odstavec) { poradi.set(pos, i++); return false; }
    return true;
  });
  const odstavce = obsah.querySelectorAll('.ps-p');
  for (const z of znacky) {
    const p = odstavce[poradi.get(z.pos)];
    if (p) { p.classList.add('ps-zacatek'); p.dataset.znacka = z.znacka; }
  }

  // Číslo strany jako vrstva.
  const c = n.cislo;
  if (c && c.zobrazit && !n.cisloSkryte && cislo != null) {
    const { x, y } = CisloStrany.poloha(c, f, strana);
    const e = element('div', 'ps-cislo-strany');
    e.textContent = String(cislo);
    e.style.cssText = `left:${mm(x)};top:${mm(y)};font-family:'${c.pismo}',sans-serif;`
      + `font-size:${c.velikost}pt;font-weight:${c.tucne ? 700 : 400};color:${c.barva}`;
    koren.appendChild(e);
  }
  return koren;
}

// Dokončení po připojení do stránky: písma a (volitelně) automatická mezera mezi akordy,
// která potřebuje změřit rozložení.
export async function dokonciStranu(koren, zdroj, meritko = 1) {
  await document.fonts.ready;
  if (zdroj.nastaveni.automatickaMezera) prepocitej(koren.querySelector('.stranka-obsah'), { automatickaMezera: true, meritko });
}

// --- obrázek strany (PNG) ---------------------------------------------------------------

const cacheDat = new Map();
async function jakoDataUrl(url) {
  if (url.startsWith('data:')) return url;
  if (!cacheDat.has(url)) {
    cacheDat.set(url, fetch(url).then((r) => r.blob()).then((b) => new Promise((ok) => {
      const fr = new FileReader();
      fr.onload = () => ok(fr.result);
      fr.readAsDataURL(b);
    })));
  }
  return cacheDat.get(url);
}

// CSS potřebné pro stranu: strana.css a písma (jen použité rodiny, jako data: URL).
async function cssProObrazek(rodiny) {
  const casti = [];
  for (const list of document.styleSheets) {
    const href = list.href || '';
    if (!/\/css\/strana\.css|\/fonts\/strana\/pisma\.css/.test(href)) continue;
    for (const pravidlo of list.cssRules) {
      if (pravidlo instanceof CSSFontFaceRule) {
        const rodina = pravidlo.style.getPropertyValue('font-family').replace(/['"]/g, '').trim();
        if (!rodiny.has(rodina)) continue;
        const m = /url\(["']?([^"')]+)["']?\)/.exec(pravidlo.style.getPropertyValue('src'));
        if (!m) continue;
        const data = await jakoDataUrl(new URL(m[1], href).href);
        casti.push(pravidlo.cssText.replace(m[0], `url("${data}")`));
      } else {
        casti.push(pravidlo.cssText);
      }
    }
  }
  return casti.join('\n');
}

function pouzitaPisma(zdroj) {
  const n = zdroj.nastaveni;
  const rodiny = new Set([n.pismoAkordu, n.cislo && n.cislo.pismo]);
  for (const s of Object.values(n.styly || {})) rodiny.add(s.pismo);
  const projdi = (u) => {
    for (const m of u.marks || []) if (m.type === 'pismo') rodiny.add(m.attrs.f);
    for (const d of u.content || []) projdi(d);
  };
  projdi(zdroj.obsah);
  rodiny.delete(undefined);
  return rodiny;
}

// PNG strany o šířce `sirkaPx` (1748 = A5 při 300 dpi). Vrací { blob, ms }.
export async function obrazekStrany(zdroj, { cislo = null, strana = 'P', sirkaPx = 1748 } = {}) {
  const zacatek = performance.now();
  const f = formatStrany(zdroj.nastaveni);
  const sirka = Math.round(f.sirka * MM_NA_PX), vyska = Math.round(f.vyska * MM_NA_PX);
  const koren = vykresliStranu(zdroj, { cislo, strana });
  // Mimo obrazovku, v měřítku 1 - kvůli měření automatické mezery a načtení písem.
  const drzak = element('div', '', 'position:fixed;left:-20000px;top:0;pointer-events:none');
  koren.style.position = 'relative';
  drzak.appendChild(koren);
  document.body.appendChild(drzak);
  try {
    await dokonciStranu(koren, zdroj, 1);
    for (const img of koren.querySelectorAll('img')) img.src = await jakoDataUrl(img.src);
    const css = await cssProObrazek(pouzitaPisma(zdroj));
    koren.style.boxShadow = 'none';
    const html = new XMLSerializer().serializeToString(koren);
    const vyskaPx = Math.round(sirkaPx * vyska / sirka);
    // Zvětšení přes CSS transform v obsahu, ne přes viewBox: WebKit obsah <foreignObject>
    // podle viewBox nezvětší a strana by zabrala jen levý horní roh obrázku.
    const k = sirkaPx / sirka;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${sirkaPx}" height="${vyskaPx}">`
      + `<foreignObject x="0" y="0" width="${sirkaPx}" height="${vyskaPx}">`
      + `<div xmlns="http://www.w3.org/1999/xhtml" style="width:${sirka}px;height:${vyska}px;background:#fff;transform:scale(${k});transform-origin:0 0">`
      + `<style>${css.replace(/</g, '\\3c ')}</style>${html}</div></foreignObject></svg>`;
    // data: URL, ne blob: - u blob: SVG s foreignObject plátno některé prohlížeče
    // „zablokují“ a obrázek z něj pak nejde uložit.
    const obr = new Image();
    const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    {
      await new Promise((ok, chyba) => { obr.onload = ok; obr.onerror = () => chyba(new Error('SVG se nenačetlo')); obr.src = url; });
      try { await obr.decode(); } catch (_) { /* nepodporuje - nevadí */ }
      const platno = element('canvas');
      platno.width = sirkaPx;
      platno.height = vyskaPx;
      const c = platno.getContext('2d');
      c.fillStyle = '#fff';
      c.fillRect(0, 0, sirkaPx, vyskaPx);
      c.drawImage(obr, 0, 0, sirkaPx, vyskaPx);
      const blob = await new Promise((ok, chyba) => {
        try { platno.toBlob((b) => (b ? ok(b) : chyba(new Error('Obrázek se nevytvořil'))), 'image/png'); } catch (e) { chyba(e); }
      });
      return { blob, ms: Math.round(performance.now() - zacatek) };
    }
  } finally {
    drzak.remove();
  }
}
