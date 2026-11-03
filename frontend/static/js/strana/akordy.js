// Akordy: rozbor názvu a transpozice. Bez závislostí, ať jde zkoušet samostatně.
//
// Mění se jen základní tón a bas za lomítkem. Přípona (mi, m, moll, maj7, sus4, dim…)
// zůstane doslova, jak ji autor napsal - knihovny, které akord „normalizují“, by z „Ami“
// udělaly „Am“. ChordSheetJS navíc je GPL a H/B nezná.
//
// Notace:
//   cz - česká (jako pisnicky-akordy.cz): H = h, B = b (tedy anglické B♭)
//   en - anglická: B = h, Bb = b
// Křížky nebo béčka podle zápisu originálu: kdo psal Eb, dostane po transpozici zase béčka.

const TONY = {
  cz: {
    krizky: ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'B', 'H'],
    becka: ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'B', 'H'],
  },
  en: {
    krizky: ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'],
    becka: ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'],
  },
};

const ZAKLAD = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9 };

// Posuvka: #, ♯, b, ♭ a česká jména (Fis, Cis, Es, As, Des…). „s“ se nesmí plést se „sus“
// (Esus4 je E + sus4, ne Es + us4).
const POSUVKA = '(#|♯|b|♭|is|es|s(?!us))?';
const TON = new RegExp('^([A-H])' + POSUVKA);

function tonNaCislo(pismeno, posuvka, notace) {
  let c;
  if (pismeno === 'H') c = 11;
  else if (pismeno === 'B') c = notace === 'en' ? 11 : 10;
  else c = ZAKLAD[pismeno];
  if (posuvka === '#' || posuvka === '♯' || posuvka === 'is') c += 1;
  else if (posuvka === 'b' || posuvka === '♭' || posuvka === 'es' || posuvka === 's') c -= 1;
  return (c + 12) % 12;
}

function jeBecko(posuvka) {
  return posuvka === 'b' || posuvka === '♭' || posuvka === 'es' || posuvka === 's';
}

// Rozbor jednoho tónu na začátku řetězce: { cislo, becko, delka } nebo null.
function ctiTon(text, notace) {
  const m = TON.exec(text);
  if (!m) return null;
  return { cislo: tonNaCislo(m[1], m[2], notace), becko: jeBecko(m[2]), delka: m[0].length };
}

// Akord -> { pred, ton, pripona, bas, za } nebo null, když to akord není (N.C., x, |:…).
// Kulaté závorky kolem akordu „(Ami)“ se zachovají.
export function rozeberAkord(nazev, notace = 'cz') {
  const m = /^(\(?)(.*?)(\)?)$/.exec(nazev.trim());
  const pred = m[1], jadro = m[2], za = m[3];
  const ton = ctiTon(jadro, notace);
  if (!ton) return null;
  let zbytek = jadro.slice(ton.delka);
  let bas = null;
  const lomitko = zbytek.lastIndexOf('/');
  if (lomitko >= 0) {
    const b = ctiTon(zbytek.slice(lomitko + 1), notace);
    if (b && b.delka === zbytek.length - lomitko - 1) {
      bas = b;
      zbytek = zbytek.slice(0, lomitko);
    }
  }
  return { pred, ton, pripona: zbytek, bas, za };
}

function nazevTonu(cislo, becka, notace) {
  const sada = TONY[notace] || TONY.cz;
  return (becka ? sada.becka : sada.krizky)[((cislo % 12) + 12) % 12];
}

// Posune akord o `pultony` (záporné dolů). Co akord není, vrátí beze změny.
export function transponuj(nazev, pultony, notace = 'cz') {
  const a = rozeberAkord(nazev, notace);
  if (!a) return nazev;
  const becka = a.ton.becko;
  let vysledek = nazevTonu(a.ton.cislo + pultony, becka, notace) + a.pripona;
  if (a.bas) vysledek += '/' + nazevTonu(a.bas.cislo + pultony, becka || a.bas.becko, notace);
  return a.pred + vysledek + a.za;
}

export function jeAkord(nazev, notace = 'cz') {
  return rozeberAkord(nazev, notace) !== null;
}
