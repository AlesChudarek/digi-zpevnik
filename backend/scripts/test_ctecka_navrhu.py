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
    # Verze stran se vyrábějí v editoru nad pomocným zpěvníkem (do testovaného se
    # připojí až níž přímo v databázi).
    with A.app.app_context():
        A.db.session.add(A.Songbook(id="test-ctecka-pomocny", title="Pomocný", owner_id=3, is_public=0))
        A.db.session.add(A.Strana(songbook_id="test-ctecka-pomocny", poradi=0))
        A.db.session.commit()
        pomocna = A.Strana.query.filter_by(songbook_id="test-ctecka-pomocny").first().id
    page.goto(f"{base}/editor-strany/{pomocna}")
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

    if jmeno == "chromium":
        # Číslo strany, které dokresluje server, sedí tam, kde ho kreslí prohlížeč (PDF
        # strany z prohlížeče s číslem - pdf-strany.js z editoru).
        import base64, io
        from pypdf import PdfReader
        b64 = page.evaluate("""async (id) => {
          const d = await (await fetch('/api/navrh/strana/' + id)).json();
          const { pdfStrany } = await import('/static/js/strana/pdf-strany.js');
          const { bytes } = await pdfStrany(d.zdroj, { cislo: 1, strana: 'P' });
          let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); }""", a)
        prohlizec = []
        stranka = PdfReader(io.BytesIO(base64.b64decode(b64))).pages[0]
        vyska_pt = float(stranka.mediabox.height)
        def vis(text, cm, tm, font, size):
            if text.strip() == "1":
                prohlizec.append(((cm[0] * tm[4] + cm[2] * tm[5] + cm[4]) * 25.4 / 72,
                                  (vyska_pt - (cm[1] * tm[4] + cm[3] * tm[5] + cm[5])) * 25.4 / 72))
        stranka.extract_text(visitor_text=vis)
        # Poslední „1“ je číslo strany (značka sloky „1.“ je v textu dřív).
        with A.app.app_context():
            sekv = A.build_songbook_export_sequence(A.db.session.get(A.Songbook, ZPEVNIK), None)
        vystup = TMP / "jedna.pdf"
        A.render_songbook_pdf(sekv[:1], vystup, A.normalizuj_recept(format="pdf"))
        st = PdfReader(str(vystup)).pages[0]
        sw, sv = float(st.mediabox.width), float(st.mediabox.height)
        k = min(sw / (148 * 72 / 25.4), sv / (210 * 72 / 25.4))
        offx = (sw - 148 * 72 / 25.4 * k) / 2
        server = []
        def vis2(text, cm, tm, font, size):
            if text.strip() == "1":
                server.append((((cm[0] * tm[4] + cm[2] * tm[5] + cm[4]) - offx) / k * 25.4 / 72,
                               (sv - (cm[1] * tm[4] + cm[3] * tm[5] + cm[5])) / k * 25.4 / 72))
        st.extract_text(visitor_text=vis2)
        rozdil = (abs(prohlizec[-1][0] - server[-1][0]), abs(prohlizec[-1][1] - server[-1][1])) if prohlizec and server else None
        ok("číslo strany ze serveru sedí s prohlížečem (do 0,3 mm)", rozdil is not None and max(rozdil) <= 0.3,
           (prohlizec[-1:], server[-1:], rozdil))

    print("── zpěvník bez navržených stran ──")
    page.goto(f"{base}/songbook/{BEZ_NAVRHU}")
    page.wait_for_load_state("networkidle")
    ok("nenačítá moduly ani písma editoru stran",
       page.evaluate("!document.querySelector('script[type=importmap]') && !document.querySelector('link[href*=\"strana.css\"]')"))
    br.close()


