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
            page.click("#nb-cancel") if page.is_visible("#nb-cancel") else page.keyboard.press("Escape")
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
