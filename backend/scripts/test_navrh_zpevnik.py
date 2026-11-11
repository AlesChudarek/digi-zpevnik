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
            radek = page.evaluate("""(() => { const st = document.querySelector('#es-seznam .es-strana');
              const r = (sel) => st.querySelector(sel).getBoundingClientRect();
              const blok = st.closest('.es-blok'), nahoru = blok.querySelector('.es-nahoru').getBoundingClientRect();
              const x = r('.es-odebrat'), o = r('.es-nahrat-obrazek'), t = r('.es-navrhnout');
              return { ikony: [o.top, t.top, x.top].map(Math.round), poradi: o.right <= t.left && t.right <= x.left,
                       nahoruVsKriz: Math.round((nahoru.top - x.top) * 10) / 10,
                       doluVsPole: Math.round((blok.querySelector('.es-dolu').getBoundingClientRect().top
                                    - st.querySelector('.es-pisen input, .es-popisek').getBoundingClientRect().top) * 10) / 10,
                       prazdnaIkona: !!st.querySelector('.es-nahled .es-prazdna-ikona'),
                       obrazkuVNahledu: st.querySelectorAll('.es-nahled img').length,
                       popisky: [...st.querySelectorAll('.es-akce-strany button')].map(b => b.getAttribute('aria-label') || b.textContent) }; })()""")
            ok("u strany jsou vpravo ikony obrázek a tužka a pak ✕, v jedné řadě",
               len(set(radek["ikony"])) == 1 and radek["poradi"], radek)
            ok("šipka výš je na stejné vodorovné jako ✕", abs(radek["nahoruVsKriz"]) <= 0.5, radek["nahoruVsKriz"])
            ok("šipka níž začíná na horní hraně psacího pole", abs(radek["doluVsPole"]) <= 0.5, radek["doluVsPole"])
            ok("prázdná strana má v náhledu ikonu, ne obrázek", radek["prazdnaIkona"] and radek["obrazkuVNahledu"] == 0, radek)
            ok("ikony mají popisek", radek["popisky"][:2] == ["Nahrát obrázek strany", "Navrhnout stranu v editoru stran (text a akordy)"],
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
            ok("editor stran je samostatná stránka s adresou nové strany",
               "/editor-strany/nova?zpevnik=" in page.url and page.url.endswith("&za=0"), page.url)
            ok("zpěvník se před přechodem uložil (3 strany), nová strana zatím nevznikla",
               len(strany_db()) == 3 and all(n is None for _, n, _ in strany_db()), strany_db())
            ok("lišta webu je vidět", page.evaluate("getComputedStyle(document.querySelector('.navbar')).display") != "none")
            ok("v liště editoru je zpěvník, dole číslo nové strany 5",
               page.inner_text(".kde-strana").split() == ["‹", "Navržené", "strany", "UI"]
               and page.input_value("#cislo-strany-zpevniku") == "5",
               (page.inner_text(".kde-strana"), page.input_value("#cislo-strany-zpevniku")))
            dole = page.evaluate("""(() => { const l = document.querySelector('.navrh-listovani').getBoundingClientRect();
              const z = document.querySelector('.navrh-zoom:not(.navrh-listovani)').getBoundingClientRect();
              const p = document.querySelector('#navrh-plocha').getBoundingClientRect();
              return { vedleZoomu: Math.abs(l.top - z.top) < 2 && l.right <= z.left, dole: p.bottom - l.bottom < 40,
                       l: [l.left, l.top, l.right, l.bottom].map(Math.round), z: [z.left, z.top, z.right, z.bottom].map(Math.round),
                       p: [p.left, p.top, p.right, p.bottom].map(Math.round) }; })()""")
            ok("listování je dole vpravo vedle přiblížení", dole["vedleZoomu"] and dole["dole"], dole)
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
                print("    ladění:", page.evaluate("location.href"), page.url, page.inner_text('[data-prikaz="ulozit"]'),
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
               and page.get_attribute("#neulozene-jinde", "data-napoveda") == "Neuložené změny i na stranách 6",
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
            ok("Uložit se zeptá: všechny / jen tuto / zrušit", tlacitka == ["Uložit všechny", "Uložit jen tuto", "Zrušit"]
               and zprava == "Neuložené změny jsou i na stranách 6. Tato strana: 7.", (tlacitka, zprava))
            page.click("#app-confirm-save")
            page.wait_for_function("document.querySelector('[data-prikaz=\"ulozit\"]').textContent === 'Uloženo'", timeout=30000)
            s2 = strany_db()
            ok("Uložit všechny uloží obě rozdělané strany do zpěvníku", s2[1][1] and s2[2][1], s2)
            ok("výstraha zmizí", page.is_hidden("#neulozene-jinde"))
            with A.app.app_context():
                zdroj6 = A.db.session.get(A.NavrzenaStrana, s2[1][1]).zdroj
            ok("strana 6 se uložila z rozdělané úpravy", "Šestá" in zdroj6)


            print("\n── dvojstrana: obě strany upravitelné ──")
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
            page.keyboard.type("Y")
            ok("nový rámeček (Text) vznikne na upravované straně 7, vpravo",
               "Y" in page.evaluate("JSON.stringify(navrhStrany.zdroj('P').obsah)") and "Y" not in page.evaluate("JSON.stringify(navrhStrany.zdroj('L').obsah)"))
            page.click('[data-prikaz="dalsi-strana"]')
            editor_stran(page)
            ok("po přelistování na 8 je výstraha s rozdělanými 6, 7",
               page.get_attribute("#neulozene-jinde", "data-napoveda") == "Neuložené změny i na stranách 6, 7",
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
            page.click("#es-navrhnout-novou")
            editor_stran(page)
            with A.app.app_context():
                posledni = A.Strana.query.filter_by(songbook_id=ZPEVNIK).order_by(A.Strana.poradi.desc()).first().id
            ok("„nebo navrhnout v editoru stran“ otevře novou stranu na konci zpěvníku",
               page.url.endswith(f"/editor-strany/nova?zpevnik={ZPEVNIK}&za={posledni}"), page.url)

            print("\n── duplikovat stranu ──")
            page.click("#zpet-do-zpevniku")
            page.wait_for_url("**/my-songbooks**")
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
