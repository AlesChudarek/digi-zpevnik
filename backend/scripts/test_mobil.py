"""Ověří lištu a dlaždice na úzké obrazovce — a že se HTML necachuje.

Vzniklo ze tří hlášení: na telefonu se nedalo odhlásit ani změnit motiv (oba
vysouvací panely odjely mimo obrazovku) a ikony úprav přečuhovaly z dlaždice.
Všechno tři jsou věci, které se z kódu nepoznají — panel měl v CSS `left: 0`
a přesto končil 112 px za okrajem, protože kotvou bylo tlačítko u protilehlého
kraje menu. Proto se to měří, a proto v číslech.

Kontrola hlaviček je tu s nimi schválně: bez `Cache-Control` na HTML prohlížeč
po nasazení servíruje starou stránku, a s ní i starou adresu skriptů, takže by
kterákoliv z těch oprav mohla u uživatele tiše chybět.

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
PORT = 5585
PASSWORD = "mobil-test"

# Šířky, na kterých se měří. 320 px je nejužší telefon, který ještě stojí za ohled,
# 1280 px je tu proto, aby se ověřilo, že se oprava netýká široké obrazovky.
SIRKY_TELEFONU = [320, 360, 390, 412]

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + detail) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "mobil"
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


RAM = """(sel) => {
  const e = document.querySelector(sel);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return {x: Math.round(r.left), y: Math.round(r.top),
          w: Math.round(r.width), pravy: Math.round(r.right),
          vejde: r.left >= -0.5 && r.right <= window.innerWidth + 0.5};
}"""

# Jde na to tlačítko opravdu kliknout, nebo jen leží na správných souřadnicích?
TREFA = """(sel) => {
  const b = document.querySelector(sel);
  if (!b) return 'prvek není';
  const r = b.getBoundingClientRect();
  const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
  if (x < 0 || x > window.innerWidth || y < 0 || y > window.innerHeight)
    return `střed je mimo obrazovku (${x}, ${y})`;
  const t = document.elementFromPoint(x, y);
  if (!t) return 'na tom místě není nic';
  return (t === b || b.contains(t)) ? 'ano' : 'překrývá .' + (t.className || t.tagName);
}"""

IKONY_DLAZDICE = """() => {
  const akce = document.querySelector('.songbook-actions');
  if (!akce) return null;
  const karta = akce.closest('.songbook-card');
  const rk = karta.getBoundingClientRect();
  const ikony = [...akce.querySelectorAll('.icon-btn')].map(e => {
    const r = e.getBoundingClientRect();
    return {x: Math.round(r.left), pravy: Math.round(r.right)};
  });
  return {karta: Math.round(rk.width),
          pocet: ikony.length,
          venku: ikony.filter(i => i.x < rk.left - 0.5 || i.pravy > rk.right + 0.5).length,
          radku: new Set([...akce.querySelectorAll('.icon-btn')]
                         .map(e => Math.round(e.getBoundingClientRect().top))).size};
}"""


def prihlas(page, base):
    page.goto(base + "/login")
    page.fill('input[name="email"]', "admin@test.com")
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_load_state("networkidle")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright")

    tmp = Path(tempfile.mkdtemp(prefix="mobil-"))
    db_copy = tmp / "mobil.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)

    server, base = start_server(db_copy)
    try:
        print("\n── hlavičky: HTML se neuloží natvrdo, obrázky ano ──")
        html = urllib.request.urlopen(base + "/login", timeout=5)
        zkontroluj(html.headers.get("Cache-Control") == "no-cache",
                   "HTML odpovídá s no-cache, aby po nasazení nešla stará stránka",
                   str(html.headers.get("Cache-Control")))
        html.read()
        # Statické soubory posílá Flask sám a `no-cache` na nich měl už předtím;
        # tahle kontrola hlídá, že jim to `after_request` nepřepsal na něco jiného.
        # (Že by se daly cachovat natvrdo, když je `static_bust` verzuje adresou,
        # je samostatný úkol v TODO.)
        staticky = urllib.request.urlopen(base + "/static/js/napoveda.js", timeout=5)
        zkontroluj("max-age=31536000" not in (staticky.headers.get("Cache-Control") or ""),
                   "statickému souboru se hlavička nepřepsala na roční cache",
                   str(staticky.headers.get("Cache-Control")))

        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            chyby = []

            print("\n── lišta na 390 px: oba vysouvací panely ──")
            ctx = browser.new_context(viewport={"width": 390, "height": 780})
            page = ctx.new_page()
            page.on("pageerror", lambda e: chyby.append(str(e)))
            prihlas(page, base)
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)

            # Stará stránka by s sebou nesla i starou adresu skriptu, takže by cache
            # busting podle mtime nepomohl.
            zkontroluj("napoveda.js?v=" in page.content(),
                       "stránka nese adresu skriptu s verzí podle mtime")

            page.evaluate("() => toggleNavMenu()")
            page.wait_for_timeout(500)
            zkontroluj(page.evaluate(RAM, ".navbar .right")["vejde"],
                       "spodní část menu (účet a motiv) se vejde do obrazovky")

            # Práce s účtem je v menu rozbalená, ne pod šipkou: šipka sama o sobě
            # neřekla, co je pod ní, a odhlášení stálo tři ťuknutí.
            ucet = page.evaluate(RAM, ".ucet-panel")
            zkontroluj(ucet and ucet["vejde"] and ucet["w"] > 300,
                       "účet je v menu rozbalený rovnou, bez druhého okna", str(ucet))
            zkontroluj(page.evaluate(TREFA, ".ucet-odhlasit") == "ano",
                       "a „Odhlásit se“ jde trefit na dvě ťuknutí",
                       page.evaluate(TREFA, ".ucet-odhlasit"))
            zkontroluj(page.evaluate(
                "() => document.querySelector('.ucet').getBoundingClientRect().width") == 0,
                "spouštěcí šipka je v menu schovaná, nemá co spouštět")
            zkontroluj(page.evaluate(
                "() => getComputedStyle(document.querySelector('.theme-popisek'))"
                ".display") != "none",
                "u palety je vidět popisek Motiv")

            page.evaluate("() => document.getElementById('theme-picker-btn').click()")
            page.wait_for_timeout(400)
            motivy = page.evaluate(RAM, ".theme-panel")
            zkontroluj(motivy["vejde"],
                       "bublina motivů se vejde celá - dřív čouhala 112 px vpravo",
                       str(motivy))
            zkontroluj(motivy["w"] < 250,
                       "a má přirozenou šířku, ne celou šířku stránky",
                       f"{motivy['w']} px")
            zkontroluj(page.evaluate(TREFA, ".theme-swatch") == "ano",
                       "a dá se na motiv kliknout",
                       page.evaluate(TREFA, ".theme-swatch"))

            print("\n── menu překrývá obsah, nestrká s ním ──")
            # Menu bylo součástí lišty, takže její výška šla do mezerníku: otevření
            # sjelo stránkou o 187 px dolů a schování lišty pak skočilo o 243 px zpátky.
            stav = """() => ({
              navbar: Math.round(document.querySelector('.navbar').getBoundingClientRect().height),
              spacer: Math.round(document.querySelector('.navbar-spacer').getBoundingClientRect().height),
              obsah: Math.round(document.querySelector('.grid-container').getBoundingClientRect().top)})"""
            page.evaluate("() => toggleNavMenu()")
            page.wait_for_timeout(500)
            zavreno = page.evaluate(stav)
            page.evaluate("() => toggleNavMenu()")
            page.wait_for_timeout(600)
            otevreno = page.evaluate(stav)
            zkontroluj(otevreno["obsah"] == zavreno["obsah"],
                       "otevření menu nepohne obsahem stránky",
                       f"{zavreno['obsah']} → {otevreno['obsah']} px")
            zkontroluj(otevreno["spacer"] == zavreno["spacer"],
                       "mezerník pod lištou zůstává na výšce zavřené lišty",
                       f"{otevreno['spacer']} px při liště {otevreno['navbar']} px")
            page.evaluate("() => toggleNavbar()")
            page.wait_for_timeout(700)
            skok = zavreno["obsah"] - page.evaluate(stav)["obsah"]
            zkontroluj(skok <= zavreno["spacer"] + 1,
                       "schování lišty posune obsah jen o výšku zavřené lišty",
                       f"{skok} px")
            page.evaluate("() => toggleNavbar()")
            page.wait_for_timeout(500)
            ctx.close()

            print("\n── menu u hosta, který účet nemá ──")
            ctx = browser.new_context(viewport={"width": 390, "height": 780})
            page = ctx.new_page()
            page.on("pageerror", lambda e: chyby.append(str(e)))
            page.goto(base + "/guest-login")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            page.evaluate("() => toggleNavMenu()")
            page.wait_for_timeout(500)
            host = page.evaluate(RAM, ".navbar .right")
            zkontroluj(host is not None and host["vejde"],
                       "spodní část menu se vejde i bez účtu", str(host))
            zkontroluj(page.evaluate(TREFA, ".right a") == "ano",
                       "a na „Přihlásit“ jde kliknout",
                       page.evaluate(TREFA, ".right a"))
            ctx.close()

            print("\n── lišta na 1280 px: široká obrazovka beze změny ──")
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.on("pageerror", lambda e: chyby.append(str(e)))
            prihlas(page, base)
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            page.evaluate("() => document.getElementById('ucet-btn').click()")
            page.wait_for_timeout(400)
            siroky = page.evaluate(RAM, ".ucet-panel")
            zkontroluj(siroky["vejde"] and siroky["w"] < 400,
                       "panel účtu zůstává úzký a ukotvený u tlačítka", str(siroky))
            zkontroluj(page.evaluate(TREFA, ".ucet-odhlasit") == "ano",
                       "a odhlásit se dá i tady")
            ctx.close()

            print("\n── ikony úprav uvnitř dlaždice ──")
            for sirka in SIRKY_TELEFONU + [1280]:
                ctx = browser.new_context(viewport={"width": sirka, "height": 800})
                page = ctx.new_page()
                page.on("pageerror", lambda e: chyby.append(str(e)))
                prihlas(page, base)
                page.goto(base + "/my-songbooks")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(600)
                v = page.evaluate(IKONY_DLAZDICE)
                zkontroluj(v is not None and v["venku"] == 0,
                           f"{sirka} px: žádná ikona nepřečuhuje z dlaždice",
                           f"dlaždice {v['karta']} px, {v['pocet']} ikon "
                           f"na {v['radku']} řádcích" if v else "dlaždice nenalezena")
                prekroceni = page.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                zkontroluj(prekroceni <= 0,
                           f"{sirka} px: stránka se nedá táhnout do strany",
                           f"{prekroceni} px navíc")
                ctx.close()

            print("\n── chyby v konzoli ──")
            zkontroluj(not chyby, "žádná chyba JavaScriptu", "; ".join(chyby[:3]))
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
