/* Okna Účet a Přátelé z nabídky účtu, plus profil na stránce Vítejte.
 *
 * Obsah oken dodává server (/okno/ucet, /okno/pratele) a po každé změně se stáhne
 * znovu - místo přátel, žádosti a pozvánky počítá server. Změny, které se ukazují
 * i v liště (fotka, přezdívka, e-mail), načtou celou stránku.
 *
 * Veřejné API:
 *   UcetOkna.otevri('ucet' | 'pratele')
 * Adresa s ?okno=ucet / ?okno=pratele okno otevře rovnou (tam vedou /profil a /pratele).
 */
window.UcetOkna = (function () {
  'use strict';

  const NADPISY = { ucet: 'Účet', pratele: 'Přátelé' };
  let otevrene = null;
  const $ = (id) => document.getElementById(id);

  async function nacti(co) {
    const obsah = $('ucet-okno-obsah');
    obsah.classList.add('nacita');
    try {
      const r = await fetch(`/okno/${co}`);
      if (!r.ok) throw new Error();
      obsah.innerHTML = await r.text();
    } catch (e) {
      obsah.textContent = 'Obsah se nepodařilo načíst.';
    }
    obsah.classList.remove('nacita');
  }

  async function otevri(co) {
    const okno = $('ucet-okno');
    if (!okno) return;
    if (typeof window.zavriPanelUctu === 'function') window.zavriPanelUctu();
    else document.getElementById('ucet-panel')?.classList.remove('open');
    otevrene = co;
    $('ucet-okno-nadpis').textContent = NADPISY[co] || '';
    $('ucet-okno-obsah').textContent = '';
    okno.hidden = false;
    document.body.style.overflow = 'hidden';
    await nacti(co);
  }

  function zavri() {
    const okno = $('ucet-okno');
    if (!okno) return;
    okno.hidden = true;
    otevrene = null;
    document.body.style.overflow = '';
  }

  const potvrd = (text) => window.showConfirm(text, 'Odebrat', { nebezpecne: true });

  /* Pošle požadavek; chybu ukáže přes oznam(). Vrací odpověď, nebo null. */
  async function posli(url, moznosti, nadpisChyby) {
    try {
      const r = await fetch(url, moznosti);
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) {
        oznam(j.error || 'Server změnu nepřijal.', { nadpis: nadpisChyby });
        return null;
      }
      return j;
    } catch (e) {
      oznam('Server neodpověděl. Příčinou bývá výpadek připojení.', { nadpis: nadpisChyby });
      return null;
    }
  }
  const json = (data) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(data) });
  const hodnota = (id) => ($(id) ? $(id).value : '');

  /* Po změně: obsah okna znovu, nebo celá stránka, když se změna ukazuje i mimo okno. */
  async function hotovo(j, celaStranka, nadpis) {
    if (j.zprava) await oznam(j.zprava, { druh: 'info', nadpis: nadpis || 'Hotovo' });
    if (celaStranka || !otevrene) {
      const url = new URL(location.href);
      if (otevrene) url.searchParams.set('okno', otevrene);
      location.href = url.toString();
    } else {
      nacti(otevrene);
    }
  }

  async function prectiKlik(e) {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.closest('.ucet-okno-zavrit') || t.id === 'ucet-okno') { zavri(); return; }

    // Stránka Vítejte: Pokračovat uloží přezdívku (je-li nová) a jde dál.
    if (t.closest('.vitejte-pokracovat')) {
      const pole = $('profil-prezdivka');
      if (pole && pole.value.trim() !== pole.defaultValue.trim()) {
        e.preventDefault();
        const j = await posli('/profil/udaje', json({ prezdivka: pole.value }), 'Přezdívku se nepodařilo uložit');
        if (j) location.href = t.closest('.vitejte-pokracovat').href;
      }
      return;
    }
    // Profil
    if (t.id === 'profil-odebrat') {
      const j = await posli('/profil/fotka', { method: 'DELETE' }, 'Fotku se nepodařilo odebrat');
      if (j) hotovo(j, true);
    } else if (t.id === 'profil-ulozit') {
      const j = await posli('/profil/udaje', json({ prezdivka: hodnota('profil-prezdivka') }),
                            'Přezdívku se nepodařilo uložit');
      if (j) hotovo(j, true);
    // E-mail a heslo
    } else if (t.id === 'ucet-overeni') {
      const j = await posli('/poslat-overeni-znovu', json({}), 'Ověřovací e-mail se nepodařilo odeslat');
      if (j) hotovo(j, false, 'Odesláno');
    } else if (t.id === 'ucet-email-ulozit') {
      const j = await posli('/profil/email', json({ email: hodnota('ucet-novy-email'),
                                                    heslo: hodnota('ucet-email-heslo') }),
                            'E-mail se nepodařilo změnit');
      if (j) hotovo(j, true, 'E-mail změněn');
    } else if (t.id === 'ucet-heslo-ulozit') {
      const j = await posli('/profil/zmena-hesla', json({ stare: hodnota('ucet-heslo-stare'),
                                                          nove: hodnota('ucet-heslo-nove'),
                                                          nove2: hodnota('ucet-heslo-nove2') }),
                            'Heslo se nepodařilo změnit');
      if (j) hotovo(j, false, 'Heslo změněno');
    // Přátelé
    } else if (t.id === 'pritel-pridat') {
      pridejPritele();
    } else {
      const zadost = t.closest('[data-zadost]');
      const uzivatel = t.closest('[data-uzivatel]');
      let j = null;
      if (t.classList.contains('zadost-prijmout') && zadost) {
        j = await posli(`/api/pratele/zadost/${zadost.dataset.zadost}/prijmout`, { method: 'POST' }, 'Potvrzení se nepovedlo');
      } else if (t.classList.contains('zadost-odmitnout') && zadost) {
        j = await posli(`/api/pratele/zadost/${zadost.dataset.zadost}/odmitnout`, { method: 'POST' }, 'Odmítnutí se nepovedlo');
      } else if (t.classList.contains('zadost-zrusit') && uzivatel) {
        j = await posli(`/api/pratele/${uzivatel.dataset.uzivatel}`, { method: 'DELETE' }, 'Žádost se nepodařilo zrušit');
      } else if (t.classList.contains('pritel-odebrat') && uzivatel) {
        if (!(await potvrd(`Odebrat ${t.dataset.jmeno} z přátel? Společné zpěvníky ani pozvánky se tím nezmění.`))) return;
        j = await posli(`/api/pratele/${uzivatel.dataset.uzivatel}`, { method: 'DELETE' }, 'Odebrání se nepovedlo');
      } else if (t.classList.contains('navrh-pridat') && uzivatel) {
        j = await posli('/api/pratele', json({ id: uzivatel.dataset.uzivatel }), 'Žádost se nepodařilo odeslat');
      } else {
        return;
      }
      // Žádost se ukazuje i v liště (tečka, počet), proto celá stránka.
      if (j) hotovo(j, true);
    }
  }

  async function pridejPritele() {
    if (!hodnota('pritel-email').trim()) return;
    const j = await posli('/api/pratele', json({ email: hodnota('pritel-email') }), 'Žádost se nepodařilo odeslat');
    if (j) hotovo(j, false, j.prijato ? 'Nový přítel' : 'Žádost odeslána');
  }

  document.addEventListener('click', prectiKlik);
  /* ---------- ořez profilové fotky ----------
     Celá fotka v ploše, přes ni kruh. Tažením se posouvá, posuvníkem a kolečkem myši
     přibližuje. Odešle se jen výřez 512 × 512 (JPEG), takže na velikosti původní
     fotky nezáleží - dřív se fotka nad limit nahrávání odmítla. */
  const VYSTUP_PX = 512;

  function orez(soubor) {
    const url = URL.createObjectURL(soubor);
    const img = new Image();
    img.onerror = () => {
      URL.revokeObjectURL(url);
      oznam('Soubor se nepodařilo otevřít jako obrázek. Podporované jsou JPG, PNG, WEBP a GIF.',
            { nadpis: 'Fotku nejde použít' });
    };
    img.onload = () => {
      const svg = /svg/i.test(soubor.type) || /\.svg$/i.test(soubor.name || '');
      if (!svg) { ukazOrez(img, url); return; }
      // SVG bez vlastní velikosti (prohlížeč hlásí 150 × 150) kreslí drawImage s výřezem
      // v jiných souřadnicích než celý obrázek - výsledek neodpovídal výběru (Aleš
      // 14. 10.). Proto se nejdřív vykreslí celé do obyčejného obrázku a ořez jde z něj.
      const delsi = 1024;
      const w = img.naturalWidth || 1, h = img.naturalHeight || 1;
      const platno = document.createElement('canvas');
      platno.width = Math.round(w >= h ? delsi : delsi * w / h);
      platno.height = Math.round(h >= w ? delsi : delsi * h / w);
      platno.getContext('2d').drawImage(img, 0, 0, platno.width, platno.height);
      URL.revokeObjectURL(url);
      platno.toBlob((blob) => {
        const url2 = URL.createObjectURL(blob);
        const img2 = new Image();
        img2.onload = () => ukazOrez(img2, url2);
        img2.src = url2;
      }, 'image/png');
    };
    img.src = url;
  }

  function ukazOrez(img, url) {
    const zaclona = document.createElement('div');
    zaclona.className = 'orez-zaclona';
    zaclona.innerHTML = `
      <div class="okno orez-okno" role="dialog" aria-modal="true" aria-label="Výřez fotky">
        <h3>Výřez fotky</h3>
        <p>Posuňte fotku tažením, přibližte posuvníkem nebo kolečkem myši.</p>
        <div class="orez-plocha"><div class="orez-kruh"></div></div>
        <input type="range" class="orez-zoom" min="1" max="4" step="0.01" value="1" aria-label="Přiblížení">
        <div class="actions">
          <button type="button" class="btn" data-orez="zrusit">Zrušit</button>
          <button type="button" class="btn primary" data-orez="ulozit">Uložit</button>
        </div>
      </div>`;
    document.body.appendChild(zaclona);
    const plocha = zaclona.querySelector('.orez-plocha');
    const zoom = zaclona.querySelector('.orez-zoom');
    plocha.prepend(img);
    img.className = 'orez-obrazek';
    img.draggable = false;

    const S = plocha.clientWidth;
    const zaklad = S / Math.min(img.naturalWidth, img.naturalHeight);
    let meritko = zaklad, x = 0, y = 0;

    function omez() {
      const w = img.naturalWidth * meritko, h = img.naturalHeight * meritko;
      x = Math.min(0, Math.max(S - w, x));
      y = Math.min(0, Math.max(S - h, y));
    }
    function vykresli() {
      img.style.width = `${img.naturalWidth * meritko}px`;
      img.style.height = `${img.naturalHeight * meritko}px`;
      img.style.transform = `translate(${x}px, ${y}px)`;
    }
    // Přiblížení kolem bodu (střed plochy nebo kurzor), ať výřez neuskakuje.
    function priblizNa(nove, bx, by) {
      const nove_m = zaklad * Math.min(4, Math.max(1, nove));
      x = bx - (bx - x) * nove_m / meritko;
      y = by - (by - y) * nove_m / meritko;
      meritko = nove_m;
      zoom.value = String(meritko / zaklad);
      omez(); vykresli();
    }
    x = (S - img.naturalWidth * meritko) / 2;
    y = (S - img.naturalHeight * meritko) / 2;
    vykresli();

    zoom.addEventListener('input', () => priblizNa(Number(zoom.value), S / 2, S / 2));
    plocha.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = plocha.getBoundingClientRect();
      priblizNa(meritko / zaklad * (e.deltaY < 0 ? 1.08 : 1 / 1.08), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let tah = null;
    plocha.addEventListener('pointerdown', (e) => {
      tah = { px: e.clientX, py: e.clientY, x, y };
      plocha.setPointerCapture(e.pointerId);
    });
    plocha.addEventListener('pointermove', (e) => {
      if (!tah) return;
      x = tah.x + e.clientX - tah.px;
      y = tah.y + e.clientY - tah.py;
      omez(); vykresli();
    });
    plocha.addEventListener('pointerup', () => { tah = null; });

    const zavriOrez = () => { zaclona.remove(); URL.revokeObjectURL(url); };
    zaclona.addEventListener('click', async (e) => {
      const akce = e.target.dataset && e.target.dataset.orez;
      if (akce === 'zrusit' || e.target === zaclona) { zavriOrez(); return; }
      if (akce !== 'ulozit') return;
      const platno = document.createElement('canvas');
      platno.width = platno.height = VYSTUP_PX;
      const ctx = platno.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, VYSTUP_PX, VYSTUP_PX);
      ctx.drawImage(img, -x / meritko, -y / meritko, S / meritko, S / meritko, 0, 0, VYSTUP_PX, VYSTUP_PX);
      const blob = await new Promise((r) => platno.toBlob(r, 'image/jpeg', 0.9));
      const fd = new FormData();
      fd.append('fotka', blob, 'profil.jpg');
      e.target.disabled = true;
      const j = await posli('/profil/fotka', { method: 'POST', body: fd }, 'Fotku se nepodařilo nahrát');
      e.target.disabled = false;
      if (j) { zavriOrez(); hotovo(j, true); }
    });
    zaclona.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); zavriOrez(); } });
    zaclona.querySelector('[data-orez="ulozit"]').focus();
  }

  document.addEventListener('change', (e) => {
    if (e.target.id !== 'profil-soubor') return;
    const soubor = e.target.files && e.target.files[0];
    e.target.value = '';      // ať jde vybrat znovu tentýž soubor
    if (soubor) orez(soubor);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && otevrene && !document.querySelector('.orez-zaclona')
        && document.getElementById('app-confirm-overlay')?.getAttribute('aria-hidden') !== 'false') { zavri(); return; }
    if (e.key !== 'Enter') return;
    const id = e.target.id;
    if (id === 'pritel-email') { e.preventDefault(); pridejPritele(); }
    else if (id === 'profil-prezdivka') {
      e.preventDefault();
      const dal = document.querySelector('.vitejte-pokracovat');
      (dal || $('profil-ulozit')).click();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const co = new URL(location.href).searchParams.get('okno');
    if (co && NADPISY[co]) {
      // Z adresy pryč, ať se okno neotevře znovu po obnovení stránky.
      const url = new URL(location.href);
      url.searchParams.delete('okno');
      history.replaceState(null, '', url.toString());
      otevri(co);
    }
  });

  return { otevri, zavri };
})();
