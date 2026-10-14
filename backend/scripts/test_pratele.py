#!/usr/bin/env python3
"""Přejímací test profilu (přezdívka, fotka) a přátel.

Běží v tomhle procesu nad kopií databáze; profilové fotky jdou do dočasné složky,
obrázky zpěvníků se jen čtou. Měří se čísly, ne screenshoty.

    python backend/scripts/test_pratele.py
"""

import io
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

TMP = Path(tempfile.mkdtemp(prefix="pratele-"))
DB = TMP / "pratele.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "pratele")

import backend.app as A  # noqa: E402
from PIL import Image  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)],
                  capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.PROFILY_DIR = TMP / "profily"
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None
A.app.config["TESTING"] = True

HESLO = "pratele-test"
with A.app.app_context():
    # Čistý stav: bez přezdívek, fotek a přátelství z dřívějška, všichni ověření.
    A.Pratelstvi.query.delete()
    for u in A.User.query.filter(A.User.id.in_([3, 4, 5, 6])):
        u.prezdivka = None
        u.fotka = None
        u.email_verified = True
        u.password = generate_password_hash(HESLO, method="pbkdf2:sha256:1000")
    A.db.session.commit()
    EMAIL = {u.id: u.email for u in A.User.query.filter(A.User.id.in_([3, 4, 5, 6]))}

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def klient(uid):
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return c


def png(rozmer, barva="red"):
    b = io.BytesIO()
    Image.new("RGB", rozmer, barva).save(b, "PNG")
    return b.getvalue()


