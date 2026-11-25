#!/usr/bin/env python3
"""Přejímací test editoru stran ze zpěvníku (TODO bod 11): z editoru zpěvníku na
samostatnou stránku editoru stran, Uložit rovnou do zpěvníku, zpět, upozornění na
neuložené změny; prémiová brána.

Server běží v tomhle procesu nad kopií databáze a klonem obrázků (ukládá PDF, náhledy
a obrázky stran), takže se skutečných dat nedotkne. Měří se čísly, ne screenshoty.

    python backend/scripts/test_navrh_zpevnik.py
"""

import json
import logging
import re
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
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


LISTA = """(() => { const h = document.querySelector('.navrh-hlava').getBoundingClientRect();
  const kde = document.querySelector('.navrh-hlava .kde-strana').getBoundingClientRect();
  const li = document.querySelector('.navrh-listovani').getBoundingClientRect();
  const dole = document.querySelector('.navrh-dole').getBoundingClientRect();
  const st = document.querySelector('.navrh-hlava .hlava-stred').getBoundingClientRect();
  const ul = document.querySelector('[data-prikaz="ulozit"]').getBoundingClientRect();
  const sk = [...document.querySelectorAll('.navrh-kontext .skupina')].filter(e => !e.hidden).map(e => Math.round(e.getBoundingClientRect().top));
  return { radku: new Set(sk).size, jedenRadek: h.height < 60,
           kdeVlevo: kde.left - h.left < 20,
           listovaniDoleUprostred: li.top >= dole.top && li.bottom <= dole.bottom
             && Math.abs((li.left + li.right) / 2 - (dole.left + dole.right) / 2) < 3,
           ulozitVpravo: h.right - ul.right < 20 && Math.abs(ul.top - kde.top) < 12,
           stred: Math.round((st.left + st.right) / 2 - (h.left + h.right) / 2) }; })()"""


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
            radek = page.evaluate("""(() => { const st = document.querySelector('#es-seznam .es-strana');
              const r = (sel) => st.querySelector(sel).getBoundingClientRect();
              const blok = st.closest('.es-blok'), nahoru = blok.querySelector('.es-nahoru').getBoundingClientRect();
              const x = r('.es-odebrat'), o = r('.es-nahrat-obrazek'), t = r('.es-navrhnout'), d = r('.es-duplikovat');
              return { ikony: [t.top, o.top, d.top, x.top].map(Math.round), poradi: t.right <= o.left && o.right <= d.left && d.right <= x.left,
                       nahoruVsKriz: Math.round((nahoru.top - x.top) * 10) / 10,
                       doluVsPole: Math.round((blok.querySelector('.es-dolu').getBoundingClientRect().top
                                    - st.querySelector('.es-pisen input, .es-popisek').getBoundingClientRect().top) * 10) / 10,
                       prazdnaIkona: !!st.querySelector('.es-nahled .es-prazdna-ikona'),
                       obrazkuVNahledu: st.querySelectorAll('.es-nahled img').length,
                       popisky: [...st.querySelectorAll('.es-akce-strany button')].map(b => b.getAttribute('aria-label') || b.textContent) }; })()""")
            ok("u strany jsou vpravo ikony tužka, obrázek, duplikovat a ✕, v jedné řadě",
               len(set(radek["ikony"])) == 1 and radek["poradi"], radek)
            ok("šipka výš je na stejné vodorovné jako ✕", abs(radek["nahoruVsKriz"]) <= 0.5, radek["nahoruVsKriz"])
            ok("šipka níž začíná na horní hraně psacího pole", abs(radek["doluVsPole"]) <= 0.5, radek["doluVsPole"])
            ok("prázdná strana má v náhledu ikonu, ne obrázek", radek["prazdnaIkona"] and radek["obrazkuVNahledu"] == 0, radek)
            ok("ikony mají popisek", radek["popisky"] == ["Navrhnout stranu v editoru stran", "Nahradit stranu obrázkem",
                                                          "Duplikovat stranu", "Odebrat stranu"],
               radek["popisky"])

            print("\n── navrhnout novou stranu (zpěvník má neuložené změny) ──")
            page.click("#es-seznam .es-mezera.prvni .es-vlozit")
            page.click(".es-nabidka-vlozeni button:has-text('Navrhnout stranu')")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            tlacitka = page.locator("#app-confirm-overlay button:visible").all_inner_texts()
            ok("zeptá se: uložit a přejít / přejít bez uložení / zůstat",
               tlacitka == ["Uložit a přejít do editoru", "Přejít bez uložení", "Zrušit"], tlacitka)
            page.click("#app-confirm-save")
            editor_stran(page)
            # Nová strana vznikne hned při otevření (ne až prvním Uložit) a adresa je pak její.
            page.wait_for_url(re.compile(r"/editor-strany/\d+$"), timeout=30000)
            page.wait_for_function("!document.querySelector('[data-prikaz=\"ulozit\"]').disabled", timeout=30000)
            s_hned = strany_db()
            ok("zpěvník se před přechodem uložil a nová prázdná strana vznikla hned na začátku",
               len(s_hned) == 4 and s_hned[0][1] is not None and all(n is None for _, n, _ in s_hned[1:]), s_hned)
            ok("nová strana je prázdná (bez ukázkové písně)", page.evaluate("navrhStrany.view.state.doc.textContent") == "",
               page.evaluate("navrhStrany.view.state.doc.textContent")[:40])
            ok("lišta webu je vidět", page.evaluate("getComputedStyle(document.querySelector('.navbar')).display") != "none")
            ok("v hlavičce editoru je zpěvník a číslo nové strany 5",
               page.inner_text(".kde-strana").split() == ["‹", "Navržené", "strany", "UI"]
               and page.input_value("#cislo-strany-zpevniku") == "5",
               (page.inner_text(".kde-strana"), page.input_value("#cislo-strany-zpevniku")))
            ok("číslo a strana podle pozice (první strana zpěvníku = 5, pravá)",
               page.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]") == [5, "P"],
               page.evaluate("[navrhStrany.nastaveni().nahledCisla, navrhStrany.nastaveni().nahledStrany]"))
            page.evaluate("navrhStrany.view.focus()")     # po otevření není nic vybrané
            s1400 = page.evaluate(LISTA)
            ok("1400 px: v hlavičce zpěvník vlevo, Zpět/Znovu uprostřed, Uložit vpravo, listování uprostřed spodní lišty, úpravy textu na jednom řádku",
               s1400["kdeVlevo"] and s1400["listovaniDoleUprostred"] and s1400["ulozitVpravo"] and s1400["jedenRadek"]
               and abs(s1400["stred"]) <= 2 and s1400["radku"] == 1, s1400)
            page.set_viewport_size({"width": 1920, "height": 1000})
            page.wait_for_timeout(200)
            s1920 = page.evaluate(LISTA)
            ok("1920 px: totéž", s1920["radku"] == 1 and abs(s1920["stred"]) <= 2 and s1920["kdeVlevo"] and s1920["listovaniDoleUprostred"]
               and s1920["ulozitVpravo"] and s1920["jedenRadek"], s1920)
            page.set_viewport_size({"width": 1400, "height": 1000})

            page.evaluate("navrhStrany.nahradTextem('Testovací píseň', 'Autor', '[Ami]První řádek\\n[C]druhý řádek')")
            odpovedi = []
            page.on("response", lambda r: odpovedi.append(r) if "/api/navrh/strana" in r.url else None)
            konzole = []
            page.on("console", lambda m: konzole.append(m.text))
            try:
                with page.expect_response(lambda r: "/api/navrh/strana" in r.url and r.request.method == "POST", timeout=30000):
                    page.click('[data-prikaz="ulozit"]')
                page.wait_for_function("!document.querySelector('[data-prikaz=\"ulozit\"]').disabled", timeout=30000)
            except Exception:
                print("    ladění:", page.evaluate("location.href"), page.url, page.inner_text('[data-prikaz="ulozit"]'),
                      page.evaluate("[...document.querySelectorAll('.oznameni-zaclona')].map(e => e.innerText).join(' | ')"),
                      strany_db(), chyby[:3], [ (r.status, r.text()[:200]) for r in odpovedi], konzole[-5:])
                raise
            s = strany_db()
            ok("Uložit uloží novou verzi té strany (žádná další strana)",
               len(s) == 4 and s[0][1] is not None and s[0][1] != s_hned[0][1] and all(n is None for _, n, _ in s[1:]), s)
            with A.app.app_context():
                strana0 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=0).first()
                navrh = strana0.navrzena
                ok("adresa je teď adresa té strany", page.url.endswith(f"/editor-strany/{strana0.id}"), page.url)
                ok("verze má PDF a náhled na disku", (OBRAZKY / navrh.pdf.cesta).is_file() and (OBRAZKY / navrh.nahled.cesta).is_file())
                ok("PDF je skutečné PDF", (OBRAZKY / navrh.pdf.cesta).read_bytes().startswith(b"%PDF-"))
            ok("číslo strany zůstalo 5", page.input_value("#cislo-strany-zpevniku") == "5")
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
              return { sirka: Math.round(z.width), podil: Math.round(z.width * z.height / (b.width * b.height) * 100), text: n.querySelector('.es-znacka-navrhu').textContent }; })()""")
            ok("navržená strana má v náhledu jen malou ikonu (bez textu, do 5 % plochy)",
               znacka["text"] == "" and znacka["podil"] <= 5, znacka)
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            page.wait_for_selector("#es-lupa:not([hidden])")
            page.wait_for_selector("#es-lupa .es-lupa-strana.ostra", timeout=20000)
            lupa = page.evaluate("""(() => { const l = document.querySelector('#es-lupa .es-lupa-strana').getBoundingClientRect();
              const t = document.querySelector('#es-lupa .es-lupa-text .stranka');
              const vid = (sel) => getComputedStyle(document.querySelector('#es-lupa ' + sel)).display !== 'none';
              return { pomer: Math.round(l.height / l.width * 1000) / 1000, text: t ? t.innerText : '',
                       sirka: Math.round(t.getBoundingClientRect().width - l.width),
                       upravit: vid('.es-upravit-navrh'), vymenit: vid('.es-vymenit') }; })()""")
            ok("náhled navržené strany otevře lupu se stranou (A5), ne editor", "/my-songbooks" in page.url
               and abs(lupa["pomer"] - 2480 / 1748) < 0.01, lupa)
            ok("v lupě je strana nakreslená z textu přes celou šířku listu",
               "Testovací píseň" in lupa["text"] and abs(lupa["sirka"]) <= 1, lupa)
            ok("lupa nabídne Upravit v editoru stran, ne výměnu obrázku", lupa["upravit"] and not lupa["vymenit"], lupa)
            page.click("#es-lupa .es-zavrit")

            page.locator("#es-seznam .es-strana").first.locator(".es-navrhnout").click()
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

            page.locator("#es-seznam .es-strana").first.locator(".es-navrhnout").click()
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

            print("\n── listování zpěvníkem a rozdělané strany ──")
            ok("předchozí i další strana jdou", page.evaluate("['predchozi-strana', 'dalsi-strana'].map(p => document.querySelector(`[data-prikaz=${p}]`).disabled)") == [False, False])
            page.evaluate("navrhStrany.nahradTextem('Šestá', 'Autor', '[G]řádek šesté')")
            page.wait_for_timeout(700)

            ok("výstraha u Uložit zatím není (jiné strany rozdělané nejsou)", page.is_hidden("#neulozene-jinde"))
            page.click('[data-prikaz="dalsi-strana"]')
            editor_stran(page)
            with A.app.app_context():
                strana2 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=2).first().id
            ok("další strana se otevře bez ptaní", page.url.endswith(f"/editor-strany/{strana2}"), page.url)
            ok("u Uložit je výstraha s rozdělanou stranou 6", page.is_visible("#neulozene-jinde")
               and page.get_attribute("#neulozene-jinde", "data-napoveda") == "Neuložené změny i jinde: strana 6",
               page.get_attribute("#neulozene-jinde", "data-napoveda"))
            page.fill("#cislo-strany-zpevniku", "5")
            page.press("#cislo-strany-zpevniku", "Enter")
            editor_stran(page)
            ok("napsané číslo strany otevře stranu 5", page.input_value("#cislo-strany-zpevniku") == "5"
               and "Testovací" in page.evaluate("navrhStrany.view.state.doc.textContent"))
            page.click('[data-prikaz="dalsi-strana"]')
            editor_stran(page)
            ok("rozdělaná strana 6 se po návratu obnoví z prohlížeče",
               "Šestá" in page.evaluate("navrhStrany.view.state.doc.textContent") and page.is_hidden("#neulozene-jinde"))
            ok("a do zpěvníku se zatím nic neuložilo", strany_db()[1][1] is None, strany_db())
            page.click('[data-prikaz="dalsi-strana"]')
            editor_stran(page)
            page.evaluate("navrhStrany.nahradTextem('Sedmá', 'Autor', '[D]řádek sedmé')")
            page.click('[data-prikaz="ulozit"]')
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            tlacitka = page.locator("#app-confirm-overlay button:visible").all_inner_texts()
            zprava = page.inner_text("#app-confirm-message")
            ok("Uložit se srozumitelně zeptá: i stranu 6 / jen stranu 7 / zrušit", tlacitka == ["Uložit i stranu 6", "Uložit jen stranu 7", "Zrušit"]
               and zprava == "Neuložené změny má i strana 6 (rozpracovaná při listování). Uložit i ji?", (tlacitka, zprava))
            page.click("#app-confirm-save")
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            s2 = strany_db()
            ok("Uložit všechny uloží obě rozdělané strany do zpěvníku", s2[1][1] and s2[2][1], s2)
            ok("výstraha zmizí", page.is_hidden("#neulozene-jinde"))
            with A.app.app_context():
                zdroj6 = A.db.session.get(A.NavrzenaStrana, s2[1][1]).zdroj
            ok("strana 6 se uložila z rozdělané úpravy", "Šestá" in zdroj6)


            print("\n── dvojstrana: obě strany upravitelné ──")
            SIPKY = "['predchozi-strana', 'dalsi-strana'].map(p => Math.round(document.querySelector(`[data-prikaz=${p}]`).getBoundingClientRect().left))"
            sipky_jedna = page.evaluate(SIPKY)
            page.click('[data-prikaz="dvojstrana"]')
            editor_stran(page)
            DV = """(() => { const s = document.getElementById('stranka'), r = s.getBoundingClientRect();
              const pl = document.getElementById('navrh-plocha');
              return { trida: s.classList.contains('dvojstrana-uprava'), pomer: Math.round(r.width / r.height * 1000) / 1000,
                       text: navrhStrany.view.state.doc.textContent, vejdeSe: pl.scrollWidth <= pl.clientWidth + 1,
                       cisla: [...document.querySelectorAll('#stranka .ps-cislo-strany')].filter(e => !e.hidden).map(e => e.textContent).sort(),
                       znacky: [...document.querySelectorAll('#stranka .ps-zacatek')].map(e => e.dataset.znacka),
                       protejsek: document.querySelectorAll('.protejsek').length }; })()"""
            dv = page.evaluate(DV)
            ok("dvojstrana 6–7 je jedno plátno přes obě strany (2 × A5), bez náhledu protějšku",
               dv["trida"] and abs(dv["pomer"] - 2 * 148 / 210) < 0.01 and dv["protejsek"] == 0, dv)
            ok("na plátně jsou obě strany (Šestá i Sedmá) a vejde se", "Šestá" in dv["text"] and "Sedmá" in dv["text"] and dv["vejdeSe"], dv)
            ok("obě čísla stran", dv["cisla"] == ["6", "7"], dv["cisla"])
            ok("sloky se číslují na každé straně zvlášť (jako ve čtečce)", dv["znacky"] == ["1.", "1."], dv["znacky"])
            hl = page.evaluate("[document.getElementById('listovani-popis').textContent, document.getElementById('cislo-strany-zpevniku').value]")
            ok("v listování „Strany 6–7“ (obě strany se stejnou vahou)", hl == ["Strany", "6–7"], hl)
            ok("šipky listování zůstaly na místě (pevné šířky)", page.evaluate(SIPKY) == sipky_jedna, (sipky_jedna, page.evaluate(SIPKY)))
            # Číslo protější strany jde vybrat a táhnout stejně; poloha platí pro obě strany.
            druhe = page.evaluate("""(() => { const e = document.querySelector('#stranka .ps-cislo-druhe'); e.scrollIntoView({ block: 'center' });
              const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, e.textContent]; })()""")
            st = page.evaluate("document.getElementById('stranka').getBoundingClientRect().toJSON()")
            pred = page.evaluate("[navrhStrany.zdroj('L').nastaveni.cislo.y, navrhStrany.zdroj('P').nastaveni.cislo.y]")
            page.mouse.move(druhe[0], druhe[1])
            page.mouse.down()
            page.mouse.move(druhe[0], druhe[1] - 30 * st['height'] / 210, steps=6)
            page.mouse.up()
            po = page.evaluate("[navrhStrany.zdroj('L').nastaveni.cislo.y, navrhStrany.zdroj('P').nastaveni.cislo.y]")
            ok("číslo protější strany jde táhnout a poloha se změní na obou stranách", po[0] == po[1] and po[0] < pred[0] - 20,
               (druhe[2], pred, po))
            ok("a je vybrané (úpravy čísla v liště)", page.evaluate("document.getElementById('navrh').dataset.vyber") == "cislo"
               and page.evaluate("document.querySelector('#stranka .ps-cislo-druhe').classList.contains('vybrane')"))
            page.evaluate("""navrhStrany.cislo().volby.zmen({ cislo: { ...navrhStrany.nastaveni().cislo, y: %s } })""" % pred[0])
            page.keyboard.press("Escape")
            ok("listování přeskakuje celou dvojstranu", page.evaluate("[...['predchozi-strana', 'dalsi-strana']].map(p => document.querySelector(`[data-prikaz=${p}]`).disabled)") == [False, False])

            # Rámeček strany 7 (vpravo) přetáhnout za lištu na levou stranu.
            RAM = """(() => { const r = [...document.querySelectorAll('.ps-ramecek')].find(e => e.textContent.includes('Sedmá'));
              const l = r.querySelector('.ps-prvek-lista').getBoundingClientRect(); return [l.left, l.top, l.height]; })()"""
            l = page.evaluate(RAM)
            k = page.evaluate("document.getElementById('stranka').getBoundingClientRect().width / 296")
            page.mouse.move(l[0] + 20, l[1] + l[2] / 2)
            page.mouse.down()
            page.mouse.move(l[0] + 20 - 100 * k, l[1] + l[2] / 2, steps=8)
            page.mouse.up()
            STRANY = "['L', 'P'].map(lp => JSON.stringify(navrhStrany.zdroj(lp).obsah))"
            z = page.evaluate(STRANY)
            ok("rámeček přetažený přes hřbet patří levé straně, pravá má jen jeho kopii (přesah)",
               "Sedmá" in z[0] and '"presah":true' not in z[0] and "Sedmá" in z[1] and '"presah":true' in z[1], [x[:80] for x in z])
            page.keyboard.press("ControlOrMeta+z")
            z = page.evaluate(STRANY)
            ok("Zpět ho vrátí na pravou stranu", "Sedmá" not in z[0] and "Sedmá" in z[1])
            page.keyboard.press("ControlOrMeta+y")
            page.click('[data-prikaz="ulozit"]')
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            zprava = page.inner_text("#app-confirm-message")
            ok("rámeček přes hřbet: při uložení volba, text zůstane obsahem jen jedné strany",
               zprava.startswith("Přes hřbet dvojstrany přesahují prvky (1).") and "zůstane jen na straně, kde má rámeček střed" in zprava, zprava)
            page.click("#app-confirm-save")     # Zachovat na obou stranách
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            s3 = strany_db()
            with A.app.app_context():
                z6 = json.loads(A.db.session.get(A.NavrzenaStrana, s3[1][1]).zdroj)
                z7 = json.loads(A.db.session.get(A.NavrzenaStrana, s3[2][1]).zdroj)
            ram7 = [r for r in z6["obsah"]["content"] if "Sedmá" in json.dumps(r, ensure_ascii=False)]
            kopie7 = [r for r in z7["obsah"]["content"] if "Sedmá" in json.dumps(r, ensure_ascii=False)]
            ok("strana 7 má kopii rámečku (přesah, x −90 mm) s pevnou značkou sloky 1.",
               len(kopie7) == 1 and kopie7[0]["attrs"]["presah"] is True and kopie7[0]["attrs"]["x"] == -90
               and [p["attrs"]["znacka"] for p in kopie7[0]["content"] if p["attrs"]["znacka"]] == ["1."],
               kopie7[0]["attrs"] if kopie7 else z7["obsah"]["content"])
            ok("Uložit uloží obě strany: rámeček je teď ve straně 6 (na jejích souřadnicích)",
               s3[1][1] != s2[1][1] and s3[2][1] != s2[2][1] and ram7 and 0 <= ram7[0]["attrs"]["x"] < 148,
               (s3, ram7[0]["attrs"] if ram7 else None))
            with A.app.app_context():
                strana7 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=2).first().id
            page.evaluate("localStorage.setItem('navrh-rezim-stran', 'jedna')")
            page.goto(f"{base}/editor-strany/{strana7}")
            editor_stran(page)
            jedna = page.evaluate("""(() => { const r = document.querySelector('.ps-ramecek.ps-presah');
              return r ? { upravitelny: r.querySelector('.ps-ramecek-obsah').isContentEditable, lista: getComputedStyle(r.querySelector('.ps-prvek-lista')).display,
                           znacka: r.querySelector('.ps-zacatek') && r.querySelector('.ps-zacatek').dataset.znacka } : null; })()""")
            ok("samotná strana 7: kopie přes hřbet je jen k vidění, se značkou 1.", jedna == {"upravitelny": False, "lista": "none", "znacka": "1."}, jedna)
            page.evaluate("localStorage.setItem('navrh-rezim-stran', 'dvojstrana')")
            page.goto(f"{base}/editor-strany/{strana7}")
            editor_stran(page)
            ram = page.evaluate("[...document.querySelectorAll('.ps-ramecek')].filter(r => r.textContent.includes('Sedmá')).map(r => r.classList.contains('ps-presah'))")
            ok("ve dvojstraně je rámeček zase jen jednou a upravitelný", ram == [False], ram)

            # Náplast přes hřbet: na každé straně její část.
            page.evaluate("""(() => { const v = navrhStrany.view, N = v.state.schema.nodes;
              v.dispatch(v.state.tr.insert(v.state.doc.content.size, N.tvar.create({ x: 140, y: 50, w: 20, h: 6, barva: '#ff0000' }))); })()""")
            TVARY = "['L', 'P'].map(lp => navrhStrany.zdroj(lp).obsah.content.filter(n => n.type === 'tvar').map(n => n.attrs.x))"
            ok("náplast přes hřbet patří oběma stranám (levá od 140 mm, pravá od −8 mm)", page.evaluate(TVARY) == [[140], [-8]],
               page.evaluate(TVARY))
            page.click('[data-prikaz="ulozit"]')
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            tlacitka = page.locator("#app-confirm-overlay button:visible").all_inner_texts()
            ok("náplast přes hřbet: Zachovat na obou stranách / Oříznout / Zrušit",
               tlacitka == ["Zachovat na obou stranách", "Oříznout", "Zrušit"], tlacitka)
            page.click("#app-confirm-save")
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            s4 = strany_db()
            with A.app.app_context():
                xs = [[n["attrs"]["x"] for n in json.loads(A.db.session.get(A.NavrzenaStrana, s4[i][1]).zdroj)["obsah"]["content"]
                       if n["type"] == "tvar"] for i in (1, 2)]
            ok("uložená strana 6 i 7 má svou část náplasti", xs == [[140], [-8]], xs)
            page.reload()
            editor_stran(page)
            na_platne = page.evaluate("navrhStrany.view.state.doc.toJSON().content.filter(n => n.type === 'tvar').map(n => n.attrs.x)")
            ok("po znovuotevření je na plátně jedna celá náplast (dá se táhnout vcelku)", na_platne == [140], na_platne)
            ok("a nic není rozdělané", page.is_hidden("#neulozene-jinde") and not page.evaluate("Object.keys(localStorage).some(k => k.startsWith('navrh-koncept:'))"))
            page.evaluate("""(() => { const v = navrhStrany.view, N = v.state.schema.nodes;
              v.dispatch(v.state.tr.insert(v.state.doc.content.size, N.tvar.create({ x: 145, y: 80, w: 20, h: 6, barva: '#00ff00' }))); })()""")
            page.click('[data-prikaz="ulozit"]')
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            page.click("#app-confirm-ok")       # Oříznout
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            s4 = strany_db()
            with A.app.app_context():
                xs = [[n["attrs"]["x"] for n in json.loads(A.db.session.get(A.NavrzenaStrana, s4[i][1]).zdroj)["obsah"]["content"]
                       if n["type"] == "tvar"] for i in (1, 2)]
            ok("Oříznout: náplasti přes hřbet zůstanou jen na straně podle středu (obě vpravo)", xs == [[], [-8, -3]], xs)
            ok("a po uložení není nic rozdělané", page.is_hidden("#neulozene-jinde") and not page.evaluate("Object.keys(localStorage).some(k => k.startsWith('navrh-koncept:'))"))

            # Rozdělané obě strany: přežijí přelistování a odchod je vyjmenuje.
            page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('X', 2)); })()")
            page.click('[data-prikaz="ramecek"]')
            st = page.evaluate("document.getElementById('stranka').getBoundingClientRect().toJSON()")
            k = st["width"] / 296
            page.mouse.click(st["x"] + 150 * k, st["y"] + 202 * k)    # pravá strana, dole
            page.keyboard.type("Y")
            ok("nový rámeček (nástroj Text, klik vpravo) patří straně 7",
               "Y" in page.evaluate("JSON.stringify(navrhStrany.zdroj('P').obsah)") and "Y" not in page.evaluate("JSON.stringify(navrhStrany.zdroj('L').obsah)"))
            page.click('[data-prikaz="dalsi-strana"]')
            editor_stran(page)
            jedna = page.evaluate("""(() => { const d = document.getElementById('dvojstrana').getBoundingClientRect(),
              s = document.getElementById('stranka-obal').getBoundingClientRect(), p = document.getElementById('navrh-plocha').getBoundingClientRect();
              return { protejsek: document.querySelectorAll('.protejsek').length, sirka: Math.round(d.width - s.width),
                       stred: Math.round((s.left + s.right) / 2 - (p.left + p.right) / 2), spoj: !!navrhStrany.dvojstrana() }; })()""")
            ok("poslední strana (8, bez protějšku) je i ve dvojstraně jedna strana na střed", jedna["protejsek"] == 0
               and jedna["sirka"] == 0 and abs(jedna["stred"]) <= 12 and not jedna["spoj"], jedna)
            with A.app.app_context():
                strana8 = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=3).first().id
            page.click("#nova-strana")
            nabidka = page.evaluate("""(() => ({ videt: !document.getElementById('nabidka-stran').hidden,
              za: document.getElementById('ns-za-popis').textContent, kopie: document.getElementById('ns-kopie').value,
              moznosti: document.getElementById('ns-kopie').options.length,
              nadListou: (() => { const r = document.getElementById('nabidka-stran').getBoundingClientRect();
                return r.top >= 0 && r.bottom <= document.querySelector('.navrh-dole').getBoundingClientRect().top; })() }))()""")
            ok("+ otevře nabídku Přidat stranu: za touto (8), ke kopii předvybraná tato",
               nabidka["videt"] and nabidka["za"] == "Za stranu 8" and nabidka["kopie"] == str(strana8) and nabidka["moznosti"] == 4 and nabidka["nadListou"], nabidka)
            page.keyboard.press("Escape")
            ok("Esc nabídku zavře", page.is_hidden("#nabidka-stran"))
            ok("po přelistování na 8 je výstraha s rozdělanými 6, 7",
               page.get_attribute("#neulozene-jinde", "data-napoveda") == "Neuložené změny i jinde: strany 6, 7",
               page.get_attribute("#neulozene-jinde", "data-napoveda"))
            page.click('[data-prikaz="predchozi-strana"]')
            editor_stran(page)
            ok("zpátky na 6–7 jsou obě rozdělané úpravy", page.evaluate("navrhStrany.view.state.doc.textContent").count("X") >= 1
               and "Y" in page.evaluate("navrhStrany.view.state.doc.textContent") and page.is_hidden("#neulozene-jinde"))
            page.click("#zpet-do-zpevniku")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            zprava = page.inner_text("#app-confirm-message")
            ok("odchod z dvojstrany vyjmenuje obě rozdělané strany", zprava == "Neuložené změny jsou na stranách 6, 7.", zprava)
            page.click("#app-confirm-ok")       # Odejít bez uložení
            page.wait_for_url("**/my-songbooks**")
            ok("odejít bez uložení koncepty zahodí", page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('navrh-koncept:')).length") == 0)
            ok("a uložené strany zůstanou", strany_db()[1][1] == s4[1][1] and strany_db()[2][1] == s4[2][1], strany_db())
            page.evaluate("localStorage.setItem('navrh-rezim-stran', 'jedna')")
            otevri_editor_zpevniku(page)
            page.locator("#es-seznam .es-strana").nth(1).locator(".es-navrhnout").click()
            editor_stran(page)
            page.click("#zpet-do-zpevniku")
            page.wait_for_url("**/my-songbooks**")
            otevri_editor_zpevniku(page)
            # Poslední strana před klikem: nová vzniká hned při otevření editoru.
            with A.app.app_context():
                posledni = A.Strana.query.filter_by(songbook_id=ZPEVNIK).order_by(A.Strana.poradi.desc()).first().id
            page.click("#es-navrhnout-novou")
            editor_stran(page)
            page.wait_for_url(re.compile(r"/editor-strany/\d+$"), timeout=30000)
            page.wait_for_function("!document.querySelector('[data-prikaz=\"ulozit\"]').disabled", timeout=30000)
            with A.app.app_context():
                nova_posledni = A.Strana.query.filter_by(songbook_id=ZPEVNIK).order_by(A.Strana.poradi.desc()).first()
                nova_id, pocet_pred = nova_posledni.id, A.Strana.query.filter_by(songbook_id=ZPEVNIK).count()
            ok("„nebo navrhnout v editoru stran“ založí novou stranu na konci zpěvníku a otevře ji",
               nova_id != posledni and page.url.endswith(f"/editor-strany/{nova_id}") and nova_posledni.navrzena_id is not None, page.url)

            print("\n── Přidat stranu: kopie, víc stran naráz, bez ptaní na uložení ──")
            page.click('[data-prikaz="ramecek"]')
            st = page.evaluate("document.getElementById('stranka').getBoundingClientRect().toJSON()")
            page.mouse.click(st["x"] + 40, st["y"] + 60)
            page.keyboard.type("Kopie")
            dialogy = []
            def zavri_dialog(d):
                dialogy.append(d.message)
                d.dismiss()
            page.on("dialog", zavri_dialog)
            page.click("#nova-strana")
            page.check('input[name="ns-kam"][value="konec"]')
            page.select_option("#ns-kopie", str(nova_id))
            ok("výběr strany ke kopii zvolí „Kopie strany“", page.is_checked('input[name="ns-obsah"][value="kopie"]'))
            page.fill("#ns-pocet", "2")
            page.click("#ns-pridat")
            page.wait_for_url(lambda u: re.search(r"/editor-strany/\d+$", u) and not u.endswith(f"/{nova_id}"), timeout=60000)
            editor_stran(page)
            with A.app.app_context():
                A.db.session.expire_all()
                strany_po = A.Strana.query.filter_by(songbook_id=ZPEVNIK).order_by(A.Strana.poradi).all()
                zdroje = [json.loads(st.navrzena.zdroj)["obsah"] if st.navrzena else None for st in strany_po[-2:]]
                nove_ids = [st.id for st in strany_po[-2:]]
            ok("přidaly se 2 strany na konec zpěvníku", len(strany_po) == pocet_pred + 2, (pocet_pred, len(strany_po)))
            ok("obě jsou kopie strany i s neuloženou úpravou", all(z and "Kopie" in json.dumps(z, ensure_ascii=False) for z in zdroje))
            ok("editor otevřel první z nich", page.url.endswith(f"/editor-strany/{nove_ids[0]}"), page.url)
            ok("přidání se na uložení neptalo", page.locator("#app-confirm-overlay:visible").count() == 0 and not dialogy, dialogy)
            ok("rozdělaná úprava zdrojové strany zůstala v prohlížeči",
               page.evaluate(f"!!localStorage.getItem('navrh-koncept:{ZPEVNIK}:s{nova_id}')"))

            # Rozdělaná úprava zdrojové strany zůstala - odchod by se (správně) zeptal.
            page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('navrh-koncept:')).forEach(k => localStorage.removeItem(k))")
            page.remove_listener("dialog", zavri_dialog)

            print("\n── Editor strany: vlastní stránka s výběrem, co upravovat ──")
            with A.app.app_context():
                ids = [st.id for st in A.db.session.get(A.Songbook, ZPEVNIK).strany]
                verejne = {sb.id for sb in A.Songbook.query.filter_by(is_public=1)}
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("domcontentloaded")
            page.locator(".songbook-card .songbook-nazev").first.click()
            page.wait_for_timeout(300)
            ok("klik myší na název zpěvníku otevře nabídku (ne čtečku)", "/my-songbooks" in page.url
               and page.locator(".akce-nabidka:not([hidden])").count() == 1, page.url)
            page.keyboard.press("Escape")
            odpoved = page.goto(base + "/editor-strany")
            page.wait_for_selector("#vyber-strany .vs-zpevniky button")
            zpevniky = page.evaluate("[...document.querySelectorAll('.vs-zpevniky button')].map(b => b.dataset.zpevnik)")
            ok("/editor-strany je stránka s výběrem přímo v ní (ne okno přes jinou stránku)",
               odpoved.status == 200 and page.locator(".vs-zaclona").count() == 0 and page.locator(".vs-zavrit").count() == 0)
            ok("zpěvníky jen z Mých zpěvníků", ZPEVNIK in zpevniky and not (set(zpevniky) & verejne), len(zpevniky))
            page.click(f".vs-zpevniky button[data-zpevnik='{ZPEVNIK}']")
            page.wait_for_selector(".vs-strany .vs-strana")
            dlazdice = page.evaluate("[...document.querySelectorAll('.vs-strana')].map(a => [a.getAttribute('href'), a.querySelector('.vs-popis b')?.textContent])")
            ok("po výběru zpěvníku strany s čísly a dlaždice Nová strana",
               [d[0] for d in dlazdice[:-1]] == [f"/editor-strany/{i}" for i in ids]
               and [d[1] for d in dlazdice[:-1]] == [str(5 + i) for i in range(len(ids))]
               and dlazdice[-1][0] == f"/editor-strany/nova?zpevnik={ZPEVNIK}&za={ids[-1]}", dlazdice)
            page.click(".vs-zpet")
            page.wait_for_selector(".vs-zpevniky button")
            ok("šipka zpět vrátí seznam zpěvníků", page.locator(".vs-zpevniky button").count() == len(zpevniky))
            page.click(f".vs-zpevniky button[data-zpevnik='{ZPEVNIK}']")
            page.wait_for_selector(".vs-strany .vs-strana")
            page.click(f".vs-strana[data-strana='{ids[1]}']")
            editor_stran(page)
            ok("klik na stranu ji otevře v editoru", page.url.endswith(f"/editor-strany/{ids[1]}"), page.url)
            page.click(".jina-strana")
            page.wait_for_selector(".vs-strany .vs-strana")
            ok("v editoru Jiná strana… otevře okno rovnou se stranami tohoto zpěvníku",
               page.inner_text("#vs-nadpis") == "Navržené strany UI" and page.locator(".vs-strana").count() == len(ids) + 1)
            page.keyboard.press("Escape")

            print("\n── odchod z editoru stran přes lištu webu ──")
            page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Q', 2)); })()")
            ok("Jiná strana… v editoru stran je okno přes editor", page.locator(".vs-zaclona").count() == 1)
            page.keyboard.press("Escape")
            page.click(".nav-links a[href='/my-songbooks']")
            page.wait_for_selector("#app-confirm-overlay", state="visible")
            ok("odkaz v liště webu s neuloženou stranou: okno aplikace, ne prohlížeče",
               page.locator("#app-confirm-overlay button:visible").all_inner_texts() == ["Uložit a odejít", "Odejít bez uložení", "Zrušit"])
            page.click("#app-confirm-ok")
            page.wait_for_url("**/my-songbooks**")

            print("\n── odchod z webu s neuloženým editorem zpěvníku ──")
            page.evaluate(f"window.openEditSongbookModal('{ZPEVNIK}')")
            otevri_editor_zpevniku(page)
            page.fill("#nb-name", "Změněný název")
            dialogy = []
            page.on("dialog", lambda d: (dialogy.append(d.type), d.dismiss()))
            page.close(run_before_unload=True)
            time.sleep(0.8)
            ok("zavření stránky s neuloženým zpěvníkem upozorní (prohlížeč)", "beforeunload" in dialogy, dialogy)
            page, chyby = prihlas(br, base, EMAIL3)
            page.fill("#nb-name", "Navržené strany UI")
            with page.expect_navigation(timeout=20000):
                page.click("#nb-save-top")
            page.wait_for_load_state("load")
            dialogy = []
            page.on("dialog", lambda d: (dialogy.append(d.type), d.accept()))
            page.close(run_before_unload=True)
            time.sleep(0.8)
            ok("po uložení (obnovení stránky) se prohlížeč neptá", dialogy == [], dialogy)
            page, chyby = prihlas(br, base, EMAIL3)

            print("\n── duplikovat stranu ──")
            otevri_editor_zpevniku(page)
            pred = strany_db()
            pocet = page.locator("#es-seznam .es-strana").count()
            page.locator("#es-seznam .es-strana").first.locator(".es-duplikovat").click()
            ok("duplikovat vloží kopii hned za stranu", page.locator("#es-seznam .es-strana").count() == pocet + 1
               and page.evaluate("EditorStran.data()[1].navrzena_id") == pred[0][1]
               and page.evaluate("EditorStran.data()[1].pisne.length") == 0, page.evaluate("EditorStran.data().slice(0, 2)"))
            page.click("#nb-save-top")
            page.wait_for_function("!document.getElementById('editor-stran') || !document.getElementById('editor-stran').offsetParent", timeout=20000)
            po = strany_db()
            ok("po uložení mají obě strany tutéž (neměnnou) verzi", len(po) == len(pred) + 1 and po[0][1] == po[1][1] == pred[0][1], po)
            with A.app.app_context():
                kopie = A.Strana.query.filter_by(songbook_id=ZPEVNIK, poradi=1).first().id
            page.goto(f"{base}/editor-strany/{kopie}")
            editor_stran(page)
            page.evaluate("(() => { const v = navrhStrany.view; v.dispatch(v.state.tr.insertText('Kopie ', 2)); })()")
            page.click('[data-prikaz="ulozit"]')
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            po2 = strany_db()
            ok("úprava kopie dá novou verzi jen jí, originál zůstane", po2[0][1] == pred[0][1] and po2[1][1] != pred[0][1], po2)
            ok("bez chyb JavaScriptu", not chyby, chyby[:3])
            page.close()

            print("\n── účet bez prémia (člen sdíleného zpěvníku) ──")
            page, chyby = prihlas(br, base, EMAIL4)
            ok("Navrhnout je zašedlé", page.locator("#es-seznam .es-navrhnout").first.is_disabled())
            page.locator("#es-seznam .es-strana").first.locator(".es-nahled").click()
            page.wait_for_selector("#es-lupa:not([hidden])")
            ok("náhled navržené strany jen zvětší, Upravit v editoru stran nenabídne",
               "/my-songbooks" in page.url and page.evaluate("getComputedStyle(document.querySelector('#es-lupa .es-upravit-navrh')).display") == "none")
            page.click("#es-lupa .es-zavrit")
            ok("„nebo navrhnout v editoru stran“ je zašedlé", page.locator("#es-navrhnout-novou").is_disabled())
            for adresa in (f"/editor-strany/{strana1}", f"/editor-strany/nova?zpevnik={ZPEVNIK}&za=0", "/editor-strany"):
                odpoved = page.goto(base + adresa)
                ok(f"místo editoru strany ukázkový editor ({adresa})", odpoved.status == 200 and page.url.endswith("/editor-strany")
                   and page.locator(".navrh-ukazka-pruh").count() == 1 and page.locator("[data-prikaz='ulozit']").count() == 0,
                   (odpoved.status, page.url))
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
