"""Ověří tlačítko Stáhnout ve čtečce: od kliknutí až po stažený soubor.

Backend hlídá test_export.py. Tenhle skript kontroluje to, co se dá zjistit jen
v prohlížeči: že se nabídka vysune a vejde na obrazovku, že hlášení o průběhu naskočí,
že se stahování opravdu spustí, a hlavně že poll doběhne až k hotovému souboru - ta cesta
má několik stavů a z kódu se nepozná, jestli se v nich neztratí.

Vyžaduje playwright (viz measure_reader.py). Běží proti KOPII databáze.
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
EXPORTS_DIR = PROJECT_ROOT / "data" / "exports"
PORT = 5583
PASSWORD = "export-ui"
BOOK = "00006"

selhani = []


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
            zkontroluj('Připravuji' in page.evaluate(
                "() => document.getElementById('download-state').textContent"),
                "hned se ukáže, že se soubor připravuje",
                page.evaluate("() => document.getElementById('download-state').textContent"))

            # Poll musí sám dojít až ke stažení, bez dalšího zásahu
            with page.expect_download(timeout=120000) as info:
                pass
            stazeny = info.value
            cesta = Path(stazeny.path())
            velikost = cesta.stat().st_size
            zkontroluj(stazeny.suggested_filename.endswith(".pdf"),
                       "stáhne se PDF", stazeny.suggested_filename)
            zkontroluj(cesta.read_bytes()[:5] == b"%PDF-", "a je to platné PDF",
                       f"{velikost // 1024} kB")
            zkontroluj(page.evaluate(
                "() => document.getElementById('download-state').textContent") == "",
                "hlášení o průběhu po dokončení zmizí")

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
            page.evaluate("() => document.querySelectorAll('#download-menu button')[1].click()")
            page.wait_for_timeout(2500)
            zkontroluj(page.url.endswith(f"/songbook/{BOOK}"),
                       "neodnaviguje na odpověď serveru, dokud se plná varianta staví",
                       page.url)
            with page.expect_download(timeout=120000) as info2:
                pass
            plne = info2.value
            zkontroluj(Path(plne.path()).read_bytes()[:5] == b"%PDF-",
                       "plná varianta se nakonec stáhne",
                       f"{Path(plne.path()).stat().st_size // 1024} kB")

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
                page.evaluate("""() => {
                  document.querySelectorAll('.download-chooser button')[0].click();
                }""")
                with page.expect_download(timeout=120000) as info3:
                    pass
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
