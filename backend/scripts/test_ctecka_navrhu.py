#!/usr/bin/env python3
"""Přejímací test navržených stran ve čtečce (TODO bod 11, krok 3b).

Čtečka kreslí navrženou stranu z textu (static/js/strana/ctecka.js): nejdřív náhled,
přes něj ostrá strana, líně jen kolem místa čtení. Ověřuje dvojstranu i svitek, číslo
a L/P z pozice, přeškálování při zoomu, přechod mezi režimy a že export se nezměnil.

Server běží v tomhle procesu nad kopií databáze a klonem obrázků (verze stran se
ukládají), skutečných dat se nedotkne. Měří se čísly, ne screenshoty. Chromium, Firefox
a WebKit.

    python backend/scripts/test_ctecka_navrhu.py
"""

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

TMP = Path(tempfile.mkdtemp(prefix="ctecka-navrhu-"))
DB = TMP / "ctecka.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "ctecka-navrhu")

import backend.app as A  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)], capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None
PORT = 5592
HESLO = "ctecka-navrhu"
ZPEVNIK = "test-ctecka-navrhu"
BEZ_NAVRHU = "test-ctecka-bez-navrhu"

with A.app.app_context():
    u = A.db.session.get(A.User, 3)
    u.password = generate_password_hash(HESLO, method="pbkdf2:sha256:1000")
    u.role = "premium"
    # Limity účtu v kopii nejsou předmětem testu (lokálně se mění).
    u.limit_mista = None
    EMAIL = u.email
    # Obrázek strany, který v klonu opravdu leží.
    OBRAZEK_ID = next(o.id for o in A.Obrazek.query.filter(A.Obrazek.cesta.like("%.png")).limit(200)
                      if (OBRAZKY / o.cesta).is_file())
    for sb_id, nazev in ((ZPEVNIK, "Čtečka navržených stran"), (BEZ_NAVRHU, "Čtečka bez navržených")):
        A.db.session.add(A.Songbook(id=sb_id, title=nazev, owner_id=3, is_public=0,
                                    prvni_cislo_strany=1, first_page_side="right"))
    A.db.session.add(A.Strana(songbook_id=BEZ_NAVRHU, poradi=0, image_id=OBRAZEK_ID))
    A.db.session.commit()

selhani = []


