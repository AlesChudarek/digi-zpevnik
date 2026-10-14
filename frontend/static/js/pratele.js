/* Stránka Přátelé: žádosti, potvrzení, odebrání. Po každé změně se stránka načte
   znovu - přehled sdílení a místa počítá server. */
(function () {
  'use strict';

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
  function potvrd(text) {
    return new Promise((resolve) => {
      const o = document.getElementById('app-confirm-overlay');
      document.getElementById('app-confirm-message').textContent = text;
      o.style.display = 'flex';
      o.setAttribute('aria-hidden', 'false');
      const konec = (vysledek) => {
        o.style.display = 'none';
        o.setAttribute('aria-hidden', 'true');
        o.removeEventListener('click', klik);
        document.removeEventListener('keydown', klavesa);
        resolve(vysledek);
      };
      const klik = (e) => {
        if (e.target.id === 'app-confirm-ok') konec(true);
        else if (e.target.id === 'app-confirm-cancel' || e.target === o) konec(false);
      };
      const klavesa = (e) => { if (e.key === 'Escape') konec(false); };
      o.addEventListener('click', klik);
      document.addEventListener('keydown', klavesa);
    });
  }

  const json = (data) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(data) });

  const pole = document.getElementById('pritel-email');
  async function pridej() {
    if (!pole.value.trim()) return;
    const j = await posli('/api/pratele', json({ email: pole.value }), 'Žádost se nepodařilo odeslat');
    if (j) { await oznam(j.zprava, { druh: 'info', nadpis: j.prijato ? 'Nový přítel' : 'Žádost odeslána' }); location.reload(); }
  }
  document.getElementById('pritel-pridat').addEventListener('click', pridej);
  pole.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); pridej(); } });

  document.addEventListener('click', async (e) => {
    const t = e.target;
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
    if (j) location.reload();
  });
})();
