/* Editor stran zpěvníku. Vzhled je v css/editor.css, obálky, název a barvu dál
   obsluhuje okno v my_songbooks.html - tenhle modul se stará jen o seznam stran.

   Zpěvník je řada stran a u každé se určí, co na ní je:
     pisen        začíná na ní píseň (nebo víc písní)
     pokracovani  pokračuje na ni poslední píseň předchozí strany
     bez          obrázek bez písně (úvod, osmisměrka), volitelně s popiskem
     prazdna      strana bez obrázku
   Přesně tak to ukládá databáze (tabulky strany a pisne_na_strane).

   Nové obrázky se nahrávají hned po přidání, jeden po druhém, takže uložení pak
   posílá jen strukturu. Co se nahraje a neuloží, server po dni uklidí sám.

   Použití:
     EditorStran.zacni({ zpevnikId, verejny });   // před každým otevřením okna
     EditorStran.nacti(strany, prvniCislo);        // strany z /api/zpevnik/<id>
     await EditorStran.pripravKUlozeni();          // {ok, chyba} - počká na nahrávání
     EditorStran.data();  EditorStran.prvniCislo();  EditorStran.zmeneno(); */
(function () {
  'use strict';

  var TYPY = [
    ['pisen', 'Píseň'],
    ['pokracovani', 'Pokračování'],
    ['bez', 'Bez písně'],
    ['prazdna', 'Prázdná'],
  ];

  var strany = [];          // stav editoru, viz novaStrana()
  var vychozi = '';         // otisk po načtení, podle něj se poznají změny
  var nastaveni = {};
  var dalsiKlic = 1;
  var vlozitNa = null;      // index, kam přijdou další přidané strany (null = na konec)
  var fronta = [];          // klíče stran čekajících na nahrání
  var nahravaSe = false;
  var cekajici = [];        // resolve funkce těch, kdo čekají na dokončení nahrávání
  var tazeny = null;        // [od, do] přesouvaného bloku
  var autoriNacteni = false;

  function $(id) { return document.getElementById(id); }

  function novaStrana(z) {
    z = z || {};
    return {
      klic: dalsiKlic++,
      id: z.id == null ? null : z.id,
      typ: z.typ || 'pisen',
      image_id: z.image_id == null ? null : z.image_id,
      nahled: z.nahled || null,
      obrazek: z.obrazek || z.nahled || null,
      soubor: null,
      stav: 'hotovo',          // ceka | nahrava | hotovo | chyba
      chyba: '',
      popisek: z.popisek || '',
      pisne: (z.pisne && z.pisne.length ? z.pisne : (z.typ && z.typ !== 'pisen' ? [] : [{}]))
        .map(function (p) {
          return { song_id: p.song_id || null, nazev: p.nazev || '', autor: p.autor || '',
                   zamcena: !!p.zamcena };
        }),
    };
  }

  // ---------- data pro server ----------

  function data() {
    return strany.map(function (s) {
      var pisne = (s.typ === 'pisen' || s.typ === 'pokracovani')
        ? s.pisne.filter(function (p) { return s.typ === 'pisen' || p.nazev.trim() || p.song_id; })
          .map(function (p) {
            return { song_id: p.song_id, nazev: p.nazev.trim(), autor: p.autor.trim() };
          })
        : [];
      if (s.typ === 'pokracovani' && pisne.length && !pisne.some(function (p) { return p.nazev; })) {
        pisne = [];
      }
      return {
        id: s.id,
        typ: s.typ,
        image_id: s.typ === 'prazdna' ? null : s.image_id,
        popisek: s.typ === 'bez' ? s.popisek.trim() : '',
        pisne: pisne,
      };
    });
  }

  function prvniCislo() {
    var n = parseInt(($('es-prvni') || {}).value, 10);
    return isNaN(n) || n < 1 ? 1 : n;
  }

  function otisk() {
    return JSON.stringify({ s: data(), p: prvniCislo() });
  }

  function zmeneno() {
    return otisk() !== vychozi || strany.some(function (s) { return s.stav !== 'hotovo'; });
  }

  // Na koho navazuje pokračování na straně i: poslední píseň předchozí strany, a když je
  // předchozí sama pokračováním, tak ta, na kterou navazuje ona.
  function navazuje(i) {
    for (var j = i - 1; j >= 0; j--) {
      var s = strany[j];
      var vlastni = s.pisne.filter(function (p) { return p.nazev.trim(); });
      if ((s.typ === 'pisen' || s.typ === 'pokracovani') && vlastni.length) {
        return vlastni[vlastni.length - 1].nazev.trim();
      }
      if (s.typ !== 'pokracovani') return null;
    }
    return null;
  }

  function problemy() {
    var vysledek = {};
    strany.forEach(function (s, i) {
      var p = [];
      if (s.stav === 'chyba') p.push('Obrázek se nepodařilo nahrát.');
      if (s.typ !== 'prazdna' && !s.image_id && s.stav === 'hotovo') p.push('Strana nemá obrázek.');
      if (s.typ === 'pisen' && !s.pisne.some(function (x) { return x.nazev.trim(); })) {
        p.push('Doplň název písně.');
      } else if (s.typ === 'pisen' && s.pisne.some(function (x) { return !x.nazev.trim(); })) {
        p.push('Některá z písní nemá název.');
      }
      if (s.typ === 'pokracovani' && !navazuje(i)) p.push('Předchozí strana nemá píseň, na kterou by šlo navázat.');
      if (p.length) vysledek[s.klic] = p;
    });
    return vysledek;
  }

  // ---------- nahrávání ----------

  function zarad(s) {
    s.stav = 'ceka';
    s.chyba = '';
    fronta.push(s.klic);
    nahravej();
  }

  function najdi(klic) {
    for (var i = 0; i < strany.length; i++) if (strany[i].klic === klic) return strany[i];
    return null;
  }

  function nahravej() {
    if (nahravaSe) return;
    var klic = fronta.shift();
    if (klic === undefined) {
      var hotovo = cekajici; cekajici = [];
      hotovo.forEach(function (f) { f(); });
      return;
    }
    var s = najdi(klic);
    if (!s || s.stav !== 'ceka' || !s.soubor) { nahravej(); return; }
    nahravaSe = true;
    s.stav = 'nahrava';
    prekresliStav(s);
    var fd = new FormData();
    fd.append('soubor', s.soubor);
    if (nastaveni.zpevnikId) fd.append('zpevnik_id', nastaveni.zpevnikId);
    else if (nastaveni.verejny) fd.append('verejny', '1');
    fetch('/api/strany/nahrat', { method: 'POST', body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return [r, j]; });
      })
      .then(function (v) {
        var r = v[0], j = v[1];
        if (!r.ok || !j.ok) throw new Error(j.error || (r.status === 413 ? 'Soubor je příliš velký.' : 'Nahrávání selhalo.'));
        s.image_id = j.image_id;
        s.stav = 'hotovo';
        s.soubor = null;
      })
      .catch(function (e) {
        s.stav = 'chyba';
        s.chyba = e.message || 'Nahrávání selhalo.';
      })
      .then(function () {
        nahravaSe = false;
        prekresliStav(s);
        nahravej();
      });
  }

  function cekejNaNahrani() {
    if (!nahravaSe && !fronta.length) return Promise.resolve();
    return new Promise(function (resolve) { cekajici.push(resolve); });
  }

  // ---------- přidávání ----------

  function seradPodleJmena(soubory) {
    return Array.prototype.slice.call(soubory).sort(function (a, b) {
      return a.name.localeCompare(b.name, 'cs', { numeric: true, sensitivity: 'base' });
    });
  }

  function vlozStrany(nove) {
    var kam = vlozitNa == null ? strany.length : vlozitNa;
    Array.prototype.splice.apply(strany, [kam, 0].concat(nove));
    vlozitNa = null;
    vykresli();
    var prvni = $('es-seznam').querySelector('[data-klic="' + nove[0].klic + '"] .es-nazev');
    if (prvni) prvni.focus();
  }

  function pridejSoubory(soubory) {
    var obrazky = seradPodleJmena(soubory).filter(function (f) { return /^image\//.test(f.type); });
    var ostatni = soubory.length - obrazky.length;
    if (ostatni) {
      zprava(ostatni === 1 ? 'Jeden soubor není obrázek, ten se přeskočil.'
                           : ostatni + ' souborů nejsou obrázky, ty se přeskočily.');
    }
    if (!obrazky.length) return;
    var nove = obrazky.map(function (f) {
      var s = novaStrana({ typ: 'pisen' });
      s.soubor = f;
      s.nahled = s.obrazek = URL.createObjectURL(f);
      return s;
    });
    vlozStrany(nove);
    nove.forEach(zarad);
  }

  function pridejPrazdnou() {
    vlozStrany([novaStrana({ typ: 'prazdna' })]);
  }

  function zprava(text) {
    var el = $('es-zprava');
    if (!el) return;
    el.textContent = text || '';
    el.hidden = !text;
  }

  // ---------- přesouvání ----------

  // Píseň se přesouvá i se stranami, které na ni navazují.
  function blok(i) {
    var od = i, k = i;
    if (strany[i].typ !== 'pokracovani') {
      while (k + 1 < strany.length && strany[k + 1].typ === 'pokracovani') k++;
    }
    return [od, k];
  }

  function zacatekBlokuPred(i) {
    var j = i - 1;
    while (j > 0 && strany[j].typ === 'pokracovani') j--;
    return j;
  }

  function presun(od, k, cil) {
    // cil = index, před který se blok vloží (v původním poli)
    if (cil >= od && cil <= k + 1) return;
    var kus = strany.splice(od, k - od + 1);
    if (cil > k) cil -= kus.length;
    Array.prototype.splice.apply(strany, [cil, 0].concat(kus));
    vykresli();
  }

  function posunNahoru(i) {
    var b = blok(i);
    if (b[0] === 0) return;
    presun(b[0], b[1], zacatekBlokuPred(b[0]));
  }

  function posunDolu(i) {
    var b = blok(i);
    if (b[1] >= strany.length - 1) return;
    var dalsi = blok(b[1] + 1);
    presun(b[0], b[1], dalsi[1] + 1);
  }

  // ---------- vykreslení ----------

  function el(tag, trida, text) {
    var e = document.createElement(tag);
    if (trida) e.className = trida;
    if (text != null) e.textContent = text;
    return e;
  }

  function tlacitko(trida, text, napoveda) {
    var b = el('button', trida, text);
    b.type = 'button';
    if (napoveda) { b.setAttribute('data-napoveda', napoveda); b.setAttribute('aria-label', napoveda); }
    return b;
  }

  function vykresliStranu(s, i) {
    var li = el('li', 'es-strana es-typ-' + s.typ);
    li.dataset.klic = s.klic;

    var drz = el('span', 'es-drzadlo', '≡');
    drz.draggable = true;
    drz.setAttribute('data-napoveda', 'Přetáhnout');
    li.appendChild(drz);

    var nahled = el('div', 'es-nahled');
    if (s.typ === 'prazdna') {
      nahled.classList.add('es-nahled-prazdny');
    } else if (s.nahled) {
      var img = document.createElement('img');
      img.src = s.nahled;
      img.alt = '';
      img.loading = 'lazy';
      nahled.appendChild(img);
      nahled.setAttribute('data-napoveda', 'Zvětšit nebo vyměnit obrázek');
      nahled.tabIndex = 0;
      nahled.setAttribute('role', 'button');
    } else {
      nahled.appendChild(tlacitko('es-nahrat-obrazek', '+ obrázek'));
    }
    var stav = el('div', 'es-stav');
    nahled.appendChild(stav);
    li.appendChild(nahled);

    var telo = el('div', 'es-telo');
    var hlava = el('div', 'es-hlava-strany');
    hlava.appendChild(el('span', 'es-cislo', String(prvniCislo() + i)));
    var typy = el('div', 'es-typy');
    typy.setAttribute('role', 'group');
    typy.setAttribute('aria-label', 'Co je na straně');
    TYPY.forEach(function (t) {
      var b = tlacitko('es-typ' + (s.typ === t[0] ? ' vybrany' : ''), t[1]);
      b.dataset.typ = t[0];
      b.setAttribute('aria-pressed', s.typ === t[0] ? 'true' : 'false');
      typy.appendChild(b);
    });
    hlava.appendChild(typy);
    telo.appendChild(hlava);

    if (s.typ === 'pisen') {
      s.pisne.forEach(function (p, pi) {
        var radek = el('div', 'es-pisen' + (p.zamcena ? ' zamcena' : ''));
        radek.dataset.pisen = pi;
        var nazev = el('input', 'es-nazev');
        nazev.type = 'text'; nazev.placeholder = 'Název písně'; nazev.value = p.nazev;
        var autor = el('input', 'es-autor');
        autor.type = 'text'; autor.placeholder = 'Autor'; autor.value = p.autor;
        autor.setAttribute('list', 'es-autori');
        if (p.zamcena) {
          nazev.readOnly = autor.readOnly = true;
          radek.setAttribute('data-napoveda', 'Píseň je i v jiném zpěvníku, název a autor se tady měnit nedají.');
        }
        radek.appendChild(nazev);
        radek.appendChild(autor);
        if (s.pisne.length > 1) radek.appendChild(tlacitko('es-odebrat-pisen', '✕', 'Odebrat píseň ze strany'));
        telo.appendChild(radek);
      });
      telo.appendChild(tlacitko('es-dalsi-pisen', '+ další píseň na této straně'));
    } else if (s.typ === 'pokracovani') {
      telo.appendChild(el('div', 'es-navazuje'));
    } else if (s.typ === 'bez') {
      var pop = el('input', 'es-popisek');
      pop.type = 'text'; pop.placeholder = 'Popisek (nepovinný), třeba Osmisměrka'; pop.value = s.popisek;
      telo.appendChild(pop);
    }
    var chyby = el('div', 'es-chyby');
    chyby.hidden = true;
    telo.appendChild(chyby);
    li.appendChild(telo);

    var akce = el('div', 'es-akce');
    akce.appendChild(tlacitko('es-nahoru', '↑', 'Posunout výš'));
    akce.appendChild(tlacitko('es-dolu', '↓', 'Posunout níž'));
    akce.appendChild(tlacitko('es-odebrat', '✕', 'Odebrat stranu'));
    li.appendChild(akce);
    return li;
  }

  function vykresli() {
    var seznam = $('es-seznam');
    if (!seznam) return;
    if (window.Napoveda) Napoveda.skryj();
    seznam.textContent = '';
    strany.forEach(function (s, i) {
      if (i > 0) {
        var mezera = el('li', 'es-mezera');
        var b = tlacitko('es-vlozit', '+', 'Vložit sem');
        b.dataset.pred = i;
        mezera.appendChild(b);
        seznam.appendChild(mezera);
      }
      seznam.appendChild(vykresliStranu(s, i));
    });
    var prazdno = $('es-prazdno');
    if (prazdno) prazdno.hidden = strany.length > 0;
    strany.forEach(prekresliStav);
    obnovPopisy();
  }

  function prvekStrany(s) {
    return $('es-seznam').querySelector('.es-strana[data-klic="' + s.klic + '"]');
  }

  function prekresliStav(s) {
    var li = prvekStrany(s);
    if (!li) return;
    var stav = li.querySelector('.es-stav');
    li.classList.toggle('nahrava', s.stav === 'ceka' || s.stav === 'nahrava');
    li.classList.toggle('chyba-nahrani', s.stav === 'chyba');
    if (!stav) return;
    stav.textContent = '';
    if (s.stav === 'ceka') stav.textContent = 'čeká';
    else if (s.stav === 'nahrava') stav.textContent = 'nahrává se…';
    else if (s.stav === 'chyba') {
      stav.appendChild(el('span', null, s.chyba));
      stav.appendChild(tlacitko('es-znovu', 'Zkusit znovu'));
    }
    hlasStav();
  }

  function hlasStav() {
    var el2 = $('es-stav-nahravani');
    if (!el2) return;
    var zbyva = strany.filter(function (s) { return s.stav === 'ceka' || s.stav === 'nahrava'; }).length;
    var chyb = strany.filter(function (s) { return s.stav === 'chyba'; }).length;
    el2.textContent = zbyva ? 'Nahrávají se strany, zbývá ' + zbyva + '.'
                    : (chyb ? 'Nepodařilo se nahrát ' + chyb + ' ' + (chyb === 1 ? 'stranu' : 'stran') + '.' : '');
    el2.hidden = !el2.textContent;
  }

  // Čísla stran a „pokračuje …“ se mění s každým psaním a přesunem, celý seznam se
  // kvůli tomu nepřekresluje (ztratil by se kurzor v poli).
  function obnovPopisy() {
    var seznam = $('es-seznam');
    if (!seznam) return;
    var p = prvniCislo();
    strany.forEach(function (s, i) {
      var li = prvekStrany(s);
      if (!li) return;
      li.querySelector('.es-cislo').textContent = String(p + i);
      var n = li.querySelector('.es-navazuje');
      if (n) {
        var na = navazuje(i);
        n.textContent = na ? 'Pokračuje „' + na + '“' : 'Pokračování nemá na co navázat';
        n.classList.toggle('nenavazuje', !na);
      }
    });
  }

  function ukazProblemy(ukazat) {
    var vse = ukazat ? problemy() : {};
    strany.forEach(function (s) {
      var li = prvekStrany(s);
      if (!li) return;
      var box = li.querySelector('.es-chyby');
      var p = vse[s.klic];
      li.classList.toggle('ma-chybu', !!p);
      box.textContent = p ? p.join(' ') : '';
      box.hidden = !p;
    });
    return vse;
  }

  // ---------- zvětšení obrázku ----------

  function otevriObrazek(s) {
    var okno = $('es-lupa');
    if (!okno) return;
    okno.querySelector('img').src = s.obrazek || s.nahled;
    okno.dataset.klic = s.klic;
    okno.hidden = false;
  }

  function zavriObrazek() {
    var okno = $('es-lupa');
    if (okno) { okno.hidden = true; okno.querySelector('img').removeAttribute('src'); }
  }

  function vymenObrazek(s) {
    var vyber = document.createElement('input');
    vyber.type = 'file';
    vyber.accept = 'image/*';
    vyber.addEventListener('change', function () {
      var f = vyber.files && vyber.files[0];
      if (!f) return;
      s.soubor = f;
      s.image_id = null;
      s.nahled = s.obrazek = URL.createObjectURL(f);
      if (s.typ === 'prazdna') s.typ = 'pisen';
      zavriObrazek();
      vykresli();
      zarad(s);
    });
    vyber.click();
  }

  // ---------- události ----------

  function indexZ(prvek) {
    var li = prvek.closest('.es-strana');
    if (!li) return -1;
    var klic = Number(li.dataset.klic);
    for (var i = 0; i < strany.length; i++) if (strany[i].klic === klic) return i;
    return -1;
  }

  function zmenTyp(s, typ) {
    if (s.typ === typ) return;
    s.typ = typ;
    if (typ === 'pisen' && !s.pisne.length) s.pisne.push({ song_id: null, nazev: '', autor: '', zamcena: false });
    vykresli();
  }

  function navaz() {
    var sekce = $('editor-stran');
    if (!sekce || sekce.dataset.navazano) return;
    sekce.dataset.navazano = '1';
    var seznam = $('es-seznam');
    var vyber = $('es-soubory');

    $('es-pridat').addEventListener('click', function () { vlozitNa = null; vyber.click(); });
    $('es-pridat-prazdnou').addEventListener('click', function () { vlozitNa = null; pridejPrazdnou(); });
    vyber.addEventListener('change', function () {
      if (vyber.files && vyber.files.length) pridejSoubory(vyber.files);
      vyber.value = '';
    });
    $('es-prvni').addEventListener('input', obnovPopisy);

    var nabidka = $('es-nabidka-vlozit');
    function zavriNabidku() { if (nabidka) nabidka.hidden = true; }
    nabidka.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      zavriNabidku();
      if (b.dataset.akce === 'obrazky') vyber.click();
      else if (b.dataset.akce === 'prazdna') pridejPrazdnou();
    });
    document.addEventListener('click', function (e) {
      if (!nabidka.hidden && !nabidka.contains(e.target) && !e.target.closest('.es-vlozit')) zavriNabidku();
    });

    seznam.addEventListener('click', function (e) {
      var cil = e.target;
      var vlozit = cil.closest('.es-vlozit');
      if (vlozit) {
        vlozitNa = Number(vlozit.dataset.pred);
        var r = vlozit.getBoundingClientRect(), rs = sekce.getBoundingClientRect();
        nabidka.style.top = (r.bottom - rs.top + 4) + 'px';
        nabidka.style.left = Math.max(0, r.left - rs.left - 60) + 'px';
        nabidka.hidden = false;
        return;
      }
      var i = indexZ(cil);
      if (i < 0) return;
      var s = strany[i];
      var b = cil.closest('button');
      if (b && b.classList.contains('es-typ')) zmenTyp(s, b.dataset.typ);
      else if (b && b.classList.contains('es-odebrat')) { strany.splice(i, 1); vykresli(); }
      else if (b && b.classList.contains('es-nahoru')) posunNahoru(i);
      else if (b && b.classList.contains('es-dolu')) posunDolu(i);
      else if (b && b.classList.contains('es-dalsi-pisen')) {
        s.pisne.push({ song_id: null, nazev: '', autor: '', zamcena: false });
        vykresli();
        var pole = prvekStrany(s).querySelectorAll('.es-nazev');
        if (pole.length) pole[pole.length - 1].focus();
      } else if (b && b.classList.contains('es-odebrat-pisen')) {
        s.pisne.splice(Number(b.closest('.es-pisen').dataset.pisen), 1);
        vykresli();
      } else if (b && b.classList.contains('es-znovu')) {
        zarad(s);
      } else if (b && b.classList.contains('es-nahrat-obrazek')) {
        vymenObrazek(s);
      } else if (cil.closest('.es-nahled') && s.nahled && s.typ !== 'prazdna') {
        otevriObrazek(s);
      }
    });

    seznam.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.closest('.es-nahled')) { e.preventDefault(); e.target.click(); return; }
      if (e.key !== 'Enter' || e.target.tagName !== 'INPUT') return;
      // Enter skočí na další stranu - při vyplňování stovky stran po importu se
      // nemusí sahat na myš.
      e.preventDefault();
      var i = indexZ(e.target);
      for (var j = i + 1; j < strany.length; j++) {
        var pole = prvekStrany(strany[j]).querySelector('.es-nazev:not([readonly]), .es-popisek');
        if (pole) { pole.focus(); pole.select(); return; }
      }
    });

    seznam.addEventListener('input', function (e) {
      var i = indexZ(e.target);
      if (i < 0) return;
      var s = strany[i];
      var radek = e.target.closest('.es-pisen');
      if (radek) {
        var p = s.pisne[Number(radek.dataset.pisen)];
        if (e.target.classList.contains('es-nazev')) p.nazev = e.target.value;
        if (e.target.classList.contains('es-autor')) p.autor = e.target.value;
      } else if (e.target.classList.contains('es-popisek')) {
        s.popisek = e.target.value;
      }
      obnovPopisy();
      if (prvekStrany(s).classList.contains('ma-chybu')) ukazProblemy(true);
    });

    // Tažení za držadlo. Na dotykové obrazovce HTML5 tažení nefunguje, tam jsou šipky.
    seznam.addEventListener('dragstart', function (e) {
      if (!e.target.classList || !e.target.classList.contains('es-drzadlo')) return;
      var i = indexZ(e.target);
      tazeny = blok(i);
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', 'strana'); } catch (_) {}
      for (var k = tazeny[0]; k <= tazeny[1]; k++) prvekStrany(strany[k]).classList.add('tazena');
    });
    seznam.addEventListener('dragover', function (e) {
      if (!tazeny) return;
      var li = e.target.closest('.es-strana');
      if (!li) return;
      e.preventDefault();
      seznam.querySelectorAll('.cil-pred, .cil-za').forEach(function (x) { x.classList.remove('cil-pred', 'cil-za'); });
      var r = li.getBoundingClientRect();
      li.classList.add(e.clientY < r.top + r.height / 2 ? 'cil-pred' : 'cil-za');
    });
    seznam.addEventListener('drop', function (e) {
      if (!tazeny) return;
      e.preventDefault();
      var li = e.target.closest('.es-strana');
      var b = tazeny;
      tazeny = null;
      if (!li) { vykresli(); return; }
      var i = indexZ(li);
      var cil = li.classList.contains('cil-za') ? blok(i)[1] + 1 : i;
      presun(b[0], b[1], cil);
    });
    seznam.addEventListener('dragend', function () { if (tazeny) { tazeny = null; vykresli(); } });

    // Soubory přetažené kamkoli do sekce stran
    sekce.addEventListener('dragover', function (e) {
      if (tazeny || !e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') < 0) return;
      e.preventDefault();
      sekce.classList.add('es-nad');
    });
    sekce.addEventListener('dragleave', function (e) {
      if (!sekce.contains(e.relatedTarget)) sekce.classList.remove('es-nad');
    });
    sekce.addEventListener('drop', function (e) {
      if (tazeny || !e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      sekce.classList.remove('es-nad');
      vlozitNa = null;
      pridejSoubory(e.dataTransfer.files);
    });

    var lupa = $('es-lupa');
    lupa.addEventListener('click', function (e) {
      var s = najdi(Number(lupa.dataset.klic));
      if (e.target.closest('.es-vymenit') && s) vymenObrazek(s);
      else if (e.target === lupa || e.target.closest('.es-zavrit')) zavriObrazek();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !lupa.hidden) { e.stopPropagation(); zavriObrazek(); }
    }, true);
  }

  function nactiAutory() {
    if (autoriNacteni) return;
    autoriNacteni = true;
    fetch('/api/autori').then(function (r) { return r.json(); }).then(function (j) {
      var dl = $('es-autori');
      if (!dl || !j || !j.autori) return;
      j.autori.forEach(function (jmeno) {
        var o = document.createElement('option');
        o.value = jmeno;
        dl.appendChild(o);
      });
    }).catch(function () { autoriNacteni = false; });
  }

  // ---------- veřejné rozhraní ----------

  window.EditorStran = {
    zacni: function (volby) {
      nastaveni = volby || {};
      navaz();
      nactiAutory();
      strany = [];
      fronta = [];
      vlozitNa = null;
      zprava('');
      if ($('es-prvni')) $('es-prvni').value = 1;
      vykresli();
      hlasStav();
      vychozi = otisk();
    },
    nacti: function (data, prvni) {
      strany = (data || []).map(novaStrana);
      if ($('es-prvni')) $('es-prvni').value = prvni || 1;
      vykresli();
      vychozi = otisk();
    },
    data: data,
    prvniCislo: prvniCislo,
    zmeneno: zmeneno,
    // Počká na rozpracované nahrávání a zkontroluje strany. Při problému je ukáže
    // u stran a na první z nich posune.
    pripravKUlozeni: function () {
      return cekejNaNahrani().then(function () {
        var p = ukazProblemy(true);
        var klice = Object.keys(p);
        if (!klice.length) return { ok: true };
        var li = $('es-seznam').querySelector('.es-strana[data-klic="' + klice[0] + '"]');
        if (li) li.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return { ok: false, chyba: klice.length === 1 ? 'Jedna strana potřebuje doplnit.'
                                                       : klice.length + ' stran potřebuje doplnit.' };
      });
    },
    nahrava: function () { return nahravaSe || fronta.length > 0; },
  };
})();
