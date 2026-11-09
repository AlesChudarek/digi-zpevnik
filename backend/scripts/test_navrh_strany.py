#!/usr/bin/env python3
"""Přejímací test editoru navržené strany (TODO bod 11, krok 1): akordy ukotvené ke znaku.

Měří v Chromiu, Firefoxu i WebKitu (Safari) - o tom, jestli se akord drží svého znaku,
rozhoduje rozložení v prohlížeči, ne kód. Stránka nic neukládá do databáze; kopie DB je
tu jen kvůli přihlášení.

    python backend/scripts/test_navrh_strany.py [chromium firefox webkit]
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_napovedy as T  # noqa: E402
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: E402

T.PORT = 5588           # vlastní port, ať běží souběžně s ostatními testy
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOD = 'Meta' if sys.platform == 'darwin' else 'Control'

selhani = []


def ok(popis, podminka, detail=''):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


POMOCNE = """
window.T = {
  // Obdélník i-tého znaku textu v r-tém řádku písně (bez textu akordů).
  znak(r, i) {
    const radek = document.querySelectorAll('.ps-p')[r];
    const w = document.createTreeWalker(radek, NodeFilter.SHOW_TEXT); let n, k = i;
    while ((n = w.nextNode())) {
      if (n.parentElement.closest('.akord-kotva')) continue;
      if (k < n.length) { const g = document.createRange(); g.setStart(n, k); g.setEnd(n, k + 1);
        return g.getBoundingClientRect().toJSON(); }
      k -= n.length;
    }
    return null;
  },
  pozice(r, i) {
    const v = navrhStrany.view, radek = document.querySelectorAll('.ps-p')[r];
    const w = document.createTreeWalker(radek, NodeFilter.SHOW_TEXT); let n, k = i;
    while ((n = w.nextNode())) { if (k < n.length) return v.posAtDOM(n, k); k -= n.length; }
    return null;
  },
  akord(i) { return document.querySelectorAll('.akord')[i].getBoundingClientRect().toJSON(); },
  // Akordy v dokumentu: název, posun a znak, ke kterému patří.
  akordy() {
    const v = navrhStrany.view, doc = v.state.doc, out = [];
    doc.descendants((n, p) => {
      if (n.type.name !== 'akord') return;
      let q = p + 1;
      while (doc.nodeAt(q) && doc.nodeAt(q).type.name === 'akord') q++;
      const za = doc.nodeAt(q);
      out.push({ n: n.attrs.n, dx: n.attrs.dx, dy: n.attrs.dy, pos: p,
                 znak: za && za.isText ? za.text[0] : '' });
    });
    return out;
  },
  // Obdélník znaku, ke kterému patří i-tý akord (kotevní znak).
  kotvaZnak(i) {
    const k = document.querySelectorAll('.akord-kotva')[i];
    const w = document.createTreeWalker(k.closest('.ps-p'), NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
      if (n.parentElement.closest('.akord-kotva') || !(k.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
      if (!n.length) continue;
      const g = document.createRange(); g.setStart(n, 0); g.setEnd(n, 1);
      return g.getBoundingClientRect().toJSON();
    }
    return null;
  },
  // Pozice uvnitř odstavce, který obsahuje daný text (na jeho začátku + posun).
  vOdstavci(text, posun = 0) {
    const v = navrhStrany.view; let vysledek = null;
    v.state.doc.descendants((n, p) => { if (vysledek == null && n.type.name === 'odstavec' && n.textContent.includes(text)) vysledek = p + 1 + posun; });
    return vysledek;
  },
  kurzor(pos) {
    const v = navrhStrany.view;
    v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, pos))); v.focus();
  },
};
null
"""


def ukaz(page, selektor):
    """Přepne panel na záložku, ve které prvek je (ostatní záložky jsou skryté)."""
    page.evaluate("""(s) => { const e = document.querySelector(s); const p = e && e.closest('[data-panel]');
      if (p && p.hidden) document.querySelector('[data-zalozka="' + p.dataset.panel + '"]').click(); }""", selektor)


def zije(page):
    """Stránka odpovídá (WebKit se dřív zasekl v nekonečné smyčce)."""
    try:
        page.wait_for_function('true', timeout=5000)
        return True
    except PWTimeout:
        return False


def otevri_editor(page, base):
    page.goto(base + '/editor-strany')
    page.wait_for_function('window.navrhStrany && document.fonts.status === "loaded"', timeout=15000)
    page.evaluate('localStorage.clear()')
    page.reload()
    page.wait_for_function('window.navrhStrany && document.fonts.status === "loaded"', timeout=15000)
    page.evaluate(POMOCNE)


# Výchozí text testu (ukázková píseň nové strany má akordy už v sobě).
TEXT_TESTU = 'Sem napiš první řádek\\na druhý řádek sloky\\n\\nR:\\nTady začíná refrén'


def zacni_testovacim_textem(page):
    page.evaluate(f"navrhStrany.nahradTextem('Název písně', 'Autor', '{TEXT_TESTU}')")
    page.wait_for_timeout(100)


def test_prohlizece(p, base, jmeno):
    print(f"\n── {jmeno} ──")
    br = getattr(p, jmeno).launch()
    page = br.new_page(viewport={'width': 1400, 'height': 1000})
    chyby = []
    page.on('pageerror', lambda e: chyby.append(str(e)))
    page.on('console', lambda m: chyby.append(m.text) if m.type == 'error' else None)
    page.goto(base + '/login')
    page.fill('input[name="email"]', 'admin@test.com')
    page.fill('input[name="password"]', T.PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state('networkidle')
    otevri_editor(page, base)
    J = page.evaluate

    # Nová strana: ukázková píseň se vejde, celá strana je vidět, lišta na jednom řádku.
    ok('ukázková píseň: dvě sloky a refrén', J("[...document.querySelectorAll('.ps-zacatek')].map(p => p.dataset.znacka)") == ['1.', 'R:', '2.'])
    ok('ukázková píseň má akordy', J("document.querySelectorAll('.akord-kotva').length") >= 20)
    ok('ukázková píseň se vejde bez přetečení a kolizí',
       J("!document.querySelector('.ps-ramecek.pretece') && !document.querySelector('.kolize')"))
    ok('na začátku je vidět celá strana', J("""(() => { const r = document.getElementById('stranka').getBoundingClientRect(),
       p = document.getElementById('navrh-plocha').getBoundingClientRect();
       return r.top >= p.top && r.bottom <= p.bottom && r.left >= p.left && r.right <= p.right; })()"""))
    ok('lišta je na jednom řádku', J("new Set([...document.querySelectorAll('.navrh-lista .skupina')].map(s => Math.round(s.getBoundingClientRect().top))).size") == 1)
    ok('ikony lišty jsou vidět', all(w >= 18 for w in J("[...document.querySelectorAll('.navrh-lista svg')].map(s => s.getBoundingClientRect().width)")))

    ok('panel má záložky Text, Akordy, Strana a Ovládání',
       J("[...document.querySelectorAll('[data-zalozka]')].map(b => b.dataset.zalozka)") == ['text', 'akordy', 'strana', 'ovladani'])
    ok('na záložce Text jsou nahoře styly',
       J("document.querySelector('[data-panel=\"text\"] > section').textContent.includes('Styly odstavců')"))

    # Akordy podle předchozí sloky (na ukázkové písni): smazat akordy 2. sloky a doplnit.
    J('navrhStrany.prepniRezimAkordu(true)')
    ok('s nástrojem Akord jsou vidět spojnice všech akordů',
       J("[...document.querySelectorAll('.spojnice line.vse')].filter(l => l.style.display !== 'none').length")
       == J("document.querySelectorAll('.akord-kotva').length"))
    pocet = J("document.querySelectorAll('.akord-kotva').length")
    J("T.kurzor(T.vOdstavci('Kamarád', 3))")
    page.wait_for_timeout(100)
    ok('u sloky s akordy je nabídka Smazat', J("document.querySelector('.nabidka-sloky').textContent") == 'Smazat akordy sloky')
    page.click('.nabidka-sloky button')
    page.wait_for_timeout(100)
    po_smazani = J("document.querySelectorAll('.akord-kotva').length")
    ok('Smazat odebere akordy sloky', po_smazani < pocet, (pocet, po_smazani))
    ok('u sloky bez akordů je nabídka Doplnit', 'Doplnit akordy jako 1. sloka' in J("document.querySelector('.nabidka-sloky').textContent"))
    page.click('.nabidka-sloky button')
    page.wait_for_timeout(100)
    ok('Doplnit vrátí akordy podle 1. sloky', J("document.querySelectorAll('.akord-kotva').length") == pocet)
    radek = J("""(() => { let s = ''; navrhStrany.view.state.doc.descendants((n) => { if (n.type.name === 'odstavec' && n.textContent.startsWith('Kamarád'))
      n.forEach(c => { s += c.type.name === 'akord' ? '[' + c.attrs.n + ']' : (c.text || ''); }); }); return s; })()""")
    ok('akordy sedí po slabikách', radek.startswith('[G]Kamarád') and '[Emi]ček' in radek, radek)
    page.keyboard.press(f'{MOD}+z')
    ok('Zpět vrátí doplnění jedním krokem', J("document.querySelectorAll('.akord-kotva').length") == po_smazani)
    J('navrhStrany.prepniRezimAkordu(false)')

    zacni_testovacim_textem(page)
    page.click('[data-prikaz="vetsi"]')
    page.click('[data-prikaz="vetsi"]')
    page.wait_for_timeout(100)
    m = J('navrhStrany.meritko()')
    tol = 1.2 * m           # do 1,2 CSS px (WebKit má kotvu o zlomek pixelu vedle)
    # Výchozí strana: 0 Název, 1 Autor, 2-3 sloka, 4 prázdný, 5 refrén.
    S1, S2, REF = 2, 3, 5

    ok('výchozí strana má jeden rámeček', J("document.querySelectorAll('.ps-ramecek').length") == 1)
    ok('sloka a refrén mají značky 1. a R:',
       J("[...document.querySelectorAll('.ps-zacatek')].map(p => p.dataset.znacka)") == ['1.', 'R:'])

    # Vložení nástrojem Akordy: klik do pravé půlky znaku patří pořád tomu znaku.
    rozestup0 = J(f'T.znak({S2}, 0).y - T.znak({S1}, 0).y')
    J('navrhStrany.prepniRezimAkordu(true)')
    z = J(f'T.znak({S2}, 4)')
    page.mouse.click(z['x'] + z['width'] * 0.75, z['y'] + z['height'] / 2)
    ok('klik na znak otevře pole', J("!document.querySelector('.akord-pole').hidden"))
    page.keyboard.type('Ami')
    page.keyboard.press('Enter')
    a = J('T.akordy()')
    ok('akord patří ke kliknutému znaku', len(a) == 1 and a[0]['n'] == 'Ami' and a[0]['znak'] == 'u', a)
    page.wait_for_timeout(100)
    r, z = J('T.akord(0)'), J(f'T.znak({S2}, 4)')
    ok('akord sedí nad svým znakem', abs(r['x'] - z['x']) < tol, round(r['x'] - z['x'], 2))
    ok('akord je nad řádkem, ne přes text', r['y'] + r['height'] <= z['y'] + 1,
       round(z['y'] - (r['y'] + r['height']), 2))
    rozestup1 = J(f'T.znak({S2}, 0).y - T.znak({S1}, 0).y')
    ok('řádek s akordem dostane místo nad sebou', rozestup1 > rozestup0 + 8 * m, (round(rozestup0, 1), round(rozestup1, 1)))

    # Zalomený odstavec: místo pro akordy jen nad vizuálním řádkem, kde akord je.
    J(f'T.kurzor(T.pozice({S1}, 0) + {len("Sem napiš první řádek")})')
    J('navrhStrany.prepniRezimAkordu(false)')
    page.keyboard.type(' a ještě dlouhé pokračování, které se určitě zalomí na další řádek rámečku')
    radky = J(f"""(() => {{ const p = document.querySelectorAll('.ps-p')[{S1}]; const tops = new Set();
      const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) for (let i = 0; i < n.length; i++) {{ const g = document.createRange(); g.setStart(n, i); g.setEnd(n, i + 1);
        const r = g.getBoundingClientRect(); if (r.width) tops.add(Math.round(r.top)); }}
      return [...tops].sort((a, b) => a - b); }})()""")
    ok('dlouhý odstavec se zalomí', len(radky) >= 2, radky)
    vyska0 = radky[1] - radky[0]
    posledni = J(f"document.querySelectorAll('.ps-p')[{S1}].textContent.length") - 3
    J(f'T.kurzor(T.pozice({S1}, {posledni}))')
    page.keyboard.type('[')
    page.keyboard.type('G')
    page.keyboard.press('Enter')
    page.wait_for_timeout(100)
    radky2 = J(f"""(() => {{ const p = document.querySelectorAll('.ps-p')[{S1}]; const tops = new Set();
      const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) for (let i = 0; i < n.length; i++) {{ const g = document.createRange(); g.setStart(n, i); g.setEnd(n, i + 1);
        const r = g.getBoundingClientRect(); if (r.width) tops.add(Math.round(r.top)); }}
      return [...tops].sort((a, b) => a - b); }})()""")
    ok('akord na zalomeném řádku zvětší jen ten řádek',
       radky2[0] == radky[0] and radky2[-1] - radky2[-2] > vyska0 + 8 * m, (radky, radky2))
    page.keyboard.press(f'{MOD}+z')
    page.keyboard.press(f'{MOD}+z')

    # Druhý akord hned vedle: kolize se jen označí, text se nehýbe.
    J('navrhStrany.prepniRezimAkordu(true)')
    x_pred = J(f'T.znak({S2}, 5).x')
    z = J(f'T.znak({S2}, 5)')
    page.mouse.click(z['x'] + 1, z['y'] + z['height'] / 2)
    page.keyboard.type('C#mi7')
    page.keyboard.press('Enter')
    ok('stránka po akordu s # odpovídá', zije(page))
    page.wait_for_timeout(150)
    ok('akordy přes sebe se jen označí', J("document.querySelectorAll('.akord-kotva.kolize').length") == 2)
    ok('a text se neposune', abs(J(f'T.znak({S2}, 5).x') - x_pred) < 0.5)
    ukaz(page, '[data-nastaveni="automatickaMezera"]')
    page.check('[data-nastaveni="automatickaMezera"]')
    page.wait_for_timeout(150)
    r0, r1, z = J('T.akord(0)'), J('T.akord(1)'), J(f'T.znak({S2}, 5)')
    ok('se zapnutou mezerou odsunou text', r1['x'] >= r0['x'] + r0['width'], round(r1['x'] - r0['x'] - r0['width'], 2))
    ok('odsunutý akord je pořád nad svým znakem', abs(r1['x'] - z['x']) < tol, round(r1['x'] - z['x'], 2))

    # Ručně posunutý akord text neodsouvá ani se zapnutou mezerou.
    J('navrhStrany.prepniRezimAkordu(false)')
    r = J('T.akord(1)')
    page.keyboard.down('Alt')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 4, r['y'] - 12, steps=4)
    page.mouse.up()
    page.keyboard.up('Alt')
    page.wait_for_timeout(150)
    a = J('T.akordy()')
    ok('s Altem drží svůj znak a uloží posun', a[1]['znak'] == 'h' and a[1]['dy'] < 0, a)
    ok('ručně posunutý akord text neodsouvá', J("document.querySelectorAll('.akord-kotva')[1].style.marginLeft") in ('', '0px'))
    page.keyboard.press(f'{MOD}+z')
    ukaz(page, '[data-nastaveni="automatickaMezera"]')
    page.uncheck('[data-nastaveni="automatickaMezera"]')

    # Vodicí čára: akord tažený kousek nad sousedy se přichytí k jejich výšce.
    J(f"T.kurzor(T.pozice({S2}, 12))")
    page.keyboard.type('[')
    page.keyboard.type('D')
    page.keyboard.press('Enter')
    page.wait_for_timeout(100)
    r, soused = J('T.akord(2)'), J('T.akord(0)')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 3, r['y'] - 1, steps=3)
    page.mouse.move(r['x'] + 3, r['y'] - 3, steps=2)
    ok('při tažení blízko výšky sousedů je vidět vodicí čára', J("document.querySelector('.voditko').classList.contains('viditelne')"))
    page.mouse.up()
    page.wait_for_timeout(100)
    r = J('T.akord(2)')
    ok('a akord je na stejné výšce jako sousedé', abs((r['y'] + r['height']) - (soused['y'] + soused['height'])) < 0.6,
       round((r['y'] + r['height']) - (soused['y'] + soused['height']), 2))
    # Shift: jen vodorovně.
    pred = J('T.akordy()')[2]
    r = J('T.akord(2)')
    page.keyboard.down('Shift')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 6, r['y'] - 25, steps=4)
    page.mouse.up()
    page.keyboard.up('Shift')
    ok('se Shiftem se akord posune jen vodorovně', J('T.akordy()')[2]['dy'] == pred['dy'], J('T.akordy()')[2])

    # Psaní před akordem: akord drží svůj znak.
    J(f'T.kurzor(T.pozice({S2}, 0))')
    page.keyboard.type('Xx ')
    a = J('T.akordy()')
    ok('psaní na začátku řádku akordy nepřesune', [x['znak'] for x in a][:2] == ['u', 'h'], a)
    J("T.kurzor(T.akordy()[0].pos + 1)")
    page.keyboard.type('Q')
    ok('psaní za akordem jde před něj', J('T.akordy()')[0]['znak'] == 'u', J('T.akordy()'))

    # Transpozice.
    page.click('[data-prikaz="transp-nahoru"]')
    page.click('[data-prikaz="transp-nahoru"]')
    ok('transpozice +2', [x['n'] for x in J('T.akordy()')][:2] == ['Hmi', 'D#mi7'], J('T.akordy()'))
    page.wait_for_timeout(600)

    # Tažení přes jiný znak: kotva se přesune; Zpět vrátí celé tažení.
    J('T.kurzor(3)')
    pred = J('T.akordy()')[0]
    r, zs, zc = J('T.akord(0)'), J(f'T.znak({S2}, 8)'), J(f'T.znak({S2}, 12)')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 3 + (zc['x'] - zs['x']), r['y'] + 3, steps=6)
    ok('při tažení je zvýrazněný budoucí znak', J("[...document.querySelectorAll('.kotva-znak')].map(e => e.textContent).join('')") == 'ř')
    page.mouse.up()
    page.wait_for_timeout(100)
    a = J('T.akordy()')
    ok('puštěný akord patří ke znaku pod sebou', any(x['n'] == pred['n'] and x['znak'] == 'ř' for x in a), a)
    page.keyboard.press(f'{MOD}+z')
    a = J('T.akordy()')
    ok('Zpět vrátí tažení jedním krokem', a[0]['n'] == pred['n'] and a[0]['znak'] == 'u', a)

    # Smazání kotevního znaku: akord přejde na další znak a blikne.
    J(f"T.kurzor(T.akordy()[0].pos + 2)")
    page.keyboard.press('Backspace')
    a = J('T.akordy()')
    ok('po smazání znaku patří akord k dalšímu', a[0]['znak'] == 'h', a)
    ok('a blikne', J("document.querySelectorAll('.akord-kotva.blik').length") >= 1)

    # Styly: styl odstavce z lišty, úprava stylu v panelu, přímé formátování.
    J(f'T.kurzor(T.pozice({S2}, 2))')
    page.select_option('#lista-styl', 'normalni')
    ok('styl z lišty změní odstavec', J(f"document.querySelectorAll('.ps-p')[{S2}].dataset.styl").startswith('normalni-'))
    ok('a bez stylu Sloka zmizí číslo druhého řádku', J("[...document.querySelectorAll('.ps-zacatek')].length") == 2)
    page.keyboard.press(f'{MOD}+z')
    ukaz(page, '#seznam-stylu li[data-styl="sloka"] .upravit')
    page.click('#seznam-stylu li[data-styl="sloka"] .upravit')
    ok('tužka rozbalí úpravu stylu', J("!document.getElementById('uprava-stylu').hidden"))
    ukaz(page, '[data-styl-pole="velikost"]')
    page.fill('[data-styl-pole="velikost"]', '14')
    page.wait_for_timeout(100)
    ok('úprava stylu změní všechny sloky', J(f"getComputedStyle(document.querySelectorAll('.ps-p')[{S1}]).fontSize")
       == J("Math.round(14 * 96 / 72 * 100) / 100 + 'px'") or abs(float(J(f"parseFloat(getComputedStyle(document.querySelectorAll('.ps-p')[{S1}]).fontSize)")) - 14 * 96 / 72) < 0.1)
    r, z = J('T.akord(0)'), J('T.kotvaZnak(0)')
    ok('po zvětšení písma je akord nad svým znakem', abs(r['x'] - z['x']) < tol, round(r['x'] - z['x'], 2))
    J(f"(() => {{ const v = navrhStrany.view; const a = T.pozice({S1}, 0), b = T.pozice({S1}, 3);"
      f" v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, a, b))); v.focus(); }})()")
    page.fill('#lista-velikost', '20')
    page.press('#lista-velikost', 'Enter')
    ok('velikost z lišty jako přímé formátování', J(f"!!document.querySelectorAll('.ps-p')[{S1}].querySelector('[data-velikost=\"20\"]')"))

    # +/- u označeného textu funguje opakovaně; barva se použije na označený text.
    J(f"(() => {{ const v = navrhStrany.view; const a = T.pozice({S1}, 4), b = T.pozice({S1}, 9);"
      f" v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, a, b))); v.focus(); }})()")
    page.click('[data-prikaz="pismo-vetsi"]')
    page.click('[data-prikaz="pismo-vetsi"]')
    page.click('[data-prikaz="pismo-vetsi"]')
    velikosti = J(f"[...document.querySelectorAll('.ps-p')][{S1}].querySelectorAll('[data-velikost]').length ? [...document.querySelectorAll('.ps-p')[{S1}].querySelectorAll('[data-velikost]')].map(e => e.dataset.velikost) : []")
    ok('+ u výběru jde zmáčknout opakovaně', '20' in velikosti and len(set(velikosti)) >= 1 and J("document.getElementById('lista-velikost').value") not in ('14', '15'), velikosti)
    page.dispatch_event('label.barva-textu', 'pointerdown')
    J("(() => { const i = document.getElementById('lista-barva'); i.value = '#00aa00'; i.dispatchEvent(new Event('input', {bubbles: true})); })()")
    ok('barva textu se použije na výběr', J(f"!!document.querySelectorAll('.ps-p')[{S1}].querySelector('[data-barva=\"#00aa00\"]')"))

    # Vlastní styl: odstavec naformátovaný celý -> „Uložit“ u jeho stylu převezme formátování.
    J(f"(() => {{ const v = navrhStrany.view; const a = T.pozice({S2}, 0); const b = a + v.state.doc.resolve(a).parent.content.size;"
      f" v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, a, b))); v.focus(); }})()")
    page.click('[data-prikaz="kurziva"]')
    ok('u stylu odstavce se objeví Uložit', J("!document.querySelector('#seznam-stylu li[data-styl=\"sloka\"] .ulozit-styl').hidden"))
    ukaz(page, '#seznam-stylu li[data-styl="sloka"] .ulozit-styl')
    page.click('#seznam-stylu li[data-styl="sloka"] .ulozit-styl')
    ok('Uložit převezme kurzívu do stylu', J("navrhStrany.knihovna().sloka.kurziva") is True)
    ok('a přímé formátování z odstavce zmizí', not J(f"!!document.querySelectorAll('.ps-p')[{S2}].querySelector('em')"))
    ok('kurzívu mají i ostatní sloky', J(f"getComputedStyle(document.querySelectorAll('.ps-p')[{S1}]).fontStyle") == 'italic')
    # Nový styl z odstavce s rolí Sloka: odstavec ho dostane a pořád se čísluje.
    J(f"(() => {{ const v = navrhStrany.view; const a = T.pozice({S1}, 0); const b = a + v.state.doc.resolve(a).parent.content.size;"
      f" v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, a, b))); v.focus(); }})()")
    page.fill('#lista-velikost', '12')
    page.press('#lista-velikost', 'Enter')
    J(f"T.kurzor(T.pozice({S1}, 2))")
    ukaz(page, '#novy-styl')
    page.click('#novy-styl')
    ukaz(page, '#novy-styl-nazev')
    page.fill('#novy-styl-nazev', 'Úzká sloka')
    ukaz(page, '#novy-styl-role')
    page.select_option('#novy-styl-role', 'sloka')
    ukaz(page, '#novy-styl-vytvorit')
    page.click('#novy-styl-vytvorit')
    ok('nový styl je v seznamu', J("[...document.querySelectorAll('#seznam-stylu li')].some(l => l.textContent.startsWith('Úzká sloka'))"))
    ok('odstavec dostal nový styl', J(f"document.querySelectorAll('.ps-p')[{S1}].dataset.styl") == 'v1-1')
    ok('sloka s vlastním stylem se čísluje', J(f"document.querySelectorAll('.ps-p')[{S1}].dataset.znacka") == '1.')
    ukaz(page, '#seznam-stylu li[data-styl="v1"] .upravit')
    page.click('#seznam-stylu li[data-styl="v1"] .upravit')
    ok('vlastní styl jde přejmenovat a smazat', J("!document.querySelector('[data-styl-pole=\"nazev\"]').closest('label').hidden"))
    vzhled = J(f"(() => {{ const c = getComputedStyle(document.querySelectorAll('.ps-p')[{S1}]); return [c.fontSize, c.fontStyle, c.paddingLeft]; }})()")
    ukaz(page, '#smazat-styl')
    page.click('#smazat-styl')
    ok('smazaný styl zmizí z knihovny', not J("'v1' in navrhStrany.knihovna()"))
    ok('odstavec namalovaný smazaným stylem vypadá stejně', J(f"(() => {{ const c = getComputedStyle(document.querySelectorAll('.ps-p')[{S1}]); return [c.fontSize, c.fontStyle, c.paddingLeft]; }})()") == vzhled)
    J(f"T.kurzor(T.pozice({S1}, 2))")
    ok('a v panelu ani v liště není označený žádný styl',
       J("!document.querySelector('#seznam-stylu li.aktivni') && document.getElementById('lista-styl').value === ''"))
    ok('sloka se smazaným stylem se pořád čísluje', J(f"document.querySelectorAll('.ps-p')[{S1}].dataset.znacka") == '1.')
    page.select_option('#lista-styl', 'sloka')

    # Styl se pozná podle vzhledu: odstavec s kopií jiné verze, která vypadá stejně jako
    # Sloka v knihovně, je Sloka.
    J("navrhStrany.nastaveni().styly['sloka-0'] = { ...navrhStrany.knihovna().sloka }; null")
    J(f"""(() => {{ const v = navrhStrany.view; const p = T.pozice({S1}, 0) - 1;
      v.dispatch(v.state.tr.setNodeMarkup(p, null, {{ ...v.state.doc.nodeAt(p).attrs, styl: 'sloka-0' }})); }})()""")
    J(f"T.kurzor(T.pozice({S1}, 2))")
    ok('stejně vypadající odstavec se pozná jako styl', J("document.getElementById('lista-styl').value") == 'sloka'
       and J("document.querySelector('#seznam-stylu li.aktivni').dataset.styl") == 'sloka')

    # Štětec: upravený styl nezmění odstavec, který má kopii starší verze (jiná strana).
    J("navrhStrany.nastaveni().styly['refren-0'] = { ...navrhStrany.knihovna().refren, velikost: 8 }; null")
    J("""(() => { const v = navrhStrany.view; let p; v.state.doc.descendants((n, q) => { if (p == null && n.type.name === 'odstavec' && n.attrs.styl.startsWith('refren-')) p = q; });
      v.dispatch(v.state.tr.setNodeMarkup(p, null, { ...v.state.doc.nodeAt(p).attrs, styl: 'refren-0' })); })()""")
    ukaz(page, '#seznam-stylu li[data-styl="refren"] .upravit')
    page.click('#seznam-stylu li[data-styl="refren"] .upravit')
    ukaz(page, '[data-styl-pole="velikost"]')
    page.fill('[data-styl-pole="velikost"]', '13')
    page.wait_for_timeout(100)
    velikosti = J("""[...document.querySelectorAll('.ps-p')].filter(p => p.dataset.styl.startsWith('refren')).map(p => p.dataset.styl + ':' + getComputedStyle(p).fontSize)""")
    ok('štětec: starší verze stylu zůstane, jak byla', any(v.startswith('refren-0:10.66') for v in velikosti), velikosti)
    ukaz(page, '#seznam-stylu li[data-styl="refren"] .upravit')
    page.click('#seznam-stylu li[data-styl="refren"] .upravit')
    page.keyboard.press(f'{MOD}+z')

    # Smíšený výběr (Název + sloka s jinou velikostí): lišta neukáže nic a zvolený styl
    # se použije na celý výběr jednotně.
    J(f"(() => {{ const v = navrhStrany.view; const a = T.pozice(0, 2), b = T.pozice({S1}, 6);"
      f" v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, a, b))); v.focus(); }})()")
    ok('smíšený výběr: styl, písmo ani velikost se neukážou',
       J("[document.getElementById('lista-styl').value, document.getElementById('lista-pismo').value, document.getElementById('lista-velikost').value]") == ['', '', ''],
       J("[document.getElementById('lista-styl').value, document.getElementById('lista-pismo').value, document.getElementById('lista-velikost').value]"))
    ok('smíšený výběr: v panelu není označený žádný styl', J("!document.querySelector('#seznam-stylu li.aktivni')"))
    page.select_option('#lista-styl', 'sloka')
    ok('zvolený styl dostane celý výběr', J(f"[0, 1, {S1}].map(i => document.querySelectorAll('.ps-p')[i].dataset.styl.replace(/-\\d+$/, ''))") == ['sloka', 'sloka', 'sloka'])
    ok('a přímá velikost zmizí', not J(f"!!document.querySelectorAll('.ps-p')[{S1}].querySelector('[data-velikost]')"))
    ok('pak už lišta styl ukazuje', J("document.getElementById('lista-styl').value") == 'sloka')
    page.keyboard.press(f'{MOD}+z')

    # Číslování slok: nová sloka dostane 2., ruční značka 5. posune další na 6.
    J("T.kurzor(navrhStrany.view.state.doc.content.size - 2)")
    page.keyboard.press('Enter')
    page.select_option('#lista-styl', 'sloka')
    page.keyboard.type('Druhá sloka')
    page.keyboard.press('Enter')
    page.keyboard.press('Enter')
    page.keyboard.type('Třetí sloka')
    znacky = J("[...document.querySelectorAll('.ps-zacatek')].map(p => p.dataset.znacka)")
    ok('sloky se číslují samy', znacky == ['1.', 'R:', '2.', '3.'], znacky)
    J("T.kurzor(T.vOdstavci('Druhá sloka', 3))")
    ukaz(page, '#znacka-sloky')
    page.fill('#znacka-sloky', '5.')
    znacky = J("[...document.querySelectorAll('.ps-zacatek')].map(p => p.dataset.znacka)")
    ok('ruční značka přepíše číslo a posune další', znacky == ['1.', 'R:', '5.', '6.'], znacky)

    # Nabídka refrénu: prázdný odstavec Refrén nabídne kopii, Tab ji vloží i s akordy.
    J(f"T.kurzor(T.pozice({REF}, 3))")
    page.click('[data-prikaz="akordy"]')
    z = J(f'T.znak({REF}, 0)')
    page.mouse.click(z['x'] + 1, z['y'] + z['height'] / 2)
    page.keyboard.type('F')
    page.keyboard.press('Enter')
    page.keyboard.press('Escape')
    J("T.kurzor(navrhStrany.view.state.doc.content.size - 2)")
    page.keyboard.press('Enter')
    page.keyboard.press('Enter')
    page.select_option('#lista-styl', 'refren')
    ok('prázdný Refrén nabídne předchozí refrén', J("!!document.querySelector('.ps-nabidka')"))
    page.keyboard.press('Tab')
    ok('Tab vloží kopii refrénu i s akordem',
       J("[...document.querySelectorAll('.ps-p')].filter(p => p.textContent.includes('Tady začíná refrén')).length") == 2
       and sum(1 for x in J('T.akordy()') if x['n'] == 'F') == 2, J('T.akordy()'))

    # Rámečky: nový, přesun za lištu (Zpět ho vrátí), smazání.
    page.click('[data-prikaz="ramecek"]')
    ok('+ Rámeček přidá rámeček', J("document.querySelectorAll('.ps-ramecek').length") == 2)
    page.keyboard.type('Poznámka')
    pred = J("navrhStrany.view.state.doc.child(1).attrs")
    l = J("document.querySelectorAll('.ps-ramecek .ps-prvek-lista')[1].getBoundingClientRect().toJSON()")
    page.mouse.move(l['x'] + 20, l['y'] + l['height'] / 2)
    page.mouse.down()
    page.mouse.move(l['x'] + 20 + 40 * m * 96 / 25.4 / 10, l['y'] + l['height'] / 2 + 20 * m * 96 / 25.4 / 10, steps=5)
    page.mouse.up()
    po = J("navrhStrany.view.state.doc.child(1).attrs")
    ok('rámeček jde přesunout (o 4 a 2 mm)', abs(po['x'] - pred['x'] - 4) < 0.6 and abs(po['y'] - pred['y'] - 2) < 0.6, (pred, po))
    page.keyboard.press(f'{MOD}+z')
    ok('Zpět vrátí přesun rámečku', J("navrhStrany.view.state.doc.child(1).attrs") == pred)
    page.click('.ps-ramecek:nth-child(2) .ps-prvek-smazat', force=True)
    ok('rámeček jde smazat', J("document.querySelectorAll('.ps-ramecek').length") == 1)

    # Obrázek: vložení souborem, přesun, změna velikosti se zachovaným poměrem, vrstva.
    png = Path(tempfile.mkdtemp(prefix='navrh-obr-')) / 'obr.png'
    from PIL import Image as PILImage
    PILImage.new('RGB', (400, 200), (40, 120, 200)).save(png)
    page.set_input_files('#soubor-obrazku', str(png))
    page.wait_for_function("document.querySelectorAll('.ps-obrazek').length === 1", timeout=5000)
    o = J("(() => { let a; navrhStrany.view.state.doc.forEach(n => { if (n.type.name === 'obrazek') a = n.attrs; }); return a; })()")
    ok('obrázek se vloží s poměrem stran 2:1', abs(o['w'] / o['h'] - 2) < 0.05, {k: o[k] for k in 'xywh'})
    ok('vložený obrázek je vybraný a panel ukáže jeho kartu', J("!document.getElementById('vybrany-obrazek').hidden"))
    r = J("document.querySelector('.ps-obrazek img').getBoundingClientRect().toJSON()")
    krok = 10 * m * 96 / 25.4
    page.mouse.move(r['x'] + r['width'] / 2, r['y'] + r['height'] / 2)
    page.mouse.down()
    page.mouse.move(r['x'] + r['width'] / 2 + krok, r['y'] + r['height'] / 2 + krok, steps=5)
    page.mouse.up()
    o2 = J("(() => { let a; navrhStrany.view.state.doc.forEach(n => { if (n.type.name === 'obrazek') a = n.attrs; }); return a; })()")
    ok('obrázek jde přesunout za plochu (o 10 mm)', abs(o2['x'] - o['x'] - 10) < 0.6 and abs(o2['y'] - o['y'] - 10) < 0.6, (o['x'], o['y'], o2['x'], o2['y']))
    roh = J("document.querySelector('.ps-obrazek .ps-prvek-roh').getBoundingClientRect().toJSON()")
    page.mouse.move(roh['x'] + roh['width'] / 2, roh['y'] + roh['height'] / 2)
    page.mouse.down()
    page.mouse.move(roh['x'] + roh['width'] / 2 + krok, roh['y'] + roh['height'] / 2 + 2, steps=5)
    page.mouse.up()
    o3 = J("(() => { let a; navrhStrany.view.state.doc.forEach(n => { if (n.type.name === 'obrazek') a = n.attrs; }); return a; })()")
    ok('roh zvětší obrázek se stejným poměrem', o3['w'] > o2['w'] + 5 and abs(o3['w'] / o3['h'] - 2) < 0.05, (o3['w'], o3['h']))
    ukaz(page, '#vybrany-obrazek [data-vrstva="za"]')
    page.click('#vybrany-obrazek [data-vrstva="za"]')
    ok('obrázek jde dát za text', J("getComputedStyle(document.querySelector('.ps-obrazek')).zIndex") == '0')
    ukaz(page, '#obrazek-smazat')
    page.click('#obrazek-smazat')
    ok('obrázek jde smazat', J("document.querySelectorAll('.ps-obrazek').length") == 0)

    # Vložení textu se závorkami a prázdnými řádky.
    J("T.kurzor(navrhStrany.view.state.doc.content.size - 2)")
    J("navrhStrany.view.pasteText('\\n\\n[C]Ahoj [G]světe\\n[Ami]druhý řádek\\n\\nR: [F]refrén')")
    odst = J("[...document.querySelectorAll('.ps-p')].slice(-5).map(p => p.dataset.styl.replace(/-\\d+$/, '') + ':' + p.textContent.replace(/[A-H]#?(mi)?7?(?=[a-zěščřžýáíéůú ])/g, ''))")
    ok('vložený text: odstavce se styly sloka a refrén',
       [o.split(':')[0] for o in odst] == ['sloka', 'sloka', 'refren', 'refren'][-4:] or [o.split(':')[0] for o in odst][-4:] == ['sloka', 'sloka', 'refren', 'refren'], odst)
    nazvy = [(x['n'], x['znak']) for x in J('T.akordy()')]
    ok('vložený text: akordy u znaků', all(x in nazvy for x in [('C', 'A'), ('G', 's'), ('Ami', 'd'), ('F', 'r')]), nazvy)

    # Číslo strany: vrstva nad stranou, tažení s přichycením ke středu, zrcadlení na levé.
    poloha = lambda: J("""(() => { const c = document.querySelector('.ps-cislo-strany'), s = document.getElementById('stranka');
      const r = c.getBoundingClientRect(), p = s.getBoundingClientRect(), k = navrhStrany.meritko() * 96 / 25.4;
      return [Math.round(((r.left + r.width / 2) - p.left) / k * 2) / 2, Math.round(((r.top + r.height / 2) - p.top) / k * 2) / 2]; })()""")
    ok('číslo strany je dole u vnějšího okraje pravé strany', poloha() == [138, 205], poloha())
    ok('číslo není v textu strany', 'ps-cislo-strany' not in J("navrhStrany.view.dom.innerHTML"))
    # Test si stranu předtím přiblížil - číslo dole může být mimo viditelnou plochu.
    J("document.querySelector('.ps-cislo-strany').scrollIntoView({ block: 'center', inline: 'center' })")
    c = J("document.querySelector('.ps-cislo-strany').getBoundingClientRect().toJSON()")
    st = J("document.getElementById('stranka').getBoundingClientRect().toJSON()")
    page.mouse.move(c['x'] + c['width'] / 2, c['y'] + c['height'] / 2)
    page.mouse.down()
    page.mouse.move(st['x'] + st['width'] / 2 + 2 * m, c['y'] + c['height'] / 2, steps=8)
    ok('při tažení je vidět náhled dvojstrany', J("!document.querySelector('.nahled-dvojstrany.plovouci').hidden"))
    page.mouse.up()
    ok('číslo se přichytí ke středu strany', poloha()[0] == 74, poloha())
    J("navrhStrany.cislo().volby.zmen({ cislo: { ...navrhStrany.nastaveni().cislo, x: 138, y: 205 } })")
    ukaz(page, '[data-nahled-strany="L"]')
    page.click('[data-nahled-strany="L"]')
    ok('na levé straně je zrcadlově u vnějšího okraje', poloha() == [10, 205], poloha())
    ukaz(page, '[data-cislo-pole="zrcadlit"]')
    page.uncheck('[data-cislo-pole="zrcadlit"]')
    ok('bez zrcadlení na stejném místě', poloha() == [138, 205], poloha())
    ukaz(page, '[data-nastaveni="cisloSkryte"]')
    page.check('[data-nastaveni="cisloSkryte"]')
    ok('na jedné straně jde číslo skrýt', J("document.querySelector('.ps-cislo-strany').classList.contains('skryte')"))
    ukaz(page, '[data-nastaveni="cisloSkryte"]')
    page.uncheck('[data-nastaveni="cisloSkryte"]')

    # Záloha v prohlížeči přežije obnovení stránky.
    page.wait_for_timeout(600)
    pocet = len(J('T.akordy()'))
    page.reload()
    page.wait_for_function('window.navrhStrany', timeout=15000)
    page.evaluate(POMOCNE)
    ok('rozpracovaná strana přežije obnovení', len(J('T.akordy()')) == pocet, len(J('T.akordy()')))

    ok('bez chyb v konzoli', not chyby, chyby[:3])
    br.close()


def test_pristup(p, base):
    print("\n── přístup ──")
    br = p.chromium.launch()
    page = br.new_page()
    page.goto(base + '/login')
    page.fill('input[name="email"]', 'user@test.com')
    page.fill('input[name="password"]', T.PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state('networkidle')
    odkazy = page.evaluate("[...document.querySelectorAll('.nav-links a')].map(a => a.textContent.trim())")
    ok('běžný uživatel nemá v liště Editor strany', 'Editor strany' not in odkazy, odkazy)
    odpoved = page.goto(base + '/editor-strany')
    ok('běžnému uživateli stránka neexistuje (404)', odpoved.status == 404, odpoved.status)
    br.close()


def test_akordy(p, base):
    """Transpozice jako čistá funkce - modul se načte přímo v prohlížeči."""
    print("\n── transpozice ──")
    br = p.chromium.launch()
    page = br.new_page()
    page.goto(base + '/login')
    vysledky = page.evaluate("""async () => {
      const { transponuj } = await import('/static/js/strana/akordy.js');
      const t = (a, n, notace = 'cz') => transponuj(a, n, notace);
      return [
        [t('Ami', 2), 'Hmi'], [t('Ami', 1), 'Bmi'], [t('H7', 1), 'C7'], [t('B', 2), 'C'],
        [t('C/G', 2), 'D/A'], [t('Esus4', 1), 'Fsus4'], [t('Es', 2), 'F'], [t('Fis', 1), 'G'],
        [t('Eb', 1), 'E'], [t('Db', -1), 'C'], [t('(Ami)', 3), '(Cmi)'], [t('N.C.', 3), 'N.C.'],
        [t('Cmaj7/E', 1), 'C#maj7/F'], [t('Ebmi7/Db', 2), 'Fmi7/Eb'], [t('Dmi', -3), 'Hmi'],
        [t('Bb', 2, 'en'), 'C'], [t('B', 1, 'en'), 'C'], [t('A', 1, 'en'), 'A#'],
      ];
    }""")
    spatne = [(a, b) for a, b in vysledky if a != b]
    ok(f'transpozice ({len(vysledky)} případů, česky i anglicky)', not spatne, spatne)
    br.close()


def main():
    prohlizece = sys.argv[1:] or ['chromium', 'firefox', 'webkit']
    tmp = Path(tempfile.mkdtemp(prefix='navrh-'))
    db = tmp / 'navrh.db'
    with sqlite3.connect(str(PROJECT_ROOT / 'backend' / 'instance' / 'zpevnik.db')) as zdroj, \
            sqlite3.connect(str(db)) as cil:
        zdroj.backup(cil)
    server, base = T.start_server(db)
    try:
        with sync_playwright() as p:
            test_pristup(p, base)
            test_akordy(p, base)
            for jmeno in prohlizece:
                test_prohlizece(p, base, jmeno)
    finally:
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Editor strany v pořádku.")


if __name__ == '__main__':
    main()