def test_skenu(p, base):
    """Strana s nahraným obrázkem v editoru stran: sken jako podklad, náplast, uložení;
    pak ji čtečka i PDF ukážou jako navrženou stranu se skenem a náplastí."""
    print("\n══ obrázková strana v editoru stran (sken + náplast) ══")
    with A.app.app_context():
        strana_id = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=1).first().id
    br = p.chromium.launch()
    page, chyby = prihlas(br, base)
    page.goto(f"{base}/editor-strany/{strana_id}")
    page.wait_for_function("window.navrhStrany && document.fonts.status === 'loaded'", timeout=20000)
    doc = page.evaluate("navrhStrany.view.state.doc.toJSON()")
    sken = doc["content"][0]
    ok("sken je první prvek strany: obrázek za textem", sken["type"] == "obrazek" and sken["attrs"]["vrstva"] == "za"
       and sken["attrs"]["src"] == f"/navrh/obrazek/{OBRAZEK_ID}", sken)
    a = sken["attrs"]
    ok("a vyplní stranu A5 (na šířku nebo na výšku, vystředěný)",
       (abs(a["w"] - 148) < 0.2 or abs(a["h"] - 210) < 0.2) and abs(a["x"] * 2 + a["w"] - 148) < 0.3 and abs(a["y"] * 2 + a["h"] - 210) < 0.3, a)
    ok("strana je jen sken, text se přidá rámečkem (žádný povinný)", [n["type"] for n in doc["content"]] == ["obrazek"], [n["type"] for n in doc["content"]])
    page.click('[data-prikaz="naplast"]')
    ok("Náplast zapne nástroj (nic nevloží) a ukáže kartu s druhem",
       page.evaluate("navrhStrany.view.state.doc.lastChild.type.name") != "tvar" and page.is_visible("#vybrany-tvar")
       and page.is_visible("#tvar-druh") and page.evaluate("document.querySelector('.navrh').classList.contains('rezim-naplasti')"))
    page.evaluate("(() => { const i = document.getElementById('tvar-barva'); i.value = '#ff0000'; i.dispatchEvent(new Event('change')); })()")
    r = page.evaluate("document.getElementById('stranka').getBoundingClientRect().toJSON()")
    k = r["width"] / 148            # px na mm
    PRVKY = "navrhStrany.view.state.doc.toJSON().content.filter(n => n.type === 'tvar').map(n => n.attrs)"
    page.mouse.click(r["x"] + 40 * k, r["y"] + 50 * k)
    t = page.evaluate(PRVKY)
    page.keyboard.press("Backspace")
    ok("nová vybraná náplast jde hned smazat Backspacem", len(page.evaluate(PRVKY)) == 0)
    page.mouse.click(r["x"] + 40 * k, r["y"] + 50 * k)
    t = page.evaluate(PRVKY)
    ok("klik vloží malý obdélník v barvě nástroje", len(t) == 1 and t[0]["w"] == 8 and t[0]["h"] == 4
       and abs(t[0]["x"] - 36) < 0.3 and t[0]["barva"] == "#ff0000" and not t[0]["body"], t)
    page.mouse.move(r["x"] + 60 * k, r["y"] + 100 * k)
    page.mouse.down()
    page.mouse.move(r["x"] + 61.5 * k, r["y"] + 101 * k, steps=4)
    page.mouse.up()
    t = page.evaluate(PRVKY)
    ok("tažení vloží obdélník podle tahu, i velmi malý (1,5 × 1 mm)", len(t) == 2 and abs(t[1]["w"] - 1.5) < 0.25
       and abs(t[1]["h"] - 1) < 0.25, t[-1:])
    page.click('[data-prikaz="naplast-volny"]')
    for (x, y) in ((90, 20), (110, 25), (100, 45)):
        page.mouse.click(r["x"] + x * k, r["y"] + y * k)
    page.mouse.click(r["x"] + 90.3 * k, r["y"] + 20.2 * k)       # klik na první bod uzavře
    t = page.evaluate(PRVKY)
    ok("volný tvar: klikání body, klik na první bod ho uzavře", len(t) == 3 and t[2]["body"]
       and len(t[2]["body"].split()) == 3 and abs(t[2]["x"] - 90) < 0.3 and abs(t[2]["w"] - 20) < 0.3, t[-1:])
    obrys = page.evaluate("[...document.querySelectorAll('.ps-tvar polygon')].map(p => getComputedStyle(p).stroke)")
    ok("při zapnutém nástroji mají všechny náplasti obrys", all(o != "none" for o in obrys), obrys)
    ok("náplast nemá horní lištu", page.locator(".ps-tvar .ps-prvek-lista").count() == 0)
    page.click('[data-prikaz="naplast-obdelnik"]')
    page.mouse.click(r["x"] + 40 * k, r["y"] + 50 * k)
    ok("klik na existující náplast ji vybere, novou nevytvoří", len(page.evaluate(PRVKY)) == 3
       and page.evaluate("navrhStrany.view.state.selection.node?.attrs.w") == 8)
    page.mouse.move(r["x"] + 40 * k, r["y"] + 50 * k)
    page.mouse.down()
    page.mouse.move(r["x"] - 60 * k, r["y"] + 50 * k, steps=6)
    page.mouse.up()
    x = page.evaluate("navrhStrany.view.state.doc.toJSON().content.filter(n => n.type === 'tvar')[0].attrs.x")
    ok("náplast jde odsunout celou mimo stranu (o 100 mm, editor ji ukáže zašedlou)", x == -64, x)
    page.keyboard.press("ControlOrMeta+z")
    kurzor = page.evaluate("getComputedStyle(document.querySelector('.ps-obrazek img')).cursor")
    ok("s nástrojem je nad obrázkem křížek, ne přesun", kurzor == "crosshair", kurzor)
    ok("u vybrané náplasti je i Smazat všechny (3)", page.is_visible("#tvar-smazat-vse")
       and page.inner_text("#tvar-smazat-vse") == "Smazat všechny (3)", page.inner_text("#tvar-smazat-vse"))
    page.click("#tvar-smazat-vse")
    ok("Smazat všechny smaže všechny náplasti strany", len(page.evaluate(PRVKY)) == 0)
    page.keyboard.press("ControlOrMeta+z")
    ok("a Zpět je vrátí jedním krokem", len(page.evaluate(PRVKY)) == 3, len(page.evaluate(PRVKY)))
    page.keyboard.press("Escape")
    ok("Esc nástroj vypne a obrysy nevybraných náplastí zmizí", not page.evaluate("document.querySelector('.navrh').classList.contains('rezim-naplasti')")
       and page.evaluate("getComputedStyle(document.querySelectorAll('.ps-tvar polygon')[2]).stroke") == "none")
    page.click('[data-prikaz="naplast"]')
    page.click('[data-prikaz="naplast-volny"]')
    page.mouse.click(r["x"] + 20 * k, r["y"] + 150 * k)
    page.mouse.click(r["x"] + 30 * k, r["y"] + 150 * k)
    page.keyboard.press("Escape")
    ok("Esc při kreslení zahodí jen rozkreslený tvar", len(page.evaluate(PRVKY)) == 3
       and page.evaluate("document.querySelector('.navrh').classList.contains('rezim-naplasti')"))
    page.keyboard.press("Escape")
    page.click('[data-prikaz="ulozit"]')
    page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
    with A.app.app_context():
        st = A.db.session.get(A.Strana, strana_id)
        navrh_id, obrazky = st.navrzena_id, [o.id for o in (st.navrzena.obrazky if st.navrzena else [])]
        ok("strana je teď navržená a sken je její součástí", st.image_id is None and navrh_id and obrazky == [OBRAZEK_ID],
           (st.image_id, navrh_id, obrazky))
        cesta_skenu = A.db.session.get(A.Obrazek, OBRAZEK_ID).cesta
    ok("soubor skenu zůstal", (OBRAZKY / cesta_skenu).is_file())

    page.goto(f"{base}/songbook/{ZPEVNIK}")
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("enableDoublePageMode(); nextPage()")     # strana 2 vlevo
    cekej_na_ostrou(page, "#left-page-navrh")
    ctecka = page.evaluate("""(() => { const e = document.querySelector('#left-page-navrh');
      const t = e.querySelectorAll('.ps-tvar polygon'), i = e.querySelector('.ps-obrazek img');
      return [[...t].map(p => p.getAttribute('fill')), i ? i.naturalWidth > 0 : false]; })()""")
    ok("čtečka ukáže sken i náplasti", ctecka == [["#ff0000"] * 3, True], ctecka)
    print("── dvojstrana se skenem vpravo ──")
    with A.app.app_context():
        leva = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=21).first()       # 22, levá
        prava = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=22).first()      # 23, obrázek
        leva_id, leva_navrh, prava_id = leva.id, leva.navrzena_id, prava.id
    page.goto(f"{base}/editor-strany/{leva_id}")
    page.evaluate("localStorage.setItem('navrh-rezim-stran', 'dvojstrana')")
    page.goto(f"{base}/editor-strany/{leva_id}")
    page.wait_for_function("window.navrhStrany && document.fonts.status === 'loaded'", timeout=20000)
    sken = page.evaluate("navrhStrany.view.state.doc.toJSON().content.filter(n => n.type === 'obrazek').map(n => n.attrs)")
    ok("protější strana se skenem je na plátně vpravo jako podklad", len(sken) == 1 and sken[0]["x"] >= 148
       and sken[0]["vrstva"] == "za", sken)
    page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Z', 2)); })()")
    page.click('[data-prikaz="ulozit"]')
    page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
    with A.app.app_context():
        A.db.session.expire_all()
        l2, p2 = A.db.session.get(A.Strana, leva_id), A.db.session.get(A.Strana, prava_id)
        ok("uloží se jen změněná levá strana, nedotčený sken zůstane obrázkovou stranou",
           l2.navrzena_id != leva_navrh and p2.image_id == OBRAZEK_ID and p2.navrzena_id is None,
           (l2.navrzena_id, leva_navrh, p2.image_id, p2.navrzena_id))
    page.evaluate("localStorage.setItem('navrh-rezim-stran', 'jedna')")
    ok("bez chyb JavaScriptu", not chyby, chyby[:3])
    br.close()

    from pypdf import PdfReader
    with A.app.app_context():
        sekv = A.build_songbook_export_sequence(A.db.session.get(A.Songbook, ZPEVNIK), None)
        vystup = TMP / "export-sken.pdf"
        A.render_songbook_pdf(sekv, vystup, A.normalizuj_recept(format="pdf"))
    strana2 = PdfReader(str(vystup)).pages[1]
    obsah = strana2.get_contents().get_data()
    xobj = strana2["/Resources"].get("/XObject")
    ok("v PDF je sken jako obrázek a náplasti jako červené vektorové tvary",
       xobj is not None and obsah.count(b"1 0 0 rg") >= 3 and b" l" in obsah, (bool(xobj), obsah.count(b"1 0 0 rg")))


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
                ok("export ví, že první strana je navržená", sekv[0].get("navrh") == a and sekv[0].get("navrh_pdf"), sekv[0])
                kratky = A.normalizuj_recept(format="pdf", prazdne=False)
                bez_prazdnych = A.build_songbook_export_sequence(A.db.session.get(A.Songbook, ZPEVNIK), kratky)
                ok("bez prázdných stran navržené zůstanou (vypadne jen jedna prázdná)",
                   len(bez_prazdnych) == len(poradi) - 1, (len(bez_prazdnych), len(poradi)))
                klic_a = A.songbook_export_key(sekv, "x")
                jina = [dict(sekv[0], navrh=b)] + sekv[1:]
                ok("jiná verze strany dá jiný klíč cache exportu", A.songbook_export_key(jina, "x") != klic_a)

                from pypdf import PdfReader
                import zipfile
                vystup = TMP / "export.pdf"
                A.render_songbook_pdf(sekv, vystup, A.normalizuj_recept(format="pdf"))
                pdf = PdfReader(str(vystup))
                ok("PDF má všechny strany", len(pdf.pages) == len(poradi), len(pdf.pages))
                text0 = " ".join((pdf.pages[0].extract_text() or "").split())
                ok("navržená strana je v PDF jako text", "Píseň Alfa" in text0 and "alfa" in text0, text0[:60])
                ok("a ostatní strany zůstaly obrázky (bez textu)", not (pdf.pages[1].extract_text() or "").strip())
                r0, r1 = pdf.pages[0].mediabox, pdf.pages[1].mediabox
                ok("navržená strana má rozměr jako ostatní (A4)",
                   abs(float(r0.width) - float(r1.width)) < 1 and abs(float(r0.height) - float(r1.height)) < 1,
                   (float(r0.width), float(r0.height), float(r1.width), float(r1.height)))
                ok("střídání verzí sedí (strana 3 = Beta)", "Píseň Beta" in " ".join((pdf.pages[2].extract_text() or "").split()))
                vystup_zip = TMP / "export.zip"
                A.render_songbook_zip(sekv, vystup_zip)
                with zipfile.ZipFile(vystup_zip) as z:
                    jmena = z.namelist()
                ok("ZIP má navržené strany jako PDF", sum(j.endswith(".pdf") for j in jmena) == 20
                   and jmena[0] == "0001-strana-1.pdf", jmena[:3])

                # Číslo strany na navržené straně dokreslí server: poloha v mm strany A5.
                def cisla(stranka):
                    nalez = []
                    def vis(text, cm, tm, font, size):
                        if text.strip().isdigit():
                            x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
                            y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
                            nalez.append((text.strip(), x, y))
                    stranka.extract_text(visitor_text=vis)
                    return nalez
                sirka_a4, vyska_a4 = float(pdf.pages[0].mediabox.width), float(pdf.pages[0].mediabox.height)
                k = min(sirka_a4 / (148 * 72 / 25.4), vyska_a4 / (210 * 72 / 25.4))
                offx = (sirka_a4 - 148 * 72 / 25.4 * k) / 2
                na_a5 = lambda x, y: ((x - offx) / k / (72 / 25.4), (vyska_a4 - y) / k / (72 / 25.4))
                c1 = [na_a5(x, y) for t, x, y in cisla(pdf.pages[0]) if t == "1"]
                ok("navržená strana 1 (pravá) má v PDF číslo vpravo dole u vnějšího okraje",
                   any(134 <= x <= 140 and 203 <= y <= 209 for x, y in c1), c1)
                c6 = [na_a5(x, y) for t, x, y in cisla(pdf.pages[5]) if t == "6"]
                ok("navržená strana 6 (levá) má číslo zrcadlově vlevo", any(7 <= x <= 13 and 203 <= y <= 209 for x, y in c6), c6)

                vystup_v = TMP / "export-vodoznak.pdf"
                A.render_songbook_pdf(sekv, vystup_v, A.normalizuj_recept(format="pdf", vodoznak=True))
                ok("vodoznak je i na navržené straně", A.VODOZNAK_TEXT in PdfReader(str(vystup_v)).pages[0].extract_text())

                vystup_cb = TMP / "export-cb.pdf"
                A.render_songbook_pdf(sekv, vystup_cb, A.normalizuj_recept(format="pdf", cernobile=True))
                st0 = PdfReader(str(vystup_cb)).pages[0]
                ops = st0.get_contents().get_data()
                ok("černobíle: navržená strana bez barev (akordy a číslo šedě)", b" rg" not in ops and b" g" in ops)

                vystup_b = TMP / "export-brozura.pdf"
                A.render_songbook_pdf(sekv, vystup_b, A.normalizuj_recept(format="pdf", brozura=True))
                br_pdf = PdfReader(str(vystup_b))
                listu = (len(poradi) + 3) // 4 * 2
                texty = " ".join(" ".join(p.extract_text().split()) for p in br_pdf.pages)
                ok("brožura: správný počet listů A4 na šířku a navržené strany v ní jako text",
                   len(br_pdf.pages) == listu and float(br_pdf.pages[0].mediabox.width) > float(br_pdf.pages[0].mediabox.height)
                   and "Píseň Alfa" in texty and "Píseň Beta" in texty, (len(br_pdf.pages), listu))
            for jmeno in ("chromium", "firefox", "webkit"):
                test_prohlizec(p, jmeno, base, a, b, poradi)
            test_skenu(p, base)
    finally:
        server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Navržené strany ve čtečce v pořádku.")


if __name__ == "__main__":
    main()
