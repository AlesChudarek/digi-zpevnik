// Navržené strany ve čtečce (songbook_view.html).
//
// Čtečka má pro navrženou stranu místo <img> rámeček .strana-navrh se stejnými rozměry
// (šířku mu dává applyZoom jako obrázkům). Uvnitř je nejdřív uložený náhled (420 px,
// rozmazaný, ale hned) a přes něj se nakreslí ostrá strana z textu (vykresleni.js) -
// ukáže se, až má načtená písma a obrázky, takže nic neposkočí.
//
// Kreslí se líně: jen rámečky na obrazovce a kousek za ní (svitek se stovkou navržených
// stran tak neskládá stovku stran při otevření). Hotová strana se při zoomu jen
// přeškáluje (ResizeObserver), text zůstává vektorový a ostrý při každém přiblížení.
//
// Rámeček nese data-navrh (id verze), data-cislo (číslo strany ve zpěvníku) a data-lp
// (L/P podle pozice). Dvojstrana tentýž rámeček použije pro jinou stranu - čtečka pak
// zavolá window.StranyCtecky.obnov(el).

import { vykresliStranu, dokonciStranu, formatStrany, pouzitaPisma, MM_NA_PX } from './vykresleni.js';

const zdroje = new Map();   // id verze -> Promise<zdroj>

function nactiZdroj(id) {
  if (!zdroje.has(id)) {
    const p = fetch(`/api/navrh/strana/${id}`, { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) throw new Error(d.error || 'Strana se nenačetla');
        return d.zdroj;
      });
    // Chyba se nedrží v paměti - při dalším přiblížení se zkusí znovu.
    p.catch(() => zdroje.delete(id));
    zdroje.set(id, p);
  }
  return zdroje.get(id);
}

const klicStrany = (el) => `${el.dataset.navrh}|${el.dataset.cislo || ''}|${el.dataset.lp || ''}`;

function meritko(el, obal) {
  const sirka = el.clientWidth;
  return sirka > 0 ? sirka / obal.sirkaPx : 0;
}

function preskaluj(el) {
  const obal = el._strana;
  if (!obal) return;
  const k = meritko(el, obal);
  if (k > 0) obal.koren.style.transform = `scale(${k})`;
}

async function pockejNaObrazky(koren) {
  const obrazky = [...koren.querySelectorAll('img')];
  await Promise.all(obrazky.map((img) => (img.decode ? img.decode() : Promise.resolve()).catch(() => {})));
}

async function vykresli(el) {
  const klic = klicStrany(el);
  if (!el.dataset.navrh || el._klic === klic) return;
  el._klic = klic;
  // Předchozí strana (dvojstrana tentýž rámeček použije znovu) pryč, náhled zpátky.
  if (el._strana) { el._strana.drzak.remove(); el._strana = null; }
  el.classList.remove('ostra');

  let zdroj;
  try {
    zdroj = await nactiZdroj(Number(el.dataset.navrh));
  } catch (chyba) {
    // Náhled v rámečku zůstane; příště (obnov / přiblížení) se zkusí znovu.
    if (el._klic === klic) el._klic = null;
    console.warn('Navržená strana se nenačetla:', el.dataset.navrh, chyba);
    return;
  }
  if (el._klic !== klic) return;   // mezitím se přelistovalo

  const f = formatStrany(zdroj.nastaveni);
  const koren = vykresliStranu(zdroj, {
    cislo: el.dataset.cislo ? Number(el.dataset.cislo) : null,
    strana: el.dataset.lp === 'L' ? 'L' : 'P',
  });
  const drzak = document.createElement('div');
  drzak.className = 'strana-navrh-text';
  drzak.appendChild(koren);
  el.appendChild(drzak);
  el._strana = { drzak, koren, sirkaPx: f.sirka * MM_NA_PX };
  preskaluj(el);

  // Písma: vynucené rozložení je teprve vyžádá, ready pak počká, až dorazí. Výslovné
  // load() pro použité rodiny pojistí prohlížeče, které při rozložení nezačnou hned.
  void koren.offsetHeight;
  await Promise.all([...pouzitaPisma(zdroj)].map((r) => document.fonts.load(`12px '${r}'`).catch(() => {})));
  await dokonciStranu(koren, zdroj, meritko(el, el._strana) || 1);
  await pockejNaObrazky(koren);
  if (el._klic !== klic) return;
  el.classList.add('ostra');
}

const hlidac = ('IntersectionObserver' in window)
  ? new IntersectionObserver((zaznamy) => {
      for (const z of zaznamy) if (z.isIntersecting) vykresli(z.target);
    }, { rootMargin: '150% 0px' })
  : null;
const velikost = ('ResizeObserver' in window)
  ? new ResizeObserver((zaznamy) => { for (const z of zaznamy) preskaluj(z.target); })
  : null;

const sledovane = new WeakSet();

function obnov(el) {
  if (!sledovane.has(el)) {
    sledovane.add(el);
    if (hlidac) hlidac.observe(el);
    if (velikost) velikost.observe(el);
  }
  if (!hlidac) { vykresli(el); return; }
  // Rámeček už na obrazovce je (dvojstrana): hlídač by o změně strany nedal vědět.
  const r = el.getBoundingClientRect();
  if (r.width > 0 && r.bottom > -window.innerHeight * 1.5 && r.top < window.innerHeight * 2.5) vykresli(el);
}

window.StranyCtecky = { obnov, preskaluj };
document.querySelectorAll('.strana-navrh').forEach(obnov);
