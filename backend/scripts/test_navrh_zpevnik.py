#!/usr/bin/env python3
"""Přejímací test editoru stran ze zpěvníku (TODO bod 11): z editoru zpěvníku na
samostatnou stránku editoru stran, Uložit rovnou do zpěvníku, zpět, upozornění na
neuložené změny; prémiová brána.

Server běží v tomhle procesu nad kopií databáze a klonem obrázků (ukládá PDF, náhledy
a obrázky stran), takže se skutečných dat nedotkne. Měří se čísly, ne screenshoty.

    python backend/scripts/test_navrh_zpevnik.py
"""

import logging
import re
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

TMP = Path(tempfile.mkdtemp(prefix="navrh-zpevnik-"))
DB = TMP / "navrh.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "navrh-zpevnik")

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
PORT = 5591
HESLO = "navrh-zpevnik"
ZPEVNIK = "test-navrh-ui"

with A.app.app_context():
    for uid, role in ((3, "premium"), (4, "user")):
        u = A.db.session.get(A.User, uid)
        u.password = generate_password_hash(HESLO, method="pbkdf2:sha256:1000")
        u.role = role
    sb = A.Songbook(id=ZPEVNIK, title="Navržené strany UI", owner_id=3, is_public=0, prvni_cislo_strany=5)
    A.db.session.add(sb)
    for i in range(2):
        A.db.session.add(A.Strana(songbook_id=ZPEVNIK, poradi=i))
    A.db.session.add(A.UserSongbookAccess(user_id=4, songbook_id=ZPEVNIK, permission="edit"))
    A.db.session.commit()
    EMAIL3 = A.db.session.get(A.User, 3).email
    EMAIL4 = A.db.session.get(A.User, 4).email

selhani = []


