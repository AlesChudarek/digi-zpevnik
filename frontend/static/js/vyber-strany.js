/* Okno „Upravit stranu“: co otevřít v editoru stran. Dva kroky - zpěvník (jen z Mých
 * zpěvníků) a jeho strana, nebo nová strana na konec. Strana se tak vždycky upravuje na
 * svém místě ve zpěvníku.
 *
 * Dvě podoby: na stránce /editor-strany (odkaz Editor strany v liště) je panel přímo
 * ve stránce (misto), v editoru stran ho tlačítko Jiná strana… (data-vyber-strany)
 * otevře jako okno přes editor. Strany jsou obyčejné odkazy: editor stran s rozdělanou
 * stranou je zachytí a zeptá se (navrh.js).
 *
 *   VyberStrany.otevri({ zpevnik, misto })
 */
(function () {
  let okno = null;

  function postav(misto) {
    if (okno) return okno;
    const zaclona = misto || document.createElement('div');
    if (!misto) {
      zaclona.className = 'vs-zaclona';
      zaclona.hidden = true;
    }
    zaclona.innerHTML = `
      <div class="okno vs-okno" role="dialog" aria-modal="true" aria-labelledby="vs-nadpis">
        <div class="vs-hlava">
          <button type="button" class="vs-zpet" aria-label="Zpět na zpěvníky" hidden>‹</button>
          <h2 id="vs-nadpis">Upravit stranu</h2>
        </div>
        <p class="vs-co" id="vs-co">Vyberte zpěvník.</p>
        <div class="vs-telo" id="vs-telo"></div>
        ${misto ? '' : '<div class="actions"><button type="button" class="btn vs-zavrit">Zavřít</button></div>'}
      </div>`;
    if (!misto) document.body.appendChild(zaclona);
    okno = {
      zaclona,
      nadpis: zaclona.querySelector('#vs-nadpis'),
      co: zaclona.querySelector('#vs-co'),
      telo: zaclona.querySelector('#vs-telo'),
      zpet: zaclona.querySelector('.vs-zpet'),
    };
    okno.zpet.addEventListener('click', () => ukazZpevniky());
    if (misto) return okno;
    zaclona.querySelector('.vs-zavrit').addEventListener('click', zavri);
    zaclona.addEventListener('click', (e) => { if (e.target === zaclona) zavri(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !zaclona.hidden) { e.stopPropagation(); zavri(); }
    }, true);
    return okno;
  }

  function zavri() {
    if (okno) okno.zaclona.hidden = true;
  }

  async function nacti(adresa) {
    try {
      const r = await fetch(adresa, { credentials: 'same-origin' });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || `Chyba serveru ${r.status}.`);
      return d;
    } catch (chyba) {
      okno.telo.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'vs-prazdno';
      p.textContent = `Seznam se nenačetl: ${chyba.message || chyba}`;
      okno.telo.appendChild(p);
      return null;
    }
  }

  const text = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function ukazZpevniky() {
    okno.nadpis.textContent = 'Upravit stranu';
    okno.co.textContent = 'Vyberte zpěvník z Mých zpěvníků.';
    okno.zpet.hidden = true;
    okno.telo.innerHTML = '<p class="vs-prazdno">Načítá se…</p>';
    const d = await nacti('/api/navrh/vyber');
    if (!d) return;
    if (!d.zpevniky.length) {
      okno.telo.innerHTML = '<p class="vs-prazdno">V Mých zpěvnících zatím žádný zpěvník není.</p>'
        + '<p><a href="/my-songbooks?create=1">Založit zpěvník</a></p>';
      return;
    }
    okno.telo.innerHTML = '<ul class="vs-zpevniky">' + d.zpevniky.map((z) => `
      <li><button type="button" data-zpevnik="${text(z.id)}">
        <span class="vs-barva" style="background:${text(z.barva)}"></span>
        <span class="vs-nazev">${text(z.nazev)}</span>
        <span class="vs-pocet">${z.stran} str.</span>
      </button></li>`).join('') + '</ul>';
    okno.telo.querySelectorAll('[data-zpevnik]').forEach((b) => {
      b.addEventListener('click', () => ukazStrany(b.dataset.zpevnik));
    });
  }

  async function ukazStrany(id) {
    okno.zpet.hidden = false;
    okno.telo.innerHTML = '<p class="vs-prazdno">Načítá se…</p>';
    const d = await nacti('/api/navrh/vyber?zpevnik=' + encodeURIComponent(id));
    if (!d) return;
    okno.nadpis.textContent = d.zpevnik.nazev;
    okno.co.textContent = 'Vyberte stranu, nebo přidejte novou na konec.';
    okno.telo.innerHTML = '<div class="vs-strany">' + d.strany.map((s) => `
      <a class="vs-strana" href="${text(s.adresa)}" data-strana="${s.id}" title="${text(s.cislo + ' – ' + s.popis)}">
        <span class="vs-nahled">${s.nahled ? `<img src="${text(s.nahled)}" alt="" loading="lazy">` : '<span class="vs-prazdna"></span>'}
          ${s.navrzena ? '<span class="vs-tuzka" title="Navržená strana"></span>' : ''}</span>
        <span class="vs-popis"><b>${s.cislo}</b> ${text(s.popis)}</span>
      </a>`).join('') + `
      <a class="vs-strana vs-nova" href="${text(d.nova)}">
        <span class="vs-nahled">+</span><span class="vs-popis">Nová strana</span>
      </a></div>`;
  }

  window.VyberStrany = {
    otevri(volby = {}) {
      postav(volby.misto || null);
      okno.zaclona.hidden = false;
      if (volby.zpevnik) ukazStrany(volby.zpevnik); else ukazZpevniky();
    },
  };

  // Tlačítko Jiná strana… v editoru stran: okno přes editor.
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('[data-vyber-strany]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    window.VyberStrany.otevri({ zpevnik: a.dataset.zpevnik || null });
  });
})();