def ok(popis, podminka, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def prihlas(prohlizec, base):
    page = prohlizec.new_page(viewport={"width": 1400, "height": 1000})
    chyby = []
    page.on("pageerror", lambda e: chyby.append(str(e)))
    page.goto(base + "/login")
    page.fill('input[name="email"]', EMAIL)
    page.fill('input[name="password"]', HESLO)
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_load_state("networkidle")
    return page, chyby


# Dvě verze stran vyrobí editor stran tak, jak to dělá Hotovo (zdroj, PDF, náhled 420 px).
VYROB_VERZI = """async ([nazev, radky]) => {
  navrhStrany.nahradTextem(nazev, 'Autor', radky);
  await document.fonts.ready;
  const zdroj = navrhStrany.zdroj();
  const { pdfStrany } = await import('/static/js/strana/pdf-strany.js');
  const { bytes } = await pdfStrany(zdroj, {});
  const { blob } = await navrhStrany.obrazekStrany(zdroj, { sirkaPx: 420 });
  const fd = new FormData();
  fd.append('zdroj', JSON.stringify(zdroj));
  fd.append('pdf', new Blob([bytes], { type: 'application/pdf' }), 'strana.pdf');
  fd.append('nahled', blob, 'nahled.png');
  const r = await fetch('/api/navrh/strana', { method: 'POST', body: fd });
  return (await r.json()).id;
}"""


def vyrob_zpevnik(br, base):
    page, _ = prihlas(br, base)
    page.goto(base + "/editor-strany")
    page.wait_for_function("window.navrhStrany && document.fonts.status === 'loaded'", timeout=20000)
    a = page.evaluate(VYROB_VERZI, ["Píseň Alfa", "[Ami]První řádek alfa\n[C]druhý řádek"])
    b = page.evaluate(VYROB_VERZI, ["Píseň Beta", "[G]První řádek beta\n[D]druhý řádek"])
    page.close()
    # 0: A (pravá, č. 1), 1: obrázek, 2: B, 3: prázdná, 4-21: střídavě A/B, 22: obrázek
    poradi = [a, "img", b, None] + [a if i % 2 == 0 else b for i in range(18)] + ["img"]
    with A.app.app_context():
        for i, co in enumerate(poradi):
            A.db.session.add(A.Strana(songbook_id=ZPEVNIK, poradi=i,
                                      image_id=OBRAZEK_ID if co == "img" else None,
                                      navrzena_id=co if isinstance(co, int) else None))
        A.db.session.commit()
    return a, b, poradi


STAV_STRANY = """(el) => {
  const s = el.querySelector('.stranka');
  const c = el.querySelector('.ps-cislo-strany');
  const r = el.getBoundingClientRect();
  return {
    ostra: el.classList.contains('ostra'),
    text: s ? s.innerText : '',
    sirka: r.width, vyska: r.height,
    sirkaStrany: s ? s.getBoundingClientRect().width : 0,
    cislo: c ? c.textContent : null,
    cisloX: c ? (c.getBoundingClientRect().left + c.getBoundingClientRect().width / 2 - r.left) / r.width : null,
    nahled: (el.querySelector('.strana-navrh-nahled') || {}).naturalWidth || 0,
  };
}"""


def cekej_na_ostrou(page, selektor, timeout=20000):
    page.wait_for_function(f"(() => {{ const e = document.querySelector({selektor!r}); return e && e.classList.contains('ostra'); }})()",
                           timeout=timeout)


def test_prohlizec(p, jmeno, base, a, b, poradi):
    print(f"\n══ {jmeno} ══")
    br = getattr(p, jmeno).launch()
    page, chyby = prihlas(br, base)
    page.goto(f"{base}/songbook/{ZPEVNIK}")
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("enableDoublePageMode()")

    print("── dvojstrana ──")
    # Zpěvník začíná vpravo: první dvojstrana je „nic | strana 1 (A)“.
    cekej_na_ostrou(page, "#right-page-navrh")
    s = page.eval_on_selector("#right-page-navrh", STAV_STRANY)
    ok("první strana se nakreslí z textu", s["ostra"] and "Píseň Alfa" in s["text"] and "První řádek alfa" in s["text"], s["text"][:60])
    ok("náhled pod ní je uložený náhled 420 px", s["nahled"] == 420, s["nahled"])
    ok("strana vyplní rámeček na šířku (±1 px)", abs(s["sirkaStrany"] - s["sirka"]) <= 1, (s["sirkaStrany"], s["sirka"]))
    ok("rámeček má poměr strany jako obrázky", abs(s["vyska"] / s["sirka"] - 2480 / 1748) < 0.01, s["vyska"] / s["sirka"])
    ok("číslo strany z pozice (1)", s["cislo"] == "1", s["cislo"])
    ok("levý obrázek strany je schovaný", page.eval_on_selector("#left-page", "e => getComputedStyle(e).display") == "none")
    ok("písma strany jsou načtená, když se ukáže",
       page.evaluate("document.fonts.check(\"12px 'Noto Sans'\") && document.fonts.check(\"12px 'Roboto Condensed'\")"))

    page.evaluate("zoomIn()")
    page.wait_for_timeout(200)
    s2 = page.eval_on_selector("#right-page-navrh", STAV_STRANY)
    ok("zoom: strana se přeškáluje s rámečkem", s2["sirka"] > s["sirka"] + 10 and abs(s2["sirkaStrany"] - s2["sirka"]) <= 1,
       (s["sirka"], s2["sirka"], s2["sirkaStrany"]))
    page.evaluate("zoomOut()")

    page.evaluate("nextPage()")   # obrázek | B (č. 2 vlevo, č. 3 vpravo)
    cekej_na_ostrou(page, "#right-page-navrh")
    s = page.eval_on_selector("#right-page-navrh", STAV_STRANY)
    ok("přelistování: tentýž rámeček ukáže jinou stranu", "Píseň Beta" in s["text"] and "Alfa" not in s["text"], s["text"][:40])
    ok("vlevo je obrázek strany", page.eval_on_selector("#left-page", "e => getComputedStyle(e).display") == "block"
       and page.eval_on_selector("#left-page-navrh", "e => getComputedStyle(e).display") == "none")
    ok("číslo 3", s["cislo"] == "3", s["cislo"])
    xp = s["cisloX"]

    page.evaluate("nextPage()")   # prázdná (4) | A (5)
    page.evaluate("nextPage()")   # B (6) | A (7)
    cekej_na_ostrou(page, "#left-page-navrh")
    cekej_na_ostrou(page, "#right-page-navrh")
    l = page.eval_on_selector("#left-page-navrh", STAV_STRANY)
    r = page.eval_on_selector("#right-page-navrh", STAV_STRANY)
    ok("dvojstrana dvou navržených stran", "Beta" in l["text"] and "Alfa" in r["text"])
    ok("čísla 6 a 7", (l["cislo"], r["cislo"]) == ("6", "7"), (l["cislo"], r["cislo"]))
    ok("číslo na levé je zrcadlově k pravé", l["cisloX"] is not None and abs(l["cisloX"] - (1 - xp)) < 0.02, (l["cisloX"], xp))
    ok("pole strany ukazuje 6–7", page.input_value("#page-input") == "6–7", page.input_value("#page-input"))

    print("── svitek ──")
    page.evaluate("enableScrollMode()")
    page.wait_for_timeout(300)
    svitek = page.evaluate("""[...document.querySelectorAll('#scroll-mode .strana-svitku')].map(e => ({
      navrh: e.dataset.navrh || null, file: e.dataset.file, lp: e.dataset.lp || null, cislo: e.dataset.cislo || null,
      sirka: e.getBoundingClientRect().width, ostra: e.classList.contains('ostra')}))""")
    navrzene = [x for x in svitek if x["navrh"]]
    ok("ve svitku jsou všechny navržené strany (20)", len(navrzene) == 20, len(navrzene))
    ok("ve svitku jsou obrázky (2) a prázdná chybí jako dřív", len(svitek) == 22, len(svitek))
    ok("L/P podle pozice (1 = P, 6 = L, 7 = P)",
       [(x["cislo"], x["lp"]) for x in navrzene[:4]] == [("1", "P"), ("3", "P"), ("5", "P"), ("6", "L")],
       [(x["cislo"], x["lp"]) for x in navrzene[:4]])
    ok("rámeček má šířku jako obrázek strany",
       abs(navrzene[0]["sirka"] - next(x["sirka"] for x in svitek if not x["navrh"])) < 1)
    page.wait_for_function("document.querySelector('#scroll-mode .strana-navrh[data-cislo=\"6\"]').classList.contains('ostra')", timeout=20000)
    vykreslene = page.evaluate("document.querySelectorAll('#scroll-mode .strana-navrh .stranka').length")
    ok("kreslí se líně: jen strany kolem místa čtení", 0 < vykreslene < 20, vykreslene)
    ok("poslední navržená strana zatím nakreslená není",
       page.evaluate("!document.querySelector('#scroll-mode .strana-navrh[data-cislo=\"22\"] .stranka')"))
    page.evaluate("document.querySelector('#scroll-mode .strana-navrh[data-cislo=\"22\"]').scrollIntoView()")
    page.wait_for_function("document.querySelector('#scroll-mode .strana-navrh[data-cislo=\"22\"]').classList.contains('ostra')", timeout=20000)
    s = page.eval_on_selector('#scroll-mode .strana-navrh[data-cislo="22"]', STAV_STRANY)
    ok("po sjetí se dokreslí i ona", "Píseň Beta" in s["text"] and s["cislo"] == "22", (s["text"][:20], s["cislo"]))
    ok("ve svitku vyplní rámeček (±1 px)", abs(s["sirkaStrany"] - s["sirka"]) <= 1, (s["sirkaStrany"], s["sirka"]))

    page.evaluate("document.querySelector('#scroll-mode .strana-navrh[data-cislo=\"14\"]').scrollIntoView({block: 'center'})")
    page.wait_for_timeout(300)
    page.evaluate("enableDoublePageMode()")
    ok("svitek → dvojstrana zůstane na navržené straně (14–15)", page.input_value("#page-input") == "14–15",
       page.input_value("#page-input"))
    page.evaluate("enableScrollMode()")
    page.wait_for_timeout(300)
    stred = page.evaluate("""(() => { const w = document.querySelector('.zoom-scroll-wrapper').getBoundingClientRect();
      const y = w.top + w.height / 2;
      const e = [...document.querySelectorAll('#scroll-mode .strana-svitku')].find(e => { const r = e.getBoundingClientRect(); return r.top <= y && r.bottom >= y; });
      return e ? e.dataset.cislo || e.dataset.pageNumber : null; })()""")
    ok("dvojstrana → svitek se vrátí na tutéž stranu", stred in ("14", "15"), stred)
    ok("bez chyb JavaScriptu", not chyby, chyby[:3])

    print("── zpěvník bez navržených stran ──")
    page.goto(f"{base}/songbook/{BEZ_NAVRHU}")
    page.wait_for_load_state("networkidle")
    ok("nenačítá moduly ani písma editoru stran",
       page.evaluate("!document.querySelector('script[type=importmap]') && !document.querySelector('link[href*=\"strana.css\"]')"))
    br.close()


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", PORT, A.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            a, b, poradi = vyrob_zpevnik(br, base)
            br.close()
            print("── server ──")
            with A.app.app_context():
                obsah = A.build_songbook_content_pages(ZPEVNIK)
                ok("obsah zpěvníku nese id verze a náhled", obsah[0].get("navrh") == a and obsah[0].get("ma_nahled"), obsah[0])
                sekv = A.build_songbook_export_sequence(A.db.session.get(A.Songbook, ZPEVNIK), None)
                ok("export navrženou stranu zatím bere jako prázdnou (krok 3c)",
                   sekv[0]["file"] == "blank" and "navrh" not in sekv[0], sekv[0])
            for jmeno in ("chromium", "firefox", "webkit"):
                test_prohlizec(p, jmeno, base, a, b, poradi)
    finally:
        server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Navržené strany ve čtečce v pořádku.")


if __name__ == "__main__":
    main()
