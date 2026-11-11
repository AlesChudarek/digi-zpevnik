#!/usr/bin/env python3
"""Přejímací test editoru stran v editoru zpěvníku (TODO bod 11, krok 4): Navrhnout
stranu, editor stran v rámu, Hotovo, Uložit zpěvník, znovu upravit; prémiová brána.

Server běží v tomhle procesu nad kopií databáze a klonem obrázků (ukládá PDF, náhledy
a obrázky stran), takže se skutečných dat nedotkne. Měří se čísly, ne screenshoty.

    python backend/scripts/test_navrh_zpevnik.py
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


def ramec(page):
    prvek = page.wait_for_selector(".es-navrh-okno iframe")
    rf = page.frame_locator(".es-navrh-okno iframe")
    frame = prvek.content_frame()
    try:
        frame.wait_for_function("window.navrhStrany && document.fonts.status === 'loaded'", timeout=20000)
    except Exception:
        print("    rám:", frame.url, frame.evaluate("document.body ? document.body.innerText.slice(0, 300) : ''"))
        print("    modul:", frame.evaluate("(async () => { const s = document.querySelector('script[type=module]'); try { await import(s.src); return 'ok ' + typeof window.navrhStrany; } catch (e) { return String(e) + ' ' + (e.stack || '').slice(0, 300); } })()"))
        raise
    return rf, frame


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

            print("\n── navrhnout stranu ──")
            page.click("#es-seznam .es-mezera.prvni .es-vlozit")
            page.click(".es-nabidka-vlozeni button:has-text('Navrhnout stranu')")
            rf, frame = ramec(page)
            ok("editor stran se otevře v rámu přes celé okno",
               page.evaluate("(() => { const r = document.querySelector('.es-navrh-okno').getBoundingClientRect(); return r.width >= innerWidth - 1 && r.height >= innerHeight - 1; })()"))
            ok("v rámu není lišta webu", frame.evaluate("getComputedStyle(document.querySelector('.navbar')).display") == "none")
            ok("ani její boční šipka", frame.evaluate("getComputedStyle(document.querySelector('.navbar-toggle')).display") == "none")
            LISTA = """(() => { const l = document.querySelector('.navrh-lista').getBoundingClientRect();
              const n = document.querySelector('.navrh-lista .nastroje').getBoundingClientRect();
              const hz = document.querySelector('.navrh-lista .hotovo-zrusit').getBoundingClientRect();
              const sk = [...document.querySelectorAll('.navrh-lista .nastroje .skupina')].map(e => e.getBoundingClientRect());
              const radky = {};
              for (const r of sk) { const k = Math.round(r.top); (radky[k] = radky[k] || []).push(r); }
              const odchylka = Math.max(...Object.values(radky).map(rr => Math.abs((Math.min(...rr.map(r => r.left)) - n.left) - (n.right - Math.max(...rr.map(r => r.right))))));
              return { radku: Object.keys(radky).length, odchylka: Math.round(odchylka),
                       hotovoNahore: Math.abs(hz.top - l.top) < 20 && hz.right <= l.right + 1,
                       stred: Math.round((n.left + n.right) / 2 - (l.left + l.right) / 2) }; })()"""
            s1400 = frame.evaluate(LISTA)
            ok("1400 px: nástroje se zalomí, každý řádek na střed, Hotovo zůstane nahoře vpravo",
               s1400["odchylka"] <= 2 and s1400["hotovoNahore"], s1400)
            page.set_viewport_size({"width": 1920, "height": 1000})
            page.wait_for_timeout(200)
            s1920 = frame.evaluate(LISTA)
            ok("1920 px: nástroje na jednom řádku uprostřed lišty", s1920["radku"] == 1 and abs(s1920["stred"]) <= 2
               and s1920["hotovoNahore"], s1920)
            page.set_viewport_size({"width": 1400, "height": 1000})
            ok("číslo a strana podle pozice (první strana zpěvníku = 5, pravá)",
               frame.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]") == [5, "P"],
               frame.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]"))
            frame.evaluate("navrhStrany.nahradTextem('Testovací píseň', 'Autor', '[Ami]První řádek\\n[C]druhý řádek')")
            rf.locator('[data-prikaz="hotovo"]').click()
            page.wait_for_selector(".es-navrh-okno", state="detached", timeout=30000)
            data = page.evaluate("EditorStran.data()")
            ok("Hotovo zavře editor stran a strana má navrženou verzi", data[0]["navrzena_id"] and data[0]["typ"] == "bez", data[0])
            nahled_w = page.evaluate("""new Promise(ok => { const img = document.querySelector('#es-seznam .es-strana .es-nahled img');
              if (!img) return ok(0); if (img.complete) return ok(img.naturalWidth); img.onload = () => ok(img.naturalWidth); img.onerror = () => ok(-1); })""")
            ok("karta ukáže náhled navržené strany", nahled_w == 420, nahled_w)
            ok("a značku „navržená“", page.locator("#es-seznam .es-strana").first.locator(".es-znacka-navrhu").count() == 1)
            znacka = page.evaluate("""(() => { const n = document.querySelector('#es-seznam .es-nahled.navrzena');
              const z = n.querySelector('.es-znacka-navrhu').getBoundingClientRect(), b = n.getBoundingClientRect();
              return [Math.round(((z.left + z.right) / 2 - (b.left + b.right) / 2) * 10) / 10, Math.round(z.width), Math.round(b.width),
                      getComputedStyle(n).cursor.slice(0, 40)]; })()""")
            ok("štítek „navržená“ je na středu náhledu a vejde se", abs(znacka[0]) <= 0.5 and znacka[1] <= znacka[2], znacka)
            ok("nad navrženou stranou je kurzor tužka (úprava), ne lupa", znacka[3].startswith('url("data:image/svg+xml'), znacka[3])
            ok("do zpěvníku se zatím nic neuložilo", strany_db()[0][1] is None, strany_db())
            page.click("#nb-save-top")
            page.wait_for_function("!document.getElementById('editor-stran') || !document.getElementById('editor-stran').offsetParent", timeout=20000)
            s = strany_db()
            ok("po Uložit má první strana zpěvníku navrženou stranu", s[0][1] == data[0]["navrzena_id"] and len(s) == 4, s)
            with A.app.app_context():
                navrh = A.db.session.get(A.NavrzenaStrana, s[0][1])
                ok("verze má PDF a náhled na disku", (OBRAZKY / navrh.pdf.cesta).is_file() and (OBRAZKY / navrh.nahled.cesta).is_file())
                ok("PDF je skutečné PDF s textem strany", (OBRAZKY / navrh.pdf.cesta).read_bytes().startswith(b"%PDF-"))

            print("\n── znovu upravit ──")
            page.evaluate(f"window.openEditSongbookModal('{ZPEVNIK}')")
            page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            rf, frame = ramec(page)
            ok("úprava otevře uloženou verzi", "Testovací píseň" in frame.evaluate("navrhStrany.view.state.doc.textContent"))
            rf.locator('[data-prikaz="zrusit"]').click()
            page.wait_for_selector(".es-navrh-okno", state="detached", timeout=10000)
            ok("Zrušit bez změn zavře bez ptaní", page.locator(".es-navrh-okno").count() == 0)
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            rf, frame = ramec(page)
            frame.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Nový ', 2)); })()")
            rf.locator('[data-prikaz="hotovo"]').click()
            page.wait_for_selector(".es-navrh-okno", state="detached", timeout=30000)
            nova = page.evaluate("EditorStran.data()[0].navrzena_id")
            ok("úprava uloží novou verzi (stará zůstává neměnná)", nova and nova != s[0][1], (nova, s[0][1]))
            page.click("#nb-save-top")
            page.wait_for_timeout(1500)
            ok("po Uložit zpěvník ukazuje na novou verzi", strany_db()[0][1] == nova, strany_db())
            ok("bez chyb JavaScriptu", not chyby, chyby[:3])
            page.close()

            print("\n── účet bez prémia (člen sdíleného zpěvníku) ──")
            page, chyby = prihlas(br, base, EMAIL4)
            ok("Navrhnout je zašedlé", page.locator("#es-seznam .es-navrhnout").first.is_disabled())
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            page.wait_for_timeout(300)
            ok("navrženou stranu neotevře", page.locator(".es-navrh-okno").count() == 0)
            odpoved = page.goto(base + "/editor-strany?vlozeny=1")
            ok("editor stran mu neodpoví (404)", odpoved.status == 404, odpoved.status)
            br.close()
    finally:
        server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Editor stran v editoru zpěvníku v pořádku.")


if __name__ == "__main__":
    main()
