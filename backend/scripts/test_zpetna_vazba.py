#!/usr/bin/env python3
"""Přejímací test zpětné vazby: API, záložka a formulář na stránkách, karta ve Správě.

Běží na kopii databáze, server je v procesu (werkzeug) a snímky obrazovky jdou do
dočasné složky - ZPETNA_VAZBA_DIR se přesměruje, ať test nesahá na skutečná data.
Vzhled se měří čísly (getBoundingClientRect, computed styles), ne screenshoty.

    python backend/scripts/test_zpetna_vazba.py
"""

import base64
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))

TMP = Path(tempfile.mkdtemp(prefix="zpetna-vazba-"))
DB = TMP / "zv.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "zpetna-vazba")
os.environ.pop("ZPETNA_VAZBA", None)

import backend.app as A  # noqa: E402
from PIL import Image  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

A.ZPETNA_VAZBA_DIR = TMP / "snimky"
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None   # podproces by běžel nad skutečnými daty
A.app.config["TESTING"] = True

PORT = 5620
HESLO = "zpetna-vazba-test"
with A.app.app_context():
    A.ZpetnaVazba.query.delete()
    for e in ("admin@test.com", "user@test.com"):
        u = A.User.query.filter_by(email=e).first()
        u.password = generate_password_hash(HESLO, method="pbkdf2:sha256")
    A.db.session.commit()
    ADMIN_ID = A.User.query.filter_by(email="admin@test.com").first().id
    USER_ID = A.User.query.filter_by(email="user@test.com").first().id

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def klient(uid=None):
    c = A.app.test_client()
    if uid is not None:
        with A.app.app_context():
            id_relace = A.db.session.get(A.User, uid).get_id()
        with c.session_transaction() as s:
            s["_user_id"] = id_relace
            s["_fresh"] = True
    return c


