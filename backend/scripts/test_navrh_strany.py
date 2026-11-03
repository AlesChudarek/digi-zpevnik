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
    const radek = document.querySelectorAll('.ps-radek')[r];
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
    const v = navrhStrany.view, radek = document.querySelectorAll('.ps-radek')[r];
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
  kurzor(pos) {
    const v = navrhStrany.view;
    v.dispatch(v.state.tr.setSelection(navrhStrany.TextSelection.create(v.state.doc, pos))); v.focus();
  },
};
null
"""


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
    m = J('navrhStrany.meritko()')
    tol = 1.2 * m           # do 1,2 CSS px (WebKit má kotvu o zlomek pixelu vedle)

    # Vložení nástrojem Akordy: klik do pravé půlky znaku patří pořád tomu znaku.
    J('navrhStrany.prepniRezimAkordu(true)')
    z = J('T.znak(0, 4)')
    page.mouse.click(z['x'] + z['width'] * 0.75, z['y'] + z['height'] / 2)
    ok('klik na znak otevře pole', J("!document.querySelector('.akord-pole').hidden"))
    page.keyboard.type('Ami')
    page.keyboard.press('Enter')
    a = J('T.akordy()')
    ok('akord patří ke kliknutému znaku', len(a) == 1 and a[0]['n'] == 'Ami' and a[0]['znak'] == 'n', a)
    r, z = J('T.akord(0)'), J('T.znak(0, 4)')
    ok('akord sedí nad svým znakem', abs(r['x'] - z['x']) < tol, round(r['x'] - z['x'], 2))
    ok('akord je nad řádkem, ne přes text', r['y'] + r['height'] <= z['y'] + 1,
       round(z['y'] - (r['y'] + r['height']), 2))

    # Druhý akord hned vedle: s automatickou mezerou se nepřekrývají a každý je nad svým.
    z = J('T.znak(0, 5)')
    page.mouse.click(z['x'] + 1, z['y'] + z['height'] / 2)
    page.keyboard.type('C#mi7')
    page.keyboard.press('Enter')
    ok('stránka po akordu s # odpovídá', zije(page))
    page.wait_for_timeout(150)
    r0, r1, z = J('T.akord(0)'), J('T.akord(1)'), J('T.znak(0, 5)')
    ok('akordy přes sebe odsunou text', r1['x'] >= r0['x'] + r0['width'], round(r1['x'] - r0['x'] - r0['width'], 2))
    ok('odsunutý akord je pořád nad svým znakem', abs(r1['x'] - z['x']) < tol, round(r1['x'] - z['x'], 2))
    page.uncheck('[data-nastaveni="automatickaMezera"]')
    page.wait_for_timeout(150)
    ok('bez mezery se kolize jen označí', J("document.querySelectorAll('.akord-kotva.kolize').length") == 2)
    page.check('[data-nastaveni="automatickaMezera"]')

    # Psaní před akordem a změna písma: akord drží svůj znak.
    J('navrhStrany.prepniRezimAkordu(false)')
    J('T.kurzor(T.pozice(0, 0))')
    page.keyboard.type('Xx ')
    a = J('T.akordy()')
    ok('psaní na začátku řádku akordy nepřesune', [x['znak'] for x in a] == ['n', 'a'], a)
    page.fill('[data-nastaveni="velikost"]', '15')
    page.wait_for_timeout(200)
    r, z = J('T.akord(0)'), J('T.znak(0, 7)')
    ok('po zvětšení písma je akord nad svým znakem', abs(r['x'] - z['x']) < tol, round(r['x'] - z['x'], 2))

    # Psaní hned za akordem (kurzor mezi akordem a znakem) nechá akord u původního písmene.
    J("T.kurzor(T.akordy()[0].pos + 1)")
    page.keyboard.type('Q')
    a = J('T.akordy()')
    ok('psaní za akordem jde před něj', a[0]['znak'] == 'n', a)
    page.keyboard.press(f'{MOD}+z')

    # Transpozice.
    page.click('[data-prikaz="transp-nahoru"]')
    page.click('[data-prikaz="transp-nahoru"]')
    ok('transpozice +2', [x['n'] for x in J('T.akordy()')] == ['Hmi', 'D#mi7'], J('T.akordy()'))
    page.wait_for_timeout(600)       # ať se další úprava neslije do jednoho kroku Zpět

    # Tažení: přichytí se ke znaku pod sebou; Zpět vrátí celé tažení.
    J('T.kurzor(1)')
    page.uncheck('[data-nastaveni="automatickaMezera"]')
    page.wait_for_timeout(100)
    pred = J('T.akordy()')[0]
    r, zs, zc = J('T.akord(0)'), J('T.znak(0, 7)'), J('T.znak(0, 11)')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 3 + (zc['x'] - zs['x']), r['y'] + 3, steps=6)
    ok('při tažení je zvýrazněný budoucí znak', J("[...document.querySelectorAll('.kotva-znak')].map(e => e.textContent).join('')") == 'š')
    ok('při tažení je vidět spojnice', J("document.querySelector('.spojnice').classList.contains('viditelna')"))
    page.mouse.up()
    page.wait_for_timeout(100)
    a = J('T.akordy()')
    ok('puštěný akord patří ke znaku pod sebou', a[1]['znak'] == 'š' and a[1]['n'] == pred['n'], a)
    r, z = J('T.akord(1)'), J('T.znak(0, 11)')
    ok('a zůstal tam, kam byl puštěn', abs(r['x'] - z['x']) < tol, round(r['x'] - z['x'], 2))
    page.keyboard.press(f'{MOD}+z')
    a = J('T.akordy()')
    ok('Zpět vrátí tažení jedním krokem', a[0]['n'] == pred['n'] and a[0]['znak'] == 'n' and a[0]['dx'] == 0, a)

    # Alt: kotva zůstane, uloží se jen posun.
    r = J('T.akord(0)')
    page.keyboard.down('Alt')
    page.mouse.move(r['x'] + 3, r['y'] + 3)
    page.mouse.down()
    page.mouse.move(r['x'] + 15, r['y'] - 6, steps=5)
    page.mouse.up()
    page.keyboard.up('Alt')
    a = J('T.akordy()')[0]
    ok('s Altem se akord jen posune', a['znak'] == 'n' and a['dx'] > 0 and a['dy'] < 0, a)
    page.check('[data-nastaveni="automatickaMezera"]')

    # Smazání kotevního znaku: akord přejde na další znak a blikne.
    zn = J('T.akordy()')
    za_n = J(f"T.akordy()[0].pos") + 2
    J(f'T.kurzor({za_n})')
    page.keyboard.press('Backspace')
    a = J('T.akordy()')
    ok('po smazání znaku patří akord k dalšímu', len(a) == len(zn) and a[0]['znak'] in ('a', ''), a)
    ok('a blikne', J("document.querySelectorAll('.akord-kotva.blik').length") >= 1)

    # Klávesa [ vloží akord u kurzoru.
    J('T.kurzor(T.pozice(1, 2))')
    page.keyboard.type('[')
    page.keyboard.type('G')
    page.keyboard.press('Enter')
    ok('[ vloží akord u kurzoru', any(x['n'] == 'G' and x['znak'] == 'd' for x in J('T.akordy()')), J('T.akordy()'))

    # Vložení textu se závorkami a prázdnými řádky.
    J("T.kurzor(navrhStrany.view.state.doc.content.size - 2)")
    # pasteText jde stejnou cestou jako vložení ze schránky (clipboardTextParser); umělou
    # událost paste s daty Firefox nepodporuje.
    J("navrhStrany.view.pasteText('\\n\\n[C]Ahoj [G]světe\\n[Ami]druhý řádek\\n\\nR: [F]refrén')")
    druhy = J("[...document.querySelectorAll('.ps-sloka')].map(s => s.dataset.druh)")
    ok('vložený text: sloky a refrén', druhy[-2:] == ['sloka', 'refren'], druhy)
    nazvy = [(x['n'], x['znak']) for x in J('T.akordy()')]
    ok('vložený text: akordy u znaků', all(x in nazvy for x in [('C', 'A'), ('G', 's'), ('Ami', 'd'), ('F', 'r')]), nazvy)

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
