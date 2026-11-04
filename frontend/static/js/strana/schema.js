// Schéma navržené strany (ProseMirror) a převod z/do prostého textu s akordy.
//
// Strana je plátno pevného rozměru s rámečky (jako v Canvě). Textový rámeček je malý
// Word: odstavce se stylem (Normální, Název, Autor, Sloka, Refrén) a přímým formátováním
// (písmo, velikost, barva, tučné, kurzíva, podtržení, zarovnání, odsazení). Styl je jen
// výchozí vzhled ze šablony zpěvníku, ne struktura - každý odstavec jde přenastavit.
//
// Akord je neviditelný inline uzel těsně PŘED znakem, ke kterému patří - totéž co `[Am]`
// v ChordPro, jen si navíc pamatuje vlastní posun a velikost. Tím přežije psaní, mazání,
// zpět i kopírování bez jakékoli vlastní logiky; když se smaže jeho znak, patří k dalšímu.

import { Schema } from 'prosemirror';

export const VERZE_SCHEMATU = 2;

export const STYLY = {
  normalni: 'Normální',
  nadpis: 'Název',
  autor: 'Autor',
  sloka: 'Sloka',
  refren: 'Refrén',
};

// Po Enteru: jakým stylem pokračuje nový odstavec (jako „další styl“ ve Wordu).
export const DALSI_STYL = { nadpis: 'autor', autor: 'sloka', sloka: 'sloka', refren: 'refren', normalni: 'normalni' };

const mm = (x) => `${x}mm`;

export const schema = new Schema({
  nodes: {
    doc: { content: 'ramecek+' },

    // Rámeček: poloha a rozměr v mm od levého horního rohu strany.
    ramecek: {
      content: 'odstavec+', isolating: true, defining: true,
      attrs: { x: { default: 12 }, y: { default: 12 }, w: { default: 124 }, h: { default: 186 } },
      toDOM: (n) => ['div', {
        class: 'ps-ramecek', 'data-x': n.attrs.x, 'data-y': n.attrs.y, 'data-w': n.attrs.w, 'data-h': n.attrs.h,
        style: `left:${mm(n.attrs.x)};top:${mm(n.attrs.y)};width:${mm(n.attrs.w)};height:${mm(n.attrs.h)}`,
      }, ['div', { class: 'ps-ramecek-obsah' }, 0]],
      parseDOM: [{
        tag: 'div.ps-ramecek', contentElement: '.ps-ramecek-obsah',
        getAttrs: (d) => ({ x: +d.dataset.x || 12, y: +d.dataset.y || 12, w: +d.dataset.w || 124, h: +d.dataset.h || 186 }),
      }],
    },

    // znacka: ruční přepis automatického čísla sloky („3.“, „R2:“); null = automaticky.
    odstavec: {
      content: 'inline*',
      attrs: {
        styl: { default: 'normalni' }, zarovnani: { default: null }, odsazeni: { default: null },
        znacka: { default: null },
      },
      toDOM: (n) => {
        const { styl, zarovnani, odsazeni, znacka } = n.attrs;
        const styly = [];
        if (zarovnani) styly.push(`text-align:${zarovnani}`);
        if (odsazeni != null) styly.push(`padding-left:${mm(odsazeni)}`);
        const a = { class: `ps-p ps-${styl}`, 'data-styl': styl };
        if (styly.length) a.style = styly.join(';');
        if (zarovnani) a['data-zarovnani'] = zarovnani;
        if (odsazeni != null) a['data-odsazeni'] = odsazeni;
        if (znacka != null) a['data-vlastni-znacka'] = znacka;
        return ['p', a, 0];
      },
      parseDOM: [{
        tag: 'p',
        getAttrs: (d) => ({
          styl: STYLY[d.dataset.styl] ? d.dataset.styl : 'normalni',
          zarovnani: d.dataset.zarovnani || null,
          odsazeni: d.dataset.odsazeni != null ? Number(d.dataset.odsazeni) : null,
          znacka: d.dataset.vlastniZnacka ?? null,
        }),
      }],
    },

    text: { group: 'inline' },

    zalomeni: {
      inline: true, group: 'inline', selectable: false,
      toDOM: () => ['br'],
      parseDOM: [{ tag: 'br' }],
    },

    // dx, dy: posun proti výchozí poloze ze šablony, v em písma textu (škáluje s ním).
    // vel: vlastní velikost v % výchozí velikosti akordů, null = výchozí.
    akord: {
      group: 'inline', inline: true, atom: true, selectable: true, marks: '',
      attrs: { n: { default: '' }, dx: { default: 0 }, dy: { default: 0 }, vel: { default: null } },
      toDOM: (n) => ['span', {
        class: 'akord-kotva', 'data-akord': n.attrs.n, 'data-dx': n.attrs.dx,
        'data-dy': n.attrs.dy, 'data-vel': n.attrs.vel ?? '',
      }],
      parseDOM: [{
        tag: 'span[data-akord]',
        getAttrs: (d) => ({
          n: d.dataset.akord || '',
          dx: Number(d.dataset.dx) || 0,
          dy: Number(d.dataset.dy) || 0,
          vel: d.dataset.vel ? Number(d.dataset.vel) : null,
        }),
      }],
    },
  },

  marks: {
    tucne: {
      toDOM: () => ['strong', 0],
      parseDOM: [{ tag: 'strong' }, { tag: 'b' }],
    },
    kurziva: {
      toDOM: () => ['em', 0],
      parseDOM: [{ tag: 'em' }, { tag: 'i' }],
    },
    podtrzeni: {
      toDOM: () => ['u', 0],
      parseDOM: [{ tag: 'u' }],
    },
    pismo: {
      attrs: { f: {} },
      toDOM: (m) => ['span', { 'data-pismo': m.attrs.f, style: `font-family:'${m.attrs.f}'` }, 0],
      parseDOM: [{ tag: 'span[data-pismo]', getAttrs: (d) => ({ f: d.dataset.pismo }) }],
    },
    // Velikost v pt.
    velikost: {
      attrs: { v: {} },
      toDOM: (m) => ['span', { 'data-velikost': m.attrs.v, style: `font-size:${m.attrs.v}pt` }, 0],
      parseDOM: [{ tag: 'span[data-velikost]', getAttrs: (d) => ({ v: Number(d.dataset.velikost) }) }],
    },
    barva: {
      attrs: { c: {} },
      toDOM: (m) => ['span', { 'data-barva': m.attrs.c, style: `color:${m.attrs.c}` }, 0],
      parseDOM: [{ tag: 'span[data-barva]', getAttrs: (d) => ({ c: d.dataset.barva }) }],
    },
  },
});