def obrazek(sirka=40, vyska=30, fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (sirka, vyska), (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


def data_url(obsah, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(obsah).decode()


IP = iter(f"10.20.0.{i}" for i in range(1, 250))


def posli(c, telo, ip=None):
    r = c.post("/api/zpetna-vazba", json=telo, headers={"X-Forwarded-For": ip or next(IP)})
    return r.status_code, (r.get_json() or {})


def radek(zid):
    with A.app.app_context():
        z = A.db.session.get(A.ZpetnaVazba, zid)
        if z is None:
            return None
        return {"druh": z.druh, "user_id": z.user_id, "stav": z.stav, "snimek": z.snimek,
                "kontext": json.loads(z.kontext or "{}"), "zprava": z.zprava, "url": z.url}


def api():
    print("\n── API: validace ──")
    c = klient()
    kod, d = posli(c, {"zprava": "   "})
    zkontroluj(kod == 400 and "prázdná" in d.get("error", ""), "prázdná zpráva → 400", (kod, d))
    kod, d = posli(c, {"zprava": "x" * 5001})
    zkontroluj(kod == 400 and "5000" in d.get("error", ""), "zpráva nad 5000 znaků → 400", (kod, d))
    kod, d = posli(c, {"zprava": "x" * 5000, "druh": "nesmysl"})
    zkontroluj(kod == 201 and radek(d.get("id"))["druh"] == "jine", "5000 znaků projde, neznámý druh → jiné", (kod, d))
    kod, d = posli(c, {"zprava": "a", "snimek": "data:image/gif;base64,R0lGOD=="})
    zkontroluj(kod == 400 and "není obrázek" in d.get("error", ""), "snímek GIF → 400", (kod, d))
    kod, d = posli(c, {"zprava": "a", "snimek": "data:image/png;base64,AAAA" + "B" * 10})
    zkontroluj(kod == 400 and "poškozený" in d.get("error", ""), "snímek, který není obrázek → 400", (kod, d))
    kod, d = posli(c, {"zprava": "a", "snimek": "data:image/png;base64," + "A" * 3_000_000})
    zkontroluj(kod == 400 and "velký" in d.get("error", ""), "snímek nad 3 MB → 400", (kod, d))
    kod, d = posli(c, {"zprava": "a", "kontext": {"url": "http://x/", "velke": "y" * 210_000}})
    r = radek(d.get("id")) if kod == 201 else None
    zkontroluj(r and r["kontext"].get("oriznuto") and "velke" not in r["kontext"],
               "kontext nad 200 kB se ořízne na adresu", (kod, r and list(r["kontext"])))
    r2 = c.post("/api/zpetna-vazba", data="nejson", content_type="application/json")
    zkontroluj(r2.status_code == 400, "nevalidní JSON → 400", r2.status_code)

    print("\n── API: snímek, host a přihlášený ──")
    soubory_pred = set(A.ZPETNA_VAZBA_DIR.glob("*")) if A.ZPETNA_VAZBA_DIR.exists() else set()
    kod, d = posli(c, {"zprava": "host se snímkem", "druh": "chyba", "url": "http://localhost/x",
                       "snimek": data_url(obrazek()), "kontext": {"viewport": "1×1"}})
    r = radek(d.get("id")) if kod == 201 else None
    zkontroluj(kod == 201 and r["user_id"] is None and r["druh"] == "chyba", "nepřihlášený smí poslat, bez uživatele", (kod, r))
    soubor = A.ZPETNA_VAZBA_DIR / (r["snimek"] or "-") if r else None
    zkontroluj(soubor and soubor.is_file() and soubor.suffix == ".png"
               and set(A.ZPETNA_VAZBA_DIR.glob("*")) - soubory_pred == {soubor},
               "snímek je soubor ve složce snímků (ne v DB)", soubor)
    zkontroluj(r and r["kontext"].get("uzivatel") == {"host": True} and "userAgent" in r["kontext"],
               "server doplní uživatele (host) a userAgent", r and r["kontext"])

    with A.app.app_context():
        host_id = A.User.query.filter_by(role="guest").first().id
    kod, d = posli(klient(host_id), {"zprava": "od hosta"})
    zkontroluj(kod == 201 and radek(d["id"])["user_id"] is None, "účet hosta se ukládá jako host", (kod, d))
    kod, d = posli(klient(USER_ID), {"zprava": "od uživatele", "snimek": data_url(obrazek(fmt="JPEG"), "image/jpeg")})
    r = radek(d.get("id")) if kod == 201 else None
    zkontroluj(r and r["user_id"] == USER_ID and r["kontext"]["uzivatel"]["email"] == "user@test.com"
               and r["snimek"].endswith(".jpg"), "přihlášený: uživatel i e-mail v kontextu, JPEG snímek", r)

    print("\n── API: brzda 30 zpráv za hodinu na IP ──")
    kody = [posli(c, {"zprava": f"spam {i}"}, ip="10.99.0.1")[0] for i in range(31)]
    zkontroluj(kody[:30] == [201] * 30 and kody[30] == 429, "30 projde, 31. → 429", kody[-3:])
    zkontroluj(posli(c, {"zprava": "jiná IP"}, ip="10.99.0.2")[0] == 201, "jiná IP dál smí")

    print("\n── vypínač ZPETNA_VAZBA=0 ──")
    os.environ["ZPETNA_VAZBA"] = "0"
    try:
        kod, d = posli(c, {"zprava": "vypnuto"})
        zkontroluj(kod == 404, "API vrací 404", kod)
        html = klient(USER_ID).get("/my-songbooks").get_data(as_text=True)
        zkontroluj("zpetna-vazba.js" not in html, "stránka skript záložky nevloží")
    finally:
        os.environ.pop("ZPETNA_VAZBA")
    html = klient(USER_ID).get("/my-songbooks").get_data(as_text=True)
    zkontroluj("zpetna-vazba.js" in html and "zpetna-vazba.css" in html, "zapnuto: skript i styl na stránce")

    print("\n── Správa: přístup jen pro admina ──")
    zid = d.get("id") or 1
    u = klient(USER_ID)
    zkontroluj(u.get("/sprava/zpetna-vazba").status_code == 404, "běžný uživatel: stránka 404")
    zkontroluj(u.get(f"/sprava/zpetna-vazba/{zid}/snimek").status_code == 404, "běžný uživatel: snímek 404")
    zkontroluj(u.post(f"/api/sprava/zpetna-vazba/{zid}/stav", json={"stav": "vyresena"}).status_code == 403,
               "běžný uživatel: změna stavu 403")
    zkontroluj(u.post(f"/api/sprava/zpetna-vazba/{zid}/smazat").status_code == 403, "běžný uživatel: smazání 403")
    zkontroluj(klient().get("/sprava/zpetna-vazba").status_code in (301, 302), "nepřihlášený: přesměrování")
    a = klient(ADMIN_ID)
    zkontroluj(a.get("/sprava/zpetna-vazba").status_code == 200, "admin: 200")
    zkontroluj(a.post(f"/api/sprava/zpetna-vazba/{zid}/stav", json={"stav": "blbost"}).status_code == 400,
               "neznámý stav → 400")
    zkontroluj(radek(zid)["stav"] == "nova", "a stav se nezměnil")


def vycisti():
    """Před prohlížečem jen pár zpráv, ať je seznam přehledný."""
    with A.app.app_context():
        for z in A.ZpetnaVazba.query.all():
            if z.snimek:
                (A.ZPETNA_VAZBA_DIR / z.snimek).unlink(missing_ok=True)
            A.db.session.delete(z)
        A.db.session.commit()


def prihlas(page, base, email):
    page.goto(base + "/login")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', HESLO)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


ZALOZKA = """() => {
  const z = document.querySelector('.zv-zalozka');
  if (!z) return null;
  const r = z.getBoundingClientRect(), st = getComputedStyle(z);
  const lista = document.querySelector('.navbar, header, .top-bar');
  return {videt: !z.hidden && st.display !== 'none' && r.width > 0, l: r.left, r: r.right, t: r.top, b: r.bottom,
          w: r.width, h: r.height, W: innerWidth, H: innerHeight, pozice: st.position, wm: st.writingMode,
          text: z.textContent.trim(), presah: document.documentElement.scrollWidth - innerWidth,
          listaDole: lista ? lista.getBoundingClientRect().bottom : 0};
}"""

OKNO = """() => {
  const o = document.querySelector('.zv-okno');
  const r = o.getBoundingClientRect(), st = getComputedStyle(o);
  const z = document.querySelector('.zv-zalozka');
  const pil = [...o.querySelectorAll('.zv-druh')].map(b => ({text: b.textContent, on: b.getAttribute('aria-checked')}));
  return {videt: !o.hidden && st.display !== 'none', l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width,
          W: innerWidth, H: innerHeight, zalozkaSkryta: z.hidden, pilulky: pil,
          odeslatZakazano: o.querySelector('.btn.primary').disabled,
          fokus: document.activeElement && document.activeElement.className,
          poznamka: o.querySelector('.zv-formular > .zv-pozn') && o.querySelector('.zv-formular > .zv-pozn').textContent,
          presah: document.documentElement.scrollWidth - innerWidth};
}"""


def prohlizec():
    from playwright.sync_api import sync_playwright
    import test_kontrast as K

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", PORT, A.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    velky_png = TMP / "velky.png"
    Image.new("RGB", (2400, 1200), (20, 120, 200)).save(velky_png)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        chyby = []
        page.on("pageerror", lambda e: chyby.append(str(e)))
        prihlas(page, base, "user@test.com")

        print("\n── záložka na stránkách ──")
        for cesta in ("/dashboard", "/my-songbooks", "/search", "/songbook/00006", "/editor-strany"):
            page.goto(base + cesta)
            page.wait_for_load_state("networkidle")
            z = page.evaluate(ZALOZKA)
            ok = (z and z["videt"] and z["pozice"] == "fixed" and abs(z["r"] - z["W"]) <= 1
                  and z["h"] > z["w"] and z["t"] > z["listaDole"] and z["b"] < z["H"] and z["presah"] <= 0)
            zkontroluj(ok, f"{cesta}: záložka svisle u pravého okraje, pod lištou, bez přetečení",
                       z and {k: round(v) if isinstance(v, float) else v for k, v in z.items()})

        print("\n── formulář ──")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        page.click(".zv-zalozka")
        o = page.evaluate(OKNO)
        zkontroluj(o["videt"] and o["zalozkaSkryta"], "klik otevře formulář a záložka se schová", o)
        zkontroluj(abs(o["r"] - (o["W"] - 16)) <= 1 and abs(o["b"] - (o["H"] - 16)) <= 1 and o["w"] <= 461,
                   "formulář vpravo dole, šířka ≤ 460 px", (o["l"], o["t"], o["r"], o["b"], o["w"]))
        zkontroluj([p["text"] for p in o["pilulky"]] == ["Chyba", "Nápad", "Jiné"]
                   and o["pilulky"][0]["on"] == "true", "druhy Chyba / Nápad / Jiné, vybraná Chyba", o["pilulky"])
        zkontroluj(o["odeslatZakazano"] and "zv-text" in (o["fokus"] or ""),
                   "prázdný: Odeslat zakázané, kurzor v poli", (o["odeslatZakazano"], o["fokus"]))
        zkontroluj(o["poznamka"] and "přibalí" in o["poznamka"], "poznámka o kontextu", o["poznamka"])
        ph = page.get_attribute(".zv-text", "placeholder")
        zkontroluj(ph == "Popis chyby nebo nápadu…", "placeholder", ph)

        page.click(".zv-druh[data-druh='napad']")
        zkontroluj(page.get_attribute(".zv-druh[data-druh='napad']", "aria-checked") == "true",
                   "klik na Nápad ho vybere")
        page.fill(".zv-text", "První řádek")
        page.press(".zv-text", "Shift+Enter")
        page.type(".zv-text", "druhý řádek")
        hodnota = page.input_value(".zv-text")
        zkontroluj(hodnota == "První řádek\ndruhý řádek" and page.is_visible(".zv-okno .zv-formular"),
                   "Shift+Enter dá nový řádek a neodešle", repr(hodnota))

        # Esc: nad formulářem oznámení → Esc zavře jen oznámení
        page.evaluate("() => { oznam('Testovací oznámení', {druh: 'info'}); }")   # Promise by čekal na zavření
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        stav = page.evaluate("() => ({oznameni: !!document.querySelector('.oznameni-zaclona'), "
                             "okno: !document.querySelector('.zv-okno').hidden})")
        zkontroluj(stav == {"oznameni": False, "okno": True}, "Esc zavře jen oznámení navrchu, formulář zůstane", stav)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        zkontroluj(page.evaluate("() => document.querySelector('.zv-okno').hidden") and page.is_visible(".zv-zalozka"),
                   "druhý Esc zavře formulář")
        page.click(".zv-zalozka")
        zkontroluj(page.input_value(".zv-text") == hodnota, "rozepsaná zpráva po zavření zůstane")

        # Snímek ze souboru: zmenší se na ≤ 1600 px JPEG
        page.set_input_files(".zv-soubor input", str(velky_png))
        page.wait_for_function("() => { const i = document.querySelector('.zv-nahled img'); "
                               "return i && i.complete && i.naturalWidth > 0; }", timeout=5000)
        nahled = page.evaluate("() => { const i = document.querySelector('.zv-nahled img'); "
                               "return {w: i.naturalWidth, h: i.naturalHeight, jpeg: i.src.startsWith('data:image/jpeg'), "
                               "akce: document.querySelector('.zv-snimek-akce').hidden, "
                               "vyska: i.getBoundingClientRect().height}; }")
        zkontroluj(nahled["w"] == 1600 and nahled["h"] == 800 and nahled["jpeg"] and nahled["akce"]
                   and nahled["vyska"] <= 72, "obrázek 2400×1200 → náhled JPEG 1600×800, tlačítka snímku schovaná", nahled)

        # Chyba JavaScriptu na stránce se přibalí
        page.evaluate("() => setTimeout(() => { throw new Error('testovaci chyba stranky'); }, 0)")
        page.wait_for_timeout(100)
        page.focus(".zv-text")
        page.keyboard.press("Enter")
        page.wait_for_selector(".zv-podekovani:not([hidden])", timeout=5000)
        diky = page.text_content(".zv-podekovani p")
        zkontroluj(diky == "Děkujeme, zpráva byla odeslána.", "Enter odešle, poděkování", diky)
        with A.app.app_context():
            z = A.ZpetnaVazba.query.order_by(A.ZpetnaVazba.id.desc()).first()
            r = radek(z.id)
        k = r["kontext"]
        zkontroluj(r["druh"] == "napad" and r["user_id"] == USER_ID and r["zprava"] == hodnota,
                   "v DB: druh, uživatel, zpráva", (r["druh"], r["user_id"]))
        zkontroluj(r["snimek"] and (A.ZPETNA_VAZBA_DIR / r["snimek"]).is_file()
                   and Image.open(A.ZPETNA_VAZBA_DIR / r["snimek"]).size == (1600, 800), "snímek uložený 1600×800", r["snimek"])
        zkontroluj(k.get("viewport") == "1280×800" and k.get("titulek") and k.get("jazyk") and k.get("userAgent")
                   and r["url"].endswith("/my-songbooks"), "kontext: okno, titulek, jazyk, prohlížeč, adresa",
                   {x: k.get(x) for x in ("viewport", "titulek", "jazyk", "motiv")})
        zkontroluj(any("testovaci chyba stranky" in c.get("zprava", "") for c in k.get("chybyJs", [])),
                   "chyba JavaScriptu je v kontextu", k.get("chybyJs"))
        page.click(".zv-podekovani .btn.primary")
        page.click(".zv-zalozka")
        zkontroluj(page.input_value(".zv-text") == "" and page.is_visible(".zv-snimek-akce")
                   and page.get_attribute(".zv-druh[data-druh='chyba']", "aria-checked") == "true",
                   "po odeslání a zavření je formulář prázdný")

        # Chyba serveru → oznámení, formulář zůstane
        page.route("**/api/zpetna-vazba", lambda route: route.fulfill(
            status=429, content_type="application/json",
            body=json.dumps({"ok": False, "error": "Příliš mnoho zpráv."})))
        page.fill(".zv-text", "neodejde")
        page.click(".zv-okno .btn.primary")
        page.wait_for_selector(".oznameni-zaclona", timeout=3000)
        oz = page.evaluate("() => ({text: document.querySelector('.oznameni-zaclona p').textContent, "
                           "chyba: !!document.querySelector('.oznameni-zaclona .okno-chyba, .oznameni-zaclona .okno.okno-chyba'), "
                           "okno: !document.querySelector('.zv-okno').hidden})")
        zkontroluj(oz["text"] == "Příliš mnoho zpráv." and oz["chyba"] and oz["okno"],
                   "chyba serveru přes oznam(), formulář zůstane otevřený", oz)
        page.keyboard.press("Escape")
        page.unroute("**/api/zpetna-vazba")
        page.keyboard.press("Escape")

        print("\n── editor stran: souhrn v kontextu ──")
        page.goto(base + "/editor-strany")
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => window.navrhStrany && window.navrhStrany.view", timeout=10000)
        page.click(".zv-zalozka")
        page.fill(".zv-text", "z editoru")
        page.click(".zv-okno .btn.primary")
        page.wait_for_selector(".zv-podekovani:not([hidden])", timeout=5000)
        with A.app.app_context():
            r = radek(A.ZpetnaVazba.query.order_by(A.ZpetnaVazba.id.desc()).first().id)
        es = r["kontext"].get("editorStran") or {}
        zkontroluj(es.get("adresa") == "/editor-strany" and isinstance(es.get("prvku"), int),
                   "adresa strany a počet prvků", es)

        print("\n── telefon 390 px ──")
        page.set_viewport_size({"width": 390, "height": 760})
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        z = page.evaluate(ZALOZKA)
        zkontroluj(z["videt"] and abs(z["r"] - 390) <= 1 and z["w"] <= 32 and z["presah"] <= 0,
                   "záložka u okraje, úzká, bez přetečení", (z["l"], z["w"], z["presah"]))
        page.click(".zv-zalozka")
        o = page.evaluate(OKNO)
        zkontroluj(o["l"] == 0 and abs(o["w"] - 390) <= 1 and abs(o["b"] - 760) <= 1 and o["t"] >= 0
                   and o["presah"] <= 0, "formulář přes celou šířku u spodního kraje", (o["l"], o["t"], o["w"], o["b"]))
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 1280, "height": 800})

        print("\n── host ──")
        hctx = browser.new_context(viewport={"width": 1280, "height": 800})
        hpage = hctx.new_page()
        hpage.goto(base + "/guest-login")
        hpage.wait_for_load_state("networkidle")
        hpage.click(".zv-zalozka")
        hpage.fill(".zv-text", "zpráva od hosta z prohlížeče")
        hpage.click(".zv-okno .btn.primary")
        hpage.wait_for_selector(".zv-podekovani:not([hidden])", timeout=5000)
        with A.app.app_context():
            r = radek(A.ZpetnaVazba.query.order_by(A.ZpetnaVazba.id.desc()).first().id)
        zkontroluj(r["zprava"] == "zpráva od hosta z prohlížeče" and r["user_id"] is None, "host pošle zprávu", r["user_id"])
        hctx.close()

        print("\n── kontrast ve všech motivech ──")
        audit = K.AUDIT.replace("document.querySelectorAll('*')",
                                "document.querySelectorAll('.zv-zalozka, .zv-zalozka *, .zv-okno, .zv-okno *')")
        spatne = []
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        for motiv in K.MOTIVY:
            page.evaluate(f"() => window.setDzTheme('{motiv}')")
            page.wait_for_timeout(100)
            nalezy = page.evaluate(audit, "záložka")
            page.click(".zv-zalozka")
            page.hover(".zv-druh[data-druh='jine']")   # hover nesmí zhoršit kontrast
            nalezy += page.evaluate(audit, "formulář")
            page.keyboard.press("Escape")
            spatne += [(motiv, n["kde"], n["prvek"], n["barva"], n["pozadi"], n["kontrast"]) for n in nalezy]
        zkontroluj(not spatne, "záložka i formulář nad prahem WCAG ve 12 motivech", spatne[:6])
        page.evaluate("() => window.setDzTheme('green')")

        print("\n── Správa: karta Zpětná vazba ──")
        actx = browser.new_context(viewport={"width": 1280, "height": 800})
        apage = actx.new_page()
        apage.on("pageerror", lambda e: chyby.append(str(e)))
        prihlas(apage, base, "admin@test.com")
        with A.app.app_context():
            novych = A.ZpetnaVazba.query.filter_by(stav="nova").count()
            se_snimkem = A.ZpetnaVazba.query.filter(A.ZpetnaVazba.snimek.isnot(None)).order_by(A.ZpetnaVazba.id.desc()).first()
            zid, snimek = se_snimkem.id, A.ZPETNA_VAZBA_DIR / se_snimkem.snimek
        apage.goto(base + "/sprava/zpevniky")
        apage.wait_for_load_state("networkidle")
        karta = apage.evaluate("""() => { const a = [...document.querySelectorAll('.sprava-karty a')];
            const z = a.find(x => x.textContent.includes('Zpětná vazba'));
            return {pocet: a.length, posledni: a[a.length - 1].textContent.trim(),
                    cislo: z && z.querySelector('.sprava-karty-pocet').textContent,
                    radku: new Set(a.map(x => Math.round(x.getBoundingClientRect().top))).size}; }""")
        zkontroluj(karta["pocet"] == 4 and karta["posledni"].startswith("Zpětná vazba")
                   and karta["cislo"] == str(novych) and karta["radku"] == 1,
                   "čtvrtá karta s počtem nových, karty na jednom řádku", karta)
        apage.click(".sprava-karty a:has-text('Zpětná vazba')")
        apage.wait_for_load_state("networkidle")
        s = apage.evaluate("""() => ({aktivni: document.querySelector('.sprava-karty a.aktivni').textContent.trim(),
            radku: document.querySelectorAll('.zv-radek:not([hidden])').length,
            nove: document.querySelector('[data-pocet=nova]').textContent,
            otevrene: document.querySelectorAll('.zv-radek .sz-detail:not([hidden])').length,
            prvni: (() => { const r = document.querySelector('.zv-radek:not([hidden]) .sz-souhrn');
                    return [...r.children].map(c => c.textContent.trim().replace(/\\s+/g, ' ')); })()})""")
        zkontroluj(s["aktivni"].startswith("Zpětná vazba") and s["radku"] == novych and s["nove"] == str(novych)
                   and s["otevrene"] == 0, "seznam nových zpráv, nic nerozbalené", s)
        zkontroluj(len(s["prvni"]) == 4 and s["prvni"][3].endswith("host"),
                   "řádek: čas, druh, začátek zprávy, autor (host)", s["prvni"])

        sel = f"#zv-{zid}"
        apage.click(f"{sel} .sz-souhrn")
        apage.wait_for_function(f"() => {{ const i = document.querySelector('{sel} img.zv-snimek'); "
                                "return i && i.complete && i.naturalWidth > 0; }", timeout=5000)
        d = apage.evaluate(f"""() => {{ const r = document.querySelector('{sel}');
            const dt = [...r.querySelectorAll('.zv-kontext dt')].map(x => x.textContent);
            return {{otevrene: document.querySelectorAll('.zv-radek .sz-detail:not([hidden])').length,
                    zprava: r.querySelector('.zv-zprava').textContent,
                    zalomeni: getComputedStyle(r.querySelector('.zv-zprava')).whiteSpace,
                    url: r.querySelector('.zv-url a') && r.querySelector('.zv-url a').href,
                    img: r.querySelector('img.zv-snimek').naturalWidth, dt,
                    chyb: r.querySelectorAll('.zv-chyby li').length,
                    smazat: getComputedStyle(r.querySelector('[data-akce=smazat]')).backgroundColor}}; }}""")
        zkontroluj(d["otevrene"] == 1 and d["img"] == 1600 and d["zalomeni"] == "pre-wrap"
                   and d["url"] and d["url"].endswith("/my-songbooks"), "detail: celá zpráva, odkaz, snímek přes admin routu", d)
        zkontroluj({"Prohlížeč", "Okno", "Jazyk"} <= set(d["dt"]) and d["chyb"] >= 1,
                   "kontext čitelně: prohlížeč, okno, jazyk, chyby JS", (d["dt"], d["chyb"]))
        zkontroluj(d["smazat"] == "rgb(179, 38, 30)", "Smazat je červené", d["smazat"])
        druhy = apage.locator(".zv-radek:not([hidden]) .sz-souhrn")
        if druhy.count() > 1:
            druhy.nth(1).click() if apage.evaluate(f"() => document.querySelector('.zv-radek:not([hidden])').id") == f"zv-{zid}" \
                else druhy.nth(0).click()
            zkontroluj(apage.evaluate("() => document.querySelectorAll('.zv-radek .sz-detail:not([hidden])').length") == 1
                       and not apage.is_visible(f"{sel} .sz-detail"), "otevřený je vždy jen jeden řádek")
            apage.click(f"{sel} .sz-souhrn")

        apage.click(f"{sel} [data-akce=stav]")
        apage.wait_for_function(f"() => document.querySelector('{sel}').dataset.stav === 'vyresena'", timeout=3000)
        po = apage.evaluate(f"""() => ({{skryty: document.querySelector('{sel}').hidden,
            nove: document.querySelector('[data-pocet=nova]').textContent,
            karta: document.getElementById('zv-pocet-novych').textContent}})""")
        zkontroluj(radek(zid)["stav"] == "vyresena" and po["skryty"] and po["nove"] == str(novych - 1)
                   and po["karta"] == str(novych - 1), "Označit jako vyřešené: z Nových zmizí, počty se sníží", po)
        apage.check("input[name=zv-stav][value=vyresena]")
        zkontroluj(apage.is_visible(sel) and "stav=vyresena" in apage.url, "filtr Vyřešené ji ukáže (a je v adrese)", apage.url)
        apage.click(f"{sel} .sz-souhrn")
        tl = apage.text_content(f"{sel} [data-akce=stav]")
        zkontroluj(tl == "Vrátit mezi nové", "tlačítko se přepne na Vrátit mezi nové", tl)
        apage.click(f"{sel} [data-akce=stav]")
        apage.wait_for_function(f"() => document.querySelector('{sel}').dataset.stav === 'nova'", timeout=3000)
        zkontroluj(radek(zid)["stav"] == "nova" and not apage.is_visible(sel), "Vrátit mezi nové", radek(zid)["stav"])
        apage.check("input[name=zv-stav][value=vse]")
        vse = apage.locator(".zv-radek:not([hidden])").count()
        with A.app.app_context():
            celkem = A.ZpetnaVazba.query.count()
        zkontroluj(vse == celkem, "filtr Vše ukáže všechny", (vse, celkem))

        print("\n── Správa: smazání ──")
        zkontroluj(snimek.is_file(), "snímek před smazáním existuje", snimek)
        apage.click(f"{sel} .sz-souhrn")
        apage.click(f"{sel} [data-akce=smazat]")
        apage.wait_for_selector("#app-confirm-overlay[aria-hidden=false]", timeout=3000)
        pot = apage.evaluate("() => ({text: document.getElementById('app-confirm-message').textContent, "
                             "cervene: document.getElementById('app-confirm-ok').classList.contains('nebezpecne')})")
        zkontroluj("nevratná" in pot["text"] and pot["cervene"], "potvrzení přes okno webu, červené Smazat", pot)
        apage.click("#app-confirm-ok")
        apage.wait_for_function(f"() => !document.querySelector('{sel}')", timeout=3000)
        zkontroluj(radek(zid) is None and not snimek.exists(), "zpráva smazaná i se souborem snímku", snimek)

        print("\n── Správa: telefon ──")
        apage.set_viewport_size({"width": 390, "height": 800})
        apage.reload()
        apage.wait_for_load_state("networkidle")
        m = apage.evaluate("() => ({presah: document.documentElement.scrollWidth - innerWidth, "
                           "sloupce: getComputedStyle(document.querySelector('.zv-radek .sz-souhrn')).gridTemplateColumns.split(' ').length})")
        zkontroluj(m["presah"] <= 0 and m["sloupce"] == 1, "seznam bez přetečení, řádek jako kartička", m)
        actx.close()

        print("\n── vypínač v prohlížeči ──")
        os.environ["ZPETNA_VAZBA"] = "0"
        try:
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            zkontroluj(page.locator(".zv-zalozka").count() == 0, "ZPETNA_VAZBA=0: záložka není")
        finally:
            os.environ.pop("ZPETNA_VAZBA")

        zkontroluj(not [c for c in chyby if "testovaci chyba stranky" not in c],
                   "žádné chyby JavaScriptu (kromě té testovací)", chyby[:3])
        browser.close()
    server.shutdown()


def main():
    try:
        api()
        vycisti()
        prohlizec()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ selhalo {len(selhani)}: " + "; ".join(selhani))
        return 1
    print("\n✅ zpětná vazba v pořádku")
    return 0


if __name__ == "__main__":
    sys.exit(main())
