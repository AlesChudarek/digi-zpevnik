// Měřicí průchod po vykreslení: akordy, které by do sebe narazily, a obsah, který se na
// stranu nevejde.
//
// Akordy jsou overlay, takže samy text neodsunou. Když se dva překrývají (krátká slabika,
// dlouhý akord, transpozice C -> C#mi7), šablona rozhodne:
//   automatická mezera - před kotvu druhého se přidá nejmenší nutná mezera v textu.
//     Je spočítaná, ne uložená: po transpozici nebo změně písma se spočítá znovu.
//   jen zvýraznit - text zůstane, oba akordy se označí a autor je posune sám.
//
// Řádky písně se nelámou (white-space: pre), takže posun jednoho akordu posune všechny
// další v řádku přesně o tolik - stačí jedno čtení a jedno psaní, bez opakovaného
// přepočítávání rozložení.

const MEZERA_EM = 0.35;      // nejmenší vzduch mezi akordy, v em písma textu

export function prepocitej(koren, { automatickaMezera, meritko }) {
  const kotvy = koren.querySelectorAll('.akord-kotva');
  for (const k of kotvy) {
    k.style.marginLeft = '';
    k.classList.remove('kolize');
  }

  // Čtení: všechny obdélníky najednou (jedno přepočítání rozložení).
  const radky = [];
  for (const radek of koren.querySelectorAll('.ps-radek')) {
    const ks = radek.querySelectorAll('.akord-kotva');
    if (ks.length < 2) continue;
    const pismo = parseFloat(getComputedStyle(radek).fontSize) || 16;
    radky.push({
      pismo,
      akordy: [...ks].map((k) => ({ k, r: k.querySelector('.akord').getBoundingClientRect() })),
    });
  }

  // Psaní.
  for (const { pismo, akordy } of radky) {
    const mezera = pismo * MEZERA_EM * meritko;
    let posun = 0;
    let predchozi = null;
    for (const a of akordy) {
      const levy = a.r.left + posun;
      if (predchozi && svisleSePrekryvaji(predchozi.r, a.r) && levy < predchozi.pravy + mezera) {
        const chybi = predchozi.pravy + mezera - levy;
        if (automatickaMezera) {
          a.k.style.marginLeft = (chybi / meritko).toFixed(2) + 'px';
          posun += chybi;
        } else {
          a.k.classList.add('kolize');
          predchozi.k.classList.add('kolize');
        }
      }
      predchozi = { k: a.k, r: a.r, pravy: a.r.right + posun };
    }
  }

  // Přetečení: obsah delší než strana, řádek širší než sazební obrazec.
  let siroke = 0;
  for (const radek of koren.querySelectorAll('.ps-radek, .ps-nadpis, .ps-autor')) {
    const sirsi = radek.scrollWidth > radek.clientWidth + 1;
    radek.classList.toggle('siroky', sirsi);
    if (sirsi) siroke += 1;
  }
  const pretece = koren.scrollHeight > koren.clientHeight + 1;
  koren.classList.toggle('pretece', pretece);
  return { pretece, siroke, kolize: koren.querySelectorAll('.akord-kotva.kolize').length };
}

function svisleSePrekryvaji(a, b) {
  return a.top < b.bottom && b.top < a.bottom;
}
