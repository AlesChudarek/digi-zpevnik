// Vektorové PDF navržené strany: text zůstane textem (ostrý, jde označit a hledat).
//
// Nesází se znovu. Stránku rozmístí prohlížeč (vykresleni.js, stejně jako čtečka
// a editor) a odsud se jen opíše, co kde je: každý úsek textu na řádku, akordy, značky
// slok, čáry podtržení a obrázky, se změřenou polohou a stejným písmem. Proto PDF
// vypadá jako obrazovka - výsledek se v testu porovnává po pixelech.
//
// Písma jsou podmnožiny latin a latin-ext (unicode-range v pisma.css): úsek textu se
// podle znaků rozdělí na části, každá se kreslí souborem, který ty znaky obsahuje.
// Knihovna (pdf-lib + fontkit, 1,4 MB) se načítá až tady, při prvním PDF. Písma do PDF
// jsou TTF (fonts/strana/ttf/) - fontkit s WOFF2 se složenými znaky padá.

import { vykresliStranu, dokonciStranu, formatStrany } from './vykresleni.js';

const PT_NA_PX = 0.75;          // 1 CSS px = 0.75 pt
const PT_NA_MM = 72 / 25.4;

let knihovna = null;
async function nactiKnihovnu() {
  // Relativně k tomuto souboru; mapa importů v šabloně dá adrese verzi (cache).
  if (!knihovna) knihovna = import(new URL('../../vendor/pdf-lib/pdf-lib.js', import.meta.url).href);
  return knihovna;
}

// --- písma: rodina + řez + znak -> soubor woff2 --------------------------------------

let souboryPisem = null;      // [{ rodina, vaha, styl, url, rozsahy: [[od, do]] }]

function rozsahy(unicodeRange) {
  return unicodeRange.split(',').map((r) => {
    const m = /U\+([0-9A-F?]+)(?:-([0-9A-F]+))?/i.exec(r.trim());
    if (!m) return null;
    const od = parseInt(m[1].replace(/\?/g, '0'), 16);
    const d = m[2] ? parseInt(m[2], 16) : parseInt(m[1].replace(/\?/g, 'F'), 16);
    return [od, d];
  }).filter(Boolean);
}

