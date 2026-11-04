// Akordy podle předchozí sloky: doplní do sloky bez akordů akordy ze vzorové sloky na
// „stejná“ místa.
//
// Sloky mívají stejnou melodii, ale jiný text, takže poloha ve znacích nesedí. Akord
// se proto přenáší po slabikách: v řádku vzoru se zjistí, na kolikáté slabice akord
// leží, a v cílovém řádku se dá na začátek slabiky se stejným pořadím. Když mají řádky
// různý počet slabik, pořadí se přepočte poměrem. Nemusí to být dokonalé - autor pak
// opraví pár akordů tažením, místo aby jich deset klikal. Při různém počtu slabik se
// akordy z první poloviny řádku počítají od začátku, z druhé od konce.

import { closeHistory } from 'prosemirror';

// Samohlásky (jádra slabik); dvojhlásky ou, au, eu jsou jedno jádro.
const SAMOHLASKA = /[aeiouyáéíóúůýěäöü]/i;
const DVOJHLASKY = ['ou', 'au', 'eu'];

function jePismeno(z) { return /[\p{L}]/u.test(z); }

// Řádek: znaky textu s pozicemi v dokumentu (akordy vynechané). Odstavec se dělí
// i ručním zalomením (Shift+Enter).
export function radkyOdstavce(uzel, pos) {
  const radky = [[]];
  uzel.forEach((n, off) => {
    const p = pos + 1 + off;
    if (n.type.name === 'zalomeni') { radky.push([]); return; }
    if (!n.isText) return;
    for (let i = 0; i < n.text.length; i++) radky[radky.length - 1].push({ z: n.text[i], pos: p + i });
  });
  return radky;
}

// Začátky slabik v řádku (indexy znaků). Začátek slabiky je první souhláska skupiny před
// jejím jádrem, nejdál začátek slova.
function zacatkySlabik(znaky) {
  const zacatky = [];
  let i = 0;
  while (i < znaky.length) {
    if (!jePismeno(znaky[i].z)) { i += 1; continue; }
    // slovo [i, konec)
    let konec = i;
    while (konec < znaky.length && jePismeno(znaky[konec].z)) konec += 1;
    let zacatekSlabiky = i;
    let j = i;
    let prvni = true;
    while (j < konec) {
      if (SAMOHLASKA.test(znaky[j].z)) {
        zacatky.push(prvni ? i : zacatekSlabiky);
        prvni = false;
        const dvoj = j + 1 < konec && DVOJHLASKY.includes((znaky[j].z + znaky[j + 1].z).toLowerCase());
        j += dvoj ? 2 : 1;
        // Souhlásky do dalšího jádra patří k další slabice (zjednodušeně celé).
        zacatekSlabiky = j;
        while (zacatekSlabiky < konec && !SAMOHLASKA.test(znaky[zacatekSlabiky].z)) zacatekSlabiky += 1;
        // Mezi jádry víc souhlásek: první zůstává předchozí slabice („pís-ně“).
        const souhlasek = zacatekSlabiky - j;
        zacatekSlabiky = souhlasek >= 2 ? j + 1 : j;
        if (zacatekSlabiky >= konec) break;
      } else j += 1;
    }
    // Slovo bez samohlásky (krk, vlk, s, v): celé slovo jako jedna slabika.
    if (prvni) zacatky.push(i);
    i = konec;
  }
  return zacatky;
}

// Index slabiky, do které patří znak na indexu `a` (nejbližší začátek slabiky vlevo).
function slabikaZnaku(zacatky, a) {
  let k = 0;
  for (let i = 0; i < zacatky.length; i++) if (zacatky[i] <= a) k = i;
  return k;
}

// Akordy vzorové sloky po řádcích: [{ slabik, akordy: [{ slabika | naKonci, n }] }].
// Akord patří ke znaku, který za ním následuje; bez něj (konec odstavce) je „na konci“.
function akordyVzoru(vzor) {
  const radky = [];
  for (const { node, pos } of vzor) {
    const radkyO = radkyOdstavce(node, pos);
    const zacatkyO = radkyO.map(zacatkySlabik);
    const vRadcich = radkyO.map(() => []);
    node.forEach((n, off) => {
      if (n.type.name !== 'akord') return;
      const p = pos + 1 + off;
      for (let r = 0; r < radkyO.length; r++) {
        const idx = radkyO[r].findIndex((z) => z.pos > p);
        if (idx >= 0) { vRadcich[r].push({ slabika: slabikaZnaku(zacatkyO[r], idx), n: n.attrs.n }); return; }
      }
      vRadcich[radkyO.length - 1].push({ naKonci: true, n: n.attrs.n });
    });
    radkyO.forEach((znaky, r) => {
      if (znaky.length || vRadcich[r].length) radky.push({ slabik: zacatkyO[r].length, akordy: vRadcich[r] });
    });
  }
  return radky;
}

