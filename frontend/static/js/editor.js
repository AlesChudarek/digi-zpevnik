/* Editor stran zpěvníku. Vzhled je v css/editor.css, obálky, název a barvu dál
   obsluhuje okno v my_songbooks.html - tenhle modul se stará jen o seznam stran.

   Strana nemá typ, který by se volil. Má obrázek (nebo ne), písně, které na ní
   začínají (žádnou, jednu i víc), popisek, když písně nemá, a přepínač „píseň
   pokračuje na další stranu“. Co na straně je, z toho plyne samo:
     bez obrázku a bez písně   prázdná strana
     obrázek bez písně         úvod, osmisměrka… (popisek je nepovinný)
     s písní                   písňová strana
   Pokračování je spojení dvou stran: zapne se na straně, ze které poslední píseň
   pokračuje, a další strana se k ní natvrdo připojí. Spojené strany tvoří blok, který
   se přesouvá jako celek. I na připojené straně může začínat další píseň.
   Serveru se posílá typ odvozený z tohohle (pisen, pokracovani, bez, prazdna), přesně
   jak ho ukládá databáze.

   Nové obrázky se nahrávají hned po přidání, jeden po druhém, takže uložení pak posílá
   jen strukturu. Co se nahraje a neuloží, server po dni uklidí sám.

   PDF a ZIP se rozloží v prohlížeči (pdf.js, JSZip - načtou se, až když jsou potřeba)
   a každá jejich strana se přidá jako obrázek.

   Použití:
     EditorStran.zacni({ zpevnikId, verejny, naObalku });   // před každým otevřením okna
       naObalku(role, soubor) vloží stranu z PDF do slotu obálky (front_outer…)
     EditorStran.nacti(strany, prvniCislo);        // strany z /api/zpevnik/<id>
     await EditorStran.pripravKUlozeni();          // {ok, chyba} - počká na nahrávání
     EditorStran.data();  EditorStran.prvniCislo();  EditorStran.zmeneno(); */
