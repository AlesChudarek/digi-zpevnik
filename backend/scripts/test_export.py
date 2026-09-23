"""Ověří stahování zpěvníku do PDF a ZIP proti běžícímu serveru.

Kontroluje to, co jde spočítat: že se PDF opravdu vygeneruje a má správný počet stran ve
správném pořadí, že cizí soukromý zpěvník nikdo nestáhne, že souběžné požadavky vytvoří
jeden soubor a ne tři, že se odpověď vrátí hned a nespadne na timeoutu, a že editace
zpěvníku vede na jiný soubor bez jakéhokoli invalidačního háku.

Nepotřebuje playwright ani žádnou PDF knihovnu: počet stran se čte z posledního /Count
přímo v souboru.

Použití:
    python backend/scripts/test_export.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
EXPORTS_DIR = PROJECT_ROOT / "data" / "exports"
PORT = 5584
ADMIN = ("admin@test.com", "export-test")
# Běžný účet: denní limit skládání se adminovi nepočítá, takže se na něm neověří.
UZIVATEL = ("user@test.com", "export-test-user")
BOOK = "00006"          # začíná na straně 3, dobrý test pořadí
PRIVATE_BOOK = "00101"  # cizí soukromý zpěvník
BOOK_RGB = "00009"      # obsahuje stranu bez alfa kanálu

selhani = []


def rozmery_stranky(data):
    """Rozměry první stránky PDF v bodech. Na rozlišení na výšku a na šířku to stačí."""
    m = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    if not m:
        return None
    return (round(float(m.group(3)) - float(m.group(1))),
            round(float(m.group(4)) - float(m.group(2))))


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + detail) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "export-test"
    env["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'backend'}"

    subprocess.run(
        [str(VENV_PY), "-c",
         'import os, sys\n'
         'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
         'from backend.app import app, db, User\n'
         'from werkzeug.security import generate_password_hash\n'
         'with app.app_context():\n'
         '    u = User.query.filter_by(email="admin@test.com").first()\n'
         f'    u.password = generate_password_hash("{ADMIN[1]}", method="pbkdf2:sha256")\n'
         '    u.role = "admin"\n'
         '    b = User.query.filter_by(email="user@test.com").first()\n'
         f'    b.password = generate_password_hash("{UZIVATEL[1]}", method="pbkdf2:sha256")\n'
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
            return proc, base, env
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("❌ server se nerozjel")


class Klient:
    """Minimální HTTP klient s cookies, ať se nemusí instalovat requests."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))

    def prihlas(self, email, heslo):
        data = urllib.parse.urlencode({"email": email, "password": heslo}).encode()
        self.opener.open(self.base + "/login", data)

    def post_json(self, cesta, data):
        req = urllib.request.Request(
            self.base + cesta, data=json.dumps(data).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            odpoved = self.opener.open(req)
            return odpoved.status, odpoved.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, cesta):
        try:
            odpoved = self.opener.open(self.base + cesta)
            # Kam se doopravdy došlo: @login_required posílá 302 na přihlášení a urllib
            # přesměrování následuje, takže samotné 200 o autorizaci nic neříká.
            return odpoved.status, odpoved.read(), odpoved.headers, odpoved.geturl()
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers, self.base + cesta


def pocet_stran_pdf(data):
    """Počet stran z posledního /Count v souboru.

    Nepočítat výskyty /Type /Page: Pillow zapisuje PDF přírůstkově, takže v souboru
    zůstávají i objekty stran z předchozích revizí. U 26 stran jich je 351 (součet
    1+2+...+26) a čtečka je ignoruje, protože se řídí posledním xref.
    """
    vyskyty = re.findall(rb"/Count\s+(\d+)", data)
    return int(vyskyty[-1]) if vyskyty else 0


def pockej_na_export(klient, cesta_status, limit=120):
    zacatek = time.time()
    while time.time() - zacatek < limit:
        status, telo, _, url = klient.get(cesta_status)
        if b'"ready"' in telo:
            return "ready", time.time() - zacatek
        if b'"error"' in telo:
            return "error", time.time() - zacatek
        time.sleep(0.5)
    return "timeout", time.time() - zacatek


