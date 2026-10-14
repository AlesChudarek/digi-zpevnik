/* Přezdívka a profilová fotka (_profil_udaje.html). Po uložení se stránka načte
   znovu - jméno a kolečko jsou i v liště, takže je to nejjistější cesta, jak je
   obnovit všude najednou. */
(function () {
  'use strict';
  const udaje = document.getElementById('profil-udaje');
  if (!udaje) return;

  async function posli(url, moznosti, nadpisChyby) {
    try {
      const r = await fetch(url, moznosti);
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) {
        oznam(j.error || 'Server změnu nepřijal.', { nadpis: nadpisChyby });
        return false;
      }
      location.reload();
      return true;
    } catch (e) {
      oznam('Server neodpověděl. Příčinou bývá výpadek připojení.', { nadpis: nadpisChyby });
      return false;
    }
  }

  document.getElementById('profil-soubor').addEventListener('change', (e) => {
    const soubor = e.target.files && e.target.files[0];
    if (!soubor) return;
    const fd = new FormData();
    fd.append('fotka', soubor);
    posli('/profil/fotka', { method: 'POST', body: fd }, 'Fotku se nepodařilo nahrát');
  });

  document.getElementById('profil-odebrat').addEventListener('click', () => {
    posli('/profil/fotka', { method: 'DELETE' }, 'Fotku se nepodařilo odebrat');
  });

  const pole = document.getElementById('profil-prezdivka');
  const ulozit = () => posli('/profil/udaje', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prezdivka: pole.value }),
  }, 'Přezdívku se nepodařilo uložit');
  document.getElementById('profil-ulozit').addEventListener('click', ulozit);
  pole.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ulozit(); } });
})();