// Kam v cílové sloce dát akordy: [{ pos, n }].
export function navrhniAkordy(vzor, cil) {
  const radkyVzoru = akordyVzoru(vzor);
  if (!radkyVzoru.some((r) => r.akordy.length)) return [];
  const navrhy = [];
  let i = 0;
  for (const { node, pos } of cil) {
    for (const znaky of radkyOdstavce(node, pos)) {
      if (!znaky.length) continue;
      const r = radkyVzoru[i % radkyVzoru.length];
      i += 1;
      const zacatky = zacatkySlabik(znaky);
      const obsazene = new Set();
      for (const a of r.akordy) {
        if (a.naKonci) { navrhy.push({ pos: znaky[znaky.length - 1].pos + 1, n: a.n }); continue; }
        if (!zacatky.length) continue;
        // Různý počet slabik: akordy z první poloviny řádku se počítají od začátku, z druhé
        // od konce - fráze se drží začátku i konce řádku, rozdíl bývá uprostřed.
        let k = a.slabika;
        if (r.slabik !== zacatky.length && a.slabika >= r.slabik / 2) k = zacatky.length - (r.slabik - a.slabika);
        k = Math.max(0, Math.min(k, zacatky.length - 1));
        while (obsazene.has(k) && k < zacatky.length - 1) k += 1;
        if (obsazene.has(k)) continue;
        obsazene.add(k);
        navrhy.push({ pos: znaky[zacatky[k]].pos, n: a.n });
      }
    }
  }
  return navrhy;
}

// --- sloky v dokumentu ---------------------------------------------------------------

// Sloka = řada neprázdných odstavců se stejnou rolí (sloka/refrén) v jednom rámečku.
// role(styl) -> 'sloka' | 'refren' | …
export function slokyDokumentu(doc, role) {
  const sloky = [];
  doc.forEach((ramecek, offR) => {
    if (ramecek.type.name !== 'ramecek') return;
    let rozepsana = null;
    ramecek.forEach((p, offP) => {
      const pos = offR + 1 + offP;
      const r = role(p.attrs.styl);
      if ((r === 'sloka' || r === 'refren') && p.content.size) {
        if (!rozepsana || rozepsana.role !== r) {
          rozepsana = { role: r, odstavce: [] };
          sloky.push(rozepsana);
        }
        rozepsana.odstavce.push({ node: p, pos });
      } else rozepsana = null;
    });
  });
  for (const s of sloky) {
    s.od = s.odstavce[0].pos;
    const posledni = s.odstavce[s.odstavce.length - 1];
    s.do = posledni.pos + posledni.node.nodeSize;
    s.akordu = 0;
    for (const { node } of s.odstavce) node.forEach((n) => { if (n.type.name === 'akord') s.akordu += 1; });
  }
  return sloky;
}

// Vzor pro slokou: nejbližší předchozí sloka téže role s akordy, jinak nejbližší další.
export function vzorProSloku(sloky, sloka) {
  const i = sloky.indexOf(sloka);
  for (let j = i - 1; j >= 0; j--) if (sloky[j].role === sloka.role && sloky[j].akordu) return sloky[j];
  for (let j = i + 1; j < sloky.length; j++) if (sloky[j].role === sloka.role && sloky[j].akordu) return sloky[j];
  return null;
}

// Vloží navržené akordy do sloky (jeden krok Zpět). Vrací pozice vložených akordů.
export function doplnAkordy(view, sloka, vzor) {
  const navrhy = navrhniAkordy(vzor.odstavce, sloka.odstavce);
  if (!navrhy.length) return [];
  let tr = closeHistory(view.state.tr);
  // Od konce, ať vkládání neposouvá pozice těch, které teprve přijdou.
  navrhy.sort((a, b) => b.pos - a.pos);
  for (const { pos, n } of navrhy) tr = tr.insert(pos, view.state.schema.nodes.akord.create({ n }));
  view.dispatch(tr);
  // i-tý vložený (zepředu) se posunul o i dřív vložených před ním.
  return navrhy.map((x) => x.pos).sort((a, b) => a - b).map((p, i) => p + i);
}

// Smaže všechny akordy sloky (jeden krok Zpět).
export function smazAkordy(view, sloka) {
  const pozice = [];
  view.state.doc.nodesBetween(sloka.od, sloka.do, (n, p) => { if (n.type.name === 'akord') pozice.push(p); });
  if (!pozice.length) return;
  let tr = closeHistory(view.state.tr);
  for (const p of pozice.reverse()) tr = tr.delete(p, p + 1);
  view.dispatch(tr);
}
