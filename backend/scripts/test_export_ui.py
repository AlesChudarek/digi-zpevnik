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

            print("\n── nabídka stahování ──")
            zkontroluj(not page.evaluate(
                "() => document.getElementById('download-menu').classList.contains('open')"),
                "nabídka je zavřená, dokud se neklikne")

            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(400)
            zkontroluj(page.evaluate(
                "() => document.getElementById('download-menu').classList.contains('open')"),
                "klik nabídku otevře")

            box = page.evaluate("""() => {
              const r = document.getElementById('download-menu').getBoundingClientRect();
              return {left: Math.round(r.left), right: Math.round(r.right)};
            }""")
            zkontroluj(box["left"] >= 0 and box["right"] <= 1280,
                       "vejde se na obrazovku", f"x {box['left']}→{box['right']}")

            print("\n── stažení PDF ──")
            # expect_download se musí zapnout PŘED kliknutím, ne po měřeních: hlídá jen
            # stažení, která začnou uvnitř. Když se soubor složil rychleji, než měření
            # doběhla, událost mu utekla a test spadl na timeout u hotového souboru.
            with page.expect_download(timeout=300000) as info:
              page.evaluate("""() => {
                document.querySelectorAll('#download-menu button')[0].click();
              }""")
              page.wait_for_timeout(600)
              # Stránka nesmí odnavigovat. Tady se to poprvé projevilo: response.ok je
              # pravdivé i pro 202, takže se "začal jsem to skládat" bralo jako hotovo
              # a prohlížeč skočil na JSON místo stažení souboru.
              zkontroluj(page.url.endswith(f"/songbook/{BOOK}"),
                         "stránka zůstane na čtečce, neodnaviguje na odpověď serveru",
                         page.url)
              zkontroluj(page.evaluate(
                  "() => { const o = document.getElementById('stahovani-okno');"
                  "        return !!o && !o.hidden; }"),
                  "okno se skládáním se otevře")
              zkontroluj('Připravuji' in page.evaluate(
                  "() => document.getElementById('stahovani-nadpis').textContent"),
                  "a hned řekne, že se soubor připravuje",
                  page.evaluate("() => document.getElementById('stahovani-nadpis').textContent"))

              # Okno visí na body, ne v .mode-buttons. Jinak by z toho sloupce zdědilo
              # kulaté 60px tlačítko, jeho stín, a hlavně mizení celé skupiny.
              rozvrzeni = page.evaluate("""() => {
                const p = document.querySelector('.stahovani-panel');
                const z = document.getElementById('stahovani-zavrit');
                const rp = p.getBoundingClientRect(), rz = z.getBoundingClientRect();
                const cz = getComputedStyle(z);
                return {panel: [Math.round(rp.width), Math.round(rp.height)],
                        zavrit: [Math.round(rz.width), Math.round(rz.height)],
                        radius: cz.borderRadius, shadow: cz.boxShadow,
                        v_mode_buttons: !!document.getElementById('stahovani-okno')
                                           .closest('.mode-buttons'),
                        pod_panelem: rz.bottom <= rp.bottom + 1 && rz.right <= rp.right + 1};
              }""")
              zkontroluj(not rozvrzeni["v_mode_buttons"],
                         "okno neleží uvnitř .mode-buttons")
              zkontroluj(rozvrzeni["zavrit"][0] < 120 and 28 <= rozvrzeni["zavrit"][1] <= 48,
                         "tlačítko Zavřít má tvar tlačítka, ne kolečka ze čtečky",
                         f"{rozvrzeni['zavrit'][0]}x{rozvrzeni['zavrit'][1]} px, "
                         f"radius {rozvrzeni['radius']}")
              zkontroluj(rozvrzeni["pod_panelem"], "a vejde se do panelu")
              zkontroluj(page.evaluate("""() => {
                const p = document.querySelector('.stahovani-panel').getBoundingClientRect();
                const e = document.elementFromPoint(Math.round(p.left + p.width / 2),
                                                    Math.round(p.top + p.height / 2));
                return !!(e && e.closest('.stahovani-zaclona'));
              }"""), "okno je nahoře a přijímá kliknutí")

              # Sloupec tlačítek čtečky po chvíli nečinnosti zmizí. Dokud v něm okno
              # leželo, zmizelo s ním - Zavřít pak nešlo kliknout.
              page.evaluate("() => document.querySelector('.mode-buttons').classList.add('fade-hidden')")
              page.wait_for_timeout(100)
              zkontroluj(page.evaluate(
                  "() => getComputedStyle(document.getElementById('stahovani-okno')).pointerEvents"
              ) != "none", "okno přežije schování sloupce tlačítek čtečky")
              page.evaluate("() => document.querySelector('.mode-buttons').classList.remove('fade-hidden')")

            stazeny = info.value
            cesta = Path(stazeny.path())
            velikost = cesta.stat().st_size
            zkontroluj(stazeny.suggested_filename.endswith(".pdf"),
                       "stáhne se PDF", stazeny.suggested_filename)
            zkontroluj(cesta.read_bytes()[:5] == b"%PDF-", "a je to platné PDF",
                       f"{velikost // 1024} kB")
            page.wait_for_timeout(2500)
            # Nezavírat samo: kdo odešel k jiné záložce, se jinak vrátí k obrazovce,
            # na které po čekání nezůstala žádná stopa.
            zkontroluj(not page.evaluate("() => document.getElementById('stahovani-okno').hidden"),
                       "okno po dokončení zůstane otevřené")
            zkontroluj(page.evaluate(
                "() => document.getElementById('stahovani-nadpis').textContent") == "Dokončeno",
                "a ukáže stav Dokončeno",
                page.evaluate("() => document.getElementById('stahovani-nadpis').textContent"))

            print("\n── motiv a zavírání ──")
            # Panel byl natvrdo bílý s tmavým textem, takže v tmavém motivu svítil.
            # Barvy teď stojí na --panel-* z _theme.html; měří se, ne čte.
            def barvy_panelu():
                return page.evaluate("""() => {
                  const p = document.querySelector('.stahovani-panel');
                  const cs = getComputedStyle(p);
                  return {panel: cs.backgroundColor, text: cs.color,
                          rada: getComputedStyle(document.getElementById('stahovani-rada')).color,
                          pruh: getComputedStyle(document.querySelector('.stahovani-pruh')).backgroundColor,
                          tlacitko: getComputedStyle(document.getElementById('stahovani-zavrit')).backgroundColor};
                }""")

            svetly = barvy_panelu()
            page.evaluate("() => setDzTheme('dark')")
            page.wait_for_timeout(200)
            tmavy = barvy_panelu()
            zkontroluj(jas(svetly["panel"]) > 0.8,
                       "ve světlém motivu je panel světlý", svetly["panel"])
            zkontroluj(jas(tmavy["panel"]) < 0.25,
                       "v tmavém motivu je panel tmavý", tmavy["panel"])
            zkontroluj(jas(tmavy["text"]) > 0.6,
                       "a text na něm světlý", tmavy["text"])
            zkontroluj(jas(tmavy["text"]) - jas(tmavy["panel"]) > 0.5,
                       "s dostatečným kontrastem",
                       f"text {jas(tmavy['text']):.2f} vs panel {jas(tmavy['panel']):.2f}")
            zkontroluj(tmavy["tlacitko"] != svetly["tlacitko"],
                       "a tlačítko se převléklo taky",
                       f"{svetly['tlacitko']} -> {tmavy['tlacitko']}")
            page.evaluate("() => setDzTheme('blue')")
            page.wait_for_timeout(200)

            # Zavřít klikem mimo panel. Na dotykové obrazovce se Escape nemačká, takže
            # okno, které jde zavřít jedině tlačítkem, je past.
            page.mouse.click(20, 20)
            page.wait_for_timeout(250)
            zkontroluj(page.evaluate("() => document.getElementById('stahovani-okno').hidden"),
                       "klik na záclonu vedle panelu okno zavře")

            print("\n── značky ✓ hned ──")
            # Past, na kterou tenhle projekt naráží popáté: vlastní `display` přebíjí
            # atribut `hidden`, takže značka svítila u všech tří variant.
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(1500)
            znacky = page.evaluate("""async () => {
              const hotove = await (await fetch(`/songbook/BOOK_ID/export-hotove`)).json();
              return [...document.querySelectorAll('#download-menu [data-varianta]')].map(b => {
                const z = b.querySelector('.hotovo-znak');
                return {v: b.dataset.varianta, cekame: !!hotove[b.dataset.varianta],
                        videt: z.getBoundingClientRect().width > 0,
                        display: getComputedStyle(z).display};
              });
            }""".replace("BOOK_ID", BOOK))
            for z in znacky:
                zkontroluj(z["videt"] == z["cekame"],
                           f"{z['v']}: značka {'svítí' if z['cekame'] else 'nesvítí'}",
                           f"vidět={z['videt']} display={z['display']}")
            zkontroluj(any(z["cekame"] for z in znacky) and not all(z["cekame"] for z in znacky),
                       "test má co rozlišovat (část variant hotová, část ne)",
                       str({z["v"]: z["cekame"] for z in znacky}))

            # Pravidlo pro span se zúžilo na přímé potomky tlačítka, aby nesahalo na
            # značku. Popisek pod názvem varianty musí dál stát na vlastním řádku
            # a značka naopak vedle názvu, ne pod ním.
            radky = page.evaluate("""() => {
              const b = document.querySelector('#download-menu [data-varianta="pdf-small"]');
              const nazev = b.querySelector('strong').getBoundingClientRect();
              const popis = b.querySelector(':scope > span').getBoundingClientRect();
              const znak = b.querySelector('.hotovo-znak').getBoundingClientRect();
              return {popis_pod_nazvem: popis.top >= nazev.bottom - 1,
                      znak_vedle_nazvu: Math.abs(znak.top - nazev.top) < 8,
                      popis_sirka: Math.round(popis.width)};
            }""")
            zkontroluj(radky["popis_pod_nazvem"], "popisek varianty zůstal na vlastním řádku",
                       f"šířka {radky['popis_sirka']} px")
            zkontroluj(radky["znak_vedle_nazvu"], "a značka stojí vedle názvu, ne pod ním")
            page.mouse.click(640, 400)
            page.wait_for_timeout(300)

            print("\n── zavření nabídky ──")
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(300)
            page.mouse.click(640, 400)
            page.wait_for_timeout(300)
            zkontroluj(not page.evaluate(
                "() => document.getElementById('download-menu').classList.contains('open')"),
                "klik mimo nabídku zavře")

            print("\n── plné rozlišení po předpřipravené menší variantě ──")
            # Dotaz na stav musí nést variantu. Bez ní odpovídal za tu menší, a když už
            # ta hotová byla, hlásil "ready" hned - klient odnavigoval na plnou variantu,
            # která se ještě stavěla, a skončilo to na JSONu místo staženého souboru.
            page.evaluate("() => document.getElementById('download-toggle').click()")
            page.wait_for_timeout(300)
            with page.expect_download(timeout=300000) as info2:
                page.evaluate("() => document.querySelectorAll('#download-menu button')[1].click()")
                page.wait_for_timeout(2500)
                zkontroluj(page.url.endswith(f"/songbook/{BOOK}"),
                           "neodnaviguje na odpověď serveru, dokud se plná varianta staví",
                           page.url)
            plne = info2.value
            zkontroluj(Path(plne.path()).read_bytes()[:5] == b"%PDF-",
                       "plná varianta se nakonec stáhne",
                       f"{Path(plne.path()).stat().st_size // 1024} kB")
            page.wait_for_timeout(2000)
            page.evaluate("() => document.getElementById('stahovani-zavrit').click()")
            page.wait_for_timeout(250)
            zkontroluj(page.evaluate("() => document.getElementById('stahovani-okno').hidden"),
                       "a tlačítko Zavřít okno zavře")

            print("\n── stažení ze seznamu Moje zpěvníky ──")
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            pocet = page.evaluate("() => document.querySelectorAll('.btn-download').length")
            zkontroluj(pocet > 0, "u dlaždic je tlačítko na stažení", f"{pocet} tlačítek")
            if pocet:
                page.evaluate("() => document.querySelector('.btn-download').click()")
                page.wait_for_timeout(400)
                zkontroluj(page.evaluate("() => !!document.querySelector('.download-chooser')"),
                           "klik otevře nabídku formátů")
                box2 = page.evaluate("""() => {
                  const r = document.querySelector('.download-chooser').getBoundingClientRect();
                  return {left: Math.round(r.left), right: Math.round(r.right)};
                }""")
                zkontroluj(box2["left"] >= 0 and box2["right"] <= 1280,
                           "nabídka se vejde na obrazovku", f"x {box2['left']}→{box2['right']}")

                znacky2 = page.evaluate("""async () => {
                  const id = document.querySelector('.btn-download').dataset.bookId;
                  const hotove = await (await fetch(`/songbook/${id}/export-hotove`)).json();
                  return [...document.querySelectorAll('.download-chooser [data-varianta]')].map(x => {
                    const z = x.querySelector('.hotovo-znak');
                    return {v: x.dataset.varianta, cekame: !!hotove[x.dataset.varianta],
                            videt: z.getBoundingClientRect().width > 0};
                  });
                }""")
                zkontroluj(len(znacky2) == 3, "i tady jsou tři varianty se značkou",
                           str(len(znacky2)))
                for z in znacky2:
                    zkontroluj(z["videt"] == z["cekame"], f"seznam, {z['v']}: značka sedí",
                               f"vidět={z['videt']} čekáme={z['cekame']}")

                # Plné rozlišení schválně: menší varianta bývá předpřipravená a stáhla
                # by se rovnou, takže by okno se skládáním nebylo co změřit.
                with page.expect_download(timeout=300000) as info3:
                    page.evaluate("""() => {
                      document.querySelectorAll('.download-chooser button')[1].click();
                    }""")
                    page.wait_for_timeout(800)
                    otevrelo_se = page.evaluate(
                        "() => { const o = document.getElementById('stahovani-okno');"
                        "        return !!o && !o.hidden; }")
                    zkontroluj(otevrelo_se or znacky2[1]["cekame"],
                               "okno se skládáním se otevře i tady",
                               "bylo předpřipravené" if not otevrelo_se else "")
                ze_seznamu = info3.value
                zkontroluj(Path(ze_seznamu.path()).read_bytes()[:5] == b"%PDF-",
                           "a stáhne se PDF", ze_seznamu.suggested_filename)
                if otevrelo_se:
                    page.wait_for_timeout(2000)
                    zkontroluj(page.evaluate(
                        "() => document.getElementById('stahovani-nadpis').textContent")
                        == "Dokončeno", "a okno skončí na Dokončeno")

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
