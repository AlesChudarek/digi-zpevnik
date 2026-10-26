/* Přetahování za držadlo — společné pro dlaždice v Mých zpěvnících a strany v editoru.

   Myší i prstem (Pointer Events; HTML5 tažení telefon neumí). Tažená položka drží pod
   prstem místo, za které ji chytil, ostatní jí plynule uhýbají (FLIP) a u okraje
   rolovací plochy se roluje samo — čím blíž okraji, tím rychleji, a s délkou držení
   ještě zrychluje.

   Tazeni.navaz({
     kontejner,            prvek, ve kterém se chytá (pointerdown)
     drzadlo, polozka,     selektory držadla a položky, ke které patří
     polozky(),            řazené položky v aktuálním pořadí DOM
     vloz(polozka, pred),  přesune položku v DOM před `pred` (null = na konec)
     animovat(polozka),    co má uhýbat (výchozí: ostatní položky)
     mrizka,               true = 2D mřížka (cíl je položka pod prstem),
                           jinak svislý seznam (cíl podle středů položek)
     rolovatko(),          co roluje u okraje (výchozí: okno)
     zacatek(polozka),
     konec(polozka, zmeneno) → prvek, který má doplout na své místo (výchozí položka)
   })

   Proč se cíl počítá z rozložení bez rozjetých animací: dokud se bral z toho, kde
   položky zrovna byly vidět, uhýbající položka se pod prstem přehazovala tam a zpět
   a tažená v mřížce poskakovala. */
