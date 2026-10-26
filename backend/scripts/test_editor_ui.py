#!/usr/bin/env python3
"""Přejímací test editoru stran v prohlížeči (static/js/editor.js).

Server běží v tomhle procesu nad kopií databáze a klonem obrázků, takže nahrávání
a mazání stran se skutečných dat nedotkne. Měří se čísly, ne screenshoty.

    python backend/scripts/test_editor_ui.py
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

TMP = Path(tempfile.mkdtemp(prefix="editor-ui-"))
DB = TMP / "editor.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "editor-ui")

import backend.app as A  # noqa: E402
from PIL import Image  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)],
                  capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None   # podproces by běžel nad skutečnými daty
HESLO = "editor-ui"
with A.app.app_context():
    uzivatel = A.db.session.get(A.User, 3)
    uzivatel.password = generate_password_hash(HESLO, method="pbkdf2:sha256:1000")
    druhy = A.db.session.get(A.User, 4)
    druhy.password = uzivatel.password
    A.db.session.commit()
    EMAIL = uzivatel.email
    EMAIL4 = druhy.email
    # Ostatním se ukazuje přezdívka, bez ní e-mail.
    JMENO = A.jmeno_uzivatele(uzivatel)
    JMENO4 = A.jmeno_uzivatele(druhy)

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def akce(page, trida, zpevnik_id):
    """Otevře nabídku Možnosti u dlaždice zpěvníku a klikne na položku."""
    karta = page.locator(f'.songbook-card[data-book-id="{zpevnik_id}"]')
    karta.locator(".akce-spoustec").click()
    karta.locator(f".{trida}").click()


def png(sirka):
    b = io.BytesIO()
    Image.new("RGB", (sirka, int(sirka * 2480 / 1748)), (200, 120, 40)).save(b, "PNG")
    return b.getvalue()


def strany_db(zpevnik_id):
    with A.app.app_context():
        A.db.session.expire_all()
        sb = A.db.session.get(A.Songbook, zpevnik_id)
        return [(A.cislo_strany(sb, st), bool(st.image_id), st.popisek,
                 [(v.song.title, v.poradi_v_pisni) for v in st.pisne]) for st in sb.strany]


def typy(page):
    """Typy stran tak, jak by odešly na server (editor je odvozuje, nevolí se)."""
    return page.evaluate("EditorStran.data().map(s => s.typ)")


def strana(page, i):
    return page.locator("#es-seznam .es-strana").nth(i)


def sedy_png(odstin, sirka=200):
    b = io.BytesIO()
    Image.new("RGB", (sirka, int(sirka * 2480 / 1748)), (odstin,) * 3).save(b, "PNG")
    return b.getvalue()


# Odstín uprostřed každého náhledu. Strany importu mají každá jinou šedou, takže
# podle ní jde poznat, která strana souboru kam přišla.
ODSTINY = """() => [...document.querySelectorAll('#es-seznam .es-nahled img')].map(img => {
  const c = document.createElement('canvas'); c.width = 8; c.height = 8;
  const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0, 8, 8);
  return ctx.getImageData(4, 4, 1, 1).data[0];
})"""


PRETEKA = """() => {
  const m = document.querySelector('#new-book-modal .modal').getBoundingClientRect();
  const venku = [...document.querySelectorAll('#editor-stran *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width && (r.right > m.right + 0.5 || r.left < m.left - 0.5);
  }).map(e => e.className || e.tagName);
  return {okno: Math.round(m.width), stranka: document.documentElement.scrollWidth,
          sirka: window.innerWidth, venku: venku.slice(0, 5)};
}"""


def main():
    from playwright.sync_api import sync_playwright

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 5597, A.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:5597"
    soubory = TMP / "nahrat"
    soubory.mkdir()
    for jmeno, sirka in (("10.png", 300), ("2.png", 200), ("1.png", 100)):
        (soubory / jmeno).write_bytes(png(sirka))
    # PDF o šesti stranách, strana n má šedou 30*n
    stranky = [Image.new("RGB", (595, 842), (30 * n,) * 3) for n in range(1, 7)]
    stranky[0].save(soubory / "zpevnik.pdf", save_all=True, append_images=stranky[1:])
    import zipfile
    with zipfile.ZipFile(soubory / "skeny.zip", "w") as z:
        for jmeno, odstin in (("skeny/strana 10.png", 100), ("skeny/strana 2.png", 60),
                              ("skeny/strana 1.png", 20)):
            z.writestr(jmeno, sedy_png(odstin))
        z.writestr("__MACOSX/skeny/._strana 1.png", b"balast")
        z.writestr("skeny/poznamky.txt", b"nic")

    with sync_playwright() as p:
        prohlizec = p.chromium.launch()
        page = prohlizec.new_page(viewport={"width": 1280, "height": 900})
        chyby = []
        page.on("pageerror", lambda e: chyby.append(str(e)))
        dialogy = []
        page.on("dialog", lambda d: (dialogy.append(d.message), d.accept()))

        page.goto(base + "/login")
        page.fill('input[name="email"]', EMAIL)
        page.fill('input[name="password"]', HESLO)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        print("\n── nový zpěvník ──")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        page.click(".add-card")
        zkontroluj(page.is_visible("#editor-stran") and page.is_visible("#es-prazdno")
                   and page.is_visible("#es-pridat"),
                   "okno nového zpěvníku má rovnou sekci stran a „Přidat stranu“")
        page.set_input_files("#es-soubory", [str(soubory / j) for j in ("10.png", "2.png", "1.png")])
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 3")
        page.wait_for_function("document.getElementById('es-stav-nahravani').hidden", timeout=20000)
        sirky = page.eval_on_selector_all(
            "#es-seznam .es-nahled img", "els => els.map(i => i.naturalWidth)")
        zkontroluj(sirky == [100, 200, 300], "soubory se seřadí podle jména (1, 2, 10)", sirky)
        zkontroluj(not page.is_visible("#es-prazdno"), "hláška o prázdném zpěvníku zmizí")
        zkontroluj(page.locator("#es-seznam .es-nazev").count() == 3,
                   "nahraná strana má rovnou pole pro název písně")
        aktivni = page.evaluate("document.activeElement.className")
        zkontroluj(aktivni == "es-nazev", "kurzor skočí do názvu první nové strany", aktivni)

        strana(page, 0).locator(".es-nazev").fill("Píseň A")
        strana(page, 0).locator(".es-autor").fill("Autor A")
        strana(page, 0).locator(".es-spojit").click()
        zkontroluj(page.locator("#es-seznam .es-blok").count() == 2
                   and "pripojena" in strana(page, 1).get_attribute("class"),
                   "spojením se další strana připojí do jednoho bloku")
        zkontroluj(strana(page, 1).locator(".es-nazev").count() == 0,
                   "prázdné pole pro název z připojené strany zmizí")
        zkontroluj(strana(page, 1).locator(".es-popisek").count() == 0,
                   "a pokračování nemá popisek")
        navaz = strana(page, 1).locator(".es-navazuje").inner_text()
        zkontroluj(navaz == "pokračuje „Píseň A“", "připojená strana ukáže, co na ni pokračuje", navaz)
        strana(page, 0).locator(".es-nazev").fill("Píseň A1")
        navaz = strana(page, 1).locator(".es-navazuje").inner_text()
        zkontroluj(navaz == "pokračuje „Píseň A1“", "a mění se s psaním názvu", navaz)
        bloky = page.locator("#es-seznam .es-blok")
        zkontroluj(bloky.nth(0).locator(".es-drzadlo").count() == 1
                   and page.locator("#es-seznam .es-mezera").count() == 1,
                   "blok má jedno držadlo a uvnitř se nic nevkládá")
        strana(page, 2).locator(".es-odebrat-pisen").click()
        zkontroluj(strana(page, 2).locator(".es-popis-strany").inner_text() == "strana bez písně",
                   "strana s obrázkem bez písně se tak i popíše")
        strana(page, 2).locator(".es-popisek").fill("Úvod")
        page.click("#es-pridat")
        zkontroluj(strana(page, 3).locator(".es-popis-strany").inner_text() == "prázdná strana"
                   and strana(page, 3).locator(".es-nahrat-obrazek").count() == 1,
                   "„Přidat stranu“ přidá prázdnou stranu s možností nahrát obrázek")
        page.fill("#es-prvni", "3")
        cisla = page.eval_on_selector_all("#es-seznam .es-cislo", "els => els.map(e => e.textContent)")
        zkontroluj(cisla == ["3", "4", "5", "6"], "čísla stran se řídí číslem první strany", cisla)
        zkontroluj(typy(page) == ["pisen", "pokracovani", "bez", "prazdna"],
                   "typy pro server se odvodí z obsahu stran", typy(page))
        page.fill("#nb-name", "Zpěvník z testu")

        print("\n── kontrola před uložením ──")
        page.click("#es-pridat")
        strana(page, 4).locator(".es-dalsi-pisen").click()
        page.click("#nb-save-top")
        page.wait_for_function("document.querySelector('#es-seznam .es-strana.ma-chybu')")
        chyba = strana(page, 4).locator(".es-chyby").inner_text()
        zkontroluj("obrázek" in chyba and "název" in chyba,
                   "neúplná strana se vyznačí a neuloží", chyba)
        page.wait_for_selector(".oznameni-zaclona .okno")
        text = page.inner_text(".oznameni-zaclona .okno")
        zkontroluj("není úplná" in text and not dialogy,
                   "a řekne se proč - ve vlastním okně, ne v alertu prohlížeče", text.replace("\n", " | "))
        vzhled = page.evaluate("""() => { const o = document.querySelector('.oznameni-zaclona .okno');
          return {varovani: o.classList.contains('okno-varovani'), znak: !!o.querySelector('.okno-znak'),
                  pruh: getComputedStyle(o).boxShadow.includes('inset')}; }""")
        zkontroluj(all(vzhled.values()), "oznámení vypadá jako varování (znak a pruh)", vzhled)
        page.keyboard.press("Enter")
        zkontroluj(page.locator(".oznameni-zaclona").count() == 0, "oznámení zavře Enter")
        strana(page, 4).locator(".es-odebrat").click()

        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")
        with A.app.app_context():
            novy = A.Songbook.query.filter_by(title="Zpěvník z testu").first()
        zkontroluj(novy is not None, "zpěvník se založí")
        s = strany_db(novy.id)
        zkontroluj(s == [(3, True, None, [("Píseň A1", 1)]), (4, True, None, [("Píseň A1", 2)]),
                         (5, True, "Úvod", []), (6, False, None, [])],
                   "strany jsou uložené, jak se zadaly", s)

        print("\n── úpravy ──")
        page.evaluate(f"window.openEditSongbookModal('{novy.id}')")
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
        zkontroluj("bez-zmen" in page.get_attribute("#nb-save-top", "class"),
                   "Uložit je tlumené, dokud není co uložit")
        page.fill("#nb-name", "Zpěvník z testu ")
        page.fill("#nb-name", "Zpěvník z testu")
        page.wait_for_timeout(50)
        zkontroluj("bez-zmen" not in page.get_attribute("#nb-save-top", "class"),
                   "a rozsvítí se se změnou")
        zkontroluj(typy(page) == ["pisen", "pokracovani", "bez", "prazdna"]
                   and page.locator("#es-seznam .es-blok").count() == 3,
                   "editor načte strany i spojení", typy(page))
        zkontroluj(page.input_value("#es-prvni") == "3", "i číslo první strany")
        strana(page, 0).locator(".es-nazev").focus()
        page.keyboard.press("Enter")
        aktivni = page.evaluate("document.activeElement.className")
        zkontroluj(aktivni == "es-popisek", "Enter přeskočí pokračování na další pole", aktivni)
        page.locator("#es-seznam .es-blok").nth(0).locator(".es-dolu").click()
        zkontroluj(typy(page) == ["bez", "pisen", "pokracovani", "prazdna"],
                   "šipka přesune píseň i s pokračováním", typy(page))
        page.locator("#es-seznam .es-blok").nth(2).locator(".es-drzadlo").drag_to(
            page.locator("#es-seznam .es-blok").nth(0), target_position={"x": 40, "y": 5})
        zkontroluj(typy(page) == ["prazdna", "bez", "pisen", "pokracovani"],
                   "tažení přesune blok", typy(page))
        # Puštěný na své místo: dřív editor nepřekreslil a blok zůstal zesvětlený
        page.locator("#es-seznam .es-blok").nth(1).locator(".es-drzadlo").drag_to(
            page.locator("#es-seznam .es-blok").nth(1), target_position={"x": 40, "y": 5})
        zbytky = page.locator("#es-seznam .tazeny, #es-seznam .taha, #es-seznam .cil-pred, #es-seznam .cil-za").count()
        zkontroluj(typy(page) == ["prazdna", "bez", "pisen", "pokracovani"] and zbytky == 0,
                   "blok puštěný na své místo zůstane a nic nezůstane zesvětlené", f"{typy(page)}, zbytků {zbytky}")
        # Na straně s pokračováním může začínat další píseň
        strana(page, 3).locator(".es-dalsi-pisen").click()
        strana(page, 3).locator(".es-nazev").fill("Krátká B")
        with page.expect_file_chooser() as vyber:
            strana(page, 0).locator(".es-nahrat-obrazek").click()
        vyber.value.set_files(str(soubory / "2.png"))
        page.wait_for_function("document.getElementById('es-stav-nahravani').hidden", timeout=20000)
        zkontroluj(typy(page)[0] == "bez", "prázdná strana s nahraným obrázkem je strana bez písně",
                   typy(page))
        # Odebrání obrázku ve zvětšeném náhledu: ze strany bez písně je prázdná strana
        strana(page, 1).locator(".es-nahled").click()
        page.wait_for_selector("#es-lupa:not([hidden])")
        page.click("#es-lupa .es-odebrat-obrazek")
        zkontroluj(page.is_hidden("#es-lupa") and typy(page)[1] == "prazdna"
                   and strana(page, 1).locator(".es-nahrat-obrazek").count() == 1,
                   "odebráním obrázku vznikne prázdná strana", typy(page))
        zkontroluj(strana(page, 1).locator(".es-popisek").input_value() == "Úvod",
                   "a popisek jí zůstane")
        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")
        s = strany_db(novy.id)
        zkontroluj(s[1][1] is False and s[1][2] == "Úvod",
                   "uloží se strana bez obrázku i s popiskem", s[1])
        zkontroluj([x[3] for x in s] == [[], [], [("Píseň A1", 1)], [("Píseň A1", 2), ("Krátká B", 1)]]
                   and s[0][1],
                   "uloží se přesuny, obrázek i píseň začínající na pokračování",
                   [(x[1], x[3]) for x in s])
        # Jen výměna obrázku musí rozsvítit Uložit. Výběr souboru je mimo okno a dřív
        # se o změně tlačítko nedozvědělo.
        page.evaluate(f"window.openEditSongbookModal('{novy.id}')")
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
        page.wait_for_timeout(100)
        pred = "bez-zmen" in page.get_attribute("#nb-save-top", "class")
        strana(page, 0).locator(".es-nahled").click()
        page.wait_for_selector("#es-lupa:not([hidden])")
        with page.expect_file_chooser() as vyber:
            page.click("#es-lupa .es-vymenit")
        vyber.value.set_files(str(soubory / "10.png"))
        page.wait_for_function("document.getElementById('es-stav-nahravani').hidden", timeout=20000)
        page.wait_for_timeout(50)
        zkontroluj(pred and "bez-zmen" not in page.get_attribute("#nb-save-top", "class"),
                   "výměna obrázku strany rozsvítí Uložit")
        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")

        print("\n── přidat píseň → nový zpěvník ──")
        with A.app.app_context():
            radek = A.db.session.execute(A.text(
                "SELECT p.song_id, COUNT(*) n, MIN(s.songbook_id) FROM pisne_na_strane p "
                "JOIN strany s ON s.id = p.strana_id JOIN songbooks b ON b.id = s.songbook_id "
                "WHERE b.is_public = 1 GROUP BY p.song_id, s.songbook_id HAVING n > 1 LIMIT 1")).first()
            pisen_id, stran, _ = radek
            nazev_pisne = A.db.session.get(A.Song, pisen_id).title
            obrazky_pisne = [st.image_id for st in A.strany_pisne(pisen_id)]
        page.goto(base + f"/my-songbooks?create=1&pisen={pisen_id}")
        page.wait_for_function(f"document.querySelectorAll('#es-seznam .es-strana').length === {stran}")
        zkontroluj(typy(page) == ["pisen"] + ["pokracovani"] * (stran - 1),
                   "nový zpěvník se otevře rovnou s písní i jejími pokračováními", typy(page))
        zkontroluj(strana(page, 0).locator(".es-nazev").input_value() == nazev_pisne
                   and strana(page, 0).locator(".es-nazev").get_attribute("readonly") is not None,
                   "píseň je zamčená jako každá cizí", nazev_pisne)
        zkontroluj("bez-zmen" not in page.get_attribute("#nb-save-top", "class"),
                   "a Uložit rovnou svítí")
        page.fill("#nb-name", "S písní z hledání")
        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")
        zkontroluj(page.evaluate("location.search") == ""
                   and page.evaluate("getComputedStyle(document.getElementById('new-book-modal')).display") == "none",
                   "po uložení se okno samo znovu neotevře")
        with A.app.app_context():
            sb = A.Songbook.query.filter_by(title="S písní z hledání").first()
            zkontroluj(sb is not None and [st.image_id for st in sb.strany] == obrazky_pisne
                       and all(v.song_id == pisen_id for st in sb.strany for v in st.pisne),
                       "uloží se s týmiž stranami a toutéž písní",
                       sb and [st.image_id for st in sb.strany])

        print("\n── vyplňování z klávesnice ──")
        page.evaluate("window.__editMode = false; window.__editBookId = null")
        page.click(".add-card")
        page.set_input_files("#es-soubory", [str(soubory / j) for j in ("1.png", "2.png", "10.png")])
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 3")
        page.keyboard.type("Dlouhá")
        page.keyboard.press("Alt+ArrowDown")
        aktivni = page.evaluate("[document.activeElement.className, "
                                "[...document.querySelectorAll('#es-seznam .es-strana')].indexOf("
                                "document.activeElement.closest('.es-strana'))]")
        zkontroluj(typy(page)[:2] == ["pisen", "pokracovani"] and aktivni == ["es-nazev", 2],
                   "Alt+↓ připojí další stranu a skočí za blok", (typy(page), aktivni))
        page.keyboard.type("Další")
        zkontroluj(page.evaluate("EditorStran.data().map(s => s.pisne.map(p => p.nazev))")
                   == [["Dlouhá"], [], ["Další"]], "názvy se dají psát bez myši")
        page.click("#nb-cancel") if page.is_visible("#nb-cancel") else None
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")

        print("\n── import PDF a ZIP ──")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        page.click(".add-card")
        page.set_input_files("#es-soubory", str(soubory / "zpevnik.pdf"))
        page.wait_for_selector("#es-import:not([hidden])", timeout=20000)
        nadpis = page.inner_text("#es-import-nadpis")
        zkontroluj("6 stran" in nadpis, "PDF se otevře a řekne, kolik má stran", nadpis)
        page.fill("#es-import-strany", "7")
        page.click("#es-import-ok")
        chyba = page.inner_text("#es-import-chyba")
        zkontroluj(page.is_visible("#es-import") and "6 stran" in chyba,
                   "rozsah mimo soubor se odmítne a řekne proč", chyba)
        page.fill("#es-import-strany", "2-5")
        zkontroluj(page.is_checked('#es-import input[value="vyber"]'),
                   "psaní do rozsahu přepne na „jen strany“")
        page.select_option("#es-import-obalka", "2")
        page.click("#es-import-ok")
        page.wait_for_function("document.getElementById('es-stav-importu').hidden && "
                               "document.getElementById('es-stav-nahravani').hidden "
                               "&& document.querySelectorAll('#es-seznam .es-strana').length === 2",
                               timeout=60000)
        odstiny = page.evaluate(ODSTINY)
        zkontroluj(len(odstiny) == 2 and abs(odstiny[0] - 90) < 12 and abs(odstiny[1] - 120) < 12,
                   "strany 3 a 4 přibyly jako strany, 2 a 5 šly na obálku", odstiny)
        obalky = page.evaluate("""() => ['front-outer', 'back-outer', 'front-inner'].map(r =>
          (document.getElementById('file-' + r).files[0] || {}).name || null)""")
        zkontroluj(obalky == ["zpevnik-2.jpg", "zpevnik-5.jpg", None],
                   "první a poslední vybraná strana je obálka zvenku", obalky)
        sirka = page.evaluate("document.querySelector('#es-seznam .es-nahled img').naturalWidth")
        zkontroluj(sirka == 1748, "strana PDF se vykreslí na šířku skenů", sirka)

        page.set_input_files("#es-soubory", str(soubory / "skeny.zip"))
        page.wait_for_selector("#es-import:not([hidden])", timeout=20000)
        nadpis = page.inner_text("#es-import-nadpis")
        zkontroluj("3 strany" in nadpis, "ZIP vidí jen obrázky, ne balast z Macu ani text", nadpis)
        page.click("#es-import-ok")
        page.wait_for_function("document.getElementById('es-stav-importu').hidden && "
                               "document.getElementById('es-stav-nahravani').hidden "
                               "&& document.querySelectorAll('#es-seznam .es-strana').length === 5",
                               timeout=60000)
        odstiny = page.evaluate(ODSTINY)[2:]
        zkontroluj(len(odstiny) == 3 and all(abs(a - b) < 12 for a, b in zip(odstiny, (20, 60, 100))),
                   "obrázky ze ZIPu jdou podle jména (1, 2, 10)", odstiny)
        radky = page.locator("#es-seznam .es-strana")
        for i in range(5):
            radky.nth(i).locator(".es-nazev").fill(f"Importovaná {i + 1}")
        page.fill("#nb-name", "Import z testu")
        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")
        with A.app.app_context():
            imp = A.Songbook.query.filter_by(title="Import z testu").first()
            zkontroluj(imp is not None and len(imp.strany) == 5
                       and imp.img_path_cover_front_outer and imp.img_path_cover_back_outer
                       and not imp.img_path_cover_front_inner,
                       "importovaný zpěvník se uloží i s obálkami",
                       imp and (len(imp.strany), imp.img_path_cover_front_outer))

        print("\n── sdílení a kopie ──")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        with A.app.app_context():
            vlastni = A.Songbook.query.filter_by(title="Import z testu").first().id
        akce(page, "btn-share", vlastni)
        zkontroluj(page.is_visible("#share-druh-spolecny")
                   and page.evaluate("document.querySelectorAll('input[name=\"share-druh\"]:checked').length") == 0
                   and page.is_disabled("#share-submit-btn"),
                   "vlastník vidí obě volby, žádná není předvybraná a tlačítko čeká")
        page.fill("#share-email-input", EMAIL4)
        page.click("#share-druh-spolecny")
        zkontroluj(page.inner_text("#share-submit-btn") == "Pozvat ke společným úpravám"
                   and page.is_enabled("#share-submit-btn"),
                   "tlačítko řekne, co se stane", page.inner_text("#share-submit-btn"))
        page.click("#share-submit-btn")
        page.wait_for_function("document.getElementById('share-feedback').textContent.length > 0")
        zprava = page.inner_text("#share-feedback")
        zkontroluj("byla odeslána" in zprava, "sdílení pošle pozvánku", zprava)
        page.click("#share-cancel-btn")

        druhy = prohlizec.new_page(viewport={"width": 1280, "height": 900})
        druhy.on("pageerror", lambda e: chyby.append("druhý účet: " + str(e)))
        druhy.goto(base + "/login")
        druhy.fill('input[name="email"]', EMAIL4)
        druhy.fill('input[name="password"]', HESLO)
        druhy.click('button[type="submit"], input[type="submit"]')
        druhy.wait_for_load_state("networkidle")
        druhy.goto(base + "/my-songbooks")
        druhy.wait_for_load_state("networkidle")
        text = druhy.inner_text(".pozvanky") if druhy.is_visible(".pozvanky") else ""
        zkontroluj("zve ke společným úpravám" in text and "Import z testu" in text,
                   "příjemce vidí pozvánku nahoře v Mých zpěvnících", text[:120])
        with druhy.expect_navigation():
            druhy.click(".pozvanka-prijmout")
        druhy.wait_for_load_state("networkidle")
        zkontroluj(druhy.locator(f'.songbook-card .btn-edit[data-book-id="{vlastni}"]').count() == 1
                   and not druhy.is_visible(".pozvanky"),
                   "po přijetí má zpěvník mezi svými a pozvánka zmizí")
        akce(druhy, "btn-share", vlastni)
        zkontroluj(druhy.is_visible("#share-druh-spolecny")
                   and druhy.is_disabled('#share-druh-spolecny input')
                   and JMENO in druhy.inner_text("#share-proc-spolecny")
                   and druhy.is_checked('input[name="share-druh"][value="kopie"]'),
                   "člen vidí společné úpravy zašedlé i s důvodem, kopie je vybraná",
                   druhy.inner_text("#share-proc-spolecny"))
        druhy.click("#share-cancel-btn")
        druhy.on("dialog", lambda d: d.accept())
        pocet = druhy.locator(".songbook-card .btn-edit").count()
        zkontroluj(druhy.locator(".songbook-card").first.locator(".akce-polozka").count() == 6,
                   "v nabídce dlaždice je šest položek")
        napoveda = (druhy.get_attribute(f'.btn-delete[data-book-id="{vlastni}"]', "data-clen"),
                    (druhy.text_content(f'.btn-delete[data-book-id="{vlastni}"]') or "").strip())
        zkontroluj(napoveda == ("1", "Odebrat"),
                   "u cizího společného zpěvníku položka říká Odebrat, ne Smazat", napoveda)
        akce(druhy, "btn-delete", vlastni)
        druhy.wait_for_selector("#app-confirm-overlay[aria-hidden='false']")
        tlacitka = druhy.evaluate("[...document.querySelectorAll('#app-confirm-overlay button')]"
                                  ".filter(b => b.offsetParent).map(b => b.textContent.trim())")
        zkontroluj(tlacitka == ["Odebrat a ponechat vlastní kopii", "Odebrat", "Zrušit"]
                   and "zůstane beze změny" in druhy.inner_text("#app-confirm-message"),
                   "koš u cizího zpěvníku nabídne Odebrat a ponechat vlastní kopii / Odebrat / Zrušit", tlacitka)
        rozlozeni = druhy.evaluate("""() => [...document.querySelectorAll('#app-confirm-overlay button')]
          .filter(b => b.offsetParent).map(b => { const r = b.getBoundingClientRect(), c = getComputedStyle(b);
            return {x: Math.round(r.left), w: Math.round(r.width), y: Math.round(r.top), pozadi: c.backgroundColor, barva: c.color}; })""")
        zkontroluj(len({(t["x"], t["w"]) for t in rozlozeni}) == 1
                   and [t["y"] for t in rozlozeni] == sorted(t["y"] for t in rozlozeni),
                   "tlačítka jsou pod sebou a stejně široká", rozlozeni)
        zkontroluj(rozlozeni[1]["pozadi"] == "rgb(179, 38, 30)" and rozlozeni[0]["barva"] == "rgb(179, 38, 30)"
                   and rozlozeni[0]["pozadi"] in ("rgba(0, 0, 0, 0)", "transparent"),
                   "Odebrat je plně červené, Odebrat a ponechat kopii jen červeně orámované",
                   [(t["pozadi"], t["barva"]) for t in rozlozeni])
        druhy.click("#app-confirm-cancel")
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        pruhy = page.evaluate("""() => [...document.querySelectorAll('.misto-zpevniku')].map(m => ({
          celkem: Math.round(m.querySelector('.celkem').getBoundingClientRect().width),
          tento: Math.round(m.querySelector('.tento').getBoundingClientRect().width)}))""")
        zkontroluj(len(pruhy) >= 2 and len({p["celkem"] for p in pruhy}) == 1
                   and all(p["tento"] <= p["celkem"] for p in pruhy),
                   "pruh místa: celkem u všech dlaždic stejně, podíl zpěvníku nikdy víc", pruhy)
        poradi = "() => [...document.querySelectorAll('.songbook-card[data-radit]')].map(k => k.dataset.bookId)"
        pred = page.evaluate(poradi)
        drzadel = page.locator(".songbook-card .dlazdice-drzadlo").count()
        zkontroluj(len(pred) >= 2 and drzadel == len(pred),
                   "od dvou zpěvníků má každá dlaždice držadlo přesunu", f"{drzadel} z {len(pred)}")
        if len(pred) >= 2:
            page.drag_and_drop(f'.songbook-card[data-book-id="{pred[-1]}"] .dlazdice-drzadlo',
                               f'.songbook-card[data-book-id="{pred[0]}"]', target_position={"x": 5, "y": 60})
            page.wait_for_timeout(400)
            page.reload()
            page.wait_for_load_state("networkidle")
            zkontroluj(page.evaluate(poradi) == [pred[-1]] + pred[:-1],
                       "přetažená dlaždice zůstane i po obnovení stránky na novém místě",
                       f"{pred} → {page.evaluate(poradi)}")
        druhy.wait_for_timeout(1500)
        nacita = druhy.locator(".cover-preview.nacita").count()
        zkontroluj(nacita == 0, "po načtení žádná obálka nezůstane s kolečkem", nacita)
        akce(druhy, "btn-kopie", vlastni)
        druhy.wait_for_selector("#app-confirm-overlay[aria-hidden='false']")
        with druhy.expect_navigation():
            druhy.click("#app-confirm-ok")
        druhy.wait_for_load_state("networkidle")
        zkontroluj(druhy.locator(".songbook-card .btn-edit").count() == pocet + 1
                   and "Import z testu (kopie)" in druhy.inner_text(".grid-container"),
                   "Duplikovat přidá samostatný zpěvník")
        # Vlastník v okně sdílení ukončí společné úpravy - člen dostane kopii a upozornění
        page.goto(base + "/my-songbooks")
        page.wait_for_load_state("networkidle")
        akce(page, "btn-share", vlastni)
        page.wait_for_selector("#share-clenove:not([hidden])")
        zkontroluj(JMENO4 in page.inner_text("#share-clenove"), "okno sdílení ukáže členy",
                   page.inner_text("#share-clenove"))
        page.click("#share-clenove .share-clen button")
        page.wait_for_selector("#app-confirm-overlay[aria-hidden='false']")
        page.click("#app-confirm-ok")
        page.wait_for_function("!document.getElementById('share-clenove').textContent.includes('Ukončit')")
        page.click("#share-cancel-btn")
        druhy.goto(base + "/my-songbooks")
        druhy.wait_for_load_state("networkidle")
        text = druhy.inner_text("[data-oznameni]") if druhy.locator("[data-oznameni]").count() else ""
        zkontroluj("ukončil společné úpravy" in text, "člen uvidí upozornění s nabídkou kopie", text[:100])
        with druhy.expect_navigation():
            druhy.click(".oznameni-ponechat")
        zkontroluj(druhy.locator("[data-oznameni]").count() == 0, "Ponechat upozornění zavře")
        druhy.close()

        print("\n── neuložené změny ──")
        page.evaluate(f"window.openEditSongbookModal('{novy.id}')")
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
        page.locator("#es-seznam .es-strana").nth(0).locator(".es-odebrat").click()
        page.click("#new-book-modal", position={"x": 5, "y": 300})
        zkontroluj(page.is_visible("#app-confirm-overlay"),
                   "zavření s neuloženou změnou se zeptá")
        page.click("#app-confirm-ok")

        print("\n── dotyk: tah prstem za držadlo ──")
        dctx = prohlizec.new_context(viewport={"width": 390, "height": 700}, has_touch=True, is_mobile=True)
        dp = dctx.new_page()
        dp.on("pageerror", lambda e: chyby.append(str(e)))
        dp.goto(base + "/login")
        dp.fill('input[name="email"]', EMAIL)
        dp.fill('input[name="password"]', HESLO)
        dp.click('button[type="submit"], input[type="submit"]')
        dp.wait_for_load_state("networkidle")
        dp.goto(base + "/my-songbooks")
        dp.wait_for_load_state("networkidle")
        dp.evaluate(f"window.openEditSongbookModal('{novy.id}')")
        dp.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
        for _ in range(26):
            dp.evaluate("document.getElementById('es-pridat').click()")
        bloky = dp.locator("#es-seznam .es-blok")
        pocet = bloky.count()
        bloky.nth(pocet - 1).locator(".es-popisek").fill("POSLEDNÍ")
        vzhled = dp.evaluate("""() => {
          const b = document.querySelectorAll('#es-seznam .es-blok');
          const d = b[b.length - 1].querySelector('.es-drzadlo').getBoundingClientRect();
          const s = b[b.length - 1].querySelector('.es-nahoru');
          return {drzadlo: [Math.round(d.width), Math.round(d.height)], sipky: !!(s && s.offsetParent)};
        }""")
        zkontroluj(not vzhled["sipky"] and min(vzhled["drzadlo"]) >= 30,
                   "na dotyku je držadlo pro prst (aspoň 30 px) a šipky ↑↓ schované", vzhled)
        dp.locator("#es-seznam .es-blok").last.scroll_into_view_if_needed()
        d = dp.locator("#es-seznam .es-blok").last.locator(".es-drzadlo").bounding_box()
        rol = dp.evaluate_handle("""() => { let p = document.getElementById('es-seznam').parentElement;
          while (p && !(['auto', 'scroll'].includes(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight)) p = p.parentElement;
          return p || document.scrollingElement; }""")
        horni = dp.evaluate("r => r === document.scrollingElement ? 0 : r.getBoundingClientRect().top", rol)
        pred_rol = dp.evaluate("r => r.scrollTop", rol)
        cdp = dctx.new_cdp_session(dp)
        def prst(typ, x=0, y=0):
            cdp.send("Input.dispatchTouchEvent",
                     {"type": typ, "touchPoints": [] if typ == "touchEnd" else [{"x": x, "y": y}]})
        x, y = d["x"] + d["width"] / 2, d["y"] + d["height"] / 2
        prst("touchStart", x, y)
        for krok in range(1, 11):
            prst("touchMove", x, y + (horni + 12 - y) * krok / 10)
            dp.wait_for_timeout(30)
        dp.wait_for_timeout(300)
        bezi = dp.evaluate("r => r.scrollTop", rol)
        dp.wait_for_function("r => r.scrollTop === 0", arg=rol, timeout=8000)
        prst("touchEnd")
        dp.wait_for_timeout(300)
        prvni = dp.locator("#es-seznam .es-blok").first.locator(".es-popisek").input_value()
        zbytky = dp.locator("#es-seznam .tazeny, #es-seznam .taha").count()
        zkontroluj(bezi < pred_rol, "u horního okraje se seznam sám roluje", f"{pred_rol} → {bezi}")
        zkontroluj(prvni == "POSLEDNÍ" and dp.locator("#es-seznam .es-blok").count() == pocet and zbytky == 0,
                   "prstem jde poslední strana jedním tahem přesunout na začátek", f"první: {prvni!r}")
        dp.click("#new-book-modal", position={"x": 5, "y": 300})
        if dp.is_visible("#app-confirm-overlay"):
            dp.click("#app-confirm-ok")
        # Dlaždice: tah prstem za držadlo vlevo nahoře, poslední na první místo
        dp.goto(base + "/my-songbooks")
        dp.wait_for_load_state("networkidle")
        pred = dp.evaluate(poradi)
        drz = dp.locator(f'.songbook-card[data-book-id="{pred[-1]}"] .dlazdice-drzadlo')
        drz.scroll_into_view_if_needed()
        d = drz.bounding_box()
        zkontroluj(min(d["width"], d["height"]) >= 36, "držadlo dlaždice je na prst dost velké",
                   f"{d['width']:.0f}×{d['height']:.0f}")
        x, y = d["x"] + d["width"] / 2, d["y"] + d["height"] / 2
        prst("touchStart", x, y)
        # Nahoru k okraji a podržet, až stránka dojede nahoru; pak na levou půlku první
        for krok in range(1, 11):
            prst("touchMove", x, y + (10 - y) * krok / 10)
            dp.wait_for_timeout(30)
        dp.wait_for_function("() => window.scrollY === 0", timeout=8000)
        prvni = dp.locator(f'.songbook-card[data-book-id="{pred[0]}"]').bounding_box()
        prst("touchMove", prvni["x"] + 10, prvni["y"] + prvni["height"] / 2)
        dp.wait_for_timeout(400)
        prst("touchEnd")
        dp.wait_for_timeout(500)
        dp.reload()
        dp.wait_for_load_state("networkidle")
        po = dp.evaluate(poradi)
        zkontroluj(po == [pred[-1]] + pred[:-1],
                   "prstem jde poslední dlaždice přetáhnout na začátek a pořadí se uloží",
                   f"{pred} → {po}")
        dctx.close()

        print("\n── telefon ──")
        for sirka in (320, 390):
            page.set_viewport_size({"width": sirka, "height": 800})
            page.evaluate(f"window.openEditSongbookModal('{novy.id}')")
            page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
            m = page.evaluate(PRETEKA)
            zkontroluj(m["stranka"] <= m["sirka"] and not m["venku"],
                       f"{sirka} px: editor se vejde do okna", m)
            page.set_input_files("#es-soubory", str(soubory / "zpevnik.pdf"))
            page.wait_for_selector("#es-import:not([hidden])", timeout=20000)
            r = page.evaluate("""() => { const r = document.querySelector('.es-import-okno').getBoundingClientRect();
              const b = document.getElementById('es-import-ok').getBoundingClientRect();
              const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
              return {vlevo: Math.round(r.left), vpravo: Math.round(r.right), tlacitko: t && t.id}; }""")
            zkontroluj(r["vlevo"] >= 0 and r["vpravo"] <= sirka and r["tlacitko"] == "es-import-ok",
                       f"{sirka} px: dialog importu se vejde a „Přidat“ jde trefit", r)
            page.click("#es-import-zrusit")
            page.goto(base + "/my-songbooks")
            page.wait_for_load_state("networkidle")
            zkontroluj(page.evaluate("document.documentElement.scrollWidth") <= sirka,
                       f"{sirka} px: dlaždice s nabídkou Možnosti stránku nerozšíří")
            page.wait_for_load_state("networkidle")

        zkontroluj(not chyby, "žádná chyba JavaScriptu", chyby[:3])
        prohlizec.close()
    server.shutdown()

    print()
    if selhani:
        print(f"❌ selhalo {len(selhani)}: {', '.join(selhani)}")
        return 1
    print("✅ všechny kontroly prošly")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