(function () {
  'use strict';

  var strany = [];          // stav editoru, viz novaStrana()
  var vychozi = '';         // otisk po načtení, podle něj se poznají změny
  var nastaveni = {};
  var dalsiKlic = 1;
  var fronta = [];          // klíče stran čekajících na nahrání
  var nahravaSe = false;
  var cekajici = [];        // resolve funkce těch, kdo čekají na dokončení nahrávání
  var tah = null;           // rozjeté tažení bloku za držadlo, viz navaz()
  var autoriNacteni = false;
  var ukazovatChyby = false;

  function $(id) { return document.getElementById(id); }

  function prazdnaPisen() { return { song_id: null, nazev: '', autor: '', zamcena: false }; }

  function novaStrana(z) {
    z = z || {};
    return {
      klic: dalsiKlic++,
      id: z.id == null ? null : z.id,
      image_id: z.image_id == null ? null : z.image_id,
      nahled: z.nahled || null,
      obrazek: z.obrazek || z.nahled || null,
      soubor: null,
      verze: 0,                // roste s každou změnou obrázku; viz nahravej()
      stav: 'hotovo',          // ceka | nahrava | hotovo | chyba
      chyba: '',
      popisek: z.popisek || '',
      pokracuje: !!z.pokracuje,
      pisne: (z.pisne || []).map(function (p) {
        return { song_id: p.song_id || null, nazev: p.nazev || '', autor: p.autor || '',
                 zamcena: !!p.zamcena };
      }),
    };
  }

  // ---------- odvozené vlastnosti ----------

  function jePokracovani(i) { return i > 0 && strany[i - 1].pokracuje; }

  // Jméno písně, která z téhle strany pokračuje dál: poslední, která na ní začíná,
  // nebo ta, která na ni sama pokračuje.
  function posledniPisen(i) {
    var s = strany[i];
    if (s.pisne.length) return s.pisne[s.pisne.length - 1].nazev.trim() || 'píseň bez názvu';
    return jePokracovani(i) ? posledniPisen(i - 1) : null;
  }

  function typ(i) {
    var s = strany[i];
    if (jePokracovani(i)) return 'pokracovani';
    if (s.pisne.length) return 'pisen';
    return (s.image_id || s.soubor) ? 'bez' : 'prazdna';
  }

  // Blok = strana, která nepokračuje z předchozí, a všechny k ní připojené.
  function blok(i) {
    var od = i, k = i;
    while (od > 0 && jePokracovani(od)) od--;
    while (k + 1 < strany.length && jePokracovani(k + 1)) k++;
    return [od, k];
  }

  // ---------- data pro server ----------

  function data() {
    return strany.map(function (s, i) {
      var t = typ(i);
      return {
        id: s.id,
        typ: t,
        image_id: t === 'prazdna' ? null : s.image_id,
        popisek: s.pisne.length ? '' : s.popisek.trim(),
        pisne: s.pisne.map(function (p) {
          return { song_id: p.song_id, nazev: p.nazev.trim(), autor: p.autor.trim() };
        }),
      };
    });
  }

  function prvniCislo() {
    var n = parseInt(($('es-prvni') || {}).value, 10);
    return isNaN(n) || n < 1 ? 1 : n;
  }

  function otisk() {
    return JSON.stringify({ s: data(), p: prvniCislo(), k: strany.length && strany[strany.length - 1].pokracuje });
  }

  function zmeneno() {
    return otisk() !== vychozi || strany.some(function (s) { return s.stav !== 'hotovo'; });
  }

  function problemy() {
    var vysledek = {};
    strany.forEach(function (s, i) {
      var p = [];
      if (s.stav === 'chyba') p.push('Obrázek se nepodařilo nahrát.');
      var potrebujeObrazek = s.pisne.length || jePokracovani(i);
      if (potrebujeObrazek && !s.image_id && s.stav === 'hotovo') {
        p.push('Strana s písní potřebuje obrázek.');
      }
      if (s.pisne.some(function (x) { return !x.nazev.trim(); })) {
        p.push('Chybí název písně.');
      }
      if (s.pokracuje && i === strany.length - 1) {
        p.push('Píseň má pokračovat na další stranu, ale žádná další není.');
      }
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
    // Obrázek se mezitím může vyměnit nebo odebrat; výsledek pak už patří starému.
    var verze = s.verze;
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
        if (verze !== s.verze) return;
        s.image_id = j.image_id;
        s.stav = 'hotovo';
        s.soubor = null;
      })
      .catch(function (e) {
        if (verze !== s.verze) return;
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

  function jePdf(f) { return f.type === 'application/pdf' || /\.pdf$/i.test(f.name); }
  function jeZip(f) { return /zip/.test(f.type) || /\.zip$/i.test(f.name); }
  function jeObrazek(f) { return /^image\//.test(f.type) || /\.(png|jpe?g|webp)$/i.test(f.name); }

  function nastavObrazek(s, f) {
    s.verze++;
    s.soubor = f;
    s.image_id = null;
    s.nahled = s.obrazek = URL.createObjectURL(f);
  }

  // Strana z nahraného obrázku dostane rovnou prázdné pole pro název písně: při
  // vyplňování importu se pak jen píše a Enterem skáče dál.
  function stranaZeSouboru(f) {
    var s = novaStrana({ pisne: [{}] });
    nastavObrazek(s, f);
    return s;
  }

  function vlozNa(kam, nove) {
    kam = kam == null ? strany.length : Math.min(kam, strany.length);
    // Doprostřed bloku se nevkládá - strana by se omylem stala pokračováním.
    if (kam > 0 && kam < strany.length && jePokracovani(kam)) kam = blok(kam)[1] + 1;
    Array.prototype.splice.apply(strany, [kam, 0].concat(nove));
    return kam;
  }

  function zamer(s) {
    var div = prvekStrany(s);
    var pole = div && div.querySelector('.es-nazev:not([readonly]), .es-popisek');
    if (pole) pole.focus();
  }

  function pridejSoubory(soubory, kam) {
    soubory = seradPodleJmena(soubory);
    var obrazky = soubory.filter(jeObrazek);
    var dokumenty = soubory.filter(function (f) { return !jeObrazek(f) && (jePdf(f) || jeZip(f)); });
    var ostatni = soubory.length - obrazky.length - dokumenty.length;
    zprava(ostatni ? (ostatni === 1 ? 'Jeden soubor není obrázek, PDF ani ZIP, ten se přeskočil.'
                                    : ostatni + ' souborů nejsou obrázky, PDF ani ZIP, ty se přeskočily.') : '');
    if (obrazky.length) {
      var nove = obrazky.map(stranaZeSouboru);
      kam = vlozNa(kam, nove) + nove.length;
      vykresli();
      zamer(nove[0]);
      nove.forEach(zarad);
    }
    dokumenty.forEach(function (f) { importy.push({ soubor: f, kam: kam }); });
    dalsiImport();
  }

  function pridejPrazdnou(kam) {
    var s = novaStrana();
    vlozNa(kam, [s]);
    vykresli();
    zamer(s);
  }

  function zprava(text) {
    var e = $('es-zprava');
    if (!e) return;
    e.textContent = text || '';
    e.hidden = !text;
  }

  // ---------- import PDF a ZIP ----------

  // Šířka, na kterou se strana PDF vykreslí - stejná jako u dnešních skenů (A4 při
  // ~210 dpi), takže se import neliší od nahraných obrázků ani velikostí, ani ostrostí.
  var SIRKA_STRANY = 1748;
  var importy = [];          // dokumenty čekající na dialog
  var importBezi = false;
  var otevreni = 0;          // roste s každým otevřením okna; import z minulého okna se pozná
  var skripty = {};

  function nactiSkript(url) {
    if (!skripty[url]) {
      skripty[url] = new Promise(function (resolve, reject) {
        var sc = document.createElement('script');
        sc.src = url;
        sc.onload = resolve;
        sc.onerror = function () { delete skripty[url]; reject(new Error('Nepodařilo se načíst knihovnu pro čtení souboru.')); };
        document.head.appendChild(sc);
      });
    }
    return skripty[url];
  }

  function otevriPdf(f) {
    var sekce = $('editor-stran');
    return nactiSkript(sekce.dataset.pdfjs).then(function () {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = sekce.dataset.pdfjsWorker;
      return f.arrayBuffer();
    }).then(function (obsah) {
      return window.pdfjsLib.getDocument({ data: obsah }).promise;
    }).then(function (pdf) {
      var zaklad = f.name.replace(/\.pdf$/i, '');
      return {
        pocet: pdf.numPages,
        strana: function (n) {
          return pdf.getPage(n).then(function (stranka) {
            var meritko = SIRKA_STRANY / stranka.getViewport({ scale: 1 }).width;
            var vp = stranka.getViewport({ scale: meritko });
            var platno = document.createElement('canvas');
            platno.width = Math.round(vp.width);
            platno.height = Math.round(vp.height);
            var ctx = platno.getContext('2d');
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, platno.width, platno.height);
            return stranka.render({ canvasContext: ctx, viewport: vp }).promise.then(function () {
              return new Promise(function (resolve) { platno.toBlob(resolve, 'image/jpeg', 0.9); });
            }).then(function (blob) {
              stranka.cleanup();
              platno.width = platno.height = 0;
              return new File([blob], zaklad + '-' + n + '.jpg', { type: 'image/jpeg' });
            });
          });
        },
        zavri: function () { pdf.destroy(); },
      };
    });
  }

  function otevriZip(f) {
    var sekce = $('editor-stran');
    return nactiSkript(sekce.dataset.jszip).then(function () {
      return window.JSZip.loadAsync(f);
    }).then(function (zip) {
      var polozky = [];
      zip.forEach(function (cesta, z) {
        // Složky, systémový balast z Macu (__MACOSX, ._soubory) a cokoli, co není obrázek
        if (z.dir || /(^|\/)(__MACOSX|\.)/.test(cesta) || !/\.(png|jpe?g|webp)$/i.test(cesta)) return;
        polozky.push(z);
      });
      polozky.sort(function (a, b) { return a.name.localeCompare(b.name, 'cs', { numeric: true, sensitivity: 'base' }); });
      return {
        pocet: polozky.length,
        strana: function (n) {
          var z = polozky[n - 1];
          var pripona = z.name.split('.').pop().toLowerCase();
          var typSouboru = pripona === 'png' ? 'image/png' : (pripona === 'webp' ? 'image/webp' : 'image/jpeg');
          return z.async('blob').then(function (blob) {
            return new File([blob], z.name.split('/').pop(), { type: typSouboru });
          });
        },
        zavri: function () {},
      };
    });
  }

  // „3-10, 12“ -> [3..10, 12]; čísla mimo 1..pocet jsou chyba, ne tiché přeskočení.
  function rozsah(zapis, pocet) {
    var vysledek = [];
    var kusy = String(zapis).replace(/–/g, '-').split(/[,;]/);
    for (var i = 0; i < kusy.length; i++) {
      var kus = kusy[i].trim();
      if (!kus) continue;
      var m = kus.match(/^(\d+)\s*(?:-\s*(\d*))?$/);
      if (!m) throw new Error('„' + kus + '“ není číslo strany ani rozsah.');
      var od = parseInt(m[1], 10);
      var po = m[2] === undefined ? od : (m[2] === '' ? pocet : parseInt(m[2], 10));
      if (od < 1 || po > pocet || po < od) throw new Error('Rozsah „' + kus + '“ nesedí, soubor má ' + pocet + ' stran.');
      for (var n = od; n <= po; n++) if (vysledek.indexOf(n) < 0) vysledek.push(n);
    }
    if (!vysledek.length) throw new Error('Chybí rozsah stran.');
    return vysledek;
  }

  function zeptejSeNaImport(nazev, pocet) {
    return new Promise(function (resolve) {
      var okno = $('es-import');
      $('es-import-nadpis').textContent = '„' + nazev + '“ má ' + pocet + ' ' +
        (pocet === 1 ? 'stranu' : (pocet < 5 ? 'strany' : 'stran')) + '.';
      okno.querySelector('input[value="vse"]').checked = true;
      $('es-import-strany').value = '';
      $('es-import-obalka').value = '0';
      var chyba = $('es-import-chyba');
      chyba.hidden = true;
      okno.hidden = false;
      var potvrd = $('es-import-ok'), zrus = $('es-import-zrusit');
      function konec(vysledek) {
        okno.hidden = true;
        potvrd.removeEventListener('click', ok);
        zrus.removeEventListener('click', ne);
        resolve(vysledek);
      }
      function ok() {
        try {
          var vse = okno.querySelector('input[value="vse"]').checked;
          var cisla = vse ? rozsah('1-' + pocet, pocet) : rozsah($('es-import-strany').value, pocet);
          var obalek = parseInt($('es-import-obalka').value, 10) || 0;
          if (obalek && cisla.length < obalek) throw new Error('Na obálku je potřeba aspoň ' + obalek + ' stran.');
          konec({ cisla: cisla, obalek: obalek });
        } catch (e) {
          chyba.textContent = e.message;
          chyba.hidden = false;
        }
      }
      function ne() { konec(null); }
      potvrd.addEventListener('click', ok);
      zrus.addEventListener('click', ne);
      $('es-import-strany').onfocus = function () { okno.querySelector('input[value="vyber"]').checked = true; };
    });
  }

  function dalsiImport() {
    if (importBezi) return;
    var uloha = importy.shift();
    if (!uloha) return;
    importBezi = true;
    var f = uloha.soubor, dokument = null, moje = otevreni;
    hlasImport('Otevírám „' + f.name + '“…');
    (jePdf(f) ? otevriPdf(f) : otevriZip(f)).then(function (d) {
      dokument = d;
      hlasImport('');
      if (!d.pocet) throw new Error('V „' + f.name + '“ nejsou žádné strany ani obrázky.');
      return zeptejSeNaImport(f.name, d.pocet);
    }).then(function (volba) {
      if (!volba) return;
      // Obálka v souboru: 2 = první a poslední strana vnější obálka, 4 = i vnitřní.
      var role = {};
      var c = volba.cisla;
      if (volba.obalek >= 2) { role[c[0]] = 'front_outer'; role[c[c.length - 1]] = 'back_outer'; }
      if (volba.obalek >= 4) { role[c[1]] = 'front_inner'; role[c[c.length - 2]] = 'back_inner'; }
      var kam = uloha.kam, hotovo = 0;
      return c.reduce(function (retez, n) {
        return retez.then(function () {
          hlasImport('Rozkládám „' + f.name + '“: strana ' + (++hotovo) + ' z ' + c.length + '…');
          return dokument.strana(n);
        }).then(function (soubor) {
          // Okno se mezitím zavřelo a otevřel se jiný zpěvník - do něj strany nepatří.
          if (moje !== otevreni) throw new Error('');
          if (role[n]) {
            if (nastaveni.naObalku) nastaveni.naObalku(role[n], soubor);
            return;
          }
          var s = stranaZeSouboru(soubor);
          kam = vlozNa(kam, [s]) + 1;
          vykresli();
          zarad(s);
        });
      }, Promise.resolve());
    }).catch(function (e) {
      if (moje === otevreni && e.message) zprava(e.message);
    }).then(function () {
      if (dokument) dokument.zavri();
      hlasImport('');
      importBezi = false;
      dalsiImport();
    });
  }

  function hlasImport(text) {
    var e = $('es-stav-importu');
    if (!e) return;
    e.textContent = text;
    e.hidden = !text;
  }

  // ---------- přesouvání ----------

  function presun(od, k, cil) {
    // cil = index, před který se blok vloží (v původním poli). Překreslí se i bez
    // změny: blok puštěný na své místo by jinak zůstal zesvětlený od tažení.
    if (cil >= od && cil <= k + 1) { vykresli(); return; }
    var kus = strany.splice(od, k - od + 1);
    if (cil > k) cil -= kus.length;
    Array.prototype.splice.apply(strany, [cil, 0].concat(kus));
    vykresli();
  }

  function posunNahoru(i) {
    var b = blok(i);
    if (b[0] === 0) return;
    presun(b[0], b[1], blok(b[0] - 1)[0]);
  }

  function posunDolu(i) {
    var b = blok(i);
    if (b[1] >= strany.length - 1) return;
    presun(b[0], b[1], blok(b[1] + 1)[1] + 1);
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
    var div = el('div', 'es-strana' + (jePokracovani(i) ? ' pripojena' : ''));
    div.dataset.klic = s.klic;

    var nahled = el('div', 'es-nahled');
    if (s.nahled) {
      var img = document.createElement('img');
      img.src = s.nahled;
      img.alt = '';
      img.loading = 'lazy';
      nahled.appendChild(img);
      nahled.setAttribute('data-napoveda', 'Zvětšit nebo vyměnit obrázek');
      nahled.tabIndex = 0;
      nahled.setAttribute('role', 'button');
    } else {
      nahled.classList.add('bez-obrazku');
      nahled.appendChild(tlacitko('es-nahrat-obrazek', '+ obrázek', 'Nahrát obrázek strany'));
    }
    nahled.appendChild(el('div', 'es-stav'));
    div.appendChild(nahled);

    var telo = el('div', 'es-telo');
    var hlava = el('div', 'es-hlava-strany');
    hlava.appendChild(el('span', 'es-cislo', String(prvniCislo() + i)));
    if (jePokracovani(i)) {
      hlava.appendChild(el('span', 'es-navazuje'));
    } else if (!s.pisne.length) {
      hlava.appendChild(el('span', 'es-popis-strany', s.nahled ? 'strana bez písně' : 'prázdná strana'));
    }
    hlava.appendChild(tlacitko('es-odebrat', '✕', 'Odebrat stranu'));
    telo.appendChild(hlava);

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
        radek.classList.add('napoveda-duvod');
        radek.setAttribute('data-napoveda', 'Píseň je i v jiném zpěvníku, název a autor se tady měnit nedají.');
      }
      radek.appendChild(nazev);
      radek.appendChild(autor);
      radek.appendChild(tlacitko('es-odebrat-pisen', '✕', 'Odebrat píseň ze strany'));
      telo.appendChild(radek);
    });
    if (!s.pisne.length && !jePokracovani(i)) {
      var pop = el('input', 'es-popisek');
      pop.type = 'text';
      pop.placeholder = 'Popisek (nepovinný), třeba Úvod nebo Osmisměrka';
      pop.value = s.popisek;
      telo.appendChild(pop);
    }
    telo.appendChild(tlacitko('es-dalsi-pisen', '+ Přidat píseň na stranu'));
    var chyby = el('div', 'es-chyby');
    chyby.hidden = true;
    telo.appendChild(chyby);
    div.appendChild(telo);

    // Spojení s další stranou: jen ze strany, ze které má co pokračovat.
    if (posledniPisen(i)) {
      var spoj = tlacitko('es-spojit' + (s.pokracuje ? ' zapnuto' : ''), null);
      spoj.setAttribute('aria-pressed', s.pokracuje ? 'true' : 'false');
      spoj.appendChild(el('span', 'es-retez', '⛓'));
      spoj.appendChild(el('span', null, s.pokracuje ? 'Píseň pokračuje na další stranu'
                                                     : 'Spojit s další stranou (píseň pokračuje)'));
      spoj.setAttribute('data-napoveda', s.pokracuje
        ? 'Rozpojit: další strana bude samostatná.'
        : 'Poslední píseň této strany pokračuje na další stranu. V názvu písně jde i Alt+↓.');
      div.appendChild(spoj);
    }
    return div;
  }

  function vykresliBlok(od, k) {
    var li = el('li', 'es-blok');
    li.dataset.od = od;
    var drz = el('span', 'es-drzadlo', '≡');
    drz.setAttribute('data-napoveda', 'Přetáhnout');
    li.appendChild(drz);
    var vnitrek = el('div', 'es-blok-strany');
    for (var i = od; i <= k; i++) vnitrek.appendChild(vykresliStranu(strany[i], i));
    li.appendChild(vnitrek);
    var akce = el('div', 'es-akce');
    akce.appendChild(tlacitko('es-nahoru', '↑', 'Posunout výš'));
    akce.appendChild(tlacitko('es-dolu', '↓', 'Posunout níž'));
    li.appendChild(akce);
    return li;
  }

  function vykresli() {
    var seznam = $('es-seznam');
    if (!seznam) return;
    if (window.Napoveda) Napoveda.skryj();
    seznam.textContent = '';
    var i = 0;
    while (i < strany.length) {
      var b = blok(i);
      if (i > 0) {
        var mezera = el('li', 'es-mezera');
        var vl = tlacitko('es-vlozit', '+', 'Vložit sem prázdnou stranu');
        vl.dataset.pred = i;
        mezera.appendChild(vl);
        seznam.appendChild(mezera);
      }
      seznam.appendChild(vykresliBlok(b[0], b[1]));
      i = b[1] + 1;
    }
    var prazdno = $('es-prazdno');
    if (prazdno) prazdno.hidden = strany.length > 0;
    strany.forEach(prekresliStav);
    obnovPopisy();
    ukazProblemy(ukazovatChyby);
  }

  function prvekStrany(s) {
    return $('es-seznam').querySelector('.es-strana[data-klic="' + s.klic + '"]');
  }

  function prekresliStav(s) {
    var div = prvekStrany(s);
    if (!div) return;
    var stav = div.querySelector('.es-stav');
    div.classList.toggle('nahrava', s.stav === 'ceka' || s.stav === 'nahrava');
    div.classList.toggle('chyba-nahrani', s.stav === 'chyba');
    if (stav) {
      stav.textContent = '';
      if (s.stav === 'ceka') stav.textContent = 'čeká';
      else if (s.stav === 'nahrava') stav.textContent = 'nahrává se…';
      else if (s.stav === 'chyba') {
        stav.appendChild(el('span', null, s.chyba));
        stav.appendChild(tlacitko('es-znovu', 'Zkusit znovu'));
      }
    }
    hlasStav();
    // Výměna obrázku jde přes výběr souboru mimo okno a nahrání doběhne samo - ani
    // jedno nevyvolá událost, podle které okno obnovuje tlačítko Uložit.
    if (nastaveni.poZmene) nastaveni.poZmene();
  }

  function hlasStav() {
    var e = $('es-stav-nahravani');
    if (!e) return;
    var zbyva = strany.filter(function (s) { return s.stav === 'ceka' || s.stav === 'nahrava'; }).length;
    var chyb = strany.filter(function (s) { return s.stav === 'chyba'; }).length;
    e.textContent = zbyva ? 'Nahrávají se strany, zbývá ' + zbyva + '.'
                  : (chyb ? 'Nepodařilo se nahrát ' + chyb + ' ' + (chyb === 1 ? 'stranu' : 'stran') + '.' : '');
    e.hidden = !e.textContent;
  }

  // Čísla stran a „pokračuje …“ se mění s psaním; celý seznam se kvůli tomu
  // nepřekresluje (ztratil by se kurzor v poli).
  function obnovPopisy() {
    if (!$('es-seznam')) return;
    var p = prvniCislo();
    strany.forEach(function (s, i) {
      var div = prvekStrany(s);
      if (!div) return;
      div.querySelector('.es-cislo').textContent = String(p + i);
      var n = div.querySelector('.es-navazuje');
      if (n) n.textContent = 'pokračuje „' + posledniPisen(i - 1) + '“';
    });
  }

  function ukazProblemy(ukazat) {
    var vse = ukazat ? problemy() : {};
    strany.forEach(function (s) {
      var div = prvekStrany(s);
      if (!div) return;
      var box = div.querySelector('.es-chyby');
      var p = vse[s.klic];
      div.classList.toggle('ma-chybu', !!p);
      box.textContent = p ? p.join(' ') : '';
      box.hidden = !p;
    });
    return vse;
  }

  // ---------- zvětšení a výměna obrázku ----------

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

  // Strana přijde o obrázek a zůstane z ní prázdná (písně a popisek jí zůstanou).
  function odeberObrazek(s) {
    s.verze++;
    s.soubor = null;
    s.image_id = null;
    s.nahled = s.obrazek = null;
    s.stav = 'hotovo';
    s.chyba = '';
    zavriObrazek();
    vykresli();
  }

  function vyberObrazek(s) {
    var vyber = document.createElement('input');
    vyber.type = 'file';
    vyber.accept = 'image/*';
    vyber.addEventListener('change', function () {
      var f = vyber.files && vyber.files[0];
      if (!f) return;
      nastavObrazek(s, f);
      zavriObrazek();
      vykresli();
      zarad(s);
    });
    vyber.click();
  }

  // ---------- události ----------

  function indexZ(prvek) {
    var div = prvek.closest('.es-strana');
    if (!div) {
      var li = prvek.closest('.es-blok');
      return li ? Number(li.dataset.od) : -1;
    }
    var klic = Number(div.dataset.klic);
    for (var i = 0; i < strany.length; i++) if (strany[i].klic === klic) return i;
    return -1;
  }

  // Zapnutí pokračování: z připojené strany zmizí prázdné pole pro název, které tam
  // bylo jen pro rychlé psaní - na pokračování píseň obvykle nezačíná.
  function prepniSpojeni(i) {
    var s = strany[i];
    s.pokracuje = !s.pokracuje;
    var dalsi = strany[i + 1];
    if (s.pokracuje && dalsi) {
      dalsi.pisne = dalsi.pisne.filter(function (p) { return p.song_id || p.nazev.trim() || p.autor.trim(); });
    }
    vykresli();
  }

  function prvniPoleOd(i) {
    for (var j = i; j < strany.length; j++) {
      var pole = prvekStrany(strany[j]).querySelector('.es-nazev:not([readonly]), .es-popisek');
      if (pole) return pole;
    }
    return null;
  }

  function navaz() {
    var sekce = $('editor-stran');
    if (!sekce || sekce.dataset.navazano) return;
    sekce.dataset.navazano = '1';
    var seznam = $('es-seznam');
    var vyber = $('es-soubory');

    $('es-pridat').addEventListener('click', function () { pridejPrazdnou(null); });
    $('es-nahrat').addEventListener('click', function () { vyber.click(); });
    vyber.addEventListener('change', function () {
      if (vyber.files && vyber.files.length) pridejSoubory(vyber.files, null);
      vyber.value = '';
    });
    $('es-prvni').addEventListener('input', obnovPopisy);

    seznam.addEventListener('click', function (e) {
      var cil = e.target;
      var vlozit = cil.closest('.es-vlozit');
      if (vlozit) { pridejPrazdnou(Number(vlozit.dataset.pred)); return; }
      var i = indexZ(cil);
      if (i < 0) return;
      var s = strany[i];
      var b = cil.closest('button');
      var je = function (trida) { return !!b && b.classList.contains(trida); };
      if (je('es-odebrat')) {
        strany.splice(i, 1);
        vykresli();
      } else if (je('es-nahoru')) posunNahoru(i);
      else if (je('es-dolu')) posunDolu(i);
      else if (je('es-spojit')) prepniSpojeni(i);
      else if (je('es-dalsi-pisen')) {
        s.pisne.push(prazdnaPisen());
        vykresli();
        var pole = prvekStrany(s).querySelectorAll('.es-nazev');
        if (pole.length) pole[pole.length - 1].focus();
      } else if (je('es-odebrat-pisen')) {
        s.pisne.splice(Number(b.closest('.es-pisen').dataset.pisen), 1);
        if (!posledniPisen(i)) s.pokracuje = false;
        vykresli();
      } else if (je('es-znovu')) {
        zarad(s);
      } else if (je('es-nahrat-obrazek')) {
        vyberObrazek(s);
      } else if (cil.closest('.es-nahled') && s.nahled) {
        otevriObrazek(s);
      }
    });

    seznam.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.closest('.es-nahled')) { e.preventDefault(); e.target.click(); return; }
      if (e.target.tagName !== 'INPUT') return;
      var i = indexZ(e.target);
      // Alt+↓: píseň pokračuje na další stranu. Kurzor pak skočí za celý blok, takže
      // se import dá vyplnit bez myši: název, Alt+↓, název, Enter…
      if (e.key === 'ArrowDown' && e.altKey && posledniPisen(i)) {
        e.preventDefault();
        if (!strany[i].pokracuje) prepniSpojeni(i);
        var pole = prvniPoleOd(blok(i)[1] + 1);
        if (pole) pole.focus();
        return;
      }
      if (e.key !== 'Enter') return;
      // Enter skočí na další stranu, při vyplňování stovky stran se nesahá na myš.
      e.preventDefault();
      var dalsi = prvniPoleOd(i + 1);
      if (dalsi) { dalsi.focus(); dalsi.select(); }
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
      if (ukazovatChyby) ukazProblemy(true);
    });

    function nesouSoubory(e) {
      return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0;
    }

    // Tažení bloku za držadlo ≡, myší i prstem (HTML5 tažení telefon neumí). Blok jede
    // s ukazatelem, čára ukazuje, kam dopadne. U okraje rolovacího okna se seznam sám
    // posouvá - čím blíž okraji, tím rychleji, a s délkou držení ještě zrychluje, ať jde
    // píseň z konce stovky stran dostat na začátek jedním tahem.
    var ZONA = 70;
    function rolovatko(prvek) {
      for (var p = prvek.parentElement; p; p = p.parentElement) {
        var oy = getComputedStyle(p).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) return p;
      }
      return document.scrollingElement;
    }
    function okrajeRolovatka(r) {
      if (r === document.scrollingElement) return { top: 0, bottom: window.innerHeight };
      return r.getBoundingClientRect();
    }
    function prekresliTah() {
      tah.li.style.transform = 'translateY(' + (tah.y - tah.y0 + tah.rol.scrollTop - tah.s0) + 'px)';
      seznam.querySelectorAll('.cil-pred, .cil-za').forEach(function (x) { x.classList.remove('cil-pred', 'cil-za'); });
      var bloky = Array.prototype.filter.call(seznam.querySelectorAll('.es-blok'), function (li) { return li !== tah.li; });
      tah.cil = null;
      for (var i = 0; i < bloky.length; i++) {
        var r = bloky[i].getBoundingClientRect();
        if (tah.y < r.top + r.height / 2) { tah.cil = bloky[i]; tah.za = false; break; }
      }
      if (!tah.cil && bloky.length) { tah.cil = bloky[bloky.length - 1]; tah.za = true; }
      if (tah.cil) tah.cil.classList.add(tah.za ? 'cil-za' : 'cil-pred');
    }
    function rolujUOkraje(cas) {
      if (!tah) return;
      var o = okrajeRolovatka(tah.rol);
      var hloubka = Math.max(o.top + ZONA - tah.y, tah.y - (o.bottom - ZONA), 0);
      if (hloubka) {
        if (!tah.odKdy) tah.odKdy = cas;
        var zrychleni = Math.min(4, 1 + (cas - tah.odKdy) / 700);
        var krok = Math.min(1, hloubka / ZONA) * 14 * zrychleni;
        var pred = tah.rol.scrollTop;
        tah.rol.scrollTop += tah.y < o.top + ZONA ? -krok : krok;
        if (tah.rol.scrollTop !== pred) prekresliTah();
      } else {
        tah.odKdy = 0;
      }
      tah.snimek = requestAnimationFrame(rolujUOkraje);
    }
    function dokonciTah(pustit) {
      if (!tah) return;
      var t = tah;
      tah = null;
      cancelAnimationFrame(t.snimek);
      document.body.classList.remove('es-tahne');
      if (pustit && t.cil) {
        var c = blok(Number(t.cil.dataset.od));
        presun(t.b[0], t.b[1], t.za ? c[1] + 1 : c[0]);
      } else {
        vykresli();
      }
    }
    seznam.addEventListener('pointerdown', function (e) {
      var drz = e.target.closest('.es-drzadlo');
      if (!drz || e.button !== 0 || tah) return;
      e.preventDefault();
      var li = drz.closest('.es-blok');
      var rol = rolovatko(seznam);
      tah = { li: li, b: blok(Number(li.dataset.od)), y0: e.clientY, y: e.clientY,
              rol: rol, s0: rol.scrollTop, cil: null, za: false, odKdy: 0 };
      try { drz.setPointerCapture(e.pointerId); } catch (_) {}
      if (window.Napoveda) Napoveda.skryj();
      li.classList.add('tazeny', 'taha');
      document.body.classList.add('es-tahne');
      tah.snimek = requestAnimationFrame(rolujUOkraje);
    });
    seznam.addEventListener('pointermove', function (e) {
      if (!tah) return;
      tah.y = e.clientY;
      prekresliTah();
    });
    seznam.addEventListener('pointerup', function () { dokonciTah(true); });
    seznam.addEventListener('pointercancel', function () { dokonciTah(false); });
    seznam.addEventListener('lostpointercapture', function () { dokonciTah(true); });

    // Soubor přetažený na náhled strany si ta strana vezme za svůj obrázek
    seznam.addEventListener('dragover', function (e) {
      if (nesouSoubory(e) && e.target.closest('.es-nahled')) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
    seznam.addEventListener('drop', function (e) {
      var nahled = e.target.closest('.es-nahled');
      if (!nahled || !e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      e.stopPropagation();
      sekce.classList.remove('es-nad');
      var f = Array.prototype.filter.call(e.dataTransfer.files, jeObrazek)[0];
      if (!f) { zprava('Na stranu jde přetáhnout jen obrázek.'); return; }
      var s = strany[indexZ(nahled)];
      nastavObrazek(s, f);
      vykresli();
      zarad(s);
    });

    // Soubory přetažené kamkoli jinam do sekce stran se přidají na konec
    sekce.addEventListener('dragover', function (e) {
      if (!nesouSoubory(e)) return;
      e.preventDefault();
      sekce.classList.add('es-nad');
    });
    sekce.addEventListener('dragleave', function (e) {
      if (!sekce.contains(e.relatedTarget)) sekce.classList.remove('es-nad');
    });
    sekce.addEventListener('drop', function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      sekce.classList.remove('es-nad');
      pridejSoubory(e.dataTransfer.files, null);
    });

    var lupa = $('es-lupa');
    lupa.addEventListener('click', function (e) {
      var s = najdi(Number(lupa.dataset.klic));
      if (e.target.closest('.es-vymenit') && s) vyberObrazek(s);
      else if (e.target.closest('.es-odebrat-obrazek') && s) odeberObrazek(s);
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
      otevreni++;
      strany = [];
      fronta = [];
      importy = [];
      ukazovatChyby = false;
      zprava('');
      if ($('es-prvni')) $('es-prvni').value = 1;
      vykresli();
      hlasStav();
      vychozi = otisk();
    },
    // Ze serveru přijde typ; pokračování se převede na spojení s předchozí stranou.
    // volby.neulozeno: strany ještě nejsou uložené (předvyplněný nový zpěvník), okno
    // je tedy od začátku změněné - zavření se zeptá a Uložit svítí.
    nacti: function (seznam, prvni, volby) {
      seznam = seznam || [];
      strany = seznam.map(function (z, i) {
        var s = novaStrana(z);
        s.pokracuje = !!(seznam[i + 1] && seznam[i + 1].typ === 'pokracovani');
        return s;
      });
      if ($('es-prvni')) $('es-prvni').value = prvni || 1;
      vykresli();
      vychozi = (volby && volby.neulozeno) ? '' : otisk();
      if (nastaveni.poZmene) nastaveni.poZmene();
    },
    data: data,
    prvniCislo: prvniCislo,
    zmeneno: zmeneno,
    // Počká na rozpracované nahrávání a zkontroluje strany. Při problému je ukáže
    // u stran a na první z nich posune.
    pripravKUlozeni: function () {
      if (importBezi || importy.length) {
        return Promise.resolve({ ok: false, chyba: 'Import souboru ještě probíhá.' });
      }
      return cekejNaNahrani().then(function () {
        ukazovatChyby = true;
        var p = ukazProblemy(true);
        var klice = Object.keys(p);
        if (!klice.length) return { ok: true };
        var div = $('es-seznam').querySelector('.es-strana[data-klic="' + klice[0] + '"]');
        if (div) div.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return { ok: false, chyba: klice.length === 1 ? 'Jedna strana není úplná.'
                                                       : 'Některé strany nejsou úplné (' + klice.length + ').' };
      });
    },
    nahrava: function () { return nahravaSe || fronta.length > 0 || importBezi || importy.length > 0; },
  };
})();
