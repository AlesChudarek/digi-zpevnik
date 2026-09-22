/* Stažení zpěvníku: okno s nastavením a se skládáním.
 *
 * Jeden soubor pro všechna tři místa, odkud se stahuje (čtečka, Moje zpěvníky, správa
 * veřejných zpěvníků). Dřív to byly dvě různá UI: vysouvací nabídka tří formátů a teprve
 * po kliknutí okno s postupem. Teď je to jedno okno se dvěma stavy - nastavení a průběh.
 *
 * Základní stav je schválně jedno mrknutí a jedno kliknutí: předvolba je vybraná
 * a vlastní nastavení sbalené. Kdo chce jen PDF do telefonu, klikne dvakrát jako dřív.
 *
 * Server na první požádání odpoví 202 a soubor skládá na pozadí: gunicorn sync worker
 * hlásí, že žije, jen mezi požadavky, takže by dlouhý export spadl na 30s timeout.
 * Klient se proto ptá endpointu export-status, dokud nedostane 'ready'.
 *
 * Veřejné API:
 *   Stahovani.otevri(bookId, nazev)   otevře okno s nastavením
 */
window.Stahovani = (function () {
  'use strict';

  /* Předvolby jsou jen pojmenované recepty, stejně jako na serveru (PREDVOLBY v app.py).
     Jména musí sedět - jsou to tokeny v názvech souborů v cache. */
  const PREDVOLBY = [
    {
      klic: 'small', format: 'pdf',
      nazev: 'PDF pro čtení',
      popis: 'menší soubor, rychle se stáhne',
    },
    {
      klic: 'high', format: 'pdf',
      nazev: 'PDF pro tisk',
      popis: 'plné rozlišení',
    },
    {
      klic: 'orig', format: 'zip',
      nazev: 'Obrázky v ZIP',
      popis: 'původní soubory beze změny',
    },
  ];

  const NAPOVEDY = {
    kvalita: 'Menší soubor se rychleji stahuje a stačí na čtení. Plné rozlišení má ' +
             'smysl na tisk, kde je vidět každý detail akordových značek.',
    obsah: 'Obálka je složený list o čtyřech stranách. Když ji vynecháš, zbyde jen ' +
           'to, co je uvnitř.',
    prazdne: 'Prázdné strany drží zpěvník tak, jak se tiskne. Když je vynecháš, může ' +
             'přestat sedět číslování stran a zpěvník se nemusí dát správně vytisknout ' +
             'ani složit. Doporučené jen na čtení na displeji.',
    cernobile: 'Pro černobílý tisk. Průhledné obálky se složí na bílou, aby z barevné ' +
               'obálky nebyla celoplošná šedá. U neprůhledných obálek s tím nejde nic ' +
               'dělat. Soubor tím nemusí být menší.',
  };

  let okno = null;          // prvky okna, postavené při prvním otevření
  let bezi = null;          // klíč běhu, na který se zrovna ptáme
  let zpevnik = null;       // id zpěvníku, se kterým je okno otevřené
  let dotazNaHotovo = null; // časovač odloženého dotazu u vlastního nastavení

  /* ---------- stavba okna ---------- */

  function poleVolby(id, popisek, moznosti, napoveda) {
    const volby = moznosti.map(
      m => `<label class="stahovani-volba">
              <input type="radio" name="${id}" value="${m.hodnota}"
                     ${m.vychozi ? 'checked' : ''}>
              <span>${m.popisek}</span>
            </label>`).join('');
    return `<div class="stahovani-radek" data-pole="${id}">
              <div class="stahovani-popisek">${popisek}${znakNapovedy(napoveda)}</div>
              <div class="stahovani-volby">${volby}</div>
            </div>`;
  }

  function znakNapovedy(klic) {
    if (!klic) return '';
    return ` <button type="button" class="napoveda-znak" data-napoveda="${klic}"
                     aria-label="Co to znamená">i</button>`;
  }

  function postavOkno() {
    if (okno) return okno;
    const zaclona = document.createElement('div');
    zaclona.className = 'stahovani-zaclona';
    zaclona.id = 'stahovani-okno';
    zaclona.hidden = true;
    zaclona.innerHTML = `
      <div class="stahovani-panel" role="dialog" aria-modal="true"
           aria-labelledby="stahovani-nadpis">
        <h2 id="stahovani-nadpis">Stáhnout zpěvník</h2>
        <p class="stahovani-co" id="stahovani-co"></p>

        <div class="stahovani-telo">
        <div id="stahovani-nastaveni">
          <div class="stahovani-predvolby" id="stahovani-predvolby"></div>

          <fieldset class="stahovani-vlastni" id="stahovani-vlastni" disabled>
            <legend class="stahovani-skryty">Vlastní nastavení</legend>
            ${poleVolby('kvalita', 'Kvalita', [
              {hodnota: 'small', popisek: 'menší soubor', vychozi: true},
              {hodnota: 'high', popisek: 'plné rozlišení'},
            ], 'kvalita')}
            ${poleVolby('format', 'Formát', [
              {hodnota: 'pdf', popisek: 'PDF', vychozi: true},
              {hodnota: 'zip', popisek: 'obrázky v ZIP'},
            ], null)}
            ${poleVolby('obsah', 'Co stáhnout', [
              {hodnota: 'vse', popisek: 'obálku i obsah', vychozi: true},
              {hodnota: 'jen-obsah', popisek: 'jen obsah'},
              {hodnota: 'jen-obalka', popisek: 'jen obálku'},
            ], 'obsah')}
            <div class="stahovani-radek">
              <div class="stahovani-popisek">Strany${znakNapovedy('prazdne')}</div>
              <div class="stahovani-volby">
                <label class="stahovani-volba">
                  <input type="checkbox" id="stahovani-prazdne" checked>
                  <span>nechat prázdné strany</span>
                </label>
              </div>
            </div>
            <div class="stahovani-radek">
              <div class="stahovani-popisek">Barvy${znakNapovedy('cernobile')}</div>
              <div class="stahovani-volby">
                <label class="stahovani-volba">
                  <input type="checkbox" id="stahovani-cernobile">
                  <span>černobíle</span>
                </label>
              </div>
            </div>
          </fieldset>

          <p class="stahovani-souhrn" id="stahovani-souhrn"></p>
        </div>

        <div id="stahovani-postup" hidden>
          <div class="stahovani-pruh"><span id="stahovani-vypln"></span></div>
          <p class="stahovani-cislo" id="stahovani-cislo">Začínám…</p>
          <p class="stahovani-rada" id="stahovani-rada">
            Můžeš počkat a soubor se stáhne sám. Nebo okno zavřít a vrátit se později —
            skládání poběží dál a příště bude ke stažení hned.
          </p>
        </div>
        </div>

        <div class="stahovani-tlacitka">
          <button type="button" id="stahovani-zavrit">Zavřít</button>
          <button type="button" id="stahovani-spustit" class="hlavni">Stáhnout</button>
        </div>
      </div>`;
    // Na <body>, ne vedle tlačítka, které okno otevřelo: ve čtečce by jinak sedělo
    // uvnitř .mode-buttons a dědilo odtud tvar tlačítek i mizení celého sloupce.
    document.body.appendChild(zaclona);

    okno = {
      zaclona,
      nadpis: zaclona.querySelector('#stahovani-nadpis'),
      co: zaclona.querySelector('#stahovani-co'),
      nastaveni: zaclona.querySelector('#stahovani-nastaveni'),
      predvolby: zaclona.querySelector('#stahovani-predvolby'),
      vlastni: zaclona.querySelector('#stahovani-vlastni'),
      souhrn: zaclona.querySelector('#stahovani-souhrn'),
      postup: zaclona.querySelector('#stahovani-postup'),
      vypln: zaclona.querySelector('#stahovani-vypln'),
      cislo: zaclona.querySelector('#stahovani-cislo'),
      rada: zaclona.querySelector('#stahovani-rada'),
      zavrit: zaclona.querySelector('#stahovani-zavrit'),
      spustit: zaclona.querySelector('#stahovani-spustit'),
    };

    okno.predvolby.innerHTML = PREDVOLBY.map(p => `
      <label class="stahovani-predvolba">
        <input type="radio" name="predvolba" value="${p.klic}"
               ${p.klic === 'small' ? 'checked' : ''}>
        <span class="stahovani-predvolba-text">
          <strong>${p.nazev} <span class="hotovo-znak" data-varianta="${p.format}-${p.klic}"
                                   hidden>✓ hned</span></strong>
          <span>${p.popis}</span>
        </span>
      </label>`).join('') + `
      <label class="stahovani-predvolba">
        <input type="radio" name="predvolba" value="vlastni">
        <span class="stahovani-predvolba-text">
          <strong>Vlastní nastavení</strong>
          <span>poskládej si, co má soubor obsahovat</span>
        </span>
      </label>`;

    okno.zavrit.addEventListener('click', zavri);
    okno.spustit.addEventListener('click', spust);
    // Klik na záclonu vedle panelu zavírá taky. Modální okno, které jde zavřít jedině
    // tlačítkem, je past na dotykové obrazovce, kde se Escape nemačká.
    zaclona.addEventListener('click', (e) => {
      if (e.target === zaclona) zavri();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !zaclona.hidden) zavri();
    });
    okno.nastaveni.addEventListener('change', zmenaVyberu);
    okno.nastaveni.addEventListener('click', (e) => {
      const znak = e.target.closest('.napoveda-znak');
      if (znak) prepniNapovedu(znak);
    });
    return okno;
  }

  /* ---------- nápověda ---------- */

  function prepniNapovedu(znak) {
    const otevrena = znak.nextElementSibling &&
                     znak.nextElementSibling.classList.contains('napoveda-text');
    okno.zaclona.querySelectorAll('.napoveda-text').forEach(n => n.remove());
    okno.zaclona.querySelectorAll('.napoveda-znak').forEach(
      z => z.setAttribute('aria-expanded', 'false'));
    if (otevrena) return;
    const bublina = document.createElement('div');
    bublina.className = 'napoveda-text';
    bublina.setAttribute('role', 'note');
    bublina.textContent = NAPOVEDY[znak.dataset.napoveda] || '';
    // Na konec celého řádku, ne hned za ikonku: ikonka sedí v úzkém sloupci s popiskem
    // a text by se do něj zalomil do třiceti řádků.
    (znak.closest('.stahovani-radek') || znak.parentElement).appendChild(bublina);
    znak.setAttribute('aria-expanded', 'true');
  }

  /* ---------- nastavení ---------- */

  function vybranaPredvolba() {
    const zvolena = okno.zaclona.querySelector('input[name="predvolba"]:checked');
    return zvolena ? zvolena.value : 'small';
  }

  function hodnota(jmeno) {
    const zvolena = okno.zaclona.querySelector(`input[name="${jmeno}"]:checked`);
    return zvolena ? zvolena.value : null;
  }

  /** Recept jako dvojice {format, parametry} - přesně to, co pochopí server. */
  function recept() {
    const volba = vybranaPredvolba();
    if (volba !== 'vlastni') {
      const p = PREDVOLBY.find(x => x.klic === volba);
      return {format: p.format, parametry: {q: p.klic}};
    }
    const format = hodnota('format') || 'pdf';
    const parametry = {obsah: hodnota('obsah') || 'vse'};
    if (!okno.zaclona.querySelector('#stahovani-prazdne').checked) parametry.prazdne = '0';
    if (format === 'pdf') {
      parametry.kvalita = hodnota('kvalita') || 'small';
      if (okno.zaclona.querySelector('#stahovani-cernobile').checked) {
        parametry.cernobile = '1';
      }
    }
    return {format, parametry};
  }

  function dotazRetezec(parametry) {
    const dvojice = Object.keys(parametry).sort()
      .map(k => `${encodeURIComponent(k)}=${encodeURIComponent(parametry[k])}`);
    return dvojice.length ? '?' + dvojice.join('&') : '';
  }

  function zmenaVyberu() {
    const vlastni = vybranaPredvolba() === 'vlastni';
    okno.vlastni.disabled = !vlastni;
    // ZIP balí originály, takže kvalita ani černobílá pro něj neznamenají nic - a server
    // je zahodí. Ať okno neukazuje volby, které nic neudělají.
    const jeZip = hodnota('format') === 'zip';
    okno.zaclona.querySelectorAll('[data-pole="kvalita"]').forEach(
      el => el.classList.toggle('nedostupne', jeZip));
    okno.zaclona.querySelector('#stahovani-cernobile').closest('.stahovani-radek')
      .classList.toggle('nedostupne', jeZip);
    obnovStav();
  }

  /** Zjistí u vybraného receptu, jestli leží v cache, a podle toho pojmenuje tlačítko. */
  function obnovStav() {
    const { format, parametry } = recept();
    const vlastni = vybranaPredvolba() === 'vlastni';
    if (!vlastni) {
      const znak = okno.zaclona.querySelector(
        `.hotovo-znak[data-varianta="${format}-${parametry.q}"]`);
      nastavTlacitko(znak && !znak.hidden);
      okno.souhrn.textContent = '';
      return;
    }
    // U vlastního nastavení se to musí doptat serveru. Odložit, ať se při proklikávání
    // voleb nestřílí dotaz za každé kliknutí.
    nastavTlacitko(null);
    clearTimeout(dotazNaHotovo);
    dotazNaHotovo = setTimeout(async () => {
      const soucasny = dotazRetezec(Object.assign({format}, parametry));
      try {
        const r = await fetch(
          `/songbook/${encodeURIComponent(zpevnik)}/export-hotove${soucasny}`);
        const j = await r.json();
        // Mezitím se mohlo překlikat jinam; platí jen odpověď na aktuální výběr.
        const { format: f2, parametry: p2 } = recept();
        if (dotazRetezec(Object.assign({format: f2}, p2)) !== soucasny) return;
        nastavTlacitko(!!j.hotovo);
        okno.souhrn.textContent = j.stran
          ? `Vyjde na ${stran(j.stran)}.`
          : 'Z tohohle nastavení nevyjde ani jedna strana.';
        okno.spustit.disabled = !j.stran;
      } catch (e) {
        nastavTlacitko(false);
        okno.souhrn.textContent = '';
      }
    }, 250);
  }

  function stran(n) {
    if (n === 1) return '1 stranu';
    if (n >= 2 && n <= 4) return `${n} strany`;
    return `${n} stran`;
  }

  function nastavTlacitko(hotovo) {
    okno.spustit.disabled = false;
    okno.spustit.textContent = hotovo === false ? 'Připravit' : 'Stáhnout';
  }

  /* ---------- otevření a zavření ---------- */

  function otevri(bookId, nazev) {
    const o = postavOkno();
    zpevnik = bookId;
    o.nadpis.textContent = 'Stáhnout zpěvník';
    o.co.textContent = nazev || '';
    o.nastaveni.hidden = false;
    o.postup.hidden = true;
    o.spustit.hidden = false;
    o.zaclona.querySelectorAll('.napoveda-text').forEach(n => n.remove());
    o.zaclona.hidden = false;
    zmenaVyberu();
    oznacHotove(bookId);
  }

  function zavri() {
    if (okno) okno.zaclona.hidden = true;
    // Skládání běží dál na serveru. Jen se na ně přestaneme ptát.
    bezi = null;
  }

  /* Které předvolby leží v cache. Jedním dotazem za všechny, ne třemi - okno se otevře
     naráz celé. Až při otevření, ne při načtení stránky: jinak by se sekvence exportu
     stavěla u stránky se třiceti dlaždicemi třicetkrát pro nic. */
  async function oznacHotove(bookId) {
    try {
      const r = await fetch(`/songbook/${encodeURIComponent(bookId)}/export-hotove`);
      const hotove = await r.json();
      okno.zaclona.querySelectorAll('.hotovo-znak[data-varianta]').forEach(znak => {
        znak.hidden = !hotove[znak.dataset.varianta];
      });
      obnovStav();
    } catch (e) { /* okno funguje i bez značek */ }
  }

  /* ---------- průběh ---------- */

  function ukazPostup() {
    okno.nadpis.textContent = 'Připravuji soubor';
    okno.nastaveni.hidden = true;
    okno.postup.hidden = false;
    okno.spustit.hidden = true;
    okno.cislo.textContent = 'Začínám…';
    okno.cislo.classList.remove('stahovani-chyba');
    okno.vypln.style.width = '0';
    okno.vypln.classList.add('ceka');
    okno.rada.hidden = false;
    okno.zavrit.classList.remove('hlavni');
  }

  function chyba(text) {
    okno.nadpis.textContent = 'Nepovedlo se';
    okno.nastaveni.hidden = true;
    okno.postup.hidden = false;
    okno.spustit.hidden = true;
    okno.cislo.textContent = text;
    okno.cislo.classList.add('stahovani-chyba');
    okno.vypln.classList.remove('ceka');
    okno.rada.hidden = true;
    okno.zavrit.classList.add('hlavni');
  }

  /* Okno po stažení nezmizí samo. Zmizet by znamenalo, že se v prohlížeči nenápadně
     objeví soubor a nikde nezůstane, že se to povedlo - uživatel, který odešel k jiné
     záložce, by se vrátil k obrazovce beze stopy po tom, na co čekal. */
  function dokonceno() {
    okno.nadpis.textContent = 'Dokončeno';
    okno.cislo.textContent = 'Soubor je hotový a stahuje se.';
    okno.cislo.classList.remove('stahovani-chyba');
    okno.vypln.classList.remove('ceka');
    okno.vypln.style.width = '100%';
    okno.rada.hidden = true;
    okno.zavrit.classList.add('hlavni');
    okno.zavrit.focus();
  }

  function cas(sekundy) {
    if (sekundy < 60) return `${sekundy} s`;
    const m = Math.floor(sekundy / 60), s = sekundy % 60;
    return s ? `${m} min ${s} s` : `${m} min`;
  }

  function vykresliPostup(j) {
    if (!j.celkem) { okno.cislo.textContent = 'Začínám…'; return; }
    okno.vypln.classList.remove('ceka');
    const procent = Math.round(j.hotovo / j.celkem * 100);
    okno.vypln.style.width = `${procent}%`;
    const zbyva = j.zbyva_s ? `, zbývá asi ${cas(j.zbyva_s)}` : '';
    okno.cislo.textContent = `Strana ${j.hotovo} z ${j.celkem}${zbyva}`;
  }

  async function spust() {
    const { format, parametry } = recept();
    const dotaz = dotazRetezec(parametry);
    const url = `/songbook/${encodeURIComponent(zpevnik)}/export.${format}${dotaz}`;
    const klic = `${format}${dotaz}`;

    let odpoved;
    try {
      odpoved = await fetch(url, { headers: { 'Accept': 'application/pdf, application/zip' } });
    } catch (e) {
      chyba('Nepodařilo se spojit se serverem.');
      return;
    }

    // Výhradně 200, ne response.ok: to je pravdivé i pro 202, tedy "začal jsem to
    // skládat" - a na tom se dá odnavigovat na JSON místo stažení souboru.
    if (odpoved.status === 200) {
      window.location = url;   // hotové, server ho vydá ze své cache
      zavri();
      return;
    }
    if (odpoved.status !== 202) {
      let hlaska = {
        429: 'Server teď skládá jiný zpěvník, zkus to za chvíli.',
        413: 'Zpěvník je na stažení příliš velký.',
      }[odpoved.status];
      if (!hlaska) {
        try { hlaska = (await odpoved.json()).error; } catch (e) { /* níž výchozí */ }
      }
      chyba(hlaska || 'Stažení se nepovedlo.');
      return;
    }

    ukazPostup();
    bezi = klic;

    // Volby patří i do dotazu na stav, jinak by odpovídal za jiný recept.
    const statusUrl =
      `/songbook/${encodeURIComponent(zpevnik)}/export-status/${format}${dotaz}`;
    const zacatek = Date.now();
    const dotazSeNaStav = async () => {
      // Zavřené okno znamená "nečekám" - ptát se dál by nemělo komu co ukázat.
      if (bezi !== klic) return;
      if (Date.now() - zacatek > 10 * 60 * 1000) {
        chyba('Trvá to podezřele dlouho. Zkus to prosím znovu.');
        return;
      }
      let j = { state: 'error' };
      try {
        j = await (await fetch(statusUrl)).json();
      } catch (e) { /* výpadek sítě bereme jako chybu níž */ }

      if (j.state === 'ready') {
        bezi = null;
        dokonceno();
        window.location = url;
        return;
      }
      if (j.state === 'error') { chyba('Skládání souboru selhalo.'); return; }
      if (j.state === 'idle') {
        // Nikdo to nestaví a hotové to není - soubor mezitím zmizel z cache.
        spust();
        return;
      }
      vykresliPostup(j);
      // Zprvu se ptáme často, po chvíli řidčeji: u velkého zpěvníku nemá smysl bušit
      // na server každou vteřinu několik minut v kuse.
      const uplynulo = Date.now() - zacatek;
      setTimeout(dotazSeNaStav, uplynulo < 20000 ? 1000 : (uplynulo < 60000 ? 2000 : 5000));
    };
    setTimeout(dotazSeNaStav, 700);
  }

  return { otevri, zavri };
})();
