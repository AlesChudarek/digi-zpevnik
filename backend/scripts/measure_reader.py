"""Změří geometrii prohlížeče zpěvníku v knižním modu při různých proporcích okna.

Vzniklo kvůli tomu, že v telefonu otočeném na šířku jsou mezi stranami dvojstrany
obrovské mezery a auto zoom vypadá, že nepočítá s nízkým a širokým oknem. Layout
se nedá diagnostikovat z kódu - tenhle skript vytáhne čísla (getBoundingClientRect,
spočtené styly, vnitřní proměnné zoomu), žádné screenshoty.

Vyžaduje jednorázově:
    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium

Použití:
    python backend/scripts/measure_reader.py
    python backend/scripts/measure_reader.py --page 3    # na kterou stranu skočit

Běží proti KOPII databáze, takže na skutečná data nesahá.
"""
import argparse
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
PORT = 5598
PASSWORD = "measure-only"
BOOK = "/songbook/00006"

# (popis, šířka, výška) - telefon na šířku je ten problémový případ, ostatní jsou
# kontrolní vzorky, aby bylo vidět, kde se chování láme.
VIEWPORTS = [
    ("nízké široké okno",      1256, 500),
    ("telefon na výšku",        390, 844),
    ("telefon na šířku",        844, 390),
    ("tablet na výšku",         820, 1180),
    ("notebook",               1280, 800),
]

# Vše, co ovlivňuje velikost stran, na jedno čtení z živé stránky.
PROBE = """() => {
  const num = (v) => Math.round(v * 10) / 10;
  const wrapper = document.querySelector('.zoom-scroll-wrapper');
  const viewer = document.querySelector('.viewer-container');
  const pair = document.getElementById('double-page');
  const nav = document.getElementById('nav-controls');
  const left = document.getElementById('left-page');
  const right = document.getElementById('right-page');
  const visible = (img) => img && img.style.display !== 'none' && img.getAttribute('src');
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: num(r.left), right: num(r.right), top: num(r.top), bottom: num(r.bottom),
            width: num(r.width), height: num(r.height)};
  };
  const pairStyles = getComputedStyle(pair);
  const l = box(visible(left) ? left : null);
  const r = box(visible(right) ? right : null);
  return {
    innerW: window.innerWidth,
    innerH: window.innerHeight,
    wrapperW: num(wrapper ? wrapper.clientWidth : 0),
    wrapperH: num(wrapper ? wrapper.clientHeight : 0),
    navH: nav ? num(nav.offsetHeight) : 0,
    viewerPadTop: num(parseFloat(getComputedStyle(viewer).paddingTop) || 0),
    viewerPadBottom: num(parseFloat(getComputedStyle(viewer).paddingBottom) || 0),
    cssGap: pairStyles.columnGap,
    cssMarginBottom: pairStyles.marginBottom,
    pairBox: box(pair),
    // .page-pair má overflow-x: auto, takže přesah dvojstrany se navenek neprojeví
    // jako přetečení stránky - musí se číst zevnitř
    pairScrollW: num(pair.scrollWidth),
    pairClientW: num(pair.clientWidth),
    viewerBox: box(viewer),
    wrapperScrollW: num(wrapper ? wrapper.scrollWidth : 0),
    wrapperScrollH: num(wrapper ? wrapper.scrollHeight : 0),
    wrapperBox: box(wrapper),
    viewerPadLeft: num(parseFloat(getComputedStyle(viewer).paddingLeft) || 0),
    viewerPadRight: num(parseFloat(getComputedStyle(viewer).paddingRight) || 0),
    // Plovoucí tlačítka po stranách: Aleš tvrdí, že se jim dvojstrana zbytečně vyhýbá.
    // Mizí po chvíli nečinnosti, takže rozhodovat má kraj obrazovky, ne ona.
    navBox: box(nav),
    zoomButtonsBox: box(document.querySelector('.zoom-buttons')),
    modeButtonsBox: box(document.querySelector('.mode-buttons')),
    // Je lišta se šipkami vůbec vidět bez rolování?
    navReachable: nav ? (nav.getBoundingClientRect().bottom <= window.innerHeight + 1) : null,
    mode: typeof currentMode !== 'undefined' ? currentMode : '?',
    // Skutečná výška lišty proti tomu, co si o ní myslí --navbar-h. Při zalomení textu
    // tlačítek lišta narostla, ale proměnná zůstala, takže prohlížeč odečítal výšku,
    // která neexistuje, a strana se schovala pod lištu.
    navbarH: num(document.querySelector('.navbar')?.getBoundingClientRect().height || 0),
    navbarVar: getComputedStyle(document.documentElement).getPropertyValue('--navbar-h').trim(),
    spacerH: num(document.querySelector('.navbar-spacer')?.getBoundingClientRect().height || 0),
    navbarHidden: document.body.classList.contains('navbar-hidden'),
    leftBox: l,
    rightBox: r,
    // vnitřní stav zoomu - jak si to sám spočítal
    zoomDouble: Math.round((typeof zoomDouble !== 'undefined' ? zoomDouble : -1) * 1000) / 1000,
    doubleBase: num(typeof doublePageBaseWidth === 'function' ? doublePageBaseWidth() : -1),
    availableViewer: num(typeof availableViewerWidth === 'function' ? availableViewerWidth() : -1),
    pageOverflowX: num(document.documentElement.scrollWidth - window.innerWidth),
  };
}"""


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


