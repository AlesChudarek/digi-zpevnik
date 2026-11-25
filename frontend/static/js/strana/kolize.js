// Měřicí průchod po vykreslení: akordy, které by do sebe narazily, a rámečky, do kterých
// se text nevejde.
//
// Akordy jsou overlay a text neovlivňují. Když se dva překrývají (krátká slabika, dlouhý
// akord, transpozice C -> C#mi7), označí se červeně. Šablona může zapnout automatickou
// mezeru: před kotvu druhého akordu se přidá nejmenší nutná mezera v textu. Ta je
// spočítaná, ne uložená (po transpozici nebo změně písma se spočítá znovu) a nikdy se
// nepoužije u ručně posunutého akordu - ten autor umístil sám a text se pod ním hýbat
// nemá.

const MEZERA_EM = 0.35;      // nejmenší vzduch mezi akordy, v em písma textu

export function prepocitej(koren, volby) {
  // Otočený rámeček: měří se neotočený (řádky jsou pak vodorovné, rozložení je stejné).
  const otocene = [...koren.querySelectorAll('.ps-ramecek')].filter((r) => r.style.transform && r.style.transform !== 'none');
  const transformy = otocene.map((r) => r.style.transform);
  for (const r of otocene) r.style.transform = 'none';
  try { return prepocitejRovne(koren, volby); } finally { otocene.forEach((r, i) => { r.style.transform = transformy[i]; }); }
}

function prepocitejRovne(koren, { automatickaMezera, meritko }) {
  const kotvy = koren.querySelectorAll('.akord-kotva');
  for (const k of kotvy) {
    k.style.marginLeft = '';
    k.classList.remove('kolize');
  }

  // Čtení: všechny obdélníky najednou (jedno přepočítání rozložení). Akordy se seskupí
  // po vizuálních řádcích - odstavec se může zalamovat.
  const radky = [];
  for (const odstavec of koren.querySelectorAll('.ps-p')) {
    const ks = odstavec.querySelectorAll('.akord-kotva');
    if (ks.length < 2) continue;
    const pismo = parseFloat(getComputedStyle(odstavec).fontSize) || 16;
    const podleRadku = new Map();
    for (const k of ks) {
      const top = Math.round(k.getBoundingClientRect().top);
      if (!podleRadku.has(top)) podleRadku.set(top, []);
      podleRadku.get(top).push({ k, r: k.querySelector('.akord').getBoundingClientRect(), rucne: k.classList.contains('rucne') });
    }
    for (const akordy of podleRadku.values()) if (akordy.length > 1) radky.push({ pismo, akordy });
  }

  // Psaní. Posun jednoho akordu posune všechny další v řádku o tolik, o kolik se odsunul
  // text - stačí sčítat, bez opakovaného měření.
  for (const { pismo, akordy } of radky) {
    const mezera = pismo * MEZERA_EM * meritko;
    let posun = 0;
    let predchozi = null;
    for (const a of akordy) {
      const levy = a.r.left + posun;
      if (predchozi && svisleSePrekryvaji(predchozi.r, a.r) && levy < predchozi.pravy + mezera) {
        const chybi = predchozi.pravy + mezera - levy;
        if (automatickaMezera && !a.rucne && !predchozi.rucne) {
          a.k.style.marginLeft = (chybi / meritko).toFixed(2) + 'px';
          posun += chybi;
        } else {
          a.k.classList.add('kolize');
          predchozi.k.classList.add('kolize');
        }
      }
      predchozi = { k: a.k, r: a.r, rucne: a.rucne, pravy: a.r.right + posun };
    }
  }

  // Přetečení: text delší než rámeček.
  let pretece = 0;
  for (const ramecek of koren.querySelectorAll('.ps-ramecek')) {
    const obsah = ramecek.querySelector('.ps-ramecek-obsah');
    const ano = obsah.scrollHeight > obsah.clientHeight + 1;
    ramecek.classList.toggle('pretece', ano);
    if (ano) pretece += 1;
  }
  return { pretece, kolize: koren.querySelectorAll('.akord-kotva.kolize').length };
}

function svisleSePrekryvaji(a, b) {
  return a.top < b.bottom && b.top < a.bottom;
}
