// Schéma navržené strany (ProseMirror) a převod z/do prostého textu s akordy.
//
// Strana je proud bloků: nadpis (název písně), autor, sloka (řádky) a volný text.
// Akord je neviditelný inline uzel těsně PŘED znakem, ke kterému patří - totéž co `[Am]`
// v ChordPro, jen si navíc pamatuje vlastní posun a velikost. Tím přežije psaní, mazání,
// zpět i kopírování bez jakékoli vlastní logiky; když se smaže jeho znak, patří k dalšímu.

import { Schema } from 'prosemirror';

export const VERZE_SCHEMATU = 1;

export const DRUHY_SLOK = {
  sloka: 'Sloka',      // číslovaná 1., 2., …
  refren: 'Refrén',    // R:
  bez: 'Bez značky',
};

export const schema = new Schema({
  nodes: {
    doc: { content: 'blok+' },

    nadpis: {
      group: 'blok', content: 'inline*', defining: true,
      toDOM: () => ['h1', { class: 'ps-nadpis' }, 0],
      parseDOM: [{ tag: 'h1' }],
    },

    autor: {
      group: 'blok', content: 'inline*', defining: true,
      toDOM: () => ['p', { class: 'ps-autor' }, 0],
      parseDOM: [{ tag: 'p.ps-autor' }],
    },

    sloka: {
      group: 'blok', content: 'radek+', defining: true,
      attrs: { druh: { default: 'sloka' } },
      toDOM: (n) => ['div', { class: `ps-sloka ps-sloka-${n.attrs.druh}`, 'data-druh': n.attrs.druh }, 0],
      parseDOM: [{ tag: 'div.ps-sloka', getAttrs: (d) => ({ druh: d.dataset.druh || 'sloka' }) }],
    },

    // Řádek písně se nikdy nezalamuje sám (white-space: pre) - kde se láme, rozhoduje
    // autor. Jinak by obrázek ze stejného zdroje mohl v jiném prohlížeči vyjít jinak.
    radek: {
      content: 'inline*',
      toDOM: () => ['p', { class: 'ps-radek' }, 0],
      parseDOM: [{ tag: 'p.ps-radek' }],
    },

    text_blok: {
      group: 'blok', content: 'inline*',
      toDOM: () => ['p', { class: 'ps-text' }, 0],
      parseDOM: [{ tag: 'p' }],
    },

    text: { group: 'inline' },

    // dx, dy: posun proti výchozí poloze ze šablony, v em písma textu (škáluje s ním).
    // vel: vlastní velikost v % výchozí velikosti akordů, null = výchozí.
    akord: {
      group: 'inline', inline: true, atom: true, selectable: true,
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
  },
});

// --- prostý text s akordy v hranatých závorkách (ChordPro bez direktiv) ---------------
// Slouží schránce: kopie z editoru jde vložit kamkoli jako „[Ami]text“, a text v tomhle
// tvaru se při vložení rozloží na sloky a ukotvené akordy. Plný import a export ChordPro
// (direktivy, posuny v {x_…}) je samostatný krok.

function radekZTextu(radek) {
  const obsah = [];
  const re = /\[([^\]\n]*)\]/g;
  let posledni = 0, m;
  while ((m = re.exec(radek))) {
    if (m.index > posledni) obsah.push(schema.text(radek.slice(posledni, m.index)));
    obsah.push(schema.nodes.akord.create({ n: m[1].trim() }));
    posledni = re.lastIndex;
  }
  if (posledni < radek.length) obsah.push(schema.text(radek.slice(posledni)));
  return schema.nodes.radek.create(null, obsah);
}

// Text -> sloky. Prázdný řádek odděluje sloky; řádek „R:“ nebo „Ref:“ na začátku sloky
// z ní udělá refrén, „1.“ apod. se zahodí (číslují se samy).
export function slokyZTextu(text) {
  const sloky = [];
  for (const kus of text.replace(/\r\n?/g, '\n').split(/\n[ \t]*\n/)) {
    const radky = kus.split('\n');
    if (!radky.some((r) => r.trim())) continue;
    let druh = 'sloka';
    const prvni = radky[0];
    const znacka = /^\s*(R(?:ef(?:rén)?)?\.?:|\d+\.)\s*/i.exec(prvni);
    if (znacka) {
      if (/^R/i.test(znacka[1])) druh = 'refren';
      radky[0] = prvni.slice(znacka[0].length);
      if (!radky[0].trim() && radky.length > 1) radky.shift();
    }
    sloky.push(schema.nodes.sloka.create({ druh }, radky.map(radekZTextu)));
  }
  return sloky;
}

// Uzel nebo fragment (i kus řádku ze schránky) -> text s akordy v závorkách.
export function textZUzlu(obsah) {
  const ven = [];
  let radek = null;           // rozepsaný řádek z volného inline obsahu
  obsah.forEach((blok) => {
    if (blok.isInline) {
      radek = (radek || '') + textRadku({ forEach: (f) => f(blok) });
      return;
    }
    if (radek != null) { ven.push(radek); radek = null; }
    if (blok.type.name === 'radek') {
      // Řádky bez sloky (výběr přes víc řádků jedné sloky) patří k sobě.
      const posledni = ven.length - 1;
      if (posledni >= 0 && ven.radkySloky === posledni) ven[posledni] += '\n' + textRadku(blok);
      else { ven.push(textRadku(blok)); ven.radkySloky = ven.length - 1; }
      return;
    }
    if (blok.type.name === 'sloka') {
      const radky = [];
      blok.forEach((r) => radky.push(textRadku(r)));
      if (blok.attrs.druh === 'refren') radky[0] = 'R: ' + radky[0];
      ven.push(radky.join('\n'));
    } else if (blok.isTextblock) {
      ven.push(textRadku(blok));
    }
  });
  if (radek != null) ven.push(radek);
  return ven.join('\n\n');
}

function textRadku(radek) {
  let s = '';
  radek.forEach((n) => {
    if (n.type.name === 'akord') s += `[${n.attrs.n}]`;
    else if (n.isText) s += n.text;
  });
  return s;
}

export function prazdnyDokument() {
  return schema.nodes.doc.create(null, [
    schema.nodes.nadpis.create(null, schema.text('Název písně')),
    schema.nodes.autor.create(null, schema.text('Autor')),
    ...slokyZTextu('Sem napiš první řádek\na druhý řádek sloky\n\nR:\nTady začíná refrén'),
  ]);
}