def report(label, w, h, m):
    print(f"\n── {label}  {w}×{h} ──  mód {m['mode']}")
    print(f"   wrapper {m['wrapperW']}×{m['wrapperH']} (obsah {m['wrapperScrollH']} vysoký)   "
          f"padding svisle {m['viewerPadTop']}/{m['viewerPadBottom']}   "
          f"vodorovně {m['viewerPadLeft']}/{m['viewerPadRight']}")
    print(f"   zoomDouble {m['zoomDouble']}   doubleBase {m['doubleBase']}   "
          f"availableViewer {m['availableViewer']}")
    print(f"   CSS gap {m['cssGap']}   margin-bottom {m['cssMarginBottom']}")
    for name, b in (("levá", m['leftBox']), ("pravá", m['rightBox'])):
        if b:
            print(f"   {name:5s} strana  {b['width']}×{b['height']}  "
                  f"x {b['left']}→{b['right']}")
        else:
            print(f"   {name:5s} strana  skrytá")

    l, r = m['leftBox'], m['rightBox']
    if l and r:
        skutecna_mezera = round(r['left'] - l['right'], 1)
        sirka_stran = l['width'] + r['width']
        print(f"   ➜ mezera mezi stranami {skutecna_mezera} px "
              f"({round(100 * skutecna_mezera / max(1, l['width']), 1)} % šířky strany)")
        volno_po_stranach = round(m['innerW'] - sirka_stran - skutecna_mezera, 1)
        print(f"   ➜ nevyužito po stranách {volno_po_stranach} px, "
              f"pod stranami {round(m['wrapperH'] - l['height'], 1)} px")
    # Se schovanou lištou je nulový spacer záměr, ne nesoulad
    rozdil = 0 if m['navbarHidden'] else round(m['navbarH'] - m['spacerH'], 1)
    flag = "  ⚠️ lišta je vyšší, než se s ní počítá" if rozdil > 1 else ""
    print(f"   horní lišta {m['navbarH']} px, --navbar-h {m['navbarVar']}, "
          f"spacer {m['spacerH']} px{flag}")

    nav = m['navBox']
    if nav:
        pod_navem = round(m['wrapperH'] - nav['height'] - (nav['top'] - m['wrapperBox']['top']), 1)
        print(f"   lišta šipek  y {nav['top']}→{nav['bottom']} ({nav['height']} px)   "
              f"pod ní {pod_navem} px   "
              f"{'dosažitelná' if m['navReachable'] else '⚠️ MIMO OBRAZOVKU'}")
    for name, b in (("zoom", m['zoomButtonsBox']), ("mód", m['modeButtonsBox'])):
        if b:
            print(f"   boční tlačítka {name:5s} x {b['left']}→{b['right']}  y {b['top']}→{b['bottom']}")

    pair_over = round(m['pairScrollW'] - m['pairClientW'], 1)
    wrap_over = round(m['wrapperScrollW'] - m['wrapperW'], 1)
    if pair_over > 1:
        print(f"   ⚠️ dvojstrana přetéká uvnitř .page-pair o {pair_over} px")
    if wrap_over > 1:
        print(f"   ⚠️ obsah přetéká z okna doprava o {wrap_over} px")
    if m['pageOverflowX'] > 0:
        print(f"   ⚠️ stránka přetéká o {m['pageOverflowX']} px")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", default="3", help="číslo strany, na kterou skočit")
    parser.add_argument("--sweep", action="store_true",
                        help="jen přejet šířky a najít, kde se horní lišta zalomí")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright. Nainstaluj:\n"
                         "   .venv/bin/pip install playwright\n"
                         "   .venv/bin/playwright install chromium")

    tmp = Path(tempfile.mkdtemp(prefix="measure-reader-"))
    db_copy = tmp / "measure.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)

    server, base = start_server(db_copy)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            jump = (f"() => {{ document.getElementById('page-input').value = "
                    f"'{args.page}'; jumpToPage(); }}")

            if args.sweep:
                page.goto(base + BOOK)
                page.wait_for_load_state("networkidle")
                print("šířka  lišta  --navbar-h  spacer  hrana str.  zoom tl.")
                for w in range(1500, 300, -20):
                    page.set_viewport_size({"width": w, "height": 800})
                    # Spacer má přechod výšky 0.3 s, takže kratší čekání měří animaci
                    page.wait_for_timeout(420)
                    m = page.evaluate(PROBE)
                    top = (m['leftBox'] or m['rightBox'] or {}).get('top', 0)
                    zoom_top = (m['zoomButtonsBox'] or {}).get('top', 0)
                    mode_top = (m['modeButtonsBox'] or {}).get('top', 0)
                    warn = ""
                    if m['navbarH'] - m['spacerH'] > 1:
                        warn = f"  ⚠️ o {round(m['navbarH'] - m['spacerH'], 1)} px víc"
                    if m['navbarH'] > top > 0:
                        warn += f"  ⚠️ zakrývá stranu o {round(m['navbarH'] - top, 1)} px"
                    # Boční tlačítka mají pod lištou zbýt 20 px (18 na úzkém okně)
                    for jmeno, t in (("zoom", zoom_top), ("mód", mode_top)):
                        odstup = round(t - m['navbarH'], 1)
                        if odstup < 14:
                            warn += f"  ⚠️ {jmeno} tlačítka {odstup} px pod lištou"
                    print(f"{w:>5}  {m['navbarH']:>5}  {m['navbarVar']:>9}  "
                          f"{m['spacerH']:>6}  {top:>10}  {zoom_top:>6}{warn}")
                browser.close()
                return 0

            for label, w, h in VIEWPORTS:
                # Dvě cesty kódem, které se chovají jinak: čerstvé načtení projde
                # počátečním auto-fitem, kdežto změna velikosti okna jde přes
                # posluchače resize. Aleš testuje tu druhou, tak se měří obě.
                page.set_viewport_size({"width": w, "height": h})
                page.goto(base + BOOK)
                page.wait_for_load_state("networkidle")
                # Na titulce je vidět jen jedna strana, tak přeskoč na dvojstranu.
                # Voláno přes JS, protože pole strany je na úzkém okně schované.
                page.evaluate(jump)
                page.wait_for_timeout(700)
                report(f"{label} (načteno)", w, h, page.evaluate(PROBE))

                # Bez reloadu: nejdřív jiná velikost, pak zpět na tu měřenou
                page.set_viewport_size({"width": 1000, "height": 900})
                page.wait_for_timeout(400)
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(700)
                report(f"{label} (zvětšeno oknem)", w, h, page.evaluate(PROBE))

                # A ještě: fit proběhl na titulce s jednou stranou, pak se listuje
                page.set_viewport_size({"width": w, "height": h})
                page.goto(base + BOOK)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(700)
                page.evaluate("() => nextPage()")
                page.wait_for_timeout(700)
                report(f"{label} (z titulky listováním)", w, h, page.evaluate(PROBE))

                # Na úzkém okně se startuje v rolovacím módu, takže do knižního se lidi
                # dostanou tlačítkem - a switchToDouble záměrně nefituje, jen podědí zoom.
                # Přesně tahle cesta se dosud neměřila.
                page.evaluate("() => { if (currentMode !== 'double') toggleReadingMode(); }")
                page.wait_for_timeout(500)
                page.evaluate("() => nextPage()")
                page.wait_for_timeout(700)
                report(f"{label} (přepnuto do knižního)", w, h, page.evaluate(PROBE))

                # Schovaná lišta uvolní celé okno. Obsah zpěvníku se posouval nahoru vždy,
                # boční tlačítka ne - zůstávala trčet v pásmu, kde lišta bývala.
                page.evaluate("() => toggleNavbar()")
                page.wait_for_timeout(700)
                report(f"{label} (schovaná horní lišta)", w, h, page.evaluate(PROBE))

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
