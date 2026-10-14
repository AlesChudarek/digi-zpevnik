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

  function potvrd(text) {
    return new Promise((resolve) => {
      const p = $('ucet-potvrzeni');
      $('ucet-potvrzeni-text').textContent = text;
      p.hidden = false;
      const konec = (e) => {
        const odpoved = e.target.dataset && e.target.dataset.odpoved;
        if (!odpoved && e.target !== p) return;
        p.hidden = true;
        p.removeEventListener('click', konec);
        resolve(odpoved === 'ano');
      };
      p.addEventListener('click', konec);
    });
  }

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
  document.addEventListener('change', async (e) => {
    if (e.target.id !== 'profil-soubor') return;
    const soubor = e.target.files && e.target.files[0];
    if (!soubor) return;
    const fd = new FormData();
    fd.append('fotka', soubor);
    const j = await posli('/profil/fotka', { method: 'POST', body: fd }, 'Fotku se nepodařilo nahrát');
    if (j) hotovo(j, true);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && otevrene && $('ucet-potvrzeni')?.hidden !== false) { zavri(); return; }
    if (e.key !== 'Enter') return;
    const id = e.target.id;
    if (id === 'pritel-email') { e.preventDefault(); pridejPritele(); }
    else if (id === 'profil-prezdivka') { e.preventDefault(); $('profil-ulozit').click(); }
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
