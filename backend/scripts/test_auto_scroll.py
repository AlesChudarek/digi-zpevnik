"""Ověří automatické sjíždění v rolovacím módu měřením, ne pohledem.

Kontroluje, co jde spočítat: že se stránka opravdu posouvá, že naměřená rychlost sedí
na nastavené "sekundy na stranu", že pomalý konec slideru nezamrzne na nule (scrollTop
bere celé pixely, takže zlomky se musí hromadit), že se to zastaví na konci zpěvníku
a že tlačítko v knižním módu zmizí.

Vyžaduje playwright, stejně jako measure_reader.py. Běží proti KOPII databáze.
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
PORT = 5597
PASSWORD = "autoscroll-test"
BOOK = "/songbook/00006"

selhani = []


def zkontroluj(podminka, popis, detail=""):
    znacka = "✅" if podminka else "❌"
    print(f"  {znacka} {popis}{('  ' + detail) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def start_server(db_copy):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_copy.as_posix()}"
    env["FLASK_SECRET_KEY"] = "autoscroll"
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


def najed_na_tlacitko(page):
    """Rozbalí panel najetím na tlačítko. Funguje jen za běhu sjíždění - a zabalený
    panel má pointer-events: none, takže myš namířená rovnou na místo, kde slider
    bývá, projde skrz a nic nerozbalí."""
    stred = page.evaluate("""() => {
      const r = document.getElementById('auto-scroll-toggle').getBoundingClientRect();
      return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""")
    page.mouse.move(stred["x"], stred["y"])
    page.wait_for_timeout(350)


def do_rolovaciho_modu(page):
    page.evaluate("() => { if (currentMode !== 'scroll') toggleReadingMode(); }")
    page.wait_for_timeout(600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Firefox je tu podstatný, ne jen pro pořádek: u rozsahového vstupu napodobuje
    # systémový posuvník a při tažení daleko od dráhy vrací hodnotu na tu před stiskem.
    # Právě to bylo hlášeno a v Chromu se to neprojeví.
    parser.add_argument("--browser", default="chromium",
                        choices=["chromium", "firefox", "webkit"])
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("❌ chybí playwright. Nainstaluj:\n"
                         "   .venv/bin/pip install playwright\n"
                         "   .venv/bin/playwright install chromium firefox")

    tmp = Path(tempfile.mkdtemp(prefix="autoscroll-"))
    db_copy = tmp / "test.db"
    shutil.copy(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db", db_copy)
    server, base = start_server(db_copy)

    try:
        with sync_playwright() as pw:
            print(f"\n═══ prohlížeč: {args.browser} ═══")
            browser = getattr(pw, args.browser).launch()
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            chyby = []
            page.on("pageerror", lambda e: chyby.append(str(e)))

            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")
            page.goto(base + BOOK)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)

            print("\n── viditelnost tlačítka ──")
            zkontroluj(page.evaluate(
                "() => getComputedStyle(document.getElementById('auto-scroll-button')).display"
            ) == "none", "v knižním módu je tlačítko schované")

            do_rolovaciho_modu(page)
            zkontroluj(page.evaluate(
                "() => getComputedStyle(document.getElementById('auto-scroll-button')).display"
            ) != "none", "v rolovacím módu je tlačítko vidět")

            print("\n── panel rychlosti ──")
            zkontroluj(not page.evaluate(
                "() => document.getElementById('auto-scroll-panel').classList.contains('open')"),
                "panel je zavřený, dokud se neklikne")

            page.evaluate("() => document.getElementById('auto-scroll-toggle').click()")
            page.wait_for_timeout(300)
            zkontroluj(page.evaluate(
                "() => document.getElementById('auto-scroll-panel').classList.contains('open')"),
                "klik panel otevře")
            zkontroluj(page.evaluate("() => autoScrollRunning"), "a zároveň spustí sjíždění")
            zkontroluj(page.evaluate(
                "() => document.getElementById('auto-scroll-toggle').classList.contains('running')"),
                "tlačítko se obarví")

            print("\n── panel se vejde na obrazovku ──")
            for sirka in (1280, 844, 390):
                page.set_viewport_size({"width": sirka, "height": 800})
                page.wait_for_timeout(400)
                prava = page.evaluate(
                    "() => document.getElementById('auto-scroll-panel').getBoundingClientRect().right")
                zkontroluj(prava <= sirka, f"při {sirka} px panel nepřetéká",
                           f"pravý okraj {round(prava, 1)}")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(400)

            print("\n── rychlé tažení sliderem ──")
            # Aleš hlásí, že při rychlém tažení kulička zaostává za kurzorem a po puštění
            # se hodnota vrátí na původní. Simuluje se tažení velkými skoky a puštění
            # mimo panel, tedy přesně to, co dělá ruka, která spěchá.
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 20; s.dispatchEvent(new Event('input'));
            }""")
            page.wait_for_timeout(200)
            pred = page.evaluate("() => document.getElementById('auto-scroll-speed').value")
            najed_na_tlacitko(page)
            box = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-speed').getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            # Chytit palec na 20 % a jedním trhnutím ho odtáhnout daleko za panel, dolů
            # i doprava - tedy pustit tam, kde slider vůbec není
            page.mouse.move(box["x"] + box["w"] * 0.2, box["y"] + box["h"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["w"] * 3, box["y"] + 320)
            page.mouse.up()
            page.wait_for_timeout(400)
            po = page.evaluate("() => document.getElementById('auto-scroll-speed').value")
            zkontroluj(float(po) > float(pred),
                       "tažení puštěné daleko mimo panel hodnotu udrží",
                       f"z {pred} na {po}")
            zkontroluj(page.evaluate(
                "() => document.getElementById('auto-scroll-panel').classList.contains('open')"),
                "panel po tažení zůstane otevřený")

            # Tažení hned po otevření, tedy uprostřed otevírací animace panelu. Dokud se
            # panel otevíral přes scaleX, byl slider vodorovně stlačený a kurzor mířil
            # jinam, než kam podle geometrie patřil palec.
            page.evaluate("() => document.getElementById('auto-scroll-toggle').click()")
            page.wait_for_timeout(300)
            page.evaluate("() => document.getElementById('auto-scroll-toggle').click()")
            box2 = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-speed').getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            page.mouse.move(box2["x"] + box2["w"] * 0.1, box2["y"] + box2["h"] / 2)
            page.mouse.down()
            page.mouse.move(box2["x"] + box2["w"] * 0.75, box2["y"] + box2["h"] / 2)
            page.mouse.up()
            page.wait_for_timeout(400)
            behem = page.evaluate("() => parseFloat(document.getElementById('auto-scroll-speed').value)")
            # Uprostřed 230px panelu je 75 % dráhy; tolerance na šířku palce
            zkontroluj(65 <= behem <= 85,
                       "tažení během otevírací animace trefí správné místo",
                       f"čekáno ~75, naměřeno {behem}")

            print("\n── rychlý hod (vzorec z hlášené chyby) ──")
            # Přesně to, co je v protokolu událostí z Firefoxu: stisk vlevo, dva velké
            # skoky s pár desítkami ms mezi nimi, poslední daleko od dráhy, pak puštění.
            # Firefox tam tažení zrušil a vrátil hodnotu z okamžiku před stiskem.
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 7; s.dispatchEvent(new Event('input'));
            }""")
            page.wait_for_timeout(200)
            najed_na_tlacitko(page)
            box3 = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-speed').getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            stred = box3["y"] + box3["h"] / 2
            page.mouse.move(box3["x"] + box3["w"] * 0.07, stred)
            page.mouse.down()
            page.mouse.move(box3["x"] + box3["w"] * 0.41, stred + 15)
            page.mouse.move(box3["x"] + box3["w"] * 0.80, stred + 300)
            page.mouse.up()
            page.wait_for_timeout(400)
            hod = page.evaluate(
                "() => parseFloat(document.getElementById('auto-scroll-speed').value)")
            zkontroluj(hod > 20, "rychlý hod se nevrátí na hodnotu před stiskem",
                       f"začínalo na 7, skončilo na {hod}")
            # Svislá vzdálenost od dráhy nesmí hrát roli - rozhoduje vodorovná poloha
            zkontroluj(70 <= hod <= 90,
                       "hodnota odpovídá vodorovné poloze kurzoru při puštění",
                       f"kurzor na 80 % dráhy, hodnota {hod}")

            print("\n── zásahová plocha slideru ──")
            vyska = page.evaluate(
                "() => document.getElementById('auto-scroll-speed').getBoundingClientRect().height")
            zkontroluj(vyska >= 30, "slider se dá chytit i mimo tenkou dráhu",
                       f"výška {round(vyska)} px")

            print("\n── rychlost sjíždění ──")
            # Nejrychlejší konec slideru, ať měření netrvá věčně
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 100; s.dispatchEvent(new Event('input'));
              document.querySelector('.zoom-scroll-wrapper').scrollTop = 0;
            }""")
            page.wait_for_timeout(300)
            udaje = page.evaluate("""async () => {
              const wrapper = document.querySelector('.zoom-scroll-wrapper');
              const zacatek = wrapper.scrollTop;
              const t0 = performance.now();
              await new Promise(r => setTimeout(r, 3000));
              return {
                ujeto: wrapper.scrollTop - zacatek,
                sekundy: (performance.now() - t0) / 1000,
                vyskaStrany: autoScrollPageHeight(),
                sekundNaStranu: autoScrollSecondsPerPage(
                  parseFloat(document.getElementById('auto-scroll-speed').value)),
              };
            }""")
            ocekavano = udaje["vyskaStrany"] / udaje["sekundNaStranu"] * udaje["sekundy"]
            odchylka = abs(udaje["ujeto"] - ocekavano) / max(1, ocekavano)
            zkontroluj(udaje["ujeto"] > 0, "stránka se opravdu posouvá",
                       f"ujeto {round(udaje['ujeto'])} px za {round(udaje['sekundy'], 1)} s")
            zkontroluj(odchylka < 0.15, "naměřená rychlost sedí na nastavenou",
                       f"čekáno {round(ocekavano)} px, odchylka {round(odchylka * 100)} %")

            print("\n── nejpomalejší nastavení nesmí zamrznout ──")
            # scrollTop bere celé pixely; bez hromadění zlomků by se tu nehnulo nic
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 0; s.dispatchEvent(new Event('input'));
            }""")
            pomalu = page.evaluate("""async () => {
              const wrapper = document.querySelector('.zoom-scroll-wrapper');
              const zacatek = wrapper.scrollTop;
              await new Promise(r => setTimeout(r, 3000));
              return wrapper.scrollTop - zacatek;
            }""")
            zkontroluj(pomalu > 0, "i na nejpomalejším stupni se posouvá",
                       f"ujeto {pomalu} px za 3 s")

            print("\n── plynulost na nejpomalejším stupni ──")
            # Dřív se zlomky pixelu hromadily a scrollTop se posouval po celých pixelech,
            # takže při 7 px/s stránka poskočila jednou za osm snímků. Měří se, v kolika
            # snímcích se poloha vůbec změnila a jak velký byl největší skok.
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 0; s.dispatchEvent(new Event('input'));
              document.querySelector('.zoom-scroll-wrapper').scrollTop = 200;
            }""")
            page.wait_for_timeout(300)
            if not page.evaluate("() => autoScrollRunning"):
                page.evaluate("() => document.getElementById('auto-scroll-toggle').click()")
            page.wait_for_timeout(300)
            plynulost = page.evaluate("""async () => {
              // Ne scrollTop: Firefox ho při čtení zaokrouhluje, takže by tvrdil, že se
              // nic nehýbe, i kdyby se hýbalo. Skutečnou vykreslenou polohu prozradí
              // horní hrana obrázku strany.
              const img = document.querySelector('#scroll-mode img');
              const polohy = [];
              await new Promise(res => {
                const tik = () => {
                  polohy.push(img.getBoundingClientRect().top);
                  if (polohy.length < 90) requestAnimationFrame(tik); else res();
                };
                requestAnimationFrame(tik);
              });
              let zmen = 0, nejvetsi = 0;
              for (let i = 1; i < polohy.length; i++) {
                const d = Math.abs(polohy[i] - polohy[i-1]);
                if (d > 0.0001) zmen++;
                if (d > nejvetsi) nejvetsi = d;
              }
              return {
                podilZmenenych: Math.round(100 * zmen / (polohy.length - 1)),
                nejvetsiSkok: Math.round(nejvetsi * 100) / 100,
              };
            }""")
            zkontroluj(plynulost["podilZmenenych"] >= 80,
                       "poloha se mění skoro v každém snímku",
                       f"{plynulost['podilZmenenych']} % snímků")
            zkontroluj(plynulost["nejvetsiSkok"] < 1.0,
                       "žádný skok není celý pixel ani větší",
                       f"největší {plynulost['nejvetsiSkok']} px")

            print("\n── viditelnost při nastavování ──")
            page.evaluate("""() => {
              const s = document.getElementById('auto-scroll-speed');
              s.value = 50; s.dispatchEvent(new Event('input'));
            }""")
            najed_na_tlacitko(page)
            box4 = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-speed').getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            # Chytit palec, odjet daleko mimo a DRŽET - dřív po třech vteřinách zmizel
            # celý sloupec i s panelem pod rukou
            page.mouse.move(box4["x"] + box4["w"] * 0.5, box4["y"] + box4["h"] / 2)
            page.mouse.down()
            page.mouse.move(box4["x"] + box4["w"] * 0.7, box4["y"] + 400)
            page.wait_for_timeout(4000)
            zkontroluj(page.evaluate(
                "() => document.getElementById('auto-scroll-panel').classList.contains('open')"),
                "držený palec panel neschová ani po 4 s")
            zkontroluj(not page.evaluate(
                "() => document.querySelector('.zoom-buttons').classList.contains('fade-hidden')"),
                "ani sloupec tlačítek nezmizí")
            page.mouse.up()
            page.wait_for_timeout(300)

            print("\n── zabalený a rozbalený stav ──")
            # Panel je přilepený k tlačítkům: mizí s nimi a jejich schování ho zároveň
            # zabalí, takže po opětovném zobrazení začíná vždy bez slideru. Rozbalí ho
            # jedině spuštění sjíždění nebo najetí na pauzu, když už jede.
            rozbaleno = ("() => document.getElementById('auto-scroll-panel')"
                         ".classList.contains('open')")
            schovano = ("() => document.querySelector('.zoom-buttons')"
                        ".classList.contains('fade-hidden')")

            # Odjet myší pryč a nechat sloupec zmizet nečinností
            page.mouse.move(640, 720)
            page.wait_for_timeout(4000)
            zkontroluj(page.evaluate(schovano), "nečinnost sloupec schová")
            zkontroluj(not page.evaluate(rozbaleno), "a zároveň panel zabalí")
            zkontroluj(page.evaluate("() => autoScrollRunning"),
                       "sjíždění přitom běží dál")

            # Pohyb myší sloupec vrátí - ale bez slideru
            page.mouse.move(645, 700)
            page.wait_for_timeout(400)
            zkontroluj(not page.evaluate(schovano), "pohyb myší sloupec zase ukáže")
            zkontroluj(not page.evaluate(rozbaleno), "a ten se vrátí zabalený")

            # Najetí na pauzu (sjíždění běží) rozbalí
            tlacitko = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-toggle').getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""")
            page.mouse.move(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(400)
            zkontroluj(page.evaluate(rozbaleno), "najetí na pauzu za běhu rozbalí")

            # Odjetí nezabaluje - rozbalený stav drží
            page.mouse.move(tlacitko["x"] + 400, tlacitko["y"] + 200)
            page.wait_for_timeout(600)
            zkontroluj(page.evaluate(rozbaleno), "odjetí myší panel nezabalí")

            # Zastavení zabalí
            page.mouse.move(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(200)
            page.mouse.click(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(400)
            zkontroluj(not page.evaluate("() => autoScrollRunning"), "klik sjíždění zastaví")
            zkontroluj(not page.evaluate(rozbaleno), "a zastavení panel zabalí")

            # Najetí na zastavené hodinky nerozbaluje
            page.mouse.move(tlacitko["x"] + 400, tlacitko["y"] + 200)
            page.wait_for_timeout(200)
            page.mouse.move(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(400)
            zkontroluj(not page.evaluate(rozbaleno),
                       "najetí na zastavené hodinky nerozbalí")

            # A spuštění zase rozbalí
            page.mouse.click(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(400)
            zkontroluj(page.evaluate("() => autoScrollRunning"), "další klik sjíždění spustí")
            zkontroluj(page.evaluate(rozbaleno), "a spuštění panel rozbalí")

            print("\n── ztracené puštění tažení ──")
            # Když se pointerup ke slideru nedostane (puštění mimo okno, prohlížeč zahodí
            # zachycení, přepnutí do jiné aplikace), zůstalo tažení navždy "probíhající":
            # sloupec se pak už nikdy neschoval a ukazatel zůstal zachycený u slideru,
            # takže po celé obrazovce ukazoval jeho kurzor a nic jiného nereagovalo.
            page.evaluate("() => { if (!autoScrollRunning) toggleAutoScroll(); }")
            page.wait_for_timeout(300)
            najed_na_tlacitko(page)
            box5 = page.evaluate("""() => {
              const r = document.getElementById('auto-scroll-speed').getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            page.mouse.move(box5["x"] + box5["w"] * 0.4, box5["y"] + box5["h"] / 2)
            page.mouse.down()
            page.mouse.move(box5["x"] + box5["w"] * 0.6, box5["y"] + box5["h"] / 2)
            # Puštění se "ztratí": pointerup se pošle mimo okno stránky
            page.evaluate("() => window.dispatchEvent(new Event('blur'))")
            page.wait_for_timeout(300)
            zkontroluj(not page.evaluate("() => autoScrollDragging"),
                       "ztracené puštění tažení ukončí")
            zkontroluj(not page.evaluate(
                "() => document.getElementById('auto-scroll-speed').hasPointerCapture(1)"),
                "a uvolní zachycený ukazatel")
            page.mouse.up()
            page.mouse.move(700, 700)
            page.wait_for_timeout(4000)
            zkontroluj(page.evaluate(schovano),
                       "sloupec se pak zase normálně schová")

            print("\n── podržení tlačítka (jediná cesta na dotyku) ──")
            # Na dotykové obrazovce není najetí, takže by za běhu nešlo ke slideru vůbec:
            # ťuknutí sjíždění zastaví. Podržení proto rozbaluje a ťuknutí se zahodí.
            if not page.evaluate("() => autoScrollRunning"):
                page.mouse.click(tlacitko["x"], tlacitko["y"])
                page.wait_for_timeout(300)
            # Nechat zabalit nečinností, ať je vidět, že rozbalení udělalo podržení
            page.mouse.move(640, 720)
            page.wait_for_timeout(3800)
            page.mouse.move(645, 700)
            page.wait_for_timeout(300)
            zkontroluj(not page.evaluate(rozbaleno), "výchozí stav před podržením je zabalený")

            page.mouse.move(tlacitko["x"], tlacitko["y"])
            page.mouse.down()
            page.wait_for_timeout(700)
            zkontroluj(page.evaluate(rozbaleno), "podržení panel rozbalí")
            page.mouse.up()
            page.wait_for_timeout(400)
            zkontroluj(page.evaluate("() => autoScrollRunning"),
                       "a sjíždění přitom nezastaví")
            zkontroluj(page.evaluate(rozbaleno), "po puštění zůstane rozbalený")

            # Krátké ťuknutí musí dál pauzovat, aby podržení nesebralo obyčejný klik
            page.mouse.click(tlacitko["x"], tlacitko["y"])
            page.wait_for_timeout(400)
            zkontroluj(not page.evaluate("() => autoScrollRunning"),
                       "krátké ťuknutí dál zastavuje")

            print("\n── zastavení ──")
            # Nezávisle na tom, v jakém stavu předchozí sekce skončila
            page.evaluate("() => { if (!autoScrollRunning) toggleAutoScroll(); }")
            page.wait_for_timeout(300)
            page.evaluate("() => document.getElementById('auto-scroll-toggle').click()")
            page.wait_for_timeout(300)
            zkontroluj(not page.evaluate("() => autoScrollRunning"), "druhý klik sjíždění zastaví")
            stalo = page.evaluate("""async () => {
              const wrapper = document.querySelector('.zoom-scroll-wrapper');
              const zacatek = wrapper.scrollTop;
              await new Promise(r => setTimeout(r, 1200));
              return wrapper.scrollTop === zacatek;
            }""")
            zkontroluj(stalo, "a stránka se pak už nehýbe")

            print("\n── konec zpěvníku ──")
            page.evaluate("""() => {
              const wrapper = document.querySelector('.zoom-scroll-wrapper');
              const s = document.getElementById('auto-scroll-speed');
              s.value = 100; s.dispatchEvent(new Event('input'));
              wrapper.scrollTop = wrapper.scrollHeight;
              document.getElementById('auto-scroll-toggle').click();
            }""")
            page.wait_for_timeout(1500)
            zkontroluj(not page.evaluate("() => autoScrollRunning"),
                       "na konci se sjíždění samo vypne")

            print("\n── přepnutí do knižního módu ──")
            do_rolovaciho_modu(page)
            page.evaluate("""() => {
              document.querySelector('.zoom-scroll-wrapper').scrollTop = 0;
              document.getElementById('auto-scroll-toggle').click();
            }""")
            page.wait_for_timeout(400)
            page.evaluate("() => { if (currentMode !== 'double') toggleReadingMode(); }")
            page.wait_for_timeout(600)
            zkontroluj(not page.evaluate("() => autoScrollRunning"),
                       "přepnutí do knižního módu sjíždění ukončí")
            zkontroluj(page.evaluate(
                "() => getComputedStyle(document.getElementById('auto-scroll-button')).display"
            ) == "none", "a tlačítko zase zmizí")

            print("\n── chyby v konzoli ──")
            zkontroluj(not chyby, "žádná chyba JavaScriptu", "; ".join(chyby[:3]))

            # Dotyk se testuje ve vlastním kontextu, protože zapnout ho jde jen při jeho
            # vytvoření. Firefox po ťuknutí nechá na tlačítku zavěšený hover (mouseleave
            # a hned zase mouseenter, a konec) - dokud na něm viselo pozastavení odpočtu,
            # sloupec po prvním ťuknutí nezmizel už nikdy. S myší se to neprojeví.
            print("\n── dotykové zařízení ──")
            dotyk_ctx = browser.new_context(viewport={"width": 1368, "height": 1018},
                                            has_touch=True)
            dp = dotyk_ctx.new_page()
            dotyk_chyby = []
            dp.on("pageerror", lambda e: dotyk_chyby.append(str(e)))
            # Vlastní kontext si nese vlastní cookies, takže se musí přihlásit znovu
            dp.goto(base + "/login")
            dp.fill('input[name="email"]', "admin@test.com")
            dp.fill('input[name="password"]', PASSWORD)
            dp.click('button[type="submit"], input[type="submit"]')
            dp.wait_for_load_state("networkidle")
            dp.goto(base + BOOK)
            dp.wait_for_load_state("networkidle")
            dp.wait_for_timeout(1200)
            do_rolovaciho_modu(dp)
            dt = dp.evaluate("""() => {
              const r = document.getElementById('auto-scroll-toggle').getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""")
            dp.touchscreen.tap(dt["x"], dt["y"])
            dp.wait_for_timeout(400)
            zkontroluj(dp.evaluate("() => autoScrollRunning"), "ťuknutí sjíždění spustí")
            dp.wait_for_timeout(5000)
            zkontroluj(dp.evaluate(
                "() => document.querySelector('.zoom-buttons').classList.contains('fade-hidden')"),
                "po ťuknutí se sloupec časem schová (zavěšený hover ho neblokuje)")
            zkontroluj(dp.evaluate("() => autoScrollRunning"),
                       "a sjíždění přitom běží dál")
            zkontroluj(not dotyk_chyby, "žádná chyba JavaScriptu na dotyku",
                       "; ".join(dotyk_chyby[:3]))
            dotyk_ctx.close()

            browser.close()
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
    sys.exit(main())