(function () {
  'use strict';

  var ZONA = 70;          // px od okraje, kde se začne rolovat
  var DOBA = 300;         // ms, jak dlouho ostatní uhýbají
  var KRIVKA = 'cubic-bezier(.25,.8,.25,1)';

  // Kde prvek v rozložení stojí, bez právě běžící animace posunu
  function rozlozeni(el) {
    var r = el.getBoundingClientRect();
    var t = getComputedStyle(el).transform;
    if (!t || t === 'none') return r;
    var m = new DOMMatrixReadOnly(t);
    return { left: r.left - m.e, right: r.right - m.e, top: r.top - m.f,
             bottom: r.bottom - m.f, width: r.width, height: r.height };
  }

  /* Změní DOM a každý z `prvky` nechá doplout z místa, kde byl vidět, na nové.
     Rozjetá animace se přeruší až po změření, takže pohyb plynule navazuje. */
  function flip(prvky, zmen) {
    var pred = prvky.map(function (p) { return p.getBoundingClientRect(); });
    zmen();
    prvky.forEach(function (p, i) {
      p.getAnimations().forEach(function (a) { a.cancel(); });
      var n = p.getBoundingClientRect();
      var dx = pred[i].left - n.left, dy = pred[i].top - n.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
      p.animate([{ transform: 'translate(' + dx + 'px, ' + dy + 'px)' }, { transform: 'none' }],
                { duration: DOBA, easing: KRIVKA });
    });
  }

  function navaz(o) {
    var tah = null;
    var rolovatko = o.rolovatko || function () { return document.scrollingElement; };
    var animovat = o.animovat || function (p) { return o.polozky().filter(function (x) { return x !== p; }); };

    function okraje(r) {
      return r === document.scrollingElement ? { top: 0, bottom: window.innerHeight }
                                             : r.getBoundingClientRect();
    }

    function drzPodPrstem() {
      var p = tah.polozka;
      p.style.transform = '';
      var r = p.getBoundingClientRect();
      var dx = o.mrizka ? tah.x - tah.chytX - r.left : 0;
      p.style.transform = 'translate(' + dx + 'px, ' + (tah.y - tah.chytY - r.top) + 'px)';
    }

    // Před kterou položkou má tažená stát; undefined = nechat, kde je
    function najdiPred() {
      var ostatni = o.polozky().filter(function (x) { return x !== tah.polozka; });
      var i;
      if (o.mrizka) {
        for (i = 0; i < ostatni.length; i++) {
          var r = rozlozeni(ostatni[i]);
          if (tah.x < r.left || tah.x > r.right || tah.y < r.top || tah.y > r.bottom) continue;
          return tah.x > r.left + r.width / 2 ? (ostatni[i + 1] || null) : ostatni[i];
        }
        return undefined;
      }
      for (i = 0; i < ostatni.length; i++) {
        var s = rozlozeni(ostatni[i]);
        if (tah.y < s.top + s.height / 2) return ostatni[i];
      }
      return null;
    }

    function prerad() {
      var pred = najdiPred();
      if (pred === undefined) return;
      var vse = o.polozky();
      var ted = vse[vse.indexOf(tah.polozka) + 1] || null;
      if (pred === ted) return;
      var p = tah.polozka;
      flip(animovat(p), function () { o.vloz(p, pred); });
    }

    function snimek(cas) {
      if (!tah) return;
      var r = okraje(tah.rol);
      var hloubka = Math.max(r.top + ZONA - tah.y, tah.y - (r.bottom - ZONA), 0);
      if (hloubka) {
        if (!tah.odKdy) tah.odKdy = cas;
        var krok = Math.min(1, hloubka / ZONA) * 14 * Math.min(4, 1 + (cas - tah.odKdy) / 700);
        tah.rol.scrollTop += tah.y < r.top + ZONA ? -krok : krok;
      } else {
        tah.odKdy = 0;
      }
      prerad();
      drzPodPrstem();
      tah.snimek = requestAnimationFrame(snimek);
    }

    function pohyb(e) {
      if (!tah || e.pointerId !== tah.id) return;
      tah.x = e.clientX;
      tah.y = e.clientY;
      prerad();
      drzPodPrstem();
    }

    function konec(e) {
      if (!tah || (e && e.pointerId !== tah.id)) return;
      var t = tah;
      tah = null;
      cancelAnimationFrame(t.snimek);
      window.removeEventListener('pointermove', pohyb);
      window.removeEventListener('pointerup', konec);
      window.removeEventListener('pointercancel', konec);
      document.body.classList.remove('tahne-se');
      var bylo = t.polozka.getBoundingClientRect();
      t.polozka.style.transform = '';
      t.polozka.classList.remove('tazena');
      var zmeneno = o.polozky().some(function (x, i) { return x !== t.puvodni[i]; });
      // Doplout z místa, kde ji prst pustil (editor po konec() kreslí seznam znovu,
      // proto může vrátit jiný prvek)
      var vysledek = o.konec && o.konec(t.polozka, zmeneno);
      var cil = vysledek instanceof Element ? vysledek : t.polozka;
      var n = cil.getBoundingClientRect();
      cil.animate([{ transform: 'translate(' + (bylo.left - n.left) + 'px, ' + (bylo.top - n.top) + 'px)' },
                   { transform: 'none' }], { duration: 220, easing: KRIVKA });
    }

    o.kontejner.addEventListener('pointerdown', function (e) {
      var drz = e.target.closest(o.drzadlo);
      if (!drz || e.button !== 0 || tah) return;
      e.preventDefault();
      var p = drz.closest(o.polozka);
      var r = p.getBoundingClientRect();
      var vse = o.polozky();
      // Pohyb a puštění na okně: položka se v DOM přesouvá a prohlížeč jí tím
      // zruší zachycení ukazatele - tah by skončil v půli.
      tah = { polozka: p, id: e.pointerId, x: e.clientX, y: e.clientY,
              chytX: e.clientX - r.left, chytY: e.clientY - r.top,
              rol: rolovatko(p), odKdy: 0, puvodni: vse };
      if (window.Napoveda) Napoveda.skryj();
      if (o.zacatek) o.zacatek(p);
      p.classList.add('tazena');
      document.body.classList.add('tahne-se');
      window.addEventListener('pointermove', pohyb);
      window.addEventListener('pointerup', konec);
      window.addEventListener('pointercancel', konec);
      tah.snimek = requestAnimationFrame(snimek);
    });
  }

  // Nejbližší předek, který opravdu roluje (okno editoru), jinak okno
  function rolujiciPredek(prvek) {
    for (var p = prvek.parentElement; p; p = p.parentElement) {
      var oy = getComputedStyle(p).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) return p;
    }
    return document.scrollingElement;
  }

  window.Tazeni = { navaz: navaz, flip: flip, rolujiciPredek: rolujiciPredek };
})();
