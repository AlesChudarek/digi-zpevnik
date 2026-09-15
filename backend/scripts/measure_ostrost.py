"""Změří, že čtečka posílá zmenšené strany a při přiblížení si dotáhne originál.

Tohle se z kódu ověřit nedá. Jsou tam tři věci, které buď fungují, nebo ne, a pozná se to
jen v prohlížeči: kolik bajtů opravdu přiteče, jestli se po přiblížení vymění zdroj
obrázku, a jestli při té výměně rámeček neprobliká do prázdna.

Poslední bod se měří tak, že se obrázek hlídá každých pár milisekund - kdyby se mezi
starým a novým zdrojem objevil snímek s nulovou šířkou nebo nedokončeným dekódováním,
je vidět v `naturalWidth`.

Vyžaduje jednorázově:
    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium

Použití:
    python backend/scripts/measure_ostrost.py

Běží proti KOPII databáze, takže na skutečná data nesahá.
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
PORT = 5597
PASSWORD = "measure-only"
BOOK = "/songbook/00001"


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "measure"
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


# Hlídá jeden obrázek a zapisuje každou změnu zdroje i rozměru. Kdyby se při výměně
# objevil prázdný snímek, bude mezi záznamy jeden s naturalWidth 0.
HLIDAC = """(sel) => {
  const img = document.querySelector(sel);
  window.__zaznam = [];
  let posledni = null;
  const vzorek = () => {
    const stav = img.currentSrc + '|' + img.naturalWidth;
    if (stav !== posledni) {
      posledni = stav;
      window.__zaznam.push({src: img.currentSrc, natural: img.naturalWidth});
    }
  };
  vzorek();
  window.__hlidac = setInterval(vzorek, 8);
}"""

# Na telefonu se čtečka otevírá ve svitku, na širokém okně v knižním módu. Měřit se musí
# ta strana, která je opravdu vidět - skrytý #left-page nemá ani zdroj, ani rozměr.
VIDITELNA = """() => {
  const dvoj = document.getElementById('double-page');
  if (dvoj && getComputedStyle(dvoj).display !== 'none') {
    for (const id of ['left-page', 'right-page']) {
      const el = document.getElementById(id);
      if (el && el.dataset.full && getComputedStyle(el).display !== 'none') return '#' + id;
    }
  }
  const strany = [...document.querySelectorAll('#scroll-mode img')];
  const v = strany.find(s => {
    const r = s.getBoundingClientRect();
    return s.dataset.full && r.bottom > 0 && r.top < window.innerHeight;
  });
  return v ? '#' + v.id : null;
}"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright. Nainstaluj:\n"
                         "   .venv/bin/pip install playwright\n"
                         "   .venv/bin/playwright install chromium")

    tmp = Path(tempfile.mkdtemp(prefix="measure-ostrost-"))
    db_copy = tmp / "measure.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)

    server, base = start_server(db_copy)
    spatne = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                      device_scale_factor=2)
            page = ctx.new_page()

            prenos = {"webp": [0, 0], "png": [0, 0]}

            def zapis(odpoved):
                u = odpoved.url
                if '/strana/' in u:
                    klic = 'webp'
                elif '/songbooks/' in u:
                    klic = 'png'
                else:
                    return
                try:
                    n = len(odpoved.body())
                except Exception:
                    return
                prenos[klic][0] += 1
                prenos[klic][1] += n

            page.on("response", zapis)

            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            # --- 1. běžné čtení na telefonu ---
            prenos["webp"] = [0, 0]; prenos["png"] = [0, 0]
            page.goto(base + BOOK)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            print("── Otevření zpěvníku na telefonu 390×844, DPR 2 ──")
            for k, popis in (("webp", "náhledy /strana/"), ("png", "originály /songbooks/")):
                ks, b = prenos[k]
                print(f"   {popis:24} {ks:3} souborů   {b/1e6:6.2f} MB")
            if prenos["png"][0] > 2:
                print("   ❌ při běžném čtení se tahají originály")
                spatne += 1
            else:
                print("   ✅ při běžném čtení jdou jen náhledy")

            sel = page.evaluate(VIDITELNA)
            rezim = page.evaluate("() => currentMode")
            sirka = page.evaluate(f"() => parseFloat(document.querySelector('{sel}').style.width)")
            print(f"   režim po otevření: {rezim}, měřená strana {sel}")
            print(f"   strana se kreslí na {sirka:.0f} CSS px (= {sirka*2:.0f} px na displeji)")

            # --- 2. výměna při přiblížení ---
            print("\n── Přiblížení ──")
            page.evaluate(HLIDAC, sel)
            pred = page.evaluate(f"() => document.querySelector('{sel}').currentSrc")
            pred_png = prenos["png"][0]
            for _ in range(8):
                page.evaluate("() => zoomIn()")
                page.wait_for_timeout(120)
            page.wait_for_timeout(2500)
            page.evaluate("() => clearInterval(window.__hlidac)")
            zaznam = page.evaluate("() => window.__zaznam")
            po = page.evaluate(f"() => document.querySelector('{sel}').currentSrc")
            sirka_po = page.evaluate(f"() => parseFloat(document.querySelector('{sel}').style.width)")

            print(f"   šířka po přiblížení: {sirka_po:.0f} CSS px (= {sirka_po*2:.0f} px na displeji)")
            print(f"   před: {'náhled' if '/strana/' in pred else 'originál'}")
            print(f"   po:   {'originál' if '/songbooks/' in po else 'náhled'}")
            if sirka_po * 2 <= 1100:
                print("   ⚠️  ani po přiblížení se nepřekročilo 1100 px, výměna se čekat nemá")
            elif '/songbooks/' in po:
                print("   ✅ zdroj se vyměnil za plné rozlišení")
            else:
                print("   ❌ zůstal náhled, i když je strana větší než náhled")
                spatne += 1

            prazdne = [z for z in zaznam if z['natural'] == 0]
            print(f"   snímků při výměně: {len(zaznam)}, z toho prázdných: {len(prazdne)}")
            for z in zaznam:
                print(f"      {z['natural']:5} px  {z['src'].split('/')[-1][:44]}")
            if prazdne:
                print("   ❌ rámeček probliknul do prázdna")
                spatne += 1
            else:
                print("   ✅ výměna bez probliknutí (obrázek nikdy neměl nulovou šířku)")
            print(f"   originálů dotaženo přiblížením: {prenos['png'][0] - pred_png}")

            # --- 3. svitek nesmí přiblížením stáhnout celý zpěvník ---
            print("\n── Svitkový režim: dotahuje se jen okolí obrazovky? ──")
            page.goto(base + BOOK)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1200)
            if page.evaluate("() => currentMode") != 'scroll':
                page.evaluate("() => toggleReadingMode()")
                page.wait_for_timeout(1200)
            # Nepočítá se síť, ale kolik stran opravdu drží originál: originály z
            # předchozího měření už jsou v cache prohlížeče a request by se neobjevil.
            for _ in range(10):
                page.evaluate("() => zoomIn()")
                page.wait_for_timeout(100)
            page.wait_for_timeout(3000)
            stav = page.evaluate("""() => {
              const s = [...document.querySelectorAll('#scroll-mode img[data-full]')];
              const vidi = s.filter(i => {
                const r = i.getBoundingClientRect();
                return r.bottom > -window.innerHeight * 2 && r.top < window.innerHeight * 3;
              });
              const ostre = s.filter(i => i.currentSrc.includes('/songbooks/'));
              return {celkem: s.length, vOkoli: vidi.length, ostre: ostre.length,
                      sirka: parseFloat(s[0].style.width)};
            }""")
            print(f"   strana ve svitku: {stav['sirka']:.0f} CSS px "
                  f"(= {stav['sirka']*2:.0f} px na displeji)")
            print(f"   stran v DOM: {stav['celkem']}, v okolí obrazovky: {stav['vOkoli']}, "
                  f"povýšeno na originál: {stav['ostre']}")
            if stav['sirka'] * 2 <= 1100:
                print("   ⚠️  přiblížení nedosáhlo na práh, test neplatí")
            elif stav['ostre'] == 0:
                print("   ❌ nedotáhla se ani viditelná strana")
                spatne += 1
            elif stav['ostre'] >= stav['celkem']:
                print("   ❌ přiblížení povýšilo celý zpěvník naráz")
                spatne += 1
            else:
                print(f"   ✅ povýšeno jen {stav['ostre']} z {stav['celkem']} stran, "
                      f"zbytek čeká na doscrollování")

            # --- 4. knižní mód: přelistování během dotahování ---
            # #left-page a #right-page se recyklují pro každou dvoustranu. Když se přelistuje
            # dřív, než dorazí originál předchozí strany, nesmí ten originál doskočit do
            # rámečku, kde už je jiná strana.
            print("\n── Knižní mód: přelistování během dotahování ──")
            page2 = ctx.new_page()
            page2.goto(base + BOOK)
            page2.wait_for_load_state("networkidle")
            page2.wait_for_timeout(1200)
            if page2.evaluate("() => currentMode") != 'double':
                page2.evaluate("() => toggleReadingMode()")
                page2.wait_for_timeout(1200)
            page2.set_viewport_size({"width": 1600, "height": 1000})
            page2.wait_for_timeout(800)
            for _ in range(10):
                page2.evaluate("() => zoomIn()")
                page2.wait_for_timeout(60)
            # Přelistovat hned, ať se do toho trefíme uprostřed dotahování
            page2.wait_for_timeout(60)
            page2.evaluate("() => nextPage()")
            page2.wait_for_timeout(2500)
            shoda = page2.evaluate("""() => {
              return ['left-page', 'right-page'].map(id => {
                const el = document.getElementById(id);
                if (!el || !el.dataset.full || getComputedStyle(el).display === 'none')
                  return {id, preskoceno: true};
                const ma = el.currentSrc.split('?')[0];
                const patri = el.dataset.full.split('/').slice(-2).join('/');
                return {id, patri, sedi: ma.includes(patri), src: ma.split('/').slice(-2).join('/'),
                        sirka: el.naturalWidth};
              });
            }""")
            spatne_par = 0
            for s in shoda:
                if s.get('preskoceno'):
                    print(f"   {s['id']}: prázdná, přeskočeno")
                    continue
                znak = '✅' if s['sedi'] else '❌'
                print(f"   {znak} {s['id']}: v rámečku {s['src']} ({s['sirka']} px), "
                      f"má tam být {s['patri']}")
                if not s['sedi']:
                    spatne_par += 1
            if spatne_par:
                print("   ❌ do rámečku doskočila cizí strana")
                spatne += 1
            else:
                print("   ✅ po přelistování drží každý rámeček svou stranu")

            browser.close()
    finally:
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("✅ všechno sedí" if not spatne else f"❌ {spatne} problémů"))
    return 1 if spatne else 0


if __name__ == "__main__":
    sys.exit(main())