def main():
    u3, u4, u5, u6 = (klient(i) for i in (3, 4, 5, 6))

    print("\n── přezdívka ──")
    r = u3.post("/profil/udaje", json={"prezdivka": "  Petr   z testu "})
    zkontroluj(r.status_code == 200 and r.get_json()["uzivatel"]["jmeno"] == "Petr z testu",
               "přezdívka se uloží s uklizenými mezerami", r.get_json())
    zkontroluj(r.get_json()["uzivatel"]["inicialy"] == "PZ", "iniciály z prvních dvou slov",
               r.get_json()["uzivatel"]["inicialy"])
    r = u3.post("/profil/udaje", json={"prezdivka": "x" * (A.MAX_PREZDIVKA + 1)})
    zkontroluj(r.status_code == 400, "příliš dlouhá přezdívka se odmítne", r.status_code)
    r = u4.post("/profil/udaje", json={"prezdivka": ""})
    zkontroluj(r.get_json()["uzivatel"]["jmeno"] == EMAIL[4], "bez přezdívky se ukazuje e-mail")

    print("\n── fotka ──")
    r = u3.post("/profil/fotka", data={"fotka": (io.BytesIO(png((800, 400))), "f.png")},
                content_type="multipart/form-data")
    j = r.get_json()
    with A.app.app_context():
        prvni = A.db.session.get(A.User, 3).fotka
    with Image.open(A.PROFILY_DIR / prvni) as im:
        tvar = (im.format, im.size)
    zkontroluj(r.status_code == 200 and tvar == ("WEBP", (256, 256)),
               "fotka se ořízne na čtverec 256 px a uloží jako WebP", tvar)
    zkontroluj(u4.get(j["uzivatel"]["fotka"]).status_code == 200,
               "fotku vidí i jiný přihlášený uživatel")
    r = u3.post("/profil/fotka", data={"fotka": (io.BytesIO(b"neni obrazek"), "x.png")},
                content_type="multipart/form-data")
    zkontroluj(r.status_code == 400 and (A.PROFILY_DIR / prvni).exists(),
               "nečitelný soubor se odmítne a stará fotka zůstane", r.status_code)
    u3.post("/profil/fotka", data={"fotka": (io.BytesIO(png((300, 300), "blue")), "f.png")},
            content_type="multipart/form-data")
    with A.app.app_context():
        druha = A.db.session.get(A.User, 3).fotka
    zkontroluj(druha != prvni and not (A.PROFILY_DIR / prvni).exists(),
               "nová fotka má nové jméno a stará se smaže", (prvni, druha))
    u3.delete("/profil/fotka")
    with A.app.app_context():
        zkontroluj(A.db.session.get(A.User, 3).fotka is None and not (A.PROFILY_DIR / druha).exists(),
                   "odebrání fotky smaže soubor i záznam")
    u3.post("/profil/fotka", data={"fotka": (io.BytesIO(png((300, 300), "green")), "f.png")},
            content_type="multipart/form-data")

    print("\n── žádosti o přátelství ──")
    r = u3.post("/api/pratele", json={"email": EMAIL[3]})
    zkontroluj(r.status_code == 400, "sám sebe přidat nejde", r.status_code)
    r = u3.post("/api/pratele", json={"email": "nikdo@nikde.cz"})
    zkontroluj(r.status_code == 404, "neexistující adresa", r.status_code)
    r = u3.post("/api/pratele", json={"email": EMAIL[4].upper()})
    zkontroluj(r.status_code == 200 and r.get_json()["prijato"] is False,
               "žádost podle e-mailu (velikost písmen nevadí)", r.get_json())
    r = u3.post("/api/pratele", json={"email": EMAIL[4]})
    zkontroluj(r.status_code == 400, "podruhé se žádost neodešle", r.status_code)
    zkontroluj(u3.get("/api/pratele").get_json()["pratele"] == [],
               "nepotvrzená žádost ještě není přátelství")
    with A.app.app_context():
        zadost = A.Pratelstvi.query.filter_by(od_id=3, komu_id=4).first().id
    r = u5.post(f"/api/pratele/zadost/{zadost}/prijmout")
    zkontroluj(r.status_code == 404, "cizí žádost potvrdit nejde", r.status_code)
    zkontroluj(f'data-zadost="{zadost}"' in u4.get("/my-songbooks").get_data(as_text=True),
               "žádost je vidět v Mých zpěvnících příjemce")
    r = u4.post(f"/api/pratele/zadost/{zadost}/prijmout")
    pratele3 = u3.get("/api/pratele").get_json()["pratele"]
    zkontroluj(r.status_code == 200 and [p["id"] for p in pratele3] == [4],
               "po potvrzení jsou přáteli", pratele3)
    zkontroluj([p["id"] for p in u4.get("/api/pratele").get_json()["pratele"]] == [3],
               "a vidí to obě strany")

    u5.post("/api/pratele", json={"email": EMAIL[3]})
    r = u3.post("/api/pratele", json={"email": EMAIL[5]})
    zkontroluj(r.get_json().get("prijato") is True,
               "když druhý už žádá nás, žádost se rovnou přijme", r.get_json())

    u6.post("/api/pratele", json={"email": EMAIL[3]})
    with A.app.app_context():
        z6 = A.Pratelstvi.query.filter_by(od_id=6, komu_id=3).first().id
    u3.post(f"/api/pratele/zadost/{z6}/odmitnout")
    with A.app.app_context():
        zkontroluj(A.Pratelstvi.query.filter_by(od_id=6, komu_id=3).first() is None,
                   "odmítnutá žádost zmizí")

    print("\n── stránka Přátelé ──")
    html = u3.get("/okno/pratele").get_data(as_text=True)
    zkontroluj("Moji přátelé" in html and f'data-uzivatel="4"' in html and "Volné místo" in html,
               "přátelé s volným místem")

    # Společný zpěvník: u3 pozve u4 ke společným úpravám, ten přijme.
    with A.app.app_context():
        sb = A.Songbook.query.filter_by(owner_id=3, is_public=0).first()
        sb_id, sb_nazev = sb.id, sb.title
    u3.post(f"/api/my-songbooks/{sb_id}/share", json={"email": EMAIL[4], "druh": "spolecny"})
    with A.app.app_context():
        poz = A.Pozvanka.query.filter_by(songbook_id=sb_id, komu_id=4).first()
    html = u3.get("/okno/pratele").get_data(as_text=True)
    zkontroluj("Čeká na odpověď" in html, "čekající pozvánka u přítele")
    u4.post(f"/api/pozvanky/{poz.id}/prijmout")
    html = u3.get("/okno/pratele").get_data(as_text=True)
    zkontroluj("Společné zpěvníky" in html and sb_nazev in html,
               "společný zpěvník u přítele", sb_nazev)

    print("\n── návrhy ──")
    u3.delete("/api/pratele/4")
    zkontroluj([p["id"] for p in u3.get("/api/pratele").get_json()["pratele"]] == [5],
               "odebrání přítele")
    html = u3.get("/okno/pratele").get_data(as_text=True)
    zkontroluj("Možná znáte" in html and 'data-uzivatel="4"' in html,
               "kdo se mnou sdílí zpěvník, je v návrzích")
    r = u3.post("/api/pratele", json={"id": 4})
    zkontroluj(r.status_code == 200, "z návrhu jde poslat žádost", r.get_json())
    u3.delete("/api/pratele/4")
    with A.app.app_context():
        zkontroluj(A.Pratelstvi.query.filter_by(od_id=3, komu_id=4).first() is None,
                   "vlastní žádost jde zrušit")

    print("\n── účet: e-mail a heslo ──")
    r = u6.post("/profil/email", json={"email": "novy-user5@test.com", "heslo": "spatne"})
    zkontroluj(r.status_code == 400, "změna e-mailu bez správného hesla neprojde", r.status_code)
    r = u6.post("/profil/email", json={"email": EMAIL[5], "heslo": HESLO})
    zkontroluj(r.status_code == 400 and "jiný účet" in r.get_json()["error"],
               "adresu jiného účtu vzít nejde", r.get_json())
    A.posli_overovaci_email = lambda u: True
    r = u6.post("/profil/email", json={"email": "novy-user5@test.com", "heslo": HESLO})
    with A.app.app_context():
        u = A.db.session.get(A.User, 6)
        stav = (u.email, u.email_verified)
    zkontroluj(r.status_code == 200 and stav == ("novy-user5@test.com", False),
               "změna e-mailu zruší ověření a pošle nové", stav)
    r = u6.post("/profil/zmena-hesla", json={"stare": HESLO, "nove": "kratke", "nove2": "kratke"})
    zkontroluj(r.status_code == 400, "krátké nové heslo se odmítne", r.get_json())
    r = u6.post("/profil/zmena-hesla", json={"stare": HESLO, "nove": "Nove-heslo-1", "nove2": "Nove-heslo-1"})
    zkontroluj(r.status_code == 200, "změna hesla", r.get_json())
    zkontroluj(u3.get("/okno/ucet").status_code == 200 and u3.get("/okno/nic").status_code == 404,
               "obsah okna Účet, neznámé okno 404")
    zkontroluj(u3.get("/profil").headers["Location"].endswith("okno=ucet")
               and u3.get("/pratele").headers["Location"].endswith("okno=pratele"),
               "staré adresy /profil a /pratele otevřou okno")

    print("\n── vodoznak a placená úroveň ──")
    strana = Image.new("RGB", (1748, 2480), "white")
    s_vodoznakem = A._vodoznak(strana.copy())
    roh = s_vodoznakem.crop((1748 - 500, 2480 - 120, 1748, 2480))
    tmave = sum(1 for p in roh.convert("L").getdata() if p < 160)
    zbytek = s_vodoznakem.crop((0, 0, 1748, 2300))
    zkontroluj(tmave > 300 and zbytek.getextrema() == ((255, 255), (255, 255), (255, 255)),
               "vodoznak je jen v pravém dolním rohu", tmave)
    zkontroluj(A.token_receptu(A.normalizuj_recept(**A.PREDVOLBY["small"], vodoznak=True)) != "small",
               "PDF s vodoznakem má v cache vlastní soubor, předgenerované small zůstane čisté")
    from flask_login import login_user
    with A.app.app_context():
        sb_any = A.Songbook.query.filter_by(is_public=1).first().id
    puvodni = A.PAYWALL
    try:
        for paywall in (False, True):
            A.PAYWALL = paywall
            with A.app.test_request_context(f"/songbook/{sb_any}/export.pdf?q=high"):
                login_user(A.db.session.get(A.User, 3))
                vysledek, chyba = A._resolve_export_request(sb_any, "pdf")
                recept_bezny = vysledek["recept"]
                login_user(A.db.session.get(A.User, 1))
                recept_admin = A._resolve_export_request(sb_any, "pdf")[0]["recept"]
            with A.app.test_request_context(f"/songbook/{sb_any}/export.zip"):
                login_user(A.db.session.get(A.User, 3))
                zip_chyba = A._resolve_export_request(sb_any, "zip")[1]
            if not paywall:
                zkontroluj(recept_bezny["kvalita"] == "high" and not recept_bezny["vodoznak"] and zip_chyba is None,
                           "bez zapnutého PAYWALL stahuje každý cokoli, bez vodoznaku")
            else:
                zkontroluj(recept_bezny["kvalita"] == "small" and recept_bezny["vodoznak"]
                           and zip_chyba is not None and zip_chyba[1] == 403,
                           "se zapnutým PAYWALL běžný uživatel jen malé PDF s vodoznakem, ZIP ne",
                           (recept_bezny, zip_chyba and zip_chyba[1]))
                zkontroluj(recept_admin["kvalita"] == "high" and not recept_admin["vodoznak"],
                           "admin (a premium) dál bez omezení")
    finally:
        A.PAYWALL = puvodni

    print("\n── v prohlížeči ──")
    from playwright.sync_api import sync_playwright
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 5593, A.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:5593"
    with sync_playwright() as p:
        prohlizec = p.chromium.launch()
        page = prohlizec.new_page(viewport={"width": 1280, "height": 900})
        chyby = []
        page.on("pageerror", lambda e: chyby.append(str(e)))
        page.goto(base + "/login")
        page.fill('input[name="email"]', EMAIL[3])
        page.fill('input[name="password"]', HESLO)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        lista = page.evaluate("""() => { const b = document.getElementById('ucet-btn');
          const f = b.querySelector('img.avatar').getBoundingClientRect(), j = b.querySelector('.ucet-email').getBoundingClientRect();
          return {jmeno: b.querySelector('.ucet-email').textContent.trim(), fotka_vpravo: f.left > j.right,
                  velikost: Math.round(f.width), sipka: !!b.querySelector('.ucet-sipka')}; }""")
        zkontroluj(lista == {"jmeno": "Petr z testu", "fotka_vpravo": True, "velikost": 38, "sipka": False},
                   "v liště jméno a vpravo od něj fotka 38 px, bez šipky", lista)
        page.click("#ucet-btn")
        polozky = page.evaluate("() => [...document.querySelectorAll('#ucet-panel .ucet-polozka')].map(e => e.textContent.trim())")
        zkontroluj(polozky[:2] == ["Účet", "Přátelé"], "v nabídce účtu Účet a Přátelé", polozky)
        page.click("#ucet-panel .ucet-polozka >> text=Účet")
        page.wait_for_selector("#ucet-okno:not([hidden]) #profil-prezdivka")
        zkontroluj(page.input_value("#profil-prezdivka") == "Petr z testu"
                   and page.is_hidden("#ucet-panel"),
                   "okno Účet s profilem, nabídka se zavře")
        # Ořez: fotka větší než limit nahrávání se už neodmítne, odejde jen výřez.
        velka = TMP / "velka.png"
        Image.new("RGB", (3000, 1800), "orange").save(velka)
        page.set_input_files("#profil-soubor", str(velka))
        page.wait_for_selector(".orez-zaclona .orez-plocha img")
        orez = page.evaluate("""() => { const p = document.querySelector('.orez-plocha').getBoundingClientRect(),
            i = document.querySelector('.orez-obrazek').getBoundingClientRect();
          return {plocha: Math.round(p.width), pokryva: i.left <= p.left + 0.5 && i.right >= p.right - 0.5
                  && i.top <= p.top + 0.5 && i.bottom >= p.bottom - 0.5}; }""")
        zkontroluj(orez["pokryva"], "okno s ořezem: fotka pokrývá celou plochu výřezu", orez)
        page.fill(".orez-zoom", "2")
        page.dispatch_event(".orez-zoom", "input")
        with page.expect_navigation():
            page.click("[data-orez=ulozit]")
        page.wait_for_load_state("networkidle")
        with A.app.app_context():
            soubor = A.db.session.get(A.User, 3).fotka
        with Image.open(A.PROFILY_DIR / soubor) as im:
            vysledek = (im.size, im.getpixel((128, 128)))
        zkontroluj(vysledek[0] == (256, 256) and vysledek[1][0] > 200 and vysledek[1][2] < 80,
                   "uložený výřez je oranžový čtverec 256 px", vysledek)
        page.wait_for_selector("#ucet-okno:not([hidden]) #profil-prezdivka")
        zkontroluj("okno=" not in page.url, "po uložení fotky zůstane otevřené okno Účet", page.url)
        # Zbrklé označení textu: stisk v poli, puštění až na pozadí okna.
        pole = page.locator("#profil-prezdivka").bounding_box()
        page.mouse.move(pole["x"] + pole["width"] - 5, pole["y"] + pole["height"] / 2)
        page.mouse.down()
        page.mouse.move(5, pole["y"] + pole["height"] / 2, steps=8)
        page.mouse.up()
        page.wait_for_timeout(200)
        zkontroluj(page.is_visible("#ucet-okno"), "tah z pole ven na pozadí okno nezavře")
        page.mouse.click(5, 300)
        page.wait_for_timeout(200)
        zkontroluj(page.is_hidden("#ucet-okno"), "obyčejný klik na pozadí okno zavře")
        page.click("#ucet-btn")
        page.click("#ucet-panel .ucet-polozka >> text=Účet")
        page.wait_for_selector("#ucet-okno:not([hidden]) #profil-prezdivka")
        # SVG: drawImage s výřezem kreslil u SVG něco jiného, než bylo vybrané.
        svg = PROJECT_ROOT / "frontend" / "static" / "img" / "icons" / "scroll_outline.svg"
        page.set_input_files("#profil-soubor", str(svg))
        page.wait_for_selector(".orez-zaclona .orez-plocha img")
        with page.expect_navigation():
            page.click("[data-orez=ulozit]")
        page.wait_for_load_state("networkidle")
        ocekavano = page.evaluate("""async (url) => { const i = new Image(); i.src = url; await i.decode();
          const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
          x.fillStyle = '#fff'; x.fillRect(0, 0, 256, 256); x.drawImage(i, 0, 0, 256, 256);
          const d = x.getImageData(0, 0, 256, 256).data; let n = 0;
          for (let k = 0; k < d.length; k += 4) if (d[k] < 80) n++; return n; }""",
                                  "/static/img/icons/scroll_outline.svg")
        with A.app.app_context():
            soubor = A.db.session.get(A.User, 3).fotka
        with Image.open(A.PROFILY_DIR / soubor) as im:
            tmave = sum(1 for p in im.convert("L").getdata() if p < 80)
        zkontroluj(abs(tmave - ocekavano) / max(1, ocekavano) < 0.1,
                   "SVG se ořízne tak, jak bylo vybrané (celé při výchozím přiblížení)", (tmave, ocekavano))
        page.wait_for_selector("#ucet-okno:not([hidden]) #profil-prezdivka")
        page.keyboard.press("Escape")
        zkontroluj(page.is_hidden("#ucet-okno"), "Escape okno zavře")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        page.locator(f'.songbook-card[data-book-id="{sb_id}"] .akce-spoustec').click()
        page.locator(f'.songbook-card[data-book-id="{sb_id}"] .btn-share').click()
        page.wait_for_selector("#share-pratele:not([hidden])")
        page.click("#share-pratele .share-pritel")
        zkontroluj(page.input_value("#share-email-input") == EMAIL[5],
                   "v okně sdílení klik na přítele doplní e-mail", page.input_value("#share-email-input"))
        clen = page.inner_text("#share-clenove")
        zkontroluj(EMAIL[4] in clen, "členové okna sdílení se jmenují", clen)
        page.goto(base + "/pratele")
        page.wait_for_selector("#ucet-okno:not([hidden]) .pritel")
        zkontroluj("okno=" not in page.url, "adresa /pratele otevře okno Přátelé a parametr zmizí", page.url)
        page.click(".pritel-odebrat")
        page.wait_for_selector("#app-confirm-overlay[aria-hidden='false']")
        page.click("#app-confirm-cancel")
        zkontroluj(page.locator(".pritel").count() == 1, "odebrání se ptá a Zrušit nic neudělá")
        okno = page.evaluate("() => { const r = document.querySelector('.ucet-okno').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), innerWidth]; }")
        sirka = page.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        page.set_viewport_size({"width": 360, "height": 780})
        sirka_uzka = page.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        okno_uzke = page.evaluate("() => { const r = document.querySelector('.ucet-okno').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), innerWidth]; }")
        zkontroluj(sirka <= 0 and sirka_uzka <= 0 and okno_uzke[0] >= 0 and okno_uzke[1] <= okno_uzke[2],
                   "okno přátel se vejde a stránka se nedá táhnout do strany", (sirka, sirka_uzka, okno, okno_uzke))
        page.goto(base + "/vitejte")
        zkontroluj(page.locator("#profil-prezdivka").count() == 1
                   and page.locator(".vitejte-preskocit").count() == 1
                   and page.is_hidden("#profil-ulozit"),
                   "stránka Vítejte s profilem a Přeskočit, bez zvláštního Uložit")
        page.fill("#profil-prezdivka", "Petr z Vítejte")
        with page.expect_navigation():
            page.click(".vitejte-pokracovat")
        with A.app.app_context():
            prez = A.db.session.get(A.User, 3).prezdivka
        zkontroluj(prez == "Petr z Vítejte" and "/vitejte" not in page.url,
                   "Pokračovat přezdívku uloží a jde dál", (prez, page.url))
        zkontroluj(not chyby, "žádná chyba JavaScriptu", "; ".join(chyby[:3]))
        prohlizec.close()
    server.shutdown()

    print()
    if selhani:
        print(f"❌ selhalo {len(selhani)}: {', '.join(selhani)}")
        return 1
    print("✅ všechny kontroly prošly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
