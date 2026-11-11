// Schéma navržené strany (ProseMirror) a převod z/do prostého textu s akordy.
//
// Strana je plátno pevného rozměru s prvky (jako v Canvě): textové rámečky a obrázky,
// každý s polohou v mm. Textový rámeček je malý
// Word: odstavce se stylem (Normální, Název, Autor, Sloka, Refrén) a přímým formátováním
// (písmo, velikost, barva, tučné, kurzíva, podtržení, zarovnání, odsazení). Styl je jen
// výchozí vzhled ze šablony zpěvníku, ne struktura - každý odstavec jde přenastavit.
//
// Akord je neviditelný inline uzel těsně PŘED znakem, ke kterému patří - totéž co `[Am]`
// v ChordPro, jen si navíc pamatuje vlastní posun a velikost. Tím přežije psaní, mazání,
// zpět i kopírování bez jakékoli vlastní logiky; když se smaže jeho znak, patří k dalšímu.

import { Schema } from 'prosemirror';

export const VERZE_SCHEMATU = 2;

// Body náplasti pro SVG polygon v souřadnicích 0-100 (viewBox), bez bodů obdélník.
export function bodyTvaru(body) {
  if (!body) return '0,0 100,0 100,100 0,100';
  return body.trim().split(/\s+/).map((b) => b.split(',').map((x) => Math.round(parseFloat(x) * 10000) / 100).join(',')).join(' ');
}

// Role stylu říká, jak se odstavec chová: sloka se čísluje, refrén dostane R:, název
// písně začíná číslování znovu. Vestavěné styly mají id stejné jako roli; vlastní styly
// („Úzká sloka“) mají id v1, v2… a jednu z těchto rolí.
export const ROLE = {
  normalni: 'Text',
  nadpis: 'Název písně',
  autor: 'Autor',
  sloka: 'Sloka (čísluje se)',
  refren: 'Refrén (R:)',
};

const mm = (x) => `${x}mm`;

