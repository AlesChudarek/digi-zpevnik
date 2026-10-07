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
HESLO = "editor-ui"
with A.app.app_context():
    uzivatel = A.db.session.get(A.User, 3)
    uzivatel.password = generate_password_hash(HESLO, method="pbkdf2:sha256:1000")
    A.db.session.commit()
    EMAIL = uzivatel.email

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


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
    return page.eval_on_selector_all(
        "#es-seznam .es-strana",
        "els => els.map(e => [...e.classList].find(c => c.startsWith('es-typ-')).slice(7))")


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
        zkontroluj(page.is_visible("#editor-stran") and page.is_visible("#es-prazdno"),
                   "okno nového zpěvníku má rovnou sekci stran")
        page.set_input_files("#es-soubory", [str(soubory / j) for j in ("10.png", "2.png", "1.png")])
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 3")
        page.wait_for_function("document.getElementById('es-stav-nahravani').hidden", timeout=20000)
        sirky = page.eval_on_selector_all(
            "#es-seznam .es-nahled img", "els => els.map(i => i.naturalWidth)")
        zkontroluj(sirky == [100, 200, 300], "soubory se seřadí podle jména (1, 2, 10)", sirky)
        zkontroluj(not page.is_visible("#es-prazdno"), "hláška o prázdném zpěvníku zmizí")
        zkontroluj(typy(page) == ["pisen"] * 3, "nová strana je píseň", typy(page))
        aktivni = page.evaluate("document.activeElement.className")
        zkontroluj(aktivni == "es-nazev", "kurzor skočí do názvu první nové strany", aktivni)

        radky = page.locator("#es-seznam .es-strana")
        radky.nth(0).locator(".es-nazev").fill("Píseň A")
        radky.nth(0).locator(".es-autor").fill("Autor A")
        radky.nth(1).locator('.es-typ[data-typ="pokracovani"]').click()
        navaz = page.locator("#es-seznam .es-strana").nth(1).locator(".es-navazuje").inner_text()
        zkontroluj(navaz == "Pokračuje „Píseň A“", "pokračování ukáže, na co navazuje", navaz)
        radky.nth(0).locator(".es-nazev").fill("Píseň A1")
        navaz = page.locator("#es-seznam .es-strana").nth(1).locator(".es-navazuje").inner_text()
        zkontroluj(navaz == "Pokračuje „Píseň A1“", "a mění se s psaním názvu", navaz)
        radky.nth(2).locator('.es-typ[data-typ="bez"]').click()
        page.locator("#es-seznam .es-strana").nth(2).locator(".es-popisek").fill("Úvod")
        page.click("#es-pridat-prazdnou")
        page.fill("#es-prvni", "3")
        cisla = page.eval_on_selector_all("#es-seznam .es-cislo", "els => els.map(e => e.textContent)")
        zkontroluj(cisla == ["3", "4", "5", "6"], "čísla stran se řídí číslem první strany", cisla)
        page.fill("#nb-name", "Zpěvník z testu")

        print("\n── kontrola před uložením ──")
        page.click("#es-pridat-prazdnou")
        posledni = page.locator("#es-seznam .es-strana").nth(4)
        posledni.locator('.es-typ[data-typ="pisen"]').click()
        page.click("#nb-save-top")
        page.wait_for_function("document.querySelector('#es-seznam .es-strana.ma-chybu')")
        chyba = page.locator("#es-seznam .es-strana").nth(4).locator(".es-chyby").inner_text()
        zkontroluj("obrázek" in chyba and "název" in chyba,
                   "neúplná strana se vyznačí a neuloží", chyba)
        zkontroluj(dialogy and "doplnit" in dialogy[-1], "a řekne se proč", dialogy[-1:] or "")
        page.locator("#es-seznam .es-strana").nth(4).locator(".es-odebrat").click()

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
        zkontroluj(typy(page) == ["pisen", "pokracovani", "bez", "prazdna"],
                   "editor načte typy stran", typy(page))
        zkontroluj(page.input_value("#es-prvni") == "3", "i číslo první strany")
        page.locator("#es-seznam .es-strana").nth(0).locator(".es-nazev").focus()
        page.keyboard.press("Enter")
        aktivni = page.evaluate("document.activeElement.className")
        zkontroluj(aktivni == "es-popisek", "Enter přeskočí pokračování na další pole", aktivni)
        # Šipkou dolů se píseň posune i se svým pokračováním za úvod
        page.locator("#es-seznam .es-strana").nth(0).locator(".es-dolu").click()
        zkontroluj(typy(page) == ["bez", "pisen", "pokracovani", "prazdna"],
                   "šipka přesune píseň i s pokračováním", typy(page))
        # Tažením prázdnou stranu na začátek
        page.locator("#es-seznam .es-strana").nth(3).locator(".es-drzadlo").drag_to(
            page.locator("#es-seznam .es-strana").nth(0), target_position={"x": 20, "y": 5})
        zkontroluj(typy(page) == ["prazdna", "bez", "pisen", "pokracovani"],
                   "tažení přesune stranu", typy(page))
        with page.expect_navigation():
            page.click("#nb-save-top")
        page.wait_for_load_state("networkidle")
        s = strany_db(novy.id)
        zkontroluj([x[3] for x in s] == [[], [], [("Píseň A1", 1)], [("Píseň A1", 2)]],
                   "přesuny se uloží", [x[3] for x in s])

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

        print("\n── neuložené změny ──")
        page.evaluate(f"window.openEditSongbookModal('{novy.id}')")
        page.wait_for_function("document.querySelectorAll('#es-seznam .es-strana').length === 4")
        page.locator("#es-seznam .es-strana").nth(0).locator(".es-odebrat").click()
        page.click("#new-book-modal", position={"x": 5, "y": 300})
        zkontroluj(page.is_visible("#app-confirm-overlay"),
                   "zavření s neuloženou změnou se zeptá")
        page.click("#app-confirm-ok")

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