const N = schema.nodes;

// --- prostý text s akordy v hranatých závorkách (ChordPro bez direktiv) ---------------
// Slouží schránce: kopie z editoru jde vložit kamkoli jako „[Ami]text“, a text v tomhle
// tvaru se při vložení rozloží na odstavce s ukotvenými akordy. Plný import a export
// ChordPro (direktivy, posuny v {x_…}) je samostatný krok.

function obsahRadku(radek) {
  const obsah = [];
  const re = /\[([^\]\n]*)\]/g;
  let posledni = 0, m;
  while ((m = re.exec(radek))) {
    if (m.index > posledni) obsah.push(schema.text(radek.slice(posledni, m.index)));
    obsah.push(N.akord.create({ n: m[1].trim() }));
    posledni = re.lastIndex;
  }
  if (posledni < radek.length) obsah.push(schema.text(radek.slice(posledni)));
  return obsah;
}

// Text -> odstavce. Každý řádek je odstavec, prázdný řádek odděluje sloky (zůstane jako
// prázdný odstavec, jako by ho autor napsal ve Wordu). „R:“ / „Ref:“ na začátku sloky
// z ní udělá refrén, „1.“ apod. se zahodí (sloky se číslují samy).
export function odstavceZTextu(text) {
  const odstavce = [];
  const kusy = text.replace(/\r\n?/g, '\n').split(/\n[ \t]*\n/);
  for (const kus of kusy) {
    const radky = kus.split('\n');
    if (!radky.some((r) => r.trim())) continue;
    let styl = 'sloka';
    const znacka = /^\s*(R(?:ef(?:rén)?)?\.?:|\d+\.)\s*/i.exec(radky[0]);
    if (znacka) {
      if (/^R/i.test(znacka[1])) styl = 'refren';
      radky[0] = radky[0].slice(znacka[0].length);
      if (!radky[0].trim() && radky.length > 1) radky.shift();
    }
    if (odstavce.length) odstavce.push(N.odstavec.create({ styl }));
    for (const r of radky) odstavce.push(N.odstavec.create({ styl }, obsahRadku(r)));
  }
  return odstavce;
}

// Uzel nebo fragment (i kus řádku ze schránky) -> text s akordy v závorkách.
export function textZUzlu(obsah) {
  const radky = [];
  let rozepsany = null;
  let predchoziStyl = null;
  const radek = (uzel) => {
    let s = '';
    uzel.forEach((n) => {
      if (n.type === N.akord) s += `[${n.attrs.n}]`;
      else if (n.type === N.zalomeni) s += '\n';
      else if (n.isText) s += n.text;
    });
    return s;
  };
  const projdi = (uzel) => {
    if (uzel.isInline) {
      rozepsany = (rozepsany || '') + radek({ forEach: (f) => f(uzel) });
      return;
    }
    if (rozepsany != null) { radky.push(rozepsany); rozepsany = null; }
    if (uzel.type === N.odstavec) {
      let s = radek(uzel);
      const styl = uzel.attrs.styl;
      if (styl === 'refren' && predchoziStyl !== 'refren' && s) s = 'R: ' + s;
      radky.push(s);
      predchoziStyl = s ? styl : null;
    } else {
      uzel.forEach(projdi);
    }
  };
  obsah.forEach(projdi);
  if (rozepsany != null) radky.push(rozepsany);
  return radky.join('\n');
}

export function prazdnyDokument(format) {
  const okraj = format.okraj;
  return N.doc.create(null, [
    N.ramecek.create(
      { x: okraj, y: okraj, w: format.sirka - 2 * okraj, h: format.vyska - 2 * okraj },
      [
        N.odstavec.create({ styl: 'nadpis' }, schema.text('Název písně')),
        N.odstavec.create({ styl: 'autor' }, schema.text('Autor')),
        ...odstavceZTextu('Sem napiš první řádek\na druhý řádek sloky\n\nR:\nTady začíná refrén'),
      ],
    ),
  ]);
}
