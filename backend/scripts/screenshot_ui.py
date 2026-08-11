"""Vyfotí stránky aplikace v několika šířkách okna a změří přetékání layoutu.

Bez tohohle se responzivní chyby hádají z popisu, což nefunguje - třeba "prázdný
sloupec vpravo" byly tooltipy u tlačítek v řádcích, které přečuhovaly 66 px za
tabulku, a to se z pohledu na kód nepozná.

Vyžaduje jednorázově:
    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium

Použití:
    python backend/scripts/screenshot_ui.py                    # vše
    python backend/scripts/screenshot_ui.py search reader      # jen vybrané
    python backend/scripts/screenshot_ui.py --widths 390,820   # jen vybrané šířky

Screenshoty se ukládají do ui-shots/ (gitignorováno). Běží proti KOPII databáze,
takže na skutečná data nesahá.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
OUT_DIR = PROJECT_ROOT / "ui-shots"
PORT = 5599
PASSWORD = "screenshot-only"

PAGES = {
    "search": "/search",
    "reader": "/songbook/00006",
    "public": "/public-songbooks",
    "mine": "/my-songbooks",
}
DEFAULT_WIDTHS = [1280, 900, 820, 725, 600, 480, 390]


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "screenshot"
    env["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'backend'}"

    subprocess.run(
        [str(VENV_PY), "-c",
         'import os, sys\n'
         'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
         'from backend.app import app, db, User\n'
         'from werkzeug.security import generate_password_hash\n'
         'with app.app_context():\n'
         '    u = User.query.filter_by(email="admin@test.com").first()\n'
         f'    u.password = generate_password_hash("{PASSWORD}", method="pbkdf2:sha256")\n'
         '    db.session.commit()\n'],
        env=env, check=True, capture_output=True)

    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "flask", "--app", "backend.app", "run",
         "--port", str(PORT), "--no-reload"],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    base = f"http://127.0.0.1:{PORT}"
    for _ in range(160):
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            return proc, base
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("❌ server se nerozjel")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="*", default=list(PAGES),
                        help=f"které stránky ({', '.join(PAGES)})")
    parser.add_argument("--widths", default=",".join(map(str, DEFAULT_WIDTHS)))
    args = parser.parse_args()

    targets = args.pages or list(PAGES)
    unknown = [t for t in targets if t not in PAGES]
    if unknown:
        raise SystemExit(f"❌ neznámé stránky: {unknown}. Známé: {list(PAGES)}")
    widths = [int(w) for w in args.widths.split(",")]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright. Nainstaluj:\n"
                         "   .venv/bin/pip install playwright\n"
                         "   .venv/bin/playwright install chromium")

    OUT_DIR.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ui-shots-"))
    db_copy = tmp / "shots.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)

    server, base = start_server(db_copy)
    problems = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            for name in targets:
                for width in widths:
                    page.set_viewport_size({"width": width, "height": 820})
                    page.goto(base + PAGES[name])
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(350)
                    page.screenshot(path=str(OUT_DIR / f"{name}_{width}.png"))
                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth - window.innerWidth")
                    flag = ""
                    if overflow > 0:
                        # Almost always a decorative absolute element (tooltip, badge)
                        # sticking out, which lets the whole page be dragged sideways.
                        flag = f"  ⚠️ stránka přetéká o {overflow}px"
                        problems += 1
                    print(f"  {name:8s} {width:>5}px{flag}")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nScreenshoty: {OUT_DIR}")
    if problems:
        print(f"⚠️  {problems}× přetékání layoutu")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