def main():
    import urllib.parse  # noqa: F401 - používá se v Klient.prihlas

    tmp = Path(tempfile.mkdtemp(prefix="export-test-"))
    db_copy = tmp / "test.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)
    # Ať se neměří cache z dřívějška
    if EXPORTS_DIR.exists():
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

    server, base, env = start_server(db_copy)
    try:
        admin = Klient(base)
        admin.prihlas(*ADMIN)

        print("\n── první požadavek se nesmí zdržet ──")
        t0 = time.time()
        status, telo, _, url = admin.get(f"/songbook/{BOOK}/export.pdf?q=small")
        odezva = time.time() - t0
        zkontroluj(status == 202, "první požadavek vrátí 202 (staví se)", f"status {status}")
        zkontroluj(odezva < 2.0, "a vrátí se do dvou sekund, ne po dogenerování",
                   f"{odezva:.2f} s")

        print("\n── vygenerování ──")
        stav, cas = pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf")
        zkontroluj(stav == "ready", "export se dokončí", f"{stav} za {cas:.1f} s")

        status, pdf, hlavicky, _ = admin.get(f"/songbook/{BOOK}/export.pdf?q=small")
        zkontroluj(status == 200, "druhý požadavek vrátí hotový soubor", f"status {status}")
        zkontroluj(pdf[:5] == b"%PDF-", "a je to opravdu PDF")
        zkontroluj("attachment" in hlavicky.get("Content-Disposition", ""),
                   "servíruje se jako příloha ke stažení",
                   hlavicky.get("Content-Disposition", ""))

        print("\n── počet a pořadí stran ──")
        ocekavano = subprocess.run(
            [str(VENV_PY), "-c",
             'import os, sys\n'
             'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
             'from backend.app import app, build_songbook_export_sequence\n'
             'from backend.models import Songbook\n'
             'with app.app_context():\n'
             f'    sb = Songbook.query.get("{BOOK}")\n'
             '    print(len(build_songbook_export_sequence(sb)))\n'],
            env=env, capture_output=True, text=True)
        ocekavano_stran = int(ocekavano.stdout.strip())
        v_pdf = pocet_stran_pdf(pdf)
        zkontroluj(v_pdf == ocekavano_stran,
                   "PDF má tolik stran, kolik má zpěvník včetně obálek",
                   f"čekáno {ocekavano_stran}, v PDF {v_pdf}")

        print("\n── zpěvník se stranami bez alfy ──")
        # Většina skenů je RGBA, ale ne všechny. Strana, která je rovnou RGB, se dřív
        # vracela jako tentýž objekt z už zavřeného souboru a export na ní spadl.
        # Ukázalo se to až při projetí všech zpěvníků, ne na těch pár testovacích.
        admin.get(f"/songbook/{BOOK_RGB}/export.pdf?q=small")
        stav, cas = pockej_na_export(admin, f"/songbook/{BOOK_RGB}/export-status/pdf")
        zkontroluj(stav == "ready", "projde i zpěvník se stranami bez alfa kanálu",
                   f"{stav} za {cas:.1f} s")
        status, pdf_rgb, _, _ = admin.get(f"/songbook/{BOOK_RGB}/export.pdf?q=small")
        zkontroluj(pdf_rgb[:5] == b"%PDF-", "a je to platné PDF",
                   f"{len(pdf_rgb) // 1024} kB")

        print("\n── ZIP ──")
        admin.get(f"/songbook/{BOOK}/export.zip")
        stav, cas = pockej_na_export(admin, f"/songbook/{BOOK}/export-status/zip")
        zkontroluj(stav == "ready", "ZIP se dokončí", f"{stav} za {cas:.1f} s")
        status, zip_data, _, _ = admin.get(f"/songbook/{BOOK}/export.zip")
        zkontroluj(zip_data[:2] == b"PK", "a je to opravdu ZIP")
        zkontroluj(len(zip_data) > len(pdf),
                   "ZIP originálů je větší než překódované PDF",
                   f"ZIP {len(zip_data) // 1024} kB, PDF {len(pdf) // 1024} kB")

        print("\n── cache klíčovaná obsahem ──")
        soubory_pred = sorted(p.name for p in EXPORTS_DIR.glob("*.pdf"))
        admin.get(f"/songbook/{BOOK}/export.pdf?q=small")
        zkontroluj(sorted(p.name for p in EXPORTS_DIR.glob("*.pdf")) == soubory_pred,
                   "opakované stažení negeneruje nový soubor")
        status, _, _, url = admin.get(f"/songbook/{BOOK}/export.pdf?q=high")
        zkontroluj(status == 202, "jiná varianta kvality se staví zvlášť", f"status {status}")

        # Úklid po dokončení nesmí sáhnout na sousední varianty. Dokud mazal podle
        # čísla zpěvníku, stažení plného rozlišení smazalo hotové menší PDF a tomu, kdo
        # na něj čekal, se stav překlopil na idle - a klient čekal až do stropu.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf?q=high")
        status, telo, _, _ = admin.get(f"/songbook/{BOOK}/export-status/pdf?q=small")
        zkontroluj(b'"ready"' in telo,
                   "dostavění jiné varianty nesmaže tu předchozí", telo.decode().strip())
        status, telo, _, _ = admin.get(f"/songbook/{BOOK}/export-status/zip")
        zkontroluj(b'"ready"' in telo,
                   "ani hotový ZIP", telo.decode().strip())

        print("\n── předgenerování po uložení ──")
        # Po úpravě zpěvníku se musí nová verze předpřipravit sama a stará zmizet.
        # Jinak by první stažení po úpravě čekalo - a hlavně by hrozilo, že si někdo
        # stáhne jiný stav, než je na webu.
        pred = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-*")}
        zmena = subprocess.run(
            [str(VENV_PY), "-c",
             'import os, sys, time\n'
             'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
             'from backend.app import app, schedule_export_warm\n'
             'from backend.models import Songbook, SongbookPage, db\n'
             'with app.app_context():\n'
             # Prohodit dvě strany. Pouhá změna čísla strany by nestačila: klíč se
             # počítá z pořadí souborů a čísla stran se do PDF netisknou, takže by
             # výstup byl opravdu totožný a nová verze by neměla vzniknout.
             f'    rows = SongbookPage.query.filter_by(songbook_id="{BOOK}").order_by(\n'
             '        SongbookPage.page_number.asc()).all()\n'
             '    prvni, druhy = rows[0].page_number, rows[1].page_number\n'
             '    rows[0].page_number, rows[1].page_number = druhy, prvni\n'
             '    db.session.commit()\n'
             f'    schedule_export_warm("{BOOK}")\n'
             '    time.sleep(25)\n'],
            env=env, capture_output=True, text=True)
        po = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-*.pdf")}
        nove = po - pred
        zkontroluj(bool(nove), "po uložení vznikne nová předpřipravená verze",
                   ", ".join(sorted(nove)) or zmena.stderr[-200:])
        zkontroluj(not (po & pred), "a stará verze zmizí",
                   f"zbylo {sorted(po & pred)}")

        print("\n── souběh ──")
        # Napřed počkat, až doběhne všechno rozdělané. Mazat soubory pod běžícím
        # buildem znamená měřit vlastní zásah, ne chování serveru.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf")
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

        # Souběžné požadavky musí sáhnout po jednom zámku, ne postavit tři soubory
        prihlaseni = [Klient(base) for _ in range(3)]
        for k in prihlaseni:
            k.prihlas(*ADMIN)
        vysledky = []
        vlakna = []
        for k in prihlaseni:
            t = threading.Thread(
                target=lambda kl=k: vysledky.append(
                    kl.get(f"/songbook/{BOOK}/export.pdf?q=small")[0]))
            vlakna.append(t)
        for t in vlakna:
            t.start()
        for t in vlakna:
            t.join()
        zamky = list(EXPORTS_DIR.glob("*.lock"))
        zkontroluj(len(zamky) <= 1, "tři souběžné požadavky drží nejvýš jeden zámek",
                   f"zámků {len(zamky)}")
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf")
        casti = list(EXPORTS_DIR.glob("*.part"))
        zkontroluj(not casti, "po dokončení nezůstal žádný rozepsaný soubor")
        zkontroluj(not list(EXPORTS_DIR.glob("*.lock")), "ani žádný zámek")

        print("\n── export-warm se nepotká se stahováním z webu ──")
        # Tohle se stalo naostro: příkaz export-warm si nebral zámek, takže webová cesta
        # nenašla ani hotový soubor, ani zámek, a spustila druhé skládání téhož zpěvníku.
        # Obě pak zapisovala do stejného `.part` souboru a výsledkem bylo rozbité PDF,
        # které se přejmenovalo na hotové - a u veřejného zpěvníku by tam zůstalo ležet,
        # protože ty se z cache nevyhazují.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf")
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

        warm = subprocess.Popen(
            [str(VENV_PY), "-m", "flask", "--app", "backend.app", "export-warm",
             "--songbook", BOOK, "--variant", "small"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        zamek_nasel = False
        for _ in range(200):
            if list(EXPORTS_DIR.glob("*.lock")):
                zamek_nasel = True
                break
            if warm.poll() is not None:
                break
            time.sleep(0.1)
        zkontroluj(zamek_nasel, "export-warm si vezme zámek, než začne stavět")

        if zamek_nasel:
            stav_kod = admin.get(f"/songbook/{BOOK}/export.pdf?q=small")[0]
            zkontroluj(stav_kod == 202,
                       "stažení z webu mezitím nezačne stavět podruhé, jen se zařadí",
                       f"status {stav_kod}")
            stav = admin.get(f"/songbook/{BOOK}/export-status/pdf?q=small")[1]
            zkontroluj(b'"building"' in stav,
                       "a čekající prohlížeč vidí postup z toho běžícího skládání",
                       stav.decode()[:90])

        vystup = warm.communicate(timeout=300)[0]
        zkontroluj("staví ho zrovna někdo jiný" not in vystup,
                   "export-warm zpěvník opravdu postavil, nepřeskočil ho",
                   vystup.strip().splitlines()[-1] if vystup.strip() else "")
        # Po doběhnutí příkazu nesmí nic dalšího stavět. Kdyby se webová cesta pustila
        # do druhého skládání, drží tu teď zámek a za chvíli přepíše hotový soubor.
        for _ in range(30):
            if not list(EXPORTS_DIR.glob("*.lock")):
                break
            time.sleep(0.1)
        zkontroluj(not list(EXPORTS_DIR.glob("*.lock")),
                   "po doběhnutí příkazu už nikdo nestaví")
        zkontroluj(not list(EXPORTS_DIR.glob("*.part")),
                   "a nezůstal rozepsaný soubor")

        hotove = list(EXPORTS_DIR.glob(f"{BOOK}-small-*.pdf"))
        zkontroluj(len(hotove) == 1, "vzniklo právě jedno hotové PDF",
                   f"{[h.name for h in hotove]}")
        if hotove:
            data = hotove[0].read_bytes()
            v_pdf = pocet_stran_pdf(data)
            zkontroluj(data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:],
                       "a je celé, ne uříznuté")
            zkontroluj(v_pdf == ocekavano_stran,
                       "a má správný počet stran, ne dvojitě zapsaný obsah",
                       f"čekáno {ocekavano_stran}, v PDF {v_pdf}")

        print("\n── denní limit skládání ──")
        # Chrání jediný stavěcí slot: kdo by si pořád dokola říkal o jiný recept,
        # obsadil by ho všem ostatním. Počítat se smí jen skutečné skládání, ne stažení
        # hotového souboru z cache - to je pár milisekund.
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

        limit_env = dict(env)
        limit_env["MAX_EXPORT_BUILDS_PER_DAY"] = "2"
        limit_port = PORT + 3
        limit_server = subprocess.Popen(
            [str(VENV_PY), "-m", "flask", "--app", "backend.app", "run",
             "--port", str(limit_port), "--no-reload"],
            cwd=str(PROJECT_ROOT), env=limit_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            limit_base = f"http://127.0.0.1:{limit_port}"
            for _ in range(160):
                try:
                    urllib.request.urlopen(limit_base + "/login", timeout=1)
                    break
                except Exception:
                    time.sleep(0.25)

            # Běžný uživatel, ne admin: adminovi se skládání nepočítá, protože
            # přestavování veřejných zpěvníků je součást jeho práce.
            uzivatel = Klient(limit_base)
            uzivatel.prihlas(*UZIVATEL)
            kody = []
            for i, dotaz in enumerate(("?obsah=jen-obalka", "?prazdne=0",
                                       "?cernobile=1", "?kvalita=high")):
                kody.append(uzivatel.get(f"/songbook/{BOOK}/export.pdf{dotaz}")[0])
                while list(EXPORTS_DIR.glob("*.lock")):
                    time.sleep(0.3)
            zkontroluj(kody[:2] == [202, 202], "první dvě skládání projdou", str(kody))
            zkontroluj(kody[2] == 429 and kody[3] == 429,
                       "další už limit odmítne", str(kody))

            kod, telo = uzivatel.get(f"/songbook/{BOOK}/export.pdf?obsah=jen-obalka")[:2]
            zkontroluj(kod == 200,
                       "ale hotový soubor z cache se stáhne dál, ten nic neskládá",
                       f"status {kod}")

            _, telo = uzivatel.get(f"/songbook/{BOOK}/export.pdf?kvalita=high")[:2]
            zkontroluj(b"limit" in telo.lower(),
                       "odmítnutí řekne, že jde o limit, ne o zaneprázdněný server",
                       telo.decode()[:90])

            spravce = Klient(limit_base)
            spravce.prihlas(*ADMIN)
            kod = spravce.get(f"/songbook/{BOOK}/export.pdf?kvalita=high")[0]
            zkontroluj(kod in (200, 202), "admina limit neomezuje", f"status {kod}")
            while list(EXPORTS_DIR.glob("*.lock")):
                time.sleep(0.3)
        finally:
            limit_server.terminate()
            limit_server.wait(timeout=10)

        print("\n── autorizace ──")
        # Samotné 200 nic neříká: @login_required posílá 302 na přihlášení a urllib
        # přesměrování následuje, takže se musí koukat, kde se to zastavilo a co přišlo.
        nikdo = Klient(base)
        status, telo, _, url = nikdo.get(f"/songbook/{PRIVATE_BOOK}/export.pdf?q=small")
        zkontroluj(telo[:5] != b"%PDF-" and "/login" in url,
                   "nepřihlášený nedostane cizí soukromý zpěvník",
                   f"skončil na {url.replace(base, '')}")
        status, telo, _, url = nikdo.get(f"/songbook/{PRIVATE_BOOK}/export-status/pdf")
        zkontroluj(b'"ready"' not in telo and "/login" in url,
                   "ani stav jeho exportu",
                   f"skončil na {url.replace(base, '')}")

        # A přihlášený uživatel, který na knihu nemá právo, musí dostat rovnou 403
        bezprav = Klient(base)
        bezprav.prihlas("user3@test.com", "nesmysl")
        status, telo, _, url = bezprav.get(f"/songbook/{BOOK}/export.pdf?q=small")
        zkontroluj(telo[:5] != b"%PDF-",
                   "kdo se nepřihlásí, nestáhne ani veřejný zpěvník")

        print("\n── předgenerování po přidání písně ──")
        # Editor volal předgenerování jen při uložení struktury a při smazání písně.
        # Přidání písně ze seznamu přitom taky přidává strany, takže klíč se změnil
        # a předpřipravené PDF přestalo platit - další stažení čekalo na skládání.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf")
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        cizi = subprocess.run(
            [str(VENV_PY), "-c",
             'import os, sys\n'
             'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
             'from backend.app import app\n'
             'from backend.models import Song, SongbookPage, db\n'
             'with app.app_context():\n'
             f'    v_knize = {{r.song_id for r in SongbookPage.query.filter_by(songbook_id="{BOOK}")}}\n'
             '    volna = [s.id for s in Song.query.all() if s.id not in v_knize]\n'
             '    print(volna[0] if volna else "")\n'],
            env=env, capture_output=True, text=True).stdout.strip()

        if not cizi:
            zkontroluj(False, "je z čeho přidat píseň", "žádná píseň mimo zpěvník")
        else:
            pred = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-small-*.pdf")}
            kod, telo = admin.post_json(
                f"/api/songbooks/{BOOK}/add-song", {"song_id": cizi})
            zkontroluj(kod == 200, "píseň se přidá", f"status {kod} {telo[:80]}")
            nove = set()
            for _ in range(600):
                nove = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-small-*.pdf")} - pred
                if nove and not list(EXPORTS_DIR.glob("*.lock")):
                    break
                time.sleep(0.1)
            zkontroluj(bool(nove),
                       "a rovnou se předgeneruje nová verze PDF, nečeká se na stažení",
                       ", ".join(sorted(nove)) or "nic nevzniklo")

        print("\n── recept: vlastní nastavení stažení ──")
        # Varianty už nejsou tři zadrátované, ale pojmenované předvolby nad obecným
        # receptem. Adresa `?q=small` je celý dosavadní tvar a musí fungovat beze změny;
        # cokoliv navíc skládá vlastní recept s vlastním souborem v cache.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf?q=small")
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)

        def stahni(dotaz, timeout=300):
            """Vyžádá si export, počká na dostavění a vrátí (status, obsah)."""
            adresa = f"/songbook/{BOOK}/export.pdf{dotaz}"
            kod = admin.get(adresa)[0]
            if kod == 202:
                pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf{dotaz}")
                kod = admin.get(adresa)[0]
            return kod, admin.get(adresa)[1]

        ocekavane = {}
        for popis, dotaz in (("jen obálka", "?obsah=jen-obalka"),
                             ("jen obsah", "?obsah=jen-obsah"),
                             ("bez prázdných stran", "?prazdne=0"),
                             ("černobíle", "?cernobile=1")):
            kolik = subprocess.run(
                [str(VENV_PY), "-c",
                 'import os, sys\n'
                 'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
                 'from backend.app import (app, build_songbook_export_sequence,\n'
                 '                         recept_z_parametru)\n'
                 'from urllib.parse import parse_qs\n'
                 'from backend.models import Songbook\n'
                 'with app.app_context():\n'
                 f'    args = {{k: v[0] for k, v in parse_qs("{dotaz[1:]}").items()}}\n'
                 '    class A(dict):\n'
                 '        def get(self, k, d=None): return dict.get(self, k, d)\n'
                 f'    r = recept_z_parametru(A(args), "pdf")\n'
                 f'    sb = Songbook.query.get("{BOOK}")\n'
                 '    print(len(build_songbook_export_sequence(sb, r)))\n'],
                env=env, capture_output=True, text=True)
            cekano = int(kolik.stdout.strip() or 0)
            ocekavane[dotaz] = cekano
            kod, data = stahni(dotaz)
            v_pdf = pocet_stran_pdf(data)
            zkontroluj(kod == 200 and data[:5] == b"%PDF-",
                       f"{popis}: stáhne se platné PDF", f"status {kod}")
            zkontroluj(v_pdf == cekano and cekano > 0,
                       f"{popis}: má tolik stran, kolik z nastavení vychází",
                       f"čekáno {cekano}, v PDF {v_pdf}")

        zkontroluj(ocekavane["?obsah=jen-obalka"] < ocekavane["?obsah=jen-obsah"],
                   "jen obálka je kratší než jen obsah",
                   f"{ocekavane['?obsah=jen-obalka']} vs {ocekavane['?obsah=jen-obsah']}")

        # Dva zápisy téhož přání nesmí dát dva soubory v cache.
        pred = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        stahni("?cernobile=1&obsah=vse")
        po = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        zkontroluj(po == pred,
                   "týž recept zapsaný jinak trefí tentýž soubor v cache",
                   f"přibylo {sorted(po - pred)}")

        vlastni = sorted(EXPORTS_DIR.glob(f"{BOOK}-c*.pdf"))
        zkontroluj(len(vlastni) == 4,
                   "každý jiný recept má vlastní soubor", f"{len(vlastni)} souborů")

        for dotaz, kde in (("?kvalita=ultra", "kvalita"), ("?obsah=neco", "obsah"),
                           ("?neznama=1", "neznámá volba")):
            kod = admin.get(f"/songbook/{BOOK}/export.pdf{dotaz}")[0]
            zkontroluj(kod == 400, f"nesmysl v {kde} se odmítne", f"status {kod}")

        print("\n── rozsah stran ──")
        # Čísla jsou ta, která zpěvník ukazuje ve čtečce a v obsahu. Zpěvník složený
        # z cizích písní má vlastní číslování a uživatel vidí to svoje.
        stav = json.loads(admin.get(
            f"/songbook/{BOOK}/export-hotove?strany=3-4&obsah=jen-obsah")[1])
        zkontroluj(stav.get("stran") == 2, "rozsah 3-4 vybere dvě strany", str(stav))
        zkontroluj(len(stav.get("pisne") or []) == 2,
                   "a řekne, které písně na nich jsou",
                   ", ".join(p["nazev"] for p in stav.get("pisne") or []))

        kod, data = stahni("?strany=3-4&obsah=jen-obsah")
        zkontroluj(kod == 200 and pocet_stran_pdf(data) == 2,
                   "a stáhne se PDF právě o těch dvou stranách",
                   f"status {kod}, stran {pocet_stran_pdf(data)}")

        # Kanonický tvar: „4,3“ je totéž přání jako „3-4“ a nesmí dát druhý soubor.
        pred = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        stahni("?strany=4,3&obsah=jen-obsah")
        po = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        zkontroluj(po == pred, "„4,3“ trefí tentýž soubor jako „3-4“",
                   f"přibylo {sorted(po - pred)}")

        # Otevřený konec: „3-“ znamená od třetí strany dál.
        stav_otevreny = json.loads(admin.get(
            f"/songbook/{BOOK}/export-hotove?strany=3-&obsah=jen-obsah")[1])
        stav_vse = json.loads(admin.get(
            f"/songbook/{BOOK}/export-hotove?obsah=jen-obsah")[1])
        zkontroluj(stav_otevreny.get("stran") == stav_vse.get("stran"),
                   "„3-“ u zpěvníku číslovaného od tří vybere celý obsah",
                   f"{stav_otevreny.get('stran')} vs {stav_vse.get('stran')}")

        # Kanonický tvar musí sedět i s otevřeným koncem.
        pred_ot = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        stahni("?strany=3-&obsah=jen-obsah")
        stahni("?strany=4-,3&obsah=jen-obsah")
        po_ot = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-c*.pdf")}
        zkontroluj(len(po_ot - pred_ot) == 1,
                   "„4-,3“ je totéž přání jako „3-“ a nedělá druhý soubor",
                   f"přibylo {sorted(po_ot - pred_ot)}")

        # Strany bez písně nepatří mezi písně. Jsou to taky řádky v songs, jen
        # s is_non_song, a "Vyjde na 2 strany. <Prázdná strana>" není seznam písní.
        stav = json.loads(admin.get(f"/songbook/{BOOK}/export-hotove?obsah=jen-obsah")[1])
        zkontroluj(all("Prázdná strana" not in p["nazev"] for p in stav.get("pisne") or []),
                   "mezi písněmi nejsou prázdné strany",
                   ", ".join(p["nazev"] for p in (stav.get("pisne") or [])[:3]))
        zkontroluj("bez_pisne" in stav and "obalek" in stav,
                   "ale počet stran bez písně a obálek se hlásí zvlášť",
                   f"bez písně {stav.get('bez_pisne')}, obálek {stav.get('obalek')}")

        for zapis, proc in (("31-24", "pozpátku"), ("abc", "nečíslo"), ("0", "nula"),
                            ("1-2-3", "tři čísla")):
            kod = admin.get(f"/songbook/{BOOK}/export.pdf?strany={zapis}")[0]
            zkontroluj(kod == 400, f"rozsah {proc} se odmítne", f"status {kod}")

        print("\n── brožura ──")
        stav = json.loads(admin.get(f"/songbook/{BOOK}/export-hotove?brozura=1")[1])
        stran_celkem = stav.get("stran") or 0
        cekano_listu = (stran_celkem + 3) // 4 * 2
        zkontroluj(stav.get("listu") == cekano_listu,
                   "okno se dozví počet listů papíru, ne jen stran",
                   f"{stran_celkem} stran -> {stav.get('listu')} listů")

        kod, data = stahni("?brozura=1")
        zkontroluj(kod == 200 and data[:5] == b"%PDF-", "brožura se stáhne",
                   f"status {kod}")
        zkontroluj(pocet_stran_pdf(data) == cekano_listu,
                   "má tolik listů, kolik z počtu stran vychází",
                   f"čekáno {cekano_listu}, v PDF {pocet_stran_pdf(data)}")
        rozmer = rozmery_stranky(data)
        zkontroluj(rozmer is not None and rozmer[0] > rozmer[1],
                   "a je na šířku, ne na výšku", str(rozmer))
        zkontroluj(rozmer == (842, 595), "přesně A4 na šířku (297x210 mm)", str(rozmer))

        # ZIP balí originály, takže brožura pro něj neznamená nic a nesmí založit
        # vlastní soubor v cache.
        pred_zip = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-*.zip")}
        kod = admin.get(f"/songbook/{BOOK}/export.zip?brozura=1")[0]
        if kod == 202:
            pockej_na_export(admin, f"/songbook/{BOOK}/export-status/zip?brozura=1")
        po_zip = {p.name for p in EXPORTS_DIR.glob(f"{BOOK}-*.zip")}
        zkontroluj(all(not n.startswith(f"{BOOK}-c") for n in po_zip),
                   "brožura u ZIPu nezaloží vlastní soubor, spadne do předvolby",
                   ", ".join(sorted(po_zip)))

        print("\n── úklid řadí podle posledního stažení, ne podle postavení ──")
        # Bez tohohle byl mtime čas postavení, takže soubor stahovaný každý týden pět let
        # vypadal jako nejstarší v adresáři a odešel dřív než něco, co si nikdo nevyžádal
        # podruhé. Díky tomu nemusí úklid dělit soubory podle toho, z jaké předvolby vznikly.
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf?q=small")
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        hotovy = next(EXPORTS_DIR.glob(f"{BOOK}-small-*.pdf"), None)
        if hotovy is None:
            zkontroluj(False, "je co stáhnout")
        else:
            os.utime(hotovy, (time.time() - 90 * 86400, time.time() - 90 * 86400))
            pred = hotovy.stat().st_mtime
            stav = admin.get(f"/songbook/{BOOK}/export.pdf?q=small")[0]
            zkontroluj(stav == 200, "hotový soubor se vydá rovnou", f"status {stav}")
            po = hotovy.stat().st_mtime
            zkontroluj(po > pred + 80 * 86400,
                       "a stažení mu omladí čas, takže ho úklid bere jako čerstvý",
                       f"z {(time.time() - pred) / 86400:.0f} dní na "
                       f"{(time.time() - po) / 86400:.1f} dní")

        print("\n── z cache se nevyhazuje jen předgenerovaná varianta ──")
        # Chráněný je jen předgenerovaný `small` veřejného zpěvníku, protože jen u něj
        # platí slib "stažení veřejného zpěvníku je hned". Dřív byl chráněný prefix id,
        # tedy i veřejné `high` a ZIP - ty se ale nepředgenerovávají, takže první
        # stažení na ně čeká tak jako tak a držet je navždycky jen hromadilo. Naměřený
        # strop toho hromadění byl 977 MB, které by už nikdy nic neuvolnilo.
        while list(EXPORTS_DIR.glob("*.lock")):
            time.sleep(0.3)
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

        env_strop = dict(env)
        env_strop["EXPORTS_CACHE_LIMIT_MB"] = "50"
        uklid = subprocess.run(
            [str(VENV_PY), "-c",
             'import os, sys, time\n'
             'sys.path[:0] = os.environ["PYTHONPATH"].split(":")\n'
             'from backend.app import app, EXPORTS_DIR, _prune_exports\n'
             'def uloz(jmeno, mb, stari):\n'
             '    p = EXPORTS_DIR / jmeno\n'
             '    p.write_bytes(b"\\0" * (mb * 1024 * 1024))\n'
             '    os.utime(p, (time.time() - stari, time.time() - stari))\n'
             '    return p\n'
             'with app.app_context():\n'
             # 60 MB chráněných je nad stropem 50 MB - samo o sobě nesmí nic spustit
             f'    uloz("{BOOK}-small-chraneny1.pdf", 30, 400)\n'
             '    uloz("00002-small-chraneny2.pdf", 30, 390)\n'
             '    novy = uloz("00101-small-soukromy-novy.pdf", 50, 10)\n'
             '    _prune_exports(novy, "nic-se-netrefi-*")\n'
             '    print("PO_PRVNIM", sorted(p.name for p in EXPORTS_DIR.glob("*")))\n'
             # Vyhoditelných je teď 130 MB (50 soukromý + 40 veřejný high + 40 veřejný
             # zip) proti stropu 50 MB, takže musí odejít oba veřejné. Kdyby jich bylo
             # 90, stačilo by smazat jeden a o druhém by test nic neřekl.
             f'    uloz("{BOOK}-high-verejny-high.pdf", 40, 300)\n'
             f'    uloz("{BOOK}-orig-verejny-zip.zip", 40, 200)\n'
             '    _prune_exports(novy, "nic-se-netrefi-*")\n'
             '    print("PO_DRUHEM", sorted(p.name for p in EXPORTS_DIR.glob("*")))\n'],
            env=env_strop, capture_output=True, text=True)
        vystup = uklid.stdout
        prvni = next((r for r in vystup.splitlines() if r.startswith("PO_PRVNIM")), "")
        druhy = next((r for r in vystup.splitlines() if r.startswith("PO_DRUHEM")), "")
        zkontroluj("chraneny1" in prvni and "chraneny2" in prvni and "soukromy-novy" in prvni,
                   "60 MB chráněných nad stropem 50 MB samo o sobě nic nevyhodí",
                   prvni or uklid.stderr[-300:])
        zkontroluj("chraneny1" in druhy and "chraneny2" in druhy,
                   "předgenerované veřejné PDF přežije i překročení stropu", druhy)
        zkontroluj("verejny-zip" not in druhy,
                   "ale veřejný ZIP je vyhoditelný jako každý jiný", druhy)
        zkontroluj("verejny-high" not in druhy,
                   "a veřejné plné rozlišení taky", druhy)
        zkontroluj("soukromy-novy" in druhy,
                   "nejnovější se nemaže, i když je soukromý", druhy)
        for p in EXPORTS_DIR.glob("*"):
            p.unlink(missing_ok=True)

        print("\n── exporty leží mimo veřejně servírované adresáře ──")
        # Poslední sekce potřebuje nějaký hotový soubor; vlastní úklid o pár řádků výš
        # adresář vyprázdnil, tak jeden znovu postavit.
        admin.get(f"/songbook/{BOOK}/export.pdf?q=small")
        pockej_na_export(admin, f"/songbook/{BOOK}/export-status/pdf?q=small")
        hotovy = next(EXPORTS_DIR.glob("*.pdf"), None)
        zkontroluj(hotovy is not None, "existuje vygenerovaný soubor")
        if hotovy:
            status, _, _, url = admin.get(f"/songbooks/../exports/{hotovy.name}")
            zkontroluj(status == 404,
                       "route na obrázky se k exportům nedostane ani přes ..",
                       f"status {status}")

    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    if selhani:
        print(f"\n❌ neprošlo {len(selhani)} kontrol:")
        for s in selhani:
            print(f"   - {s}")
        return 1
    print("\n✅ všechny kontroly prošly")
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