export const schema = new Schema({
  nodes: {
    doc: { content: 'prvek+' },

    // Rámeček: poloha a rozměr v mm od levého horního rohu strany.
    ramecek: {
      group: 'prvek', content: 'odstavec+', isolating: true, defining: true,
      // presah: kopie rámečku přes hřbet dvojstrany na straně, kde nemá střed - vykreslí
      // se (každá strana ukáže svou část), ale nepočítá se do slok (značky nese pevně
      // v odstavcích) a obsahem zpěvníku (hledání, export) je jen originál.
      attrs: { x: { default: 12 }, y: { default: 12 }, w: { default: 124 }, h: { default: 186 }, presah: { default: false } },
      toDOM: (n) => ['div', {
        class: 'ps-ramecek', 'data-x': n.attrs.x, 'data-y': n.attrs.y, 'data-w': n.attrs.w, 'data-h': n.attrs.h,
        ...(n.attrs.presah ? { 'data-presah': '1' } : {}),
        style: `left:${mm(n.attrs.x)};top:${mm(n.attrs.y)};width:${mm(n.attrs.w)};height:${mm(n.attrs.h)}`,
      }, ['div', { class: 'ps-ramecek-obsah' }, 0]],
      parseDOM: [{
        tag: 'div.ps-ramecek', contentElement: '.ps-ramecek-obsah',
        getAttrs: (d) => ({ x: +d.dataset.x || 12, y: +d.dataset.y || 12, w: +d.dataset.w || 124, h: +d.dataset.h || 186,
          presah: d.dataset.presah === '1' }),
      }],
    },

    // Obrázek na straně. src je zatím data: URL (strana se ještě neukládá na server);
    // vrstva: 'pred' nad textem, 'za' pod textem.
    obrazek: {
      group: 'prvek', atom: true, selectable: true, draggable: false,
      attrs: {
        src: {}, x: { default: 20 }, y: { default: 20 }, w: { default: 50 }, h: { default: 40 },
        vrstva: { default: 'pred' },
      },
      toDOM: (n) => ['div', {
        class: `ps-obrazek ps-vrstva-${n.attrs.vrstva}`, 'data-x': n.attrs.x, 'data-y': n.attrs.y,
        'data-w': n.attrs.w, 'data-h': n.attrs.h, 'data-vrstva': n.attrs.vrstva,
        style: `left:${mm(n.attrs.x)};top:${mm(n.attrs.y)};width:${mm(n.attrs.w)};height:${mm(n.attrs.h)}`,
      }, ['img', { src: n.attrs.src, alt: '' }]],
      parseDOM: [{
        tag: 'div.ps-obrazek',
        getAttrs: (d) => {
          const img = d.querySelector('img');
          if (!img) return false;
          return { src: img.getAttribute('src'), x: +d.dataset.x || 20, y: +d.dataset.y || 20,
            w: +d.dataset.w || 50, h: +d.dataset.h || 40, vrstva: d.dataset.vrstva || 'pred' };
        },
      }],
    },

    // Tvar (náplast): plocha s výplní nad obrázky za textem a pod textem - zakryje třeba
    // vypálené číslo strany nebo chybu ve skenu. x, y, w, h je obdélník kolem v mm,
    // body mnohoúhelník uvnitř v podílech šířky a výšky („0,0 1,0 1,1 0,1“); bez nich
    // je to obdélník. Kreslí se jako SVG, ať jde vidět i obrys volného tvaru.
    tvar: {
      group: 'prvek', atom: true, selectable: true, draggable: false,
      attrs: {
        x: { default: 20 }, y: { default: 20 }, w: { default: 30 }, h: { default: 10 },
        barva: { default: '#ffffff' }, body: { default: null },
      },
      toDOM: (n) => ['div', {
        class: 'ps-tvar', 'data-x': n.attrs.x, 'data-y': n.attrs.y, 'data-w': n.attrs.w, 'data-h': n.attrs.h,
        'data-barva': n.attrs.barva, 'data-body': n.attrs.body || '',
        style: `left:${mm(n.attrs.x)};top:${mm(n.attrs.y)};width:${mm(n.attrs.w)};height:${mm(n.attrs.h)}`,
      }, ['http://www.w3.org/2000/svg svg', { viewBox: '0 0 100 100', preserveAspectRatio: 'none' },
        ['http://www.w3.org/2000/svg polygon', { points: bodyTvaru(n.attrs.body), fill: n.attrs.barva }]]],
      parseDOM: [{
        tag: 'div.ps-tvar',
        getAttrs: (d) => ({ x: +d.dataset.x || 20, y: +d.dataset.y || 20, w: +d.dataset.w || 30, h: +d.dataset.h || 10,
          barva: d.dataset.barva || '#ffffff', body: d.dataset.body || null }),
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
          styl: /^[a-z0-9]+$/.test(d.dataset.styl || '') ? d.dataset.styl : 'normalni',
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

// Ukázková píseň nové strany (vymyšlená, ať se nemusí řešit autorská práva).
export const UKAZKA = `[G]Večer u [D]ohně sedí[Emi]me,
[C]staré písně [G]zpívá[D]me,
[G]nad lesem se [D]hvězdy roz[Emi]svítí,
[C]nikomu se [D]spát ne[G]chce.

R: [C]Hraj, kytaro, [G]hraj,
[D]ať to slyší [Emi]celý kraj,
[C]hraj, kytaro, [G]hraj
[D]až do rána [G]dál.

[G]Kamarád mi [D]podá hrne[Emi]ček,
[C]v ohni praská [G]suchý [D]klacek,
[G]a když oheň [D]tiše dohasí[Emi]ná,
[C]každý ví, že [D]ráno začí[G]ná.`;

export function prazdnyDokument(format) {
  const okraj = format.okraj;
  return N.doc.create(null, [
    N.ramecek.create(
      { x: okraj, y: okraj, w: format.sirka - 2 * okraj, h: format.vyska - 2 * okraj },
      [
        N.odstavec.create({ styl: 'nadpis' }, schema.text('U ohně')),
        N.odstavec.create({ styl: 'autor' }, schema.text('ukázková píseň')),
        ...odstavceZTextu(UKAZKA),
      ],
    ),
  ]);
}
