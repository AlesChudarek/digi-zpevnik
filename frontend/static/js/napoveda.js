/* Nápovědy — jedno sdílené chování pro celý projekt. Vzhled je v css/napoveda.css.

   Použití v šabloně stačí jako atribut, nic se nevolá:
     <button data-napoveda="Předchozí strana">…</button>
     <button class="napoveda-znak" data-napoveda="Delší vysvětlení volby">i</button>

   Text se čte až v okamžiku zobrazení, takže na změnu nápovědy stačí přepsat atribut.

   Nápověda, která je celá HTML (seznam, tučný nadpis), se do atributu nevejde.
   Napíše se dovnitř kotvy a ta se označí:
     <span class="napoveda-kotva" tabindex="0">
       …<div class="napoveda-obsah" hidden><strong>Sdíleno s:</strong><ul>…</ul></div>
     </span>

   Ručně (dynamický text, který se nedá napsat do atributu):
     Napoveda.ukaz(kotva, text, { html: true, seznam: true });
     Napoveda.skryj();

   Proč jedna plovoucí bublina a ne `::after` u každého prvku: takový tooltip ořízne
   první nadřazený `overflow: hidden` a na dotykové obrazovce se nikdy neukáže. */
(function () {
  'use strict';

  var bublina = null;
  var kotva = null;        // prvek, jehož nápověda je zrovna vidět
  var casovac = null;

  function priprav() {
    if (bublina) return bublina;
    bublina = document.createElement('div');
    bublina.className = 'napoveda-bublina';
    bublina.setAttribute('role', 'tooltip');
    bublina.hidden = true;
    document.body.appendChild(bublina);
    // Přejetí na samotnou bublinu ji nesmí shodit, jinak se v rolovacím seznamu nedá
    // nic přečíst do konce.
    bublina.addEventListener('mouseenter', zrusOdklad);
    bublina.addEventListener('mouseleave', skryjSOdkladem);
    return bublina;
  }

  function ukaz(prvek, text, volby) {
    if (!prvek) return;
    volby = volby || {};
    if (text == null) {
      var vlastni = prvek.querySelector('.napoveda-obsah');
      if (vlastni) {
        text = vlastni.innerHTML;
        volby = { html: true, seznam: true };
      } else {
        text = prvek.getAttribute('data-napoveda') || '';
      }
    }
    if (!text) { skryj(); return; }
    var b = priprav();
    zrusOdklad();
    if (volby.html) b.innerHTML = text; else b.textContent = text;
    b.classList.toggle('seznam', !!volby.seznam);
    b.hidden = false;
    umisti(prvek);
    if (kotva && kotva !== prvek) kotva.removeAttribute('aria-expanded');
    if (prvek.classList.contains('napoveda-znak')) {
      prvek.setAttribute('aria-expanded', 'true');
    }
    kotva = prvek;
  }

  /* Až po zviditelnění: skrytý prvek nemá rozměry, podle kterých by se dal umístit. */
  function umisti(prvek) {
    var kotvaRam = prvek.getBoundingClientRect();
    var bublinaRam = bublina.getBoundingClientRect();
    var okraj = 8;
    var x = kotvaRam.left + kotvaRam.width / 2 - bublinaRam.width / 2;
    x = Math.min(x, window.innerWidth - bublinaRam.width - okraj);
    x = Math.max(okraj, x);
    var y = kotvaRam.bottom + 6;
    if (y + bublinaRam.height > window.innerHeight - okraj) {
      y = Math.max(okraj, kotvaRam.top - bublinaRam.height - 6);
    }
    bublina.style.left = Math.round(x) + 'px';
    bublina.style.top = Math.round(y) + 'px';
  }

  /* Nápověda, která se mění za běhu (tlačítko přepínající stav). Přepsat atribut
     nestačí — když je bublina zrovna vidět, musí se překreslit. */
  function nastav(prvek, text) {
    if (!prvek) return;
    prvek.setAttribute('data-napoveda', text);
    if (prvek === kotva) ukaz(prvek, text);
  }

  function skryj() {
    zrusOdklad();
    if (bublina) bublina.hidden = true;
    if (kotva) kotva.removeAttribute('aria-expanded');
    kotva = null;
  }

  function zrusOdklad() { clearTimeout(casovac); }

  /* Malý odklad, ať se dá přejet z kotvy na samotnou bublinu a rolovat v ní.
     Bez něj bublina zmizela v půli cesty. */
  function skryjSOdkladem() {
    zrusOdklad();
    casovac = setTimeout(skryj, 160);
  }

  function najdiKotvu(cil) {
    return cil && cil.closest
      ? cil.closest('[data-napoveda], .napoveda-kotva') : null;
  }

  // Na najetí i na zaměření klávesnicí. Na dotykové obrazovce najetí neexistuje,
  // takže ⓘ musí jít i ťuknout — a druhé ťuknutí ji zase schová.
  document.addEventListener('mouseover', function (e) {
    var k = najdiKotvu(e.target);
    if (!k || k === kotva) return;
    ukaz(k);
  });
  // Podle právě zobrazené kotvy, ne podle atributu: ručně otevřená nápověda
  // (dynamický text předaný přes Napoveda.ukaz) žádný atribut nemá a jinak by se
  // neměla jak zavřít.
  document.addEventListener('mouseout', function (e) {
    if (!kotva || !kotva.contains(e.target)) return;
    if (e.relatedTarget && (kotva.contains(e.relatedTarget) ||
        (bublina && bublina.contains(e.relatedTarget)))) return;
    skryjSOdkladem();
  });
  document.addEventListener('focusin', function (e) {
    var k = najdiKotvu(e.target);
    if (k) ukaz(k); else skryj();
  });
  document.addEventListener('click', function (e) {
    var k = najdiKotvu(e.target);
    // Klik na obyčejné tlačítko dělá svou práci, nápověda jen zmizí. Znak „i" žádnou
    // jinou práci nemá, takže u něj klik nápovědu přepíná — kvůli dotyku.
    if (k && k.classList.contains('napoveda-znak')) {
      e.preventDefault();
      if (k === kotva) skryj(); else ukaz(k);
      return;
    }
    // Klik uvnitř právě otevřené nápovědy si obsloužil ten, kdo ji otevřel.
    if (kotva && kotva.contains(e.target)) return;
    skryj();
  });
  // Escape nad otevřenou nápovědou patří jí a nikomu dalšímu: pod nápovědou bývá
  // okno, které by se na tomtéž Escapu zavřelo zároveň s ní.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !kotva) return;
    e.stopImmediatePropagation();
    skryj();
  });
  // Bublina je přilepená na souřadnice, takže při rolování odjede od své kotvy.
  // Zachytávací fáze proto, že rolovat může i vnitřní panel, nejen okno.
  window.addEventListener('scroll', skryj, true);
  window.addEventListener('resize', skryj);

  window.Napoveda = { ukaz: ukaz, nastav: nastav, skryj: skryj, jeVidet: function (prvek) { return prvek ? kotva === prvek : !!kotva; } };
})();
