"""Ověří tlačítko Stáhnout ve čtečce i v seznamu: od kliknutí až po stažený soubor.

Backend hlídá test_export.py. Tenhle skript kontroluje to, co se dá zjistit jen
v prohlížeči: že se nabídka vysune a vejde na obrazovku, že okno se skládáním naskočí,
že se stahování opravdu spustí, a hlavně že poll doběhne až k hotovému souboru - ta cesta
má několik stavů a z kódu se nepozná, jestli se v nich neztratí.

Měří se i rozvržení okna. Okno bývalo uvnitř .mode-buttons a dědilo odtud kulaté 60px
tlačítko a `pointer-events: none`; z kódu to vidět nebylo, z naměřeného rámečku ano.

Vyžaduje playwright (viz measure_reader.py). Běží proti KOPII databáze.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
EXPORTS_DIR = PROJECT_ROOT / "data" / "exports"
PORT = 5583
PASSWORD = "export-ui"
BOOK = "00006"

selhani = []


def jas(css_barva):
    """Světlost barvy z computed style, 0 (černá) az 1 (bílá)."""
    cisla = [float(c) for c in re.findall(r"[\d.]+", css_barva)[:3]]
    if len(cisla) < 3:
        return None
    r, g, b = [c / 255 for c in cisla]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + detail) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "export-ui"
    env["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'backend'}"
    subprocess.run(
        [str(VENV_PY), "-c",
         'import os, sys\n'
         'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
         'from backend.app import app, db, User\n'
         'from werkzeug.security import generate_password_hash\n'
         'with app.app_context():\n'
         '    u = User.query.filter_by(email="admin@test.com").first()\n'
         f'    u.password = generate_password_hash("{PASSWORD}", method="pbkdf2:sha256")\n'
         '    db.session.commit()\n'],
        env=env, check=True, capture_output=True)
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "flask", "--app", "backend.app", "run",
         "--port", str(PORT), "--no-reload"],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(160):
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            return proc, base
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("❌ server se nerozjel")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright")

    tmp = Path(tempfile.mkdtemp(prefix="export-ui-"))
    db_copy = tmp / "ui.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)
    if EXPORTS_DIR.exists():
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

    server, base = start_server(db_copy)
    try:
        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                      accept_downloads=True)
            page = ctx.new_page()
            chyby = []
            page.on("pageerror", lambda e: chyby.append(str(e)))

            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")
            page.goto(base + f"/songbook/{BOOK}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

            print("\n── okno s nastavením ──")
            zkontroluj(page.evaluate("() => !document.getElementById('stahovani-okno')"),
                       "okno neexistuje, dokud se neklikne")

            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(1200)
            zaklad = page.evaluate("""() => {
              const o = document.getElementById('stahovani-okno');
              const p = o.querySelector('.stahovani-panel').getBoundingClientRect();
              return {otevrene: !o.hidden, rodic: o.parentElement.tagName,
                      nazev: document.getElementById('stahovani-co').textContent,
                      predvoleb: o.querySelectorAll('input[name=predvolba]').length,
                      vybrano: o.querySelector('input[name=predvolba]:checked').value,
                      vlastni_zasedle: document.getElementById('stahovani-vlastni').disabled,
                      panel: [Math.round(p.width), Math.round(p.height)]};
            }""")
            zkontroluj(zaklad["otevrene"], "klik otevře okno rovnou s nastavením")
            zkontroluj(zaklad["rodic"] == "BODY",
                       "okno visí na body, ne uvnitř sloupce tlačítek čtečky",
                       zaklad["rodic"])
            zkontroluj(bool(zaklad["nazev"]), "ukáže, o který zpěvník jde", zaklad["nazev"])
            zkontroluj(zaklad["predvoleb"] == 4,
                       "tři předvolby a vlastní nastavení", str(zaklad["predvoleb"]))
            zkontroluj(zaklad["vybrano"] == "small",
                       "předvolba je vybraná předem, ať běžné stažení zůstane na dvě kliknutí",
                       zaklad["vybrano"])
            zkontroluj(zaklad["vlastni_zasedle"],
                       "vlastní volby jsou zašedlé, dokud se nezvolí vlastní nastavení")

            # Past, na kterou tenhle projekt naráží popáté: vlastní `display` přebíjí
            # atribut `hidden`, takže značka svítila u všech variant.
            znacky = page.evaluate("""async () => {
              const h = await (await fetch(`/songbook/BOOK_ID/export-hotove`)).json();
              return [...document.querySelectorAll('.hotovo-znak[data-varianta]')].map(z => ({
                v: z.dataset.varianta, cekame: !!h[z.dataset.varianta],
                videt: z.getBoundingClientRect().width > 0,
                display: getComputedStyle(z).display}));
            }""".replace("BOOK_ID", BOOK))
            for z in znacky:
                zkontroluj(z["videt"] == z["cekame"],
                           f"{z['v']}: značka {'svítí' if z['cekame'] else 'nesvítí'}",
                           f"vidět={z['videt']} display={z['display']}")

            print("\n── dvě tlačítka nesmí splývat ──")
            tlacitka = page.evaluate("""() => {
              const z = document.getElementById('stahovani-zavrit');
              const s = document.getElementById('stahovani-spustit');
              const rz = z.getBoundingClientRect(), rs = s.getBoundingClientRect();
              const cz = getComputedStyle(z), cs = getComputedStyle(s);
              return {mezera: Math.round(rs.left - rz.right),
                      stejna_barva: cz.backgroundColor === cs.backgroundColor,
                      hlavni_vetsi: parseFloat(cs.fontSize) >= parseFloat(cz.fontSize),
                      hlavni_tucne: parseInt(cs.fontWeight, 10) > parseInt(cz.fontWeight, 10)};
            }""")
            zkontroluj(tlacitka["mezera"] >= 8,
                       "mezi Zavřít a Stáhnout je mezera", f"{tlacitka['mezera']} px")
            zkontroluj(not tlacitka["stejna_barva"],
                       "a nemají stejnou barvu pozadí")
            zkontroluj(tlacitka["hlavni_vetsi"] and tlacitka["hlavni_tucne"],
                       "hlavní akce je výraznější, ne stejná jako Zavřít")

            print("\n── vlastní nastavení ──")
            page.evaluate("() => document.querySelector('input[name=predvolba][value=vlastni]').click()")
            page.wait_for_timeout(800)
            zkontroluj(not page.evaluate(
                "() => document.getElementById('stahovani-vlastni').disabled"),
                "výběr vlastního nastavení volby zpřístupní")
            souhrn = page.evaluate("() => document.getElementById('stahovani-souhrn').textContent")
            zkontroluj("stran" in souhrn or "strany" in souhrn or "stranu" in souhrn,
                       "a rovnou řekne, na kolik stran to vyjde", souhrn)

            usporadani = page.evaluate("""() => {
              const radky = [...document.querySelectorAll('#stahovani-vlastni .stahovani-radek')];
              return {poradi: radky.map(r => r.dataset.pole),
                      zalomene: radky.filter(r => {
                        const v = r.querySelector('.stahovani-volby');
                        return v && v.scrollHeight > 30;
                      }).map(r => r.dataset.pole)};
            }""")
            zkontroluj(usporadani["poradi"][0] == "format",
                       "formát je první, protože jeho volba schovává řádky pod ním",
                       ", ".join(usporadani["poradi"]))
            zkontroluj(not usporadani["zalomene"],
                       "žádný řádek voleb se neláme na dva",
                       ", ".join(usporadani["zalomene"]))

            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=jen-obalka]').click()")
            page.wait_for_timeout(900)
            jen_obalka = page.evaluate("""() => ({
              souhrn: document.getElementById('stahovani-souhrn').textContent,
              tlacitko: document.getElementById('stahovani-spustit').textContent})""")
            zkontroluj("4" in jen_obalka["souhrn"],
                       "jen obálka vyjde na čtyři strany", jen_obalka["souhrn"])
            zkontroluj(jen_obalka["tlacitko"] == "Připravit",
                       "co není v cache, se nabízí jako Připravit", jen_obalka["tlacitko"])

            print("\n── tlačítko neprobliká ──")
            # Odpověď serveru přijde až za okamžik. Kdyby bylo výchozí „Stáhnout",
            # probliklo by při každé změně nastavení něco, co ještě nikdo nevěděl.
            page.evaluate("() => document.getElementById('stahovani-brozura').click()")
            behem = []
            for _ in range(10):
                behem.append(page.evaluate(
                    "() => document.getElementById('stahovani-spustit').textContent"))
                page.wait_for_timeout(60)
            zkontroluj(all(t == "Připravit" for t in behem),
                       "po změně nastavení tlačítko neblikne na Stáhnout",
                       ", ".join(sorted(set(behem))))
            page.wait_for_timeout(800)
            souhrn_brozura = page.evaluate(
                "() => document.getElementById('stahovani-souhrn').textContent")
            zkontroluj("listů" in souhrn_brozura or "listy" in souhrn_brozura,
                       "u brožury se píše i počet listů papíru", souhrn_brozura)
            page.evaluate("() => document.getElementById('stahovani-brozura').click()")
            page.wait_for_timeout(800)

            print("\n── rozsah stran ──")
            # Zpátky na celý zpěvník: kdo si výslovně zvolil „jen obálku", o ni přijít
            # nemá, takže se rozsah do volby obsahu plete jen z výchozího stavu.
            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=vse]').click()")
            page.wait_for_timeout(700)
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '3-4';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(1000)
            rozsah = page.evaluate("""() => ({
              souhrn: document.getElementById('stahovani-souhrn').textContent,
              obsah: document.querySelector('input[name=obsah]:checked').value})""")
            zkontroluj(rozsah["obsah"] == "jen-obsah",
                       "vyplnění rozsahu odklikne obálku, ať nevyjde víc stran, než kdo zadal",
                       rozsah["obsah"])
            zkontroluj("2 strany" in rozsah["souhrn"],
                       "rozsah 3-4 vyjde na dvě strany", rozsah["souhrn"])
            pisne = page.evaluate("""() => ({
              videt: !document.getElementById('stahovani-pisne').hidden,
              popisek: document.getElementById('stahovani-pisne-prepinac').textContent,
              seznam_skryty: document.getElementById('stahovani-pisne-seznam').hidden})""")
            zkontroluj(pisne["videt"] and "2" in pisne["popisek"],
                       "sekce s písněmi řekne, kolik jich ve výběru je", str(pisne))
            zkontroluj(pisne["seznam_skryty"],
                       "ale seznam se sám nerozbalí, u dlouhého zpěvníku by zabral celé okno")
            page.evaluate("() => document.getElementById('stahovani-pisne-prepinac').click()")
            page.wait_for_timeout(300)
            zkontroluj(page.evaluate(
                "() => document.querySelectorAll('#stahovani-pisne-seznam li').length") == 2,
                "klik seznam rozbalí")
            page.evaluate("() => document.getElementById('stahovani-pisne-prepinac').click()")

            # Ale jen z výchozího stavu. Kdo si obálku vrátí, o ni znovu nepřijde.
            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=vse]').click()")
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '3-5';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(900)
            zkontroluj(page.evaluate(
                       "() => document.querySelector('input[name=obsah]:checked').value")
                       == "vse",
                       "ale výslovnou volbu obsahu už nepřepíše")

            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '31-24';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(1000)
            chybny = page.evaluate("""() => ({
              souhrn: document.getElementById('stahovani-souhrn').textContent,
              cervene: document.getElementById('stahovani-souhrn').classList.contains('stahovani-chyba'),
              vypnute: document.getElementById('stahovani-spustit').disabled})""")
            zkontroluj(chybny["cervene"] and chybny["vypnute"],
                       "chybný rozsah se ukáže červeně a tlačítko se vypne",
                       chybny["souhrn"])
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(900)

            # ZIP balí originály, takže kvalita, barvy ani brožura pro něj nic neznamenají.
            page.evaluate("() => document.querySelector('input[name=format][value=zip]').click()")
            page.wait_for_timeout(600)
            u_zipu = page.evaluate("""() => {
              const stav = p => {
                const r = document.querySelector(`[data-pole="${p}"]`);
                return {videt: r.getBoundingClientRect().height > 0,
                        zamcene: r.classList.contains('zamcene')};
              };
              return {kvalita: stav('kvalita'), barvy: stav('barvy'),
                      brozura: stav('brozura'), strany: stav('strany')};
            }""")
            zkontroluj(all(u_zipu[p]["zamcene"] for p in ('kvalita', 'barvy', 'brozura')),
                       "u ZIPu se kvalita, barvy i brožura zamknou, protože by nic neudělaly",
                       str(u_zipu))
            zkontroluj(all(u_zipu[p]["videt"] for p in ('kvalita', 'barvy', 'brozura')),
                       "ale zůstanou na místě, ať pod rukou nenadskakuje celé okno")
            zkontroluj(not u_zipu["strany"]["zamcene"],
                       "rozsah stran zamčený není, ten u ZIPu funguje")
            page.hover('[data-pole=kvalita]')
            page.wait_for_timeout(400)
            zkontroluj("ZIP" in page.evaluate(
                "() => document.getElementById('stahovani-napoveda').textContent"),
                "a najetí na zamčený řádek řekne proč",
                page.evaluate("() => document.getElementById('stahovani-napoveda').textContent"))
            page.evaluate("() => document.querySelector('input[name=format][value=pdf]').click()")
            page.wait_for_timeout(300)

            print("\n── otevřený rozsah ──")
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '3-';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(1000)
            otevreny = page.evaluate(
                "() => document.getElementById('stahovani-souhrn').textContent")
            zkontroluj("stran" in otevreny and "nevyjde" not in otevreny,
                       "„3-“ znamená od třetí strany dál", otevreny)
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(800)

            print("\n── žádný řádek voleb se neláme ──")
            radky = page.evaluate("""() => [...document.querySelectorAll(
                '#stahovani-vlastni .stahovani-radek')].map(r => ({
                  pole: r.dataset.pole,
                  vyska: Math.round(r.querySelector('.stahovani-volby').scrollHeight),
                  popisek: Math.round(
                      r.querySelector('.stahovani-popisek').getBoundingClientRect().height)}))""")
            for r in radky:
                zkontroluj(r["vyska"] <= 32,
                           f"{r['pole']}: volby se vejdou na jeden řádek", f"{r['vyska']} px")
                zkontroluj(r["popisek"] <= 20,
                           f"{r['pole']}: ikonka nápovědy nespadla pod popisek",
                           f"{r['popisek']} px")

            print("\n── jen obálka zamkne rozsah stran ──")
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '3-4';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(700)
            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=jen-obalka]').click()")
            page.wait_for_timeout(900)
            zamek = page.evaluate("""() => {
              const r = document.querySelector('[data-pole=strany]');
              return {videt: r.getBoundingClientRect().height > 0,
                      zamcene: r.classList.contains('zamcene'),
                      vypnute: [...r.querySelectorAll('input')].every(i => i.disabled),
                      duvod: r.dataset.duvod || ''};
            }""")
            zkontroluj(zamek["videt"] and zamek["zamcene"] and zamek["vypnute"],
                       "u jen obálky se rozsah stran zamkne, ale zůstane na místě",
                       str(zamek))
            zkontroluj("obálka" in zamek["duvod"].lower(),
                       "a nese důvod, který se ukáže na najetí", zamek["duvod"])
            zkontroluj("4 strany" in page.evaluate(
                "() => document.getElementById('stahovani-souhrn').textContent"),
                "a zbylý text v poli už výsledek neovlivní",
                page.evaluate("() => document.getElementById('stahovani-souhrn').textContent"))
            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=vse]').click()")
            page.evaluate("() => { const e = document.getElementById('stahovani-strany');"
                          "        e.value = '';"
                          "        e.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(700)

            print("\n── varování u rizikových kombinací ──")
            page.evaluate("() => document.getElementById('stahovani-bez-prazdnych').click()")
            page.wait_for_timeout(600)
            zkontroluj("číslování" in page.evaluate(
                "() => document.getElementById('stahovani-varovani').textContent"),
                "vynechání prázdných stran upozorní na číslování")
            page.evaluate("() => document.getElementById('stahovani-brozura').click()")
            page.wait_for_timeout(600)
            zkontroluj("brožur" in page.evaluate(
                "() => document.getElementById('stahovani-varovani').textContent"),
                "a s brožurou upozorní na to, že se nemusí složit, jak člověk čeká",
                page.evaluate("() => document.getElementById('stahovani-varovani').textContent"))
            page.evaluate("() => document.getElementById('stahovani-brozura').click()")
            page.evaluate("() => document.getElementById('stahovani-bez-prazdnych').click()")
            page.wait_for_timeout(600)
            zkontroluj(page.evaluate(
                "() => document.getElementById('stahovani-varovani').hidden"),
                "a bez rizikové kombinace se varování schová")

            print("\n── nápověda ──")
            vyska_pred = page.evaluate(
                "() => Math.round(document.querySelector('.stahovani-panel')"
                ".getBoundingClientRect().height)")
            page.hover('.napoveda-znak[data-napoveda=prazdne]')
            page.wait_for_timeout(400)
            napoveda = page.evaluate("""() => {
              const n = document.getElementById('stahovani-napoveda');
              if (!n || n.hidden) return null;
              const r = n.getBoundingClientRect();
              return {text: n.textContent, sirka: Math.round(r.width),
                      vyska: Math.round(r.height),
                      v_obrazovce: r.left >= 0 && r.right <= window.innerWidth &&
                                   r.top >= 0 && r.bottom <= window.innerHeight,
                      panel: Math.round(document.querySelector('.stahovani-panel')
                                        .getBoundingClientRect().height)};
            }""")
            zkontroluj(napoveda is not None, "najetí na ikonku i ukáže nápovědu")
            if napoveda:
                zkontroluj("číslování" in napoveda["text"] and "vytisknout" in napoveda["text"],
                           "a u prázdných stran varuje před číslováním i tiskem")
                # Nápověda vsunutá do toku roztahovala okno pokaždé, když si ji někdo
                # otevřel, a zůstávala viset, dokud ji člověk netrefil znovu.
                zkontroluj(napoveda["panel"] == vyska_pred,
                           "a nemění výšku okna",
                           f"{vyska_pred} -> {napoveda['panel']} px")
                zkontroluj(napoveda["v_obrazovce"], "vejde se do obrazovky",
                           f"{napoveda['sirka']}x{napoveda['vyska']} px")
            page.hover('#stahovani-nadpis')
            page.wait_for_timeout(400)
            zkontroluj(page.evaluate(
                "() => document.getElementById('stahovani-napoveda').hidden"),
                "a po odjetí myši sama zmizí")

            print("\n── okno se vejde i na nízkou obrazovku ──")
            # S rozbalenou nápovědou okno vyroste. Na telefonu na šířku je výšky málo
            # a tlačítko, kterým se to potvrzuje, nesmí skončit pod okrajem.
            for sirka, vyska in ((1280, 800), (820, 500), (390, 844), (740, 360)):
                page.set_viewport_size({"width": sirka, "height": vyska})
                page.wait_for_timeout(250)
                v = page.evaluate("""() => {
                  const p = document.querySelector('.stahovani-panel').getBoundingClientRect();
                  const t = document.getElementById('stahovani-spustit').getBoundingClientRect();
                  return {v_obrazovce: p.top >= -1 && p.bottom <= window.innerHeight + 1,
                          tlacitko: t.bottom <= window.innerHeight + 1 && t.top >= -1,
                          pretece: document.documentElement.scrollWidth - window.innerWidth};
                }""")
                zkontroluj(v["v_obrazovce"] and v["tlacitko"] and v["pretece"] <= 0,
                           f"{sirka}x{vyska}: panel i tlačítko v obrazovce",
                           f"panel {v['v_obrazovce']}, tlačítko {v['tlacitko']}, "
                           f"přetečení {v['pretece']}px")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(200)

            print("\n── stažení předvolbou ──")
            page.evaluate("() => document.querySelector('input[name=predvolba][value=small]').click()")
            page.wait_for_timeout(400)
            with page.expect_download(timeout=300000) as info:
                page.evaluate("() => document.getElementById('stahovani-spustit').click()")
                page.wait_for_timeout(600)
                zkontroluj(page.url.endswith(f"/songbook/{BOOK}"),
                           "stránka zůstane na čtečce, neodnaviguje na odpověď serveru",
                           page.url)
                stav = page.evaluate("""() => {
                  const o = document.getElementById('stahovani-okno');
                  if (o.hidden) return {hotovo_hned: true};
                  return {hotovo_hned: false,
                          nadpis: document.getElementById('stahovani-nadpis').textContent,
                          postup_videt: !document.getElementById('stahovani-postup').hidden,
                          nastaveni_skryte: document.getElementById('stahovani-nastaveni').hidden};
                }""")
                if not stav["hotovo_hned"]:
                    zkontroluj(stav["postup_videt"] and stav["nastaveni_skryte"],
                               "okno se překlopí z nastavení do průběhu", str(stav))
            stazeny = info.value
            cesta = Path(stazeny.path())
            zkontroluj(stazeny.suggested_filename.endswith(".pdf"),
                       "stáhne se PDF", stazeny.suggested_filename)
            zkontroluj(cesta.read_bytes()[:5] == b"%PDF-", "a je to platné PDF",
                       f"{cesta.stat().st_size // 1024} kB")

            print("\n── stažení vlastním nastavením ──")
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(900)
            page.evaluate("() => document.querySelector('input[name=predvolba][value=vlastni]').click()")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelector("
                          "     'input[name=obsah][value=jen-obalka]').click()")
            page.wait_for_timeout(900)
            with page.expect_download(timeout=300000) as info2:
                page.evaluate("() => document.getElementById('stahovani-spustit').click()")
                page.wait_for_timeout(1500)
            vlastni = info2.value
            data = Path(vlastni.path()).read_bytes()
            zkontroluj(data[:5] == b"%PDF-", "vlastní nastavení se taky stáhne",
                       vlastni.suggested_filename)
            import re as _re
            stran_v_pdf = [int(m) for m in _re.findall(rb"/Count\s+(\d+)", data)]
            zkontroluj(bool(stran_v_pdf) and stran_v_pdf[-1] == 4,
                       "a má jen čtyři strany obálky, ne celý zpěvník",
                       f"stran {stran_v_pdf[-1] if stran_v_pdf else '?'}")

            page.wait_for_timeout(2000)
            if not page.evaluate("() => document.getElementById('stahovani-okno').hidden"):
                zkontroluj(page.evaluate(
                    "() => document.getElementById('stahovani-nadpis').textContent") == "Dokončeno",
                    "okno skončí na Dokončeno a nezavře se samo")
                page.evaluate("() => document.getElementById('stahovani-zavrit').click()")
                page.wait_for_timeout(250)
                zkontroluj(page.evaluate("() => document.getElementById('stahovani-okno').hidden"),
                           "a Zavřít ho zavře")

                # Na stavu Dokončeno je zvýrazněné Zavřít. Po znovuotevření mělo to
                # zvýraznění zůstat oběma tlačítkům a nebylo poznat, které je hlavní.
                page.evaluate("() => document.getElementById('download-toggle').click()")
                page.wait_for_timeout(700)
                zkontroluj(not page.evaluate("""() => {
                  const z = document.getElementById('stahovani-zavrit');
                  const s = document.getElementById('stahovani-spustit');
                  return getComputedStyle(z).backgroundColor ===
                         getComputedStyle(s).backgroundColor;
                }"""), "a po znovuotevření nesplynou ani po dokončeném stažení")

            print("\n── motiv ──")
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(500)

            def barvy_panelu():
                return page.evaluate("""() => {
                  const cs = getComputedStyle(document.querySelector('.stahovani-panel'));
                  return {panel: cs.backgroundColor, text: cs.color};
                }""")

            svetly = barvy_panelu()
            page.evaluate("() => setDzTheme('dark')")
            page.wait_for_timeout(250)
            tmavy = barvy_panelu()
            zkontroluj(jas(svetly["panel"]) > 0.8,
                       "ve světlém motivu je panel světlý", svetly["panel"])
            zkontroluj(jas(tmavy["panel"]) < 0.25,
                       "v tmavém motivu je panel tmavý", tmavy["panel"])
            zkontroluj(jas(tmavy["text"]) - jas(tmavy["panel"]) > 0.5,
                       "s dostatečným kontrastem",
                       f"text {jas(tmavy['text']):.2f} vs panel {jas(tmavy['panel']):.2f}")
            page.evaluate("() => setDzTheme('blue')")
            page.wait_for_timeout(250)

            page.mouse.click(20, 20)
            page.wait_for_timeout(250)
            zkontroluj(page.evaluate("() => document.getElementById('stahovani-okno').hidden"),
                       "klik na záclonu vedle panelu okno zavře")

            print("\n── totéž okno v Mých zpěvnících ──")
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            pocet = page.evaluate("() => document.querySelectorAll('.btn-download').length")
            zkontroluj(pocet > 0, "u dlaždic je tlačítko na stažení", f"{pocet} tlačítek")
            if pocet:
                page.evaluate("() => document.querySelector('.btn-download').click()")
                page.wait_for_timeout(1200)
                v_seznamu = page.evaluate("""() => {
                  const o = document.getElementById('stahovani-okno');
                  return {otevrene: !!o && !o.hidden,
                          nazev: document.getElementById('stahovani-co').textContent,
                          predvoleb: o ? o.querySelectorAll('input[name=predvolba]').length : 0};
                }""")
                zkontroluj(v_seznamu["otevrene"] and v_seznamu["predvoleb"] == 4,
                           "otevře se stejné okno jako ve čtečce", str(v_seznamu))
                zkontroluj(bool(v_seznamu["nazev"]),
                           "a ví, o který zpěvník jde", v_seznamu["nazev"])
                with page.expect_download(timeout=300000) as info3:
                    page.evaluate("() => document.getElementById('stahovani-spustit').click()")
                    page.wait_for_timeout(800)
                ze_seznamu = info3.value
                zkontroluj(Path(ze_seznamu.path()).read_bytes()[:5] == b"%PDF-",
                           "a stáhne se PDF", ze_seznamu.suggested_filename)

            print("\n── chyby v konzoli ──")
            zkontroluj(not chyby, "žádná chyba JavaScriptu", "; ".join(chyby[:3]))

            ctx.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    if selhani:
        print(f"\n❌ neprošlo {len(selhani)} kontrol:")
        for s in selhani:
            print(f"   - {s}")
        return 1
    print("\n✅ všechny kontroly prošly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