function nactiSouboryPisem() {
  if (souboryPisem) return souboryPisem;
  souboryPisem = [];
  for (const list of document.styleSheets) {
    if (!/\/fonts\/strana\/pisma\.css/.test(list.href || '')) continue;
    for (const p of list.cssRules) {
      if (!(p instanceof CSSFontFaceRule)) continue;
      const st = p.style;
      const m = /url\(["']?([^"')]+)["']?\)/.exec(st.getPropertyValue('src'));
      if (!m) continue;
      souboryPisem.push({
        rodina: st.getPropertyValue('font-family').replace(/['"]/g, '').trim(),
        vaha: parseInt(st.getPropertyValue('font-weight'), 10) >= 600 ? 700 : 400,
        styl: /italic/.test(st.getPropertyValue('font-style')) ? 'italic' : 'normal',
        // Do PDF se vkládá TTF: fontkit na WOFF2 se složenými znaky (ě, š…) padá
        // („Index out of range“). V ttf/ je každý řez jako JEDEN soubor (latin
        // a latin-ext sloučené fontTools) - slovo s „ě“ je pak v PDF jedno slovo a jde
        // najít i zkopírovat.
        url: new URL(m[1].replace(/^\.\/(.*)-latin(?:-ext)?-(.*)\.woff2$/, './ttf/$1-$2.ttf'), list.href).href,
        rozsahy: rozsahy(st.getPropertyValue('unicode-range') || 'U+0-10FFFF'),
      });
    }
  }
  return souboryPisem;
}

function souborProZnak(rodina, vaha, styl, kod) {
  const kandidati = nactiSouboryPisem().filter((s) => s.rodina === rodina);
  const presny = kandidati.filter((s) => s.vaha === vaha && s.styl === styl);
  const vyber = presny.length ? presny : kandidati.filter((s) => s.styl === styl).length
    ? kandidati.filter((s) => s.styl === styl) : kandidati;
  return vyber.find((s) => s.rozsahy.some(([od, d]) => kod >= od && kod <= d)) || vyber[0] || null;
}

// --- měření vykreslené strany ----------------------------------------------------------

const prvniRodina = (ff) => ff.split(',')[0].replace(/['"]/g, '').trim();

// Účaří v rámečku `r` (top, height): prohlížeč dá písmo (výška nad a pod účařím, jak ji
// měří i canvas) doprostřed rámečku. U obdélníku znaku je rámeček přesně tak vysoký
// jako písmo (účaří = top + nad), u akordu s výškou řádku 1 je menší a písmo z něj
// nahoře i dole přečnívá.
const platnoMereni = document.createElement('canvas').getContext('2d');
function ucari(top, vyska, styl) {
  platnoMereni.font = `${styl.fontStyle} ${styl.fontWeight} ${styl.fontSize} ${styl.fontFamily}`;
  const m = platnoMereni.measureText('Hg');
  const nad = m.fontBoundingBoxAscent, pod = m.fontBoundingBoxDescent;
  return top + (vyska - (nad + pod)) / 2 + nad;
}

// Obrázek do PDF. pdf-lib umí jen PNG a JPEG; typ se pozná z obsahu (adresa nahraného
// obrázku /navrh/obrazek/<id> příponu nemá). Jiný formát (sken stránky bývá i WEBP
// nebo GIF) se převede na PNG přes plátno.
async function vlozObrazek(doc, src) {
  const data = new Uint8Array(await fetch(src).then((x) => x.arrayBuffer()));
  if (data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47) return doc.embedPng(data);
  if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return doc.embedJpg(data);
  const bitmapa = await createImageBitmap(new Blob([data]));
  const platno = document.createElement('canvas');
  platno.width = bitmapa.width;
  platno.height = bitmapa.height;
  platno.getContext('2d').drawImage(bitmapa, 0, 0);
  const png = await new Promise((ok) => platno.toBlob(ok, 'image/png'));
  return doc.embedPng(new Uint8Array(await png.arrayBuffer()));
}

function barva(css) {
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(css || '');
  if (hex) return hex.slice(1).map((x) => parseInt(x, 16) / 255);
  const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)/.exec(css);
  return m ? [m[1] / 255, m[2] / 255, m[3] / 255] : [0, 0, 0];
}

const podpis = (st) => [st.fontFamily, st.fontSize, st.fontWeight, st.fontStyle, st.color, st.textDecorationLine].join('|');

// Úseky textu: slova (souvislé znaky na jednom vizuálním řádku) se změřenou polohou.
function useckyTextu(koren, r0) {
  const useky = [];
  const chodec = document.createTreeWalker(koren, NodeFilter.SHOW_TEXT);
  const g = document.createRange();
  let uzel;
  while ((uzel = chodec.nextNode())) {
    const rodic = uzel.parentElement;
    if (!rodic || rodic.closest('.akord-kotva')) continue;
    const st = getComputedStyle(rodic);
    const text = uzel.data;
    let usek = null;
    for (let i = 0; i < text.length; i++) {
      g.setStart(uzel, i);
      g.setEnd(uzel, i + 1);
      const r = [...g.getClientRects()].find((x) => x.width > 0) || g.getBoundingClientRect();
      const top = Math.round((r.top - r0.top) * 4) / 4;
      // Nový úsek: jiný řádek, nebo nové slovo - každé slovo začne přesně tam, kde ho
      // má prohlížeč (i při zarovnání do bloku), a drobný rozdíl šířek se nesčítá.
      const noveSlovo = usek && /\s/.test(usek.text.slice(-1)) && !/\s/.test(text[i]);
      if (!usek || usek.top !== top || text[i] === '\n' || noveSlovo) {
        if (usek) useky.push(usek);
        // Kus, který přesně navazuje na předchozí (stejný řádek a písmo, text rozdělený
        // jen akordem uprostřed slova), pokračuje v něm - „sedí[Emi]me“ je v PDF jedno
        // slovo „sedíme“.
        const minuly = !usek && useky[useky.length - 1];
        if (minuly && minuly.top === top && Math.abs(minuly.prav - (r.left - r0.left)) < 0.6
            && podpis(minuly.styl) === podpis(st) && !/\s$/.test(minuly.text)) {
          usek = useky.pop();
        } else {
          usek = { text: '', x: r.left - r0.left, top, vyska: r.height, styl: st, prvek: rodic };
        }
      }
      if (text[i] !== '\n') {
        usek.text += text[i];
        usek.prav = r.right - r0.left;
        if (!/\s/.test(text[i])) usek.pravSlova = r.right - r0.left;
      }
    }
    if (usek) useky.push(usek);
  }
  return useky.filter((u) => u.text.trim());
}

// --- PDF -------------------------------------------------------------------------------

// Vektorové PDF jedné strany. Vrací { bytes: Uint8Array, ms }.
export async function pdfStrany(zdroj, { cislo = null, strana = 'P' } = {}) {
  const zacatek = performance.now();
  const { PDFDocument, rgb, fontkit, setCharacterSpacing } = await nactiKnihovnu();
  const f = formatStrany(zdroj.nastaveni);
  const koren = vykresliStranu(zdroj, { cislo, strana });
  const drzak = document.createElement('div');
  drzak.style.cssText = 'position:fixed;left:-20000px;top:0;pointer-events:none';
  koren.style.position = 'relative';
  drzak.appendChild(koren);
  document.body.appendChild(drzak);
  try {
    await dokonciStranu(koren, zdroj, 1);
    // Názvy akordů a značky slok jsou na obrazovce ::before (CSS), jejich písmo se dá
    // změřit jen nepřímo. V téhle kopii (mimo obrazovku) se z nich stane skutečný text
    // na stejném místě a změří se stejně přesně jako ostatní slova.
    koren.classList.add('ps-pro-pdf');
    for (const a of koren.querySelectorAll('.akord')) a.textContent = a.dataset.n || '';
    for (const p of koren.querySelectorAll('.ps-zacatek')) {
      const z = document.createElement('span');
      z.className = 'ps-znacka-text';
      z.textContent = p.dataset.znacka;
      p.insertBefore(z, p.firstChild);
    }
    const r0 = koren.getBoundingClientRect();
    const doc = await PDFDocument.create();
    doc.registerFontkit(fontkit);
    const stranka = doc.addPage([f.sirka * PT_NA_MM, f.vyska * PT_NA_MM]);
    const H = f.vyska * PT_NA_MM;
    // Co je celé mimo stranu (prvek odsunutý přes okraj), do PDF nejde - nebylo by vidět,
    // ale hledání a kopírování textu by ho našlo. Každé slovo má vlastní polohu, takže
    // vynechání nic dalšího neposune; zčásti viditelné slovo se zapíše a strana ho ořízne.
    const mimo = (l, t, r, b) => r <= r0.left || l >= r0.right || b <= r0.top || t >= r0.bottom;
    const vlozena = new Map();
    const pismo = async (soubor) => {
      if (!vlozena.has(soubor.url)) {
        vlozena.set(soubor.url, fetch(soubor.url).then((r) => r.arrayBuffer()).then((b) => doc.embedFont(b, { subset: true })));
      }
      return vlozena.get(soubor.url);
    };

    // Text v daném stylu od x (px) na účaří (px): rozdělí se podle souborů písma.
    // sirkaPx: šířka, kterou má text v prohlížeči. Když je text jedním písmem, dorovná
    // se rozestupem znaků - prohlížeč na obrazovce šířky zaokrouhluje jinak než písmo
    // v PDF a ve slově by se rozdíl sčítal.
    const kresliText = async (text, x, ucari, st, rodinaPrebit = null, sirkaPx = null) => {
      const rodina = rodinaPrebit || prvniRodina(st.fontFamily);
      const vaha = parseInt(st.fontWeight, 10) >= 600 ? 700 : 400;
      const styl = st.fontStyle === 'italic' ? 'italic' : 'normal';
      const velikostPt = parseFloat(st.fontSize) * PT_NA_PX;
      const [cr, cg, cb] = barva(st.color);
      let kus = '', souborKusu = null, xKusu = x;
      const kusy = [];
      for (const z of text) {
        const s = souborProZnak(rodina, vaha, styl, z.codePointAt(0));
        if (!souborKusu || (s && s.url !== souborKusu.url)) { if (kus) kusy.push([kus, souborKusu]); kus = ''; souborKusu = s; }
        kus += z;
      }
      if (kus) kusy.push([kus, souborKusu]);
      for (const [t, soubor] of kusy) {
        if (!soubor) continue;
        const font = await pismo(soubor);
        const sirkaPdf = font.widthOfTextAtSize(t, velikostPt);
        const znaku = [...t].length;
        const rozestup = sirkaPx != null && kusy.length === 1 && znaku > 1
          ? (sirkaPx * PT_NA_PX - sirkaPdf) / (znaku - 1) : 0;
        if (rozestup) stranka.pushOperators(setCharacterSpacing(rozestup));
        stranka.drawText(t, { x: xKusu * PT_NA_PX, y: H - ucari * PT_NA_PX, size: velikostPt, font, color: rgb(cr, cg, cb) });
        if (rozestup) stranka.pushOperators(setCharacterSpacing(0));
        xKusu += (sirkaPdf + rozestup * (znaku - 1)) / PT_NA_PX;
      }
      return xKusu;
    };

    // Prvky odspodu nahoru podle vrstvy (z-index), při shodě v pořadí stránky.
    const kresliObrazek = async (o) => {
      const img = o.querySelector('img');
      const r = o.getBoundingClientRect();
      if (mimo(r.left, r.top, r.right, r.bottom)) return;
      const emb = await vlozObrazek(doc, img.src);
      stranka.drawImage(emb, {
        x: (r.left - r0.left) * PT_NA_PX, y: H - (r.bottom - r0.top) * PT_NA_PX,
        width: r.width * PT_NA_PX, height: r.height * PT_NA_PX,
      });
    };
    // Náplast: mnohoúhelník (body v podílech obdélníku kolem, viz schema.js) jako
    // vektorová cesta; SVG cesta v pdf-lib má osu y dolů od zadaného počátku.
    const kresliTvar = (t) => {
      const r = t.getBoundingClientRect();
      if (mimo(r.left, r.top, r.right, r.bottom)) return;
      const body = (t.dataset.body || '0,0 1,0 1,1 0,1').trim().split(/\s+/).map((b) => b.split(',').map(Number));
      // Otočená náplast: getBoundingClientRect dá obdélník kolem otočeného tvaru - střed
      // sedí, rozměr je z rozložení (offsetWidth) a body se otočí kolem středu.
      const w = t.offsetWidth * PT_NA_PX, h = t.offsetHeight * PT_NA_PX;
      const u = (parseFloat(t.dataset.otoceni) || 0) * Math.PI / 180;
      const cos = Math.cos(u), sin = Math.sin(u);
      const cesta = body.map(([fx, fy], i) => {
        const lx = fx * w - w / 2, ly = fy * h - h / 2;
        return `${i ? 'L' : 'M'}${lx * cos - ly * sin} ${lx * sin + ly * cos}`;
      }).join(' ') + ' Z';
      stranka.drawSvgPath(cesta, {
        x: ((r.left + r.right) / 2 - r0.left) * PT_NA_PX, y: H - ((r.top + r.bottom) / 2 - r0.top) * PT_NA_PX,
        color: rgb(...barva(t.dataset.barva || '#ffffff')), borderWidth: 0,
      });
    };
    // Text odstavců rámečku a jeho akordy (v této kopii skutečný text, viz výš).
    const kresliRamecek = async (ramecek) => {
      for (const u of useckyTextu(ramecek, r0)) {
        if (mimo(r0.left + u.x, r0.top + u.top, r0.left + (u.pravSlova ?? u.prav), r0.top + u.top + u.vyska)) continue;
        const ucariU = ucari(u.top, u.vyska, u.styl);
        // Mezera za slovem se nekreslí: další slovo má vlastní polohu a mezery navíc by
        // se při kopírování textu z PDF zdvojovaly.
        const konec = await kresliText(u.text.replace(/\s+$/, ''), u.x, ucariU, u.styl, null, (u.pravSlova ?? u.prav) - u.x);
        if (/underline/.test(u.styl.textDecorationLine)) {
          const tl = Math.max(0.5, parseFloat(u.styl.fontSize) / 16);
          stranka.drawLine({
            start: { x: u.x * PT_NA_PX, y: H - (ucariU + tl * 1.6) * PT_NA_PX },
            end: { x: konec * PT_NA_PX, y: H - (ucariU + tl * 1.6) * PT_NA_PX },
            thickness: tl * PT_NA_PX, color: rgb(...barva(u.styl.color)),
          });
        }
      }
      for (const a of ramecek.querySelectorAll('.akord')) {
        if (!a.firstChild) continue;
        const st = getComputedStyle(a);
        const g = document.createRange();
        g.selectNodeContents(a);
        const r = g.getBoundingClientRect();
        if (mimo(r.left, r.top, r.right, r.bottom)) continue;
        await kresliText(a.textContent, r.left - r0.left, ucari(r.top - r0.top, r.height, st), st, null, r.width);
      }
    };

    const prvky = [...koren.querySelectorAll('.stranka-obsah > .ps-ramecek, .stranka-obsah > .ps-obrazek, .stranka-obsah > .ps-tvar')];
    const vrstva = (o) => parseInt(o.style.zIndex, 10) || 0;
    prvky.sort((a, b) => vrstva(a) - vrstva(b));    // sort je stabilní - shoda zůstane v pořadí
    for (const o of prvky) {
      if (o.classList.contains('ps-tvar')) kresliTvar(o);
      else if (o.classList.contains('ps-obrazek')) await kresliObrazek(o);
      else await kresliRamecek(o);
    }

    // Číslo strany (ve zpěvníku ho dokreslí server - tady jen pro náhled).
    const c = koren.querySelector('.ps-cislo-strany');
    if (c) {
      const st = getComputedStyle(c);
      const g = document.createRange();
      g.selectNodeContents(c);
      const r = g.getBoundingClientRect();
      await kresliText(c.textContent, r.left - r0.left, ucari(r.top - r0.top, r.height, st), st);
    }

    const bytes = await doc.save();
    return { bytes, ms: Math.round(performance.now() - zacatek) };
  } finally {
    drzak.remove();
  }
}
