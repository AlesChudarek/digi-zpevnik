/* Zpětná vazba: záložka u pravého okraje každé stránky, po kliknutí krátký formulář
 * vpravo dole (na telefonu přes celou šířku). Ke zprávě se přibalí kontext stránky
 * (adresa, titulek, okno, jazyk, motiv, poslední chyby JavaScriptu, u editoru stran
 * krátký souhrn) a volitelně snímek obrazovky - pořízený prohlížečem, ze souboru,
 * nebo vložený ze schránky. Prohlížení a vyřizování: Správa → Zpětná vazba.
 *
 * Načítá se v dashboard_base bez `defer` co nejdřív, ať zachytí i chyby skriptů,
 * které běží při načítání stránky. Formulář se staví až po DOMContentLoaded.
 * Vypíná se na serveru proměnnou ZPETNA_VAZBA=0 - šablona pak skript vůbec nevloží.
 */
(function () {
  'use strict';

  // --- Chyby JavaScriptu na stránce, posledních 10 --------------------------------
  const MAX_CHYB = 10;
  const chyby = [];
  function zapisChybu(zprava, zdroj, stack) {
    chyby.push({
      cas: new Date().toISOString(),
      zprava: String(zprava || '').slice(0, 500),
      zdroj: String(zdroj || '').slice(0, 300),
      stack: String(stack || '').slice(0, 1500),
    });
    if (chyby.length > MAX_CHYB) chyby.shift();
  }
  window.addEventListener('error', (e) => {
    // Chyba načtení obrázku nebo skriptu nemá message, jen cíl.
    if (!e.message && e.target && e.target !== window) {
      zapisChybu('Nenačetl se zdroj: ' + (e.target.src || e.target.href || e.target.tagName), '', '');
      return;
    }
    const zdroj = e.filename ? `${e.filename}:${e.lineno || 0}:${e.colno || 0}` : '';
    zapisChybu(e.message, zdroj, e.error && e.error.stack);
  }, true);
  window.addEventListener('unhandledrejection', (e) => {
    const d = e.reason;
    zapisChybu('Nezachycená chyba v Promise: ' + ((d && d.message) || String(d)), '', d && d.stack);
  });

  const DRUHY = [['chyba', 'Chyba'], ['napad', 'Nápad'], ['jine', 'Jiné']];
  const MAX_ZPRAVA = 5000;
  const lzePoriditSnimek = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);

  let zalozka, zaclona, okno, formular, podekovani, textarea, nahled, nahledImg, akceSnimku, odeslat;
  let otevreno = false;
  let odeslano = false;
  let odesila = false;
  let druh = 'chyba';
  let snimek = '';

  function el(tag, trida, text) {
    const e = document.createElement(tag);
    if (trida) e.className = trida;
    if (text != null) e.textContent = text;
    return e;
  }

  function postav() {
    zalozka = el('button', 'zv-zalozka');
    zalozka.type = 'button';
    zalozka.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
      '<path fill="currentColor" d="M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2zm1 2v12.2L7.3 16H20V5H5zm2 3h10v2H7V8zm0 4h7v2H7v-2z"/></svg>';
    zalozka.appendChild(el('span', null, 'Zpětná vazba'));
    zalozka.addEventListener('click', otevri);

    zaclona = el('div', 'zv-zaclona');
    zaclona.hidden = true;
    zaclona.addEventListener('click', (e) => { if (e.target === zaclona) zavri(); });

    okno = el('section', 'okno zv-okno');
    okno.hidden = true;
    okno.setAttribute('role', 'dialog');
    okno.setAttribute('aria-modal', 'true');
    okno.setAttribute('aria-labelledby', 'zv-nadpis');
    // Klávesy z formuláře nepatří stránce pod ním (čtečka listuje šipkami, editor má
    // zkratky). Esc obsluhuje naEsc výš na window, ten se sem nedostane.
    okno.addEventListener('keydown', (e) => e.stopPropagation());

    const hlava = el('div', 'zv-hlava');
    const h = el('h2', null, 'Zpětná vazba');
    h.id = 'zv-nadpis';
    const krizek = el('button', 'zv-krizek', '×');
    krizek.type = 'button';
    krizek.setAttribute('aria-label', 'Zavřít');
    krizek.addEventListener('click', zavri);
    hlava.append(h, krizek);

    // --- formulář ---
    formular = el('div', 'zv-formular');
    const druhy = el('div', 'zv-druhy');
    druhy.setAttribute('role', 'radiogroup');
    druhy.setAttribute('aria-label', 'Druh zprávy');
    DRUHY.forEach(([hodnota, popis]) => {
      const b = el('button', 'zv-druh', popis);
      b.type = 'button';
      b.dataset.druh = hodnota;
      b.setAttribute('role', 'radio');
      b.addEventListener('click', () => { druh = hodnota; vykresliDruh(); });
      druhy.appendChild(b);
    });

    textarea = el('textarea', 'zv-text');
    textarea.rows = 5;
    textarea.maxLength = MAX_ZPRAVA;
    textarea.placeholder = 'Popis chyby nebo nápadu…';
    textarea.setAttribute('aria-label', 'Zpráva');
    textarea.addEventListener('input', vykresliTlacitko);
    // Enter odešle, Shift+Enter nový řádek.
    textarea.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      posli();
    });

    nahled = el('div', 'zv-nahled');
    nahled.hidden = true;
    nahledImg = el('img');
    nahledImg.alt = 'Přiložený snímek obrazovky';
    const odebrat = el('button', 'btn zv-male', 'Odebrat snímek');
    odebrat.type = 'button';
    odebrat.addEventListener('click', () => nastavSnimek(''));
    nahled.append(nahledImg, odebrat);

    akceSnimku = el('div', 'zv-snimek-akce');
    if (lzePoriditSnimek) {
      const poridit = el('button', 'btn zv-male', 'Pořídit snímek obrazovky');
      poridit.type = 'button';
      poridit.addEventListener('click', poridSnimek);
      akceSnimku.appendChild(poridit);
    }
    const soubor = el('label', 'btn zv-male zv-soubor', 'Přiložit obrázek');
    const vstup = el('input');
    vstup.type = 'file';
    vstup.accept = 'image/*';
    vstup.addEventListener('change', () => {
      const f = vstup.files && vstup.files[0];
      vstup.value = '';
      if (f) pouzijObrazek(f);
    });
    soubor.appendChild(vstup);
    akceSnimku.append(soubor, el('span', 'zv-pozn', 'nebo vložit ze schránky (Ctrl+V)'));

    const pozn = el('p', 'zv-pozn', 'Ke zprávě se přibalí adresa stránky, údaje o prohlížeči ' +
      'a případné chyby stránky.');

    const akce = el('div', 'actions');
    const zrusit = el('button', 'btn', 'Zrušit');
    zrusit.type = 'button';
    zrusit.addEventListener('click', zavri);
    odeslat = el('button', 'btn primary', 'Odeslat');
    odeslat.type = 'button';
    odeslat.addEventListener('click', posli);
    akce.append(zrusit, odeslat);
    formular.append(druhy, textarea, nahled, akceSnimku, pozn, akce);

    // --- poděkování ---
    podekovani = el('div', 'zv-podekovani');
    podekovani.hidden = true;
    const diky = el('p', null, 'Děkujeme, zpráva byla odeslána.');
    const akce2 = el('div', 'actions');
    const zavrit = el('button', 'btn primary', 'Zavřít');
    zavrit.type = 'button';
    zavrit.addEventListener('click', zavri);
    akce2.appendChild(zavrit);
    podekovani.append(diky, akce2);

    okno.append(hlava, formular, podekovani);
    document.body.append(zalozka, zaclona, okno);
    vykresliDruh();
    vykresliTlacitko();
  }

  function vykresliDruh() {
    okno.querySelectorAll('.zv-druh').forEach((b) => {
      const zapnuty = b.dataset.druh === druh;
      b.classList.toggle('zapnuty', zapnuty);
      b.setAttribute('aria-checked', zapnuty ? 'true' : 'false');
    });
  }

  function vykresliTlacitko() {
    odeslat.disabled = odesila || !textarea.value.trim();
    odeslat.textContent = odesila ? 'Odesílá se…' : 'Odeslat';
  }

  function nastavSnimek(data) {
    snimek = data || '';
    nahled.hidden = !snimek;
    akceSnimku.hidden = !!snimek;
    if (snimek) nahledImg.src = snimek; else nahledImg.removeAttribute('src');
  }

  function otevri() {
    odeslano = false;
    formular.hidden = false;
    podekovani.hidden = true;
    otevreno = true;
    zaclona.hidden = false;
    okno.hidden = false;
    zalozka.hidden = true;
    textarea.focus();
  }

  function zavri() {
    if (!otevreno) return;
    otevreno = false;
    zaclona.hidden = true;
    okno.hidden = true;
    zalozka.hidden = false;
    // Rozepsaná zpráva zůstává; vymaže se až po odeslání.
    if (odeslano) {
      textarea.value = '';
      nastavSnimek('');
      druh = 'chyba';
      vykresliDruh();
      vykresliTlacitko();
    }
    zalozka.focus();
  }

  // Esc zavře formulář, jen když je navrchu - oznámení nebo nápověda nad ním ho
  // dostanou dřív (jeOknoNahore v dashboard_base). Zachytávací fáze na window, ať
  // Esc nezavře zároveň okno nebo editor pod formulářem.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !otevreno) return;
    if (window.jeOknoNahore && window.jeOknoNahore()) return;
    e.stopPropagation();
    e.preventDefault();
    zavri();
  }, true);

  // Obrázek ze schránky (Ctrl/⌘+V), když je formulář otevřený.
  document.addEventListener('paste', (e) => {
    if (!otevreno || odeslano || !e.clipboardData) return;
    const polozka = Array.from(e.clipboardData.items || []).find((i) => i.type && i.type.indexOf('image/') === 0);
    const f = polozka && polozka.getAsFile();
    if (!f) return;
    e.preventDefault();
    pouzijObrazek(f);
  });

  /** Zmenší na ≤ 1600 px a převede na JPEG, ať se zpráva vejde do limitu serveru. */
  function naJpeg(zdroj, sirka, vyska) {
    const meritko = Math.min(1, 1600 / Math.max(sirka, vyska, 1));
    const platno = document.createElement('canvas');
    platno.width = Math.max(1, Math.round(sirka * meritko));
    platno.height = Math.max(1, Math.round(vyska * meritko));
    const ctx = platno.getContext('2d');
    // Průhledné PNG by v JPEG zčernalo.
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, platno.width, platno.height);
    ctx.drawImage(zdroj, 0, 0, platno.width, platno.height);
    let kvalita = 0.82;
    let data = platno.toDataURL('image/jpeg', kvalita);
    while (data.length > 2500000 && kvalita > 0.4) {
      kvalita -= 0.15;
      data = platno.toDataURL('image/jpeg', kvalita);
    }
    return data;
  }

  async function pouzijObrazek(soubor) {
    const url = URL.createObjectURL(soubor);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      nastavSnimek(naJpeg(img, img.naturalWidth, img.naturalHeight));
    } catch (_) {
      chyba('Soubor se nepodařilo načíst jako obrázek.', 'Obrázek nejde přiložit');
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /** Snímek přes prohlížeč: formulář se na chvíli schová, ať na snímku není. */
  async function poridSnimek() {
    zaclona.hidden = true;
    okno.hidden = true;
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { displaySurface: 'browser' },
        preferCurrentTab: true,
      });
      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      // Okno s výběrem sdílení se zavírá chvíli po potvrzení.
      await new Promise((hotovo) => setTimeout(hotovo, 400));
      nastavSnimek(naJpeg(video, video.videoWidth, video.videoHeight));
    } catch (_) {
      /* zrušeno nebo nepovoleno - formulář zůstane bez snímku */
    } finally {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (otevreno) {
        zaclona.hidden = false;
        okno.hidden = false;
        textarea.focus();
      }
    }
  }

  function kontext() {
    const k = {
      url: location.href,
      titulek: document.title,
      viewport: `${window.innerWidth}×${window.innerHeight}`,
      pomerPixelu: window.devicePixelRatio || 1,
      jazyk: navigator.language || '',
      motiv: ((document.documentElement.className || '').match(/\btheme-([a-z]+)\b/) || [null, 'green'])[1],
      chybyJs: chyby.slice(),
    };
    // Editor stran: jen adresa a počet prvků, nic velkého.
    try {
      const ns = window.navrhStrany;
      if (ns) {
        let prvku = null;
        try { prvku = ns.view.state.doc.childCount; } catch (_) { /* editor ještě nenaběhl */ }
        k.editorStran = { adresa: location.pathname + location.search, prvku: prvku };
      }
    } catch (_) { /* souhrn editoru je jen navíc */ }
    return k;
  }

  function chyba(text, nadpis) {
    if (window.oznam) window.oznam(text, { nadpis: nadpis, druh: 'chyba' });
    else window.alert(text);   // eslint-disable-line no-alert
  }

  async function posli() {
    const zprava = textarea.value.trim();
    if (!zprava || odesila) return;
    odesila = true;
    vykresliTlacitko();
    let data = {};
    let ok = false;
    try {
      const res = await fetch('/api/zpetna-vazba', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ druh: druh, zprava: zprava, url: location.href, snimek: snimek || null, kontext: kontext() }),
      });
      data = await res.json().catch(() => ({}));
      ok = res.ok && data.ok;
      if (!ok) chyba(data.error || `Chyba serveru ${res.status}.`, 'Zprávu se nepodařilo odeslat');
    } catch (_) {
      chyba('Server neodpověděl. Příčinou bývá výpadek připojení.', 'Zprávu se nepodařilo odeslat');
    }
    odesila = false;
    vykresliTlacitko();
    if (ok) {
      odeslano = true;
      formular.hidden = true;
      podekovani.hidden = false;
      podekovani.querySelector('.btn').focus();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', postav);
  else postav();
})();