def ok(popis, podminka, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def strany_db():
    with A.app.app_context():
        A.db.session.expire_all()
        return [(st.poradi, st.navrzena_id, st.image_id) for st in A.db.session.get(A.Songbook, ZPEVNIK).strany]


def prihlas(prohlizec, base, email):
    page = prohlizec.new_page(viewport={"width": 1400, "height": 1000})
    chyby = []
    page.on("pageerror", lambda e: chyby.append(str(e)))
    page.goto(base + "/login")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', HESLO)
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_load_state("networkidle")
    page.goto(base + "/my-songbooks")
    page.wait_for_load_state("networkidle")
    page.evaluate(f"window.openEditSongbookModal('{ZPEVNIK}')")
    try:
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length >= 2", timeout=10000)
    except Exception:
        print("    editor:", email, page.url, page.evaluate("[document.querySelectorAll('#es-seznam .es-strana').length, (document.querySelector('.oznameni, .hlaska') || {}).innerText]"))
        with A.app.app_context():
            u = A.User.query.filter_by(email=email).first()
            print("    zamky:", A.zamcene_zpevniky(u), A.zabrane_misto(u.id), A.limit_uzivatele(u))
        raise
    return page, chyby


def editor_stran(page):
    """Počká na stránku editoru stran (samostatná stránka s lištou webu)."""
    page.wait_for_url(re.compile(r"/editor-strany/"), timeout=30000)
    page.wait_for_function("window.navrhStrany && document.fonts.status === 'loaded'", timeout=20000)


def otevri_editor_zpevniku(page):
    page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length >= 1", timeout=20000)


LISTA = """(() => { const l = document.querySelector('.navrh-lista').getBoundingClientRect();
  const n = document.querySelector('.navrh-lista .nastroje').getBoundingClientRect();
  const hz = document.querySelector('.navrh-lista .hotovo-zrusit').getBoundingClientRect();
  const kde = document.querySelector('.navrh-lista .kde-strana').getBoundingClientRect();
  const sk = [...document.querySelectorAll('.navrh-lista .nastroje .skupina')].map(e => e.getBoundingClientRect());
  const radky = {};
  for (const r of sk) { const k = Math.round(r.top); (radky[k] = radky[k] || []).push(r); }
  const odchylka = Math.max(...Object.values(radky).map(rr => Math.abs((Math.min(...rr.map(r => r.left)) - n.left) - (n.right - Math.max(...rr.map(r => r.right))))));
  return { radku: Object.keys(radky).length, odchylka: Math.round(odchylka),
           hotovoNahore: Math.abs(hz.top - l.top) < 20 && hz.right <= l.right + 1,
           kdeVlevo: kde.left - l.left < 20 && kde.right <= n.left + 1,
           stred: Math.round((n.left + n.right) / 2 - (l.left + l.right) / 2) }; })()"""


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", PORT, A.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()

            print("\n── přidání strany na začátek a konec (schránka TODO) ──")
            page, chyby = prihlas(br, base, EMAIL3)
            ok("plus je i před první a za poslední stranou",
               page.locator("#es-seznam .es-mezera.prvni").count() == 1
               and page.locator("#es-seznam .es-mezera.posledni").count() == 1)
            page.click("#es-seznam .es-mezera.posledni .es-vlozit")
            ok("plus nabídne prázdnou a navrženou stranu",
               page.locator(".es-nabidka-vlozeni button").all_inner_texts() == ["Prázdná strana", "✎ Navrhnout stranu"],
               page.locator(".es-nabidka-vlozeni button").all_inner_texts())
            page.click(".es-nabidka-vlozeni button:has-text('Prázdná strana')")
            ok("prázdná strana se přidá na konec", page.locator("#es-seznam .es-strana").count() == 3)
            radky = page.evaluate("""(() => { const b = document.querySelector('#es-seznam .es-nahled.bez-obrazku .es-navrhnout');
              const r = document.createRange(); r.selectNodeContents(b);
              const n = b.closest('.es-nahled').getBoundingClientRect(), t = r.getBoundingClientRect();
              return [r.getClientRects().length, Math.round(t.width), Math.round(n.width)]; })()""")
            ok("„✎ navrhnout“ je na jednom řádku a vejde se do náhledu", radky[0] == 1 and radky[1] <= radky[2], radky)

            print("\n── navrhnout novou stranu (zpěvník má neuložené změny) ──")
            page.click("#es-seznam .es-mezera.prvni .es-vlozit")
            page.click(".es-nabidka-vlozeni button:has-text('Navrhnout stranu')")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            tlacitka = page.locator("#app-confirm-overlay button:visible").all_inner_texts()
            ok("zeptá se: uložit a přejít / přejít bez uložení / zůstat",
               tlacitka == ["Uložit a přejít do editoru", "Přejít bez uložení", "Zrušit"], tlacitka)
            page.click("#app-confirm-save")
            editor_stran(page)
            ok("editor stran je samostatná stránka s adresou nové strany",
               "/editor-strany/nova?zpevnik=" in page.url and page.url.endswith("&za=0"), page.url)
            ok("zpěvník se před přechodem uložil (3 strany), nová strana zatím nevznikla",
               len(strany_db()) == 3 and all(n is None for _, n, _ in strany_db()), strany_db())
            ok("lišta webu je vidět", page.evaluate("getComputedStyle(document.querySelector('.navbar')).display") != "none")
            ok("v liště editoru je zpěvník a číslo nové strany",
               page.inner_text(".kde-strana").replace("\n", " ").split() == ["‹", "Navržené", "strany", "UI", "nová", "strana", "5"],
               page.inner_text(".kde-strana"))
            ok("číslo a strana podle pozice (první strana zpěvníku = 5, pravá)",
               page.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]") == [5, "P"],
               page.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]"))
            s1400 = page.evaluate(LISTA)
            ok("1400 px: zpěvník vlevo, nástroje (zalomené) na střed, Uložit nahoře vpravo",
               s1400["odchylka"] <= 2 and s1400["hotovoNahore"] and s1400["kdeVlevo"], s1400)
            page.set_viewport_size({"width": 1920, "height": 1000})
            page.wait_for_timeout(200)
            s1920 = page.evaluate(LISTA)
            ok("1920 px: nástroje na jednom řádku uprostřed lišty", s1920["radku"] == 1 and abs(s1920["stred"]) <= 2
               and s1920["hotovoNahore"] and s1920["kdeVlevo"], s1920)
            page.set_viewport_size({"width": 1400, "height": 1000})

            page.evaluate("navrhStrany.nahradTextem('Testovací píseň', 'Autor', '[Ami]První řádek\\n[C]druhý řádek')")
            odpovedi = []
            page.on("response", lambda r: odpovedi.append(r) if "/api/navrh/strana" in r.url else None)
            konzole = []
            page.on("console", lambda m: konzole.append(m.text))
            page.click('[data-prikaz="ulozit"]')
            try:
                page.wait_for_url(re.compile(r"/editor-strany/\d+$"), timeout=30000)
            except Exception:
                print("    ladění:", page.evaluate("location.href"), page.inner_text(".kde-cislo"), page.url, page.inner_text('[data-prikaz="ulozit"]'),
                      page.evaluate("[...document.querySelectorAll('.oznameni-zaclona')].map(e => e.innerText).join(' | ')"),
                      strany_db(), chyby[:3], [ (r.status, r.text()[:200]) for r in odpovedi], konzole[-5:])
                raise
            s = strany_db()
            ok("Uložit vloží novou stranu na začátek zpěvníku s navrženou verzí",
               len(s) == 4 and s[0][1] is not None and all(n is None for _, n, _ in s[1:]), s)
            with A.app.app_context():
                strana0 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=0).first()
                navrh = strana0.navrzena
                ok("adresa je teď adresa té strany", page.url.endswith(f"/editor-strany/{strana0.id}"), page.url)
                ok("verze má PDF a náhled na disku", (OBRAZKY / navrh.pdf.cesta).is_file() and (OBRAZKY / navrh.nahled.cesta).is_file())
                ok("PDF je skutečné PDF", (OBRAZKY / navrh.pdf.cesta).read_bytes().startswith(b"%PDF-"))
            ok("v liště je číslo strany bez „nová“", page.inner_text(".kde-cislo").strip() == "5", page.inner_text(".kde-cislo"))
            prvni_verze = s[0][1]

            print("\n── zpět do zpěvníku a znovu upravit ──")
            page.click("#zpet-do-zpevniku")
            page.wait_for_url("**/my-songbooks**")
            otevri_editor_zpevniku(page)
            ok("zpět bez změn vede rovnou do editoru zpěvníku (bez ptaní)",
               page.locator("#es-seznam .es-strana").count() == 4)
            nahled_w = page.evaluate("""new Promise(ok => { const img = document.querySelector('#es-seznam .es-strana .es-nahled img');
              if (!img) return ok(0); if (img.complete) return ok(img.naturalWidth); img.onload = () => ok(img.naturalWidth); img.onerror = () => ok(-1); })""")
            ok("karta ukáže náhled navržené strany", nahled_w == 420, nahled_w)
            znacka = page.evaluate("""(() => { const n = document.querySelector('#es-seznam .es-nahled.navrzena');
              const z = n.querySelector('.es-znacka-navrhu').getBoundingClientRect(), b = n.getBoundingClientRect();
              return [Math.round(((z.left + z.right) / 2 - (b.left + b.right) / 2) * 10) / 10, Math.round(z.width), Math.round(b.width)]; })()""")
            ok("štítek „navržená“ je na středu náhledu a vejde se", abs(znacka[0]) <= 0.5 and znacka[1] <= znacka[2], znacka)

            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            editor_stran(page)
            ok("úprava otevře uloženou verzi", "Testovací píseň" in page.evaluate("navrhStrany.view.state.doc.textContent"))
            page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Nový ', 2)); })()")
            page.click("#zpet-do-zpevniku")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            tlacitka = page.locator("#app-confirm-overlay button:visible").all_inner_texts()
            ok("odchod s neuloženou stranou se zeptá", tlacitka == ["Uložit a odejít", "Odejít bez uložení", "Zrušit"], tlacitka)
            page.click("#app-confirm-ok")       # Odejít bez uložení
            page.wait_for_url("**/my-songbooks**")
            ok("bez uložení zůstane stará verze", strany_db()[0][1] == prvni_verze, strany_db())
            otevri_editor_zpevniku(page)

            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            editor_stran(page)
            page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Nový ', 2)); })()")
            page.click("#zpet-do-zpevniku")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            page.click("#app-confirm-save")     # Uložit a odejít
            page.wait_for_url("**/my-songbooks**", timeout=30000)
            nova = strany_db()[0][1]
            ok("Uložit a odejít uloží novou verzi rovnou do zpěvníku", nova and nova != prvni_verze, (nova, prvni_verze))

            print("\n── navrhnout na existující prázdné straně (bez neuložených změn) ──")
            otevri_editor_zpevniku(page)
            page.locator("#es-seznam .es-strana").nth(1).locator(".es-navrhnout").click()
            editor_stran(page)
            with A.app.app_context():
                strana1 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=1).first().id
            ok("prázdná strana se otevře rovnou na své adrese, bez ptaní", page.url.endswith(f"/editor-strany/{strana1}"), page.url)
            ok("číslo 6, levá", page.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]") == [6, "L"])
            ok("bez chyb JavaScriptu", not chyby, chyby[:3])
            page.close()

            print("\n── účet bez prémia (člen sdíleného zpěvníku) ──")
            page, chyby = prihlas(br, base, EMAIL4)
            ok("Navrhnout je zašedlé", page.locator("#es-seznam .es-navrhnout").first.is_disabled())
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            page.wait_for_timeout(300)
            ok("navrženou stranu neotevře", "/my-songbooks" in page.url, page.url)
            for adresa in (f"/editor-strany/{strana1}", f"/editor-strany/nova?zpevnik={ZPEVNIK}&za=0", "/editor-strany"):
                odpoved = page.goto(base + adresa)
                ok(f"editor stran mu neodpoví ({adresa})", odpoved.status == 404, odpoved.status)
            br.close()
    finally:
        server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Editor stran ze zpěvníku v pořádku.")


if __name__ == "__main__":
    main()
