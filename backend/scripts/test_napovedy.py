"""Ověří sjednocené nápovědy: že se ukážou, kde mají, a že je nic neořízne.

Nápovědy byly čtyři různé implementace (`.tooltip` s `data-tooltip`, `.tooltip-text`
ve čtečce, plovoucí `#toc-fly-tooltip` v obsahu a `.napoveda-znak` v okně stahování).
Teď je jedna, static/js/napoveda.js. Z kódu se nepozná, jestli se bublina opravdu
ukáže a vejde na obrazovku - proto se to měří tady.

Hlídá se hlavně to, co staré řešení neumělo: bublina v `::after` se ořízla o první
nadřazený `overflow: hidden` (v tabulce hledání to dělalo 66 px neviditelného sloupce)
a na dotykové obrazovce se neukázala vůbec. A past, na kterou tenhle projekt naráží
popáté: vlastní `display:` přebíjí atribut `hidden`.

Vyžaduje playwright. Běží proti KOPII databáze.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
PORT = 5584
PASSWORD = "napovedy-test"
BOOK = "00006"

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + detail) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "napovedy"
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


# Stav bubliny tak, jak ji vidí uživatel: rozměr, text a jestli se vejde na obrazovku.
STAV = """() => {
  const b = document.querySelector('.napoveda-bublina');
  if (!b) return {existuje: false};
  const r = b.getBoundingClientRect();
  return {existuje: true, skryta: b.hidden,
          display: getComputedStyle(b).display,
          text: b.textContent.trim(),
          ram: [Math.round(r.left), Math.round(r.top),
                Math.round(r.width), Math.round(r.height)],
          vejde: r.left >= 0 && r.top >= 0
                 && r.right <= window.innerWidth && r.bottom <= window.innerHeight,
          nahore: b.parentElement.tagName};
}"""


def najed(page, selektor):
    """Najetí myší a chvilka na dokreslení."""
    page.hover(selektor)
    page.wait_for_timeout(200)


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright")

    tmp = Path(tempfile.mkdtemp(prefix="napovedy-"))
    db_copy = tmp / "napovedy.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)

    server, base = start_server(db_copy)
    try:
        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            chyby = []
            page.on("pageerror", lambda e: chyby.append(str(e)))

            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            print("\n── stará řešení jsou pryč ──")
            for cesta, jmeno in [("/my-songbooks", "Moje zpěvníky"),
                                 (f"/songbook/{BOOK}", "čtečka"),
                                 ("/search", "hledání")]:
                page.goto(base + cesta)
                page.wait_for_load_state("networkidle")
                zbytky = page.evaluate("""() => ({
                  data_tooltip: document.querySelectorAll('[data-tooltip]').length,
                  tooltip_text: document.querySelectorAll('.tooltip-text').length,
                  toc_fly: document.querySelectorAll('#toc-fly-tooltip').length,
                  ma_modul: typeof window.Napoveda === 'object'})""")
                zkontroluj(zbytky["data_tooltip"] == 0 and zbytky["tooltip_text"] == 0
                           and zbytky["toc_fly"] == 0,
                           f"{jmeno}: žádný pozůstatek starých tooltipů", str(zbytky))
                zkontroluj(zbytky["ma_modul"], f"{jmeno}: sdílený modul je načtený")

            print("\n── čtečka: nápověda u ikon ──")
            page.goto(base + f"/songbook/{BOOK}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            zkontroluj(page.evaluate(
                "() => !document.querySelector('.napoveda-bublina')"),
                "bublina neexistuje, dokud není potřeba")

            najed(page, "#mode-toggle-button")
            stav = page.evaluate(STAV)
            zkontroluj(stav["existuje"] and not stav["skryta"],
                       "najetí na přepínač módu nápovědu ukáže", str(stav.get("text")))
            zkontroluj(stav.get("nahore") == "BODY",
                       "bublina visí na body, takže ji nic neořízne",
                       str(stav.get("nahore")))
            zkontroluj(stav.get("vejde"), "a vejde se na obrazovku", str(stav.get("ram")))
            sirka = stav["ram"][2]
            zkontroluj(sirka < 200,
                       "krátký text zůstane úzký, nezalomí se na šířku bubliny",
                       f"{sirka} px")

            page.hover("#page-input")
            page.wait_for_timeout(300)
            zkontroluj(page.evaluate(STAV)["skryta"], "odjetí myši ji schová")

            print("\n── text, který se mění za běhu ──")
            pred = page.evaluate(
                "() => document.getElementById('mode-toggle-button').dataset.napoveda")
            page.evaluate("() => toggleReadingMode()")
            page.wait_for_timeout(1200)
            po = page.evaluate(
                "() => document.getElementById('mode-toggle-button').dataset.napoveda")
            zkontroluj(pred != po and bool(po),
                       "přepnutí módu přepíše i nápovědu tlačítka", f"{pred} → {po}")

            print("\n── past s atributem hidden ──")
            page.evaluate("() => Napoveda.skryj()")
            page.wait_for_timeout(100)
            po_skryti = page.evaluate(STAV)
            zkontroluj(po_skryti["skryta"] and po_skryti["display"] == "none",
                       "skrytá bublina má opravdu display: none, ne jen atribut hidden",
                       po_skryti["display"])
            zkontroluj(po_skryti["ram"][2] == 0 and po_skryti["ram"][3] == 0,
                       "a nulový rozměr, takže nepohlcuje kliknutí",
                       str(po_skryti["ram"]))

            print("\n── okno stahování: ⓘ u slovních voleb ──")
            page.goto(base + f"/songbook/{BOOK}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(1000)
            page.evaluate(
                "() => document.querySelector('input[name=predvolba][value=vlastni]').click()")
            page.wait_for_timeout(600)
            znaky = page.evaluate(
                "() => document.querySelectorAll('#stahovani-vlastni .napoveda-znak').length")
            zkontroluj(znaky >= 4, "volby mají svoje ⓘ", f"{znaky} znaků")

            najed(page, "#stahovani-vlastni .napoveda-znak")
            stav = page.evaluate(STAV)
            zkontroluj(not stav["skryta"] and len(stav["text"]) > 30,
                       "najetí na ⓘ ukáže delší vysvětlení", stav["text"][:60])
            zkontroluj(stav["vejde"], "a vejde se na obrazovku", str(stav["ram"]))
            zkontroluj(stav["ram"][2] <= 300,
                       "delší text se zalomí do bubliny, neroztáhne se přes celé okno",
                       f"{stav['ram'][2]} px")

            # Na dotykové obrazovce najetí neexistuje, ⓘ tedy musí jít i ťuknout.
            page.evaluate("() => Napoveda.skryj()")
            page.dispatch_event("#stahovani-vlastni .napoveda-znak", "click")
            page.wait_for_timeout(200)
            zkontroluj(not page.evaluate(STAV)["skryta"], "klik na ⓘ ji taky ukáže")
            page.dispatch_event("#stahovani-vlastni .napoveda-znak", "click")
            page.wait_for_timeout(200)
            zkontroluj(page.evaluate(STAV)["skryta"], "a druhý klik ji schová")

            print("\n── zamčený řádek řekne důvod ──")
            page.evaluate("() => document.querySelector('input[name=format][value=zip]').click()")
            page.wait_for_timeout(700)
            zamcene = page.evaluate("""() => {
              const r = document.querySelector('#stahovani-vlastni .stahovani-radek.zamcene');
              return r ? {pole: r.dataset.pole, napoveda: r.dataset.napoveda || ''} : null;
            }""")
            zkontroluj(zamcene and len(zamcene["napoveda"]) > 20,
                       "ZIP zamkne řádek a dá k němu důvod jako nápovědu",
                       str(zamcene))
            if zamcene:
                najed(page, "#stahovani-vlastni .stahovani-radek.zamcene")
                stav = page.evaluate(STAV)
                zkontroluj(not stav["skryta"] and stav["text"] == zamcene["napoveda"],
                           "a najetí na něj ten důvod ukáže", stav["text"][:50])

            print("\n── nápověda uvnitř rolovacího panelu ──")
            # Bublina je přilepená na souřadnice, takže po odrolování panelu by
            # visela u prázdného místa.
            # V plném okně se panel vejde celý; na nízkém už rolovat musí.
            page.set_viewport_size({"width": 1280, "height": 420})
            page.wait_for_timeout(300)
            najed(page, "#stahovani-vlastni .napoveda-znak")
            posun = page.evaluate("""() => {
              const t = document.querySelector('.stahovani-telo');
              t.scrollTop = 0; t.scrollTop = 60;
              return t.scrollTop;
            }""")
            page.wait_for_timeout(300)
            zkontroluj(posun > 0, "panel je delší než okno, takže je co rolovat",
                       f"scrollTop {posun}")
            zkontroluj(page.evaluate(STAV)["skryta"],
                       "rolování panelu nápovědu schová, ať neodjede od své ikony")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(300)

            print("\n── Escape zavře nejdřív nápovědu, až pak okno ──")
            page.dispatch_event("#stahovani-vlastni .napoveda-znak", "click")
            page.wait_for_timeout(200)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            po_escape = page.evaluate("""() => ({
              napoveda_skryta: document.querySelector('.napoveda-bublina').hidden,
              okno_otevrene: !document.getElementById('stahovani-okno').hidden})""")
            zkontroluj(po_escape["napoveda_skryta"] and po_escape["okno_otevrene"],
                       "první Escape schová nápovědu a okno nechá", str(po_escape))
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            zkontroluj(page.evaluate(
                "() => document.getElementById('stahovani-okno').hidden"),
                "druhý Escape zavře okno")

            print("\n── hledání: bublina u krajního sloupce ──")
            page.goto(base + "/search")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            kotva = page.evaluate("""() => {
              const p = [...document.querySelectorAll('#results-table [data-napoveda]')].pop();
              if (!p) return null;
              const r = p.getBoundingClientRect();
              return {x: Math.round(r.left), sirka_okna: window.innerWidth};
            }""")
            if kotva:
                page.hover("#results-table [data-napoveda]:last-of-type")
                page.wait_for_timeout(250)
                stav = page.evaluate(STAV)
                zkontroluj(not stav["skryta"], "nápověda v tabulce se ukáže", stav["text"][:40])
                zkontroluj(stav["vejde"],
                           "a nepřečuhuje přes okraj - dřív to dělalo 66 px falešného sloupce",
                           str(stav["ram"]))
            else:
                zkontroluj(False, "v tabulce hledání je co změřit")

            sirka_stranky = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
            zkontroluj(sirka_stranky <= 0,
                       "a stránka se kvůli ní nedá táhnout do strany",
                       f"{sirka_stranky} px navíc")

            print("\n── sdílení: nápověda, která je celá HTML ──")
            # Tahle bublina mívala pevných 200 px ukotvených na pravý okraj ikony:
            # na 390px displeji čouhala 54 px ven a šlo o ni táhnout celou stránkou.
            page.set_viewport_size({"width": 390, "height": 780})
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            ma_sdileni = page.evaluate("() => !!document.querySelector('.napoveda-kotva')")
            zkontroluj(ma_sdileni, "je co změřit - aspoň jeden sdílený zpěvník v seznamu")
            if ma_sdileni:
                najed(page, ".napoveda-kotva")
                stav = page.evaluate(STAV)
                zkontroluj(not stav["skryta"] and "Sdíleno s" in stav["text"],
                           "najetí na ikonu sdílení vypíše, s kým", stav["text"][:40])
                zkontroluj(stav["vejde"],
                           "a na 390px displeji se celá vejde", str(stav["ram"]))
                sirka_navic = page.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                zkontroluj(sirka_navic <= 0,
                           "a stránka se kvůli ní nedá táhnout do strany",
                           f"{sirka_navic} px navíc")
                zkontroluj(page.evaluate(
                    "() => getComputedStyle(document.querySelector('.napoveda-obsah'))"
                    ".display") == "none",
                    "zdrojové HTML nápovědy zůstává skryté v kotvě")

            print("\n── editor zpěvníku ──")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(200)
            page.evaluate("() => document.querySelector('.btn-edit').click()")
            page.wait_for_timeout(1500)
            editor = page.evaluate("""() => {
              const z = document.querySelector('#edit-songs-section .napoveda-znak');
              return z ? {text: z.dataset.napoveda || '',
                          sirka: Math.round(z.getBoundingClientRect().width)} : null;
            }""")
            zkontroluj(editor is not None,
                       "u čísla první strany je ⓘ, ne vlastní otazník", str(editor))
            if editor:
                zkontroluj("číslování" in editor["text"],
                           "a vysvětluje, proč se to nastavuje", editor["text"][:50])
                zkontroluj(editor["sirka"] == 15,
                           "a má stejnou velikost jako ⓘ v okně stahování",
                           f"{editor['sirka']} px")
                najed(page, "#edit-songs-section .napoveda-znak")
                stav = page.evaluate(STAV)
                zkontroluj(not stav["skryta"] and stav["vejde"],
                           "bublina se v editoru vejde na obrazovku", str(stav["ram"]))

            print("\n── telefon (390 px) ──")
            page.set_viewport_size({"width": 390, "height": 780})
            page.goto(base + f"/songbook/{BOOK}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            # Na dotyku není najetí, takže se ťuká. U obyčejného tlačítka má klik
            # udělat svou práci a nápovědu nechat být.
            page.evaluate("() => Napoveda.ukaz(document.getElementById('mode-toggle-button'))")
            page.wait_for_timeout(200)
            stav = page.evaluate(STAV)
            zkontroluj(not stav["skryta"] and stav["vejde"],
                       "na úzké obrazovce se bublina vejde celá", str(stav["ram"]))
            zkontroluj(stav["ram"][2] <= 390 - 16,
                       "a drží se uvnitř okraje", f"{stav['ram'][2]} px")

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
