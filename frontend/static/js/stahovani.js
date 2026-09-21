/* Stažení zpěvníku: okno se skládáním a dotazování na průběh.
 *
 * Jeden soubor pro všechna tři místa, odkud se stahuje (čtečka, Moje zpěvníky,
 * správa veřejných zpěvníků). Dřív to byly dvě kopie, které se rozešly - ve čtečce
 * okno s postupem, v seznamu holý text s uběhlými sekundami.
 *
 * Server na první požádání odpoví 202 a soubor skládá na pozadí: gunicorn sync worker
 * hlásí, že žije, jen mezi požadavky, takže by dlouhý export spadl na 30s timeout.
 * Klient se proto ptá endpointu export-status, dokud nedostane 'ready'.
 *
 * Veřejné API:
 *   Stahovani.spust(bookId, kind, variant)   'pdf'|'zip', u PDF 'small'|'high'
 *   Stahovani.oznacHotove(bookId, koren)     doplní značky ✓ hned v dané nabídce
 */
window.Stahovani = (function () {
  'use strict';

  const POPIS_VARIANTY = {
    'pdf-small': 'PDF, menší soubor',
    'pdf-high': 'PDF, plné rozlišení',
    'zip-orig': 'Obrázky v ZIP',
  };

  let okno = null;      // prvky okna, postavené při prvním otevření
  let bezi = null;      // klíč varianty, kterou zrovna sledujeme
  let posledniKoren = null;  // nabídka, ve které se po dokončení obnoví značky

  function postavOkno() {
    if (okno) return okno;
    const zaclona = document.createElement('div');
    zaclona.className = 'stahovani-zaclona';
    zaclona.id = 'stahovani-okno';
    zaclona.hidden = true;
    zaclona.innerHTML = `
      <div class="stahovani-panel" role="dialog" aria-modal="true"
           aria-labelledby="stahovani-nadpis">
        <h2 id="stahovani-nadpis">Připravuji soubor</h2>
        <p class="stahovani-co" id="stahovani-co"></p>
        <div class="stahovani-pruh"><span id="stahovani-vypln"></span></div>
        <p class="stahovani-cislo" id="stahovani-cislo">Začínám…</p>
        <p class="stahovani-rada" id="stahovani-rada">
          Můžeš počkat a soubor se stáhne sám. Nebo okno zavřít a vrátit se později —
          skládání poběží dál a příště bude ke stažení hned.
        </p>
        <div class="stahovani-tlacitka">
          <button type="button" id="stahovani-zavrit">Zavřít</button>
        </div>
      </div>`;
    // Na <body>, ne vedle tlačítka, které okno otevřelo: ve čtečce by jinak sedělo
    // uvnitř .mode-buttons a dědilo odtud tvar tlačítek i mizení celého sloupce.
    document.body.appendChild(zaclona);
    okno = {
      zaclona,
      nadpis: zaclona.querySelector('#stahovani-nadpis'),
      co: zaclona.querySelector('#stahovani-co'),
      vypln: zaclona.querySelector('#stahovani-vypln'),
      cislo: zaclona.querySelector('#stahovani-cislo'),
      rada: zaclona.querySelector('#stahovani-rada'),
      zavrit: zaclona.querySelector('#stahovani-zavrit'),
    };
    okno.zavrit.addEventListener('click', zavri);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !zaclona.hidden) zavri();
    });
    return okno;
  }

  function otevri(klic) {
    const o = postavOkno();
    o.nadpis.textContent = 'Připravuji soubor';
    o.co.textContent = POPIS_VARIANTY[klic] || '';
    o.cislo.textContent = 'Začínám…';
    o.cislo.classList.remove('stahovani-chyba');
    o.vypln.style.width = '0';
    o.vypln.classList.add('ceka');
    o.rada.hidden = false;
    o.zavrit.classList.remove('hlavni');
    o.zaclona.hidden = false;
  }

  function zavri() {
    if (okno) okno.zaclona.hidden = true;
    // Skládání běží dál na serveru. Jen se na ně přestaneme ptát.
    bezi = null;
  }

  function chyba(text) {
    const o = postavOkno();
    o.nadpis.textContent = 'Nepovedlo se';
    o.cislo.textContent = text;
    o.cislo.classList.add('stahovani-chyba');
    o.vypln.classList.remove('ceka');
    o.rada.hidden = true;
    o.zavrit.classList.add('hlavni');
  }

  /* Okno po stažení nezmizí samo. Zmizet by znamenalo, že se v prohlížeči nenápadně
     objeví soubor a nikde nezůstane, že se to povedlo - uživatel, který odešel k jiné
     záložce, by se vrátil k obrazovce beze stopy po tom, na co čekal. */
  function dokonceno() {
    const o = postavOkno();
    o.nadpis.textContent = 'Dokončeno';
    o.cislo.textContent = 'Soubor je hotový a stahuje se.';
    o.cislo.classList.remove('stahovani-chyba');
    o.vypln.classList.remove('ceka');
    o.vypln.style.width = '100%';
    o.rada.hidden = true;
    o.zavrit.classList.add('hlavni');
    o.zavrit.focus();
  }

  function cas(sekundy) {
    if (sekundy < 60) return `${sekundy} s`;
    const m = Math.floor(sekundy / 60), s = sekundy % 60;
    return s ? `${m} min ${s} s` : `${m} min`;
  }

  function vykresliPostup(j) {
    const o = postavOkno();
    if (!j.celkem) { o.cislo.textContent = 'Začínám…'; return; }
    o.vypln.classList.remove('ceka');
    const procent = Math.round(j.hotovo / j.celkem * 100);
    o.vypln.style.width = `${procent}%`;
    const zbyva = j.zbyva_s ? `, zbývá asi ${cas(j.zbyva_s)}` : '';
    o.cislo.textContent = `Strana ${j.hotovo} z ${j.celkem}${zbyva}`;
  }

  /* Které varianty leží v cache. Ptá se to jedním dotazem za všechny tři: nabídka je
     jedna a otevře se naráz celá. Volá se až při otevření nabídky, ne při načtení
     stránky - jinak by se sekvence exportu stavěla každému návštěvníkovi zbytečně. */
  async function oznacHotove(bookId, koren) {
    const kde = koren || document;
    try {
      const r = await fetch(`/songbook/${encodeURIComponent(bookId)}/export-hotove`);
      const hotove = await r.json();
      kde.querySelectorAll('[data-varianta]').forEach(b => {
        const znak = b.querySelector('.hotovo-znak');
        if (znak) znak.hidden = !hotove[b.dataset.varianta];
      });
    } catch (e) { /* nabídka funguje i bez značek */ }
  }

  async function spust(bookId, kind, variant, koren) {
    posledniKoren = koren || posledniKoren;
    const query = kind === 'pdf' ? `?q=${variant}` : '';
    const url = `/songbook/${encodeURIComponent(bookId)}/export.${kind}${query}`;
    const klic = `${kind}-${variant || 'orig'}`;

    let odpoved;
    try {
      odpoved = await fetch(url, { headers: { 'Accept': 'application/pdf, application/zip' } });
    } catch (e) {
      otevri(klic); chyba('Nepodařilo se spojit se serverem.');
      return;
    }

    // Výhradně 200, ne response.ok: to je pravdivé i pro 202, tedy "začal jsem to
    // skládat" - a na tom se dá odnavigovat na JSON místo stažení souboru.
    if (odpoved.status === 200) {
      window.location = url;   // hotové, server ho vydá ze své cache
      return;
    }
    if (odpoved.status !== 202) {
      otevri(klic);
      chyba({
        429: 'Server teď skládá jiný zpěvník, zkus to za chvíli.',
        413: 'Zpěvník je na stažení příliš velký.',
      }[odpoved.status] || 'Stažení se nepovedlo.');
      return;
    }

    otevri(klic);
    bezi = klic;

    // Varianta patří i do dotazu na stav, jinak by odpovídal za jinou variantu.
    const statusUrl = `/songbook/${encodeURIComponent(bookId)}/export-status/${kind}${query}`;
    const zacatek = Date.now();
    const dotaz = async () => {
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
        oznacHotove(bookId, posledniKoren);
        return;
      }
      if (j.state === 'error') { chyba('Skládání souboru selhalo.'); return; }
      if (j.state === 'idle') {
        // Nikdo to nestaví a hotové to není - soubor mezitím zmizel z cache.
        spust(bookId, kind, variant, posledniKoren);
        return;
      }
      vykresliPostup(j);
      // Zprvu se ptáme často, po chvíli řidčeji: u velkého zpěvníku nemá smysl bušit
      // na server každou vteřinu několik minut v kuse.
      const uplynulo = Date.now() - zacatek;
      setTimeout(dotaz, uplynulo < 20000 ? 1000 : (uplynulo < 60000 ? 2000 : 5000));
    };
    setTimeout(dotaz, 700);
  }

  return { spust, oznacHotove, zavri, POPIS_VARIANTY };
})();
