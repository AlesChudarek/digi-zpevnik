#!/usr/bin/env python3
"""Přejímací test správy pro admina (Moje zpěvníky u admina, filtr Cizí v hledání, ...).

Běží na kopii databáze a na klonu obrázků - správa maže zpěvníky i účty, takže na
skutečná data nesmí sáhnout. Klon je na macOS copy-on-write (cp -c).

    python backend/scripts/test_sprava.py
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sprava-"))
DB = TMP / "sprava.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "sprava")

import backend.app as A  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)],
                  capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None   # podproces by běžel nad skutečnými daty
A.app.config["TESTING"] = True

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


def admin_a_cizi():
    """Admin a soukromé zpěvníky, které nejsou jeho ani mu nejsou sdílené."""
    with A.app.app_context():
        admin = A.User.query.filter_by(role="admin").order_by(A.User.id).first()
        sdilene = {r.songbook_id for r in A.UserSongbookAccess.query.filter_by(user_id=admin.id)}
        cizi = [sb.id for sb in A.Songbook.query.filter(A.Songbook.is_public == 0,
                                                        A.Songbook.owner_id != admin.id)
                if sb.id not in sdilene]
        vlastni = [sb.id for sb in A.Songbook.query.filter_by(owner_id=admin.id, is_public=0)]
        return admin.id, cizi, vlastni


def zaloz_adminovi_zpevnik():
    """Lokální admin žádný soukromý zpěvník nemá - bez něj by se nedalo ověřit, že svoje vidí."""
    with A.app.app_context():
        admin = A.User.query.filter_by(role="admin").order_by(A.User.id).first()
        A.db.session.add(A.Songbook(id=f"u{admin.id}-1", title="Adminův test",
                                    owner_id=admin.id, is_public=0))
        A.db.session.commit()


def moje_zpevniky():
    print("\n── Moje zpěvníky u admina ──")
    zaloz_adminovi_zpevnik()
    aid, cizi, vlastni = admin_a_cizi()
    zkontroluj(bool(cizi), "v kopii DB jsou cizí soukromé zpěvníky", len(cizi))
    admin = klient(aid)
    html = admin.get("/my-songbooks").get_data(as_text=True)
    dlazdice = set(re.findall(r'class="songbook-card" data-book-id="([^"]+)"', html))
    videt = [sid for sid in cizi if sid in dlazdice]
    zkontroluj(not videt, "admin v Mých zpěvnících nevidí cizí zpěvníky", videt[:5])
    chybi = [sid for sid in vlastni if sid not in dlazdice]
    zkontroluj(dlazdice and not chybi, "a své vlastní vidí", (len(dlazdice), chybi[:5]))

    polozky = admin.get("/api/my-songbooks/options").get_json()["items"]
    zkontroluj(not [p for p in polozky if p["id"] in cizi],
               "nabídka Přidat do zpěvníku nemá cizí zpěvníky", len(polozky))
    zkontroluj(admin.get(f"/songbook/{cizi[0]}").status_code == 200,
               "cizí zpěvník admin pořád otevře")


def hledani():
    print("\n── Hledání: filtr Cizí ──")
    aid, cizi, _ = admin_a_cizi()
    html = klient(aid).get("/search").get_data(as_text=True)
    zkontroluj('id="t-cizi"' in html and not re.search(r'id="t-cizi"\s+checked', html),
               "admin má filtr Cizí, výchozí vypnutý")
    radky = re.findall(r'data-songbook-id="([^"]+)"[^>]*?data-cizi="([01])"', html)
    oznacene = {sid for sid, c in radky if c == "1"}
    zkontroluj(oznacene and oznacene <= set(cizi),
               "jako cizí jsou označené právě cizí zpěvníky", len(oznacene))
    zkontroluj(not [sid for sid, c in radky if c == "0" and sid in cizi],
               "žádný cizí zpěvník není bez označení")

    with A.app.app_context():
        uzivatel = A.User.query.filter_by(role="user").order_by(A.User.id).first().id
    html = klient(uzivatel).get("/search").get_data(as_text=True)
    zkontroluj('id="t-cizi"' not in html, "běžný uživatel filtr Cizí nemá")
    zkontroluj('data-cizi="1"' not in html, "a nic cizího ve výsledcích")


def post(c, url, telo=None):
    r = c.post(url, json=telo or {})
    return r.status_code, (r.get_json() or {})


def pocet_zasahu():
    with A.app.app_context():
        return A.ZasahAdmina.query.count()


def vsechny_zpevniky():
    print("\n── Všechny zpěvníky: přístup ──")
    aid, cizi, _ = admin_a_cizi()
    with A.app.app_context():
        uzivatel = A.User.query.filter_by(role="user").order_by(A.User.id).first().id
        verejny = A.Songbook.query.filter_by(is_public=1).first().id
    zkontroluj(klient(uzivatel).get("/sprava/zpevniky").status_code == 404,
               "běžný uživatel stránku správy nenajde")
    kod, _ = post(klient(uzivatel), f"/api/sprava/zpevnik/{cizi[0]}/smazat")
    zkontroluj(kod == 403, "a akci správy nesmí", kod)
    with A.app.app_context():
        zkontroluj(A.db.session.get(A.Songbook, cizi[0]) is not None, "zpěvník zůstal")

    admin = klient(aid)
    html = admin.get("/sprava/zpevniky").get_data(as_text=True)
    radky = set(re.findall(r'class="sz-radek" id="z-([^"]+)"', html))
    zkontroluj(set(cizi) <= radky, "admin vidí všechny cizí soukromé zpěvníky", len(radky))
    zkontroluj(verejny not in radky, "veřejné zpěvníky ve správě nejsou")
    kod, _ = post(admin, f"/api/sprava/zpevnik/{verejny}/smazat")
    zkontroluj(kod == 404, "veřejný zpěvník správa smazat nedovolí", kod)
    zkontroluj("Všechny zpěvníky" in admin.get("/dashboard").get_data(as_text=True),
               "admin má v liště odkaz Všechny zpěvníky")
    zkontroluj("Všechny zpěvníky" not in klient(uzivatel).get("/dashboard").get_data(as_text=True),
               "běžný uživatel ho nemá")


def akce_se_zpevniky():
    aid, _, _ = admin_a_cizi()
    admin = klient(aid)
    with A.app.app_context():
        sdileny = (A.Songbook.query.join(A.UserSongbookAccess,
                                         A.UserSongbookAccess.songbook_id == A.Songbook.id)
                   .filter(A.Songbook.is_public == 0).first())
        kniha, vlastnik = sdileny.id, sdileny.owner_id
        clen = A.UserSongbookAccess.query.filter_by(songbook_id=kniha).first().user_id
        oznameni_pred = A.Oznameni.query.filter_by(user_id=clen).count()
        zpevniku_clena = A.Songbook.query.filter_by(owner_id=clen).count()
        dalsi = [u.id for u in A.User.query.filter(A.User.role == "user",
                                                   A.User.id.notin_([vlastnik, clen]))]

    print("\n── odebrání ze společných úprav ──")
    zasahu = pocet_zasahu()
    kod, _ = post(admin, f"/api/sprava/zpevnik/{kniha}/clen/{clen}/odebrat")
    with A.app.app_context():
        zkontroluj(kod == 200 and not A.UserSongbookAccess.query.filter_by(
            songbook_id=kniha, user_id=clen).first(), "člen přišel o přístup", kod)
        zkontroluj(A.Oznameni.query.filter_by(user_id=clen).count() == oznameni_pred,
                   "bez upozornění")
        zkontroluj(A.Songbook.query.filter_by(owner_id=clen).count() == zpevniku_clena,
                   "a bez kopie")
    zkontroluj(pocet_zasahu() == zasahu + 1, "zásah je v záznamu")

    print("\n── zrušení pozvánky ──")
    with A.app.app_context():
        p = A.Pozvanka(songbook_id=kniha, od_id=vlastnik, komu_id=dalsi[0], druh="kopie",
                       vytvoreno=A.datetime.now())
        A.db.session.add(p)
        A.db.session.commit()
        pid = p.id
    html = admin.get("/sprava/zpevniky").get_data(as_text=True)
    zkontroluj(f'data-pozvanka="{pid}"' in html, "pozvánka je v detailu zpěvníku")
    kod, _ = post(admin, f"/api/sprava/pozvanka/{pid}/zrusit")
    with A.app.app_context():
        zkontroluj(kod == 200 and A.db.session.get(A.Pozvanka, pid) is None, "pozvánka zrušená", kod)

    print("\n── převod na jiného vlastníka ──")
    kod, telo = post(admin, f"/api/sprava/zpevnik/{kniha}/prevest", {"komu": vlastnik})
    zkontroluj(kod == 400, "na současného vlastníka převést nejde", telo.get("error"))
    kod, telo = post(admin, f"/api/sprava/zpevnik/{kniha}/prevest", {"komu": dalsi[0], "ponechat": True})
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, kniha)
        pristup = A.UserSongbookAccess.query.filter_by(songbook_id=kniha, user_id=vlastnik).first()
        zkontroluj(kod == 200 and sb.owner_id == dalsi[0], "zpěvník má nového vlastníka", (kod, telo))
        zkontroluj(pristup is not None and pristup.permission == "edit",
                   "původní vlastník zůstal členem společných úprav")
    # Zpátky na původního, tentokrát bez ponechání - ten byl členem, takže členem být přestane.
    kod, _ = post(admin, f"/api/sprava/zpevnik/{kniha}/prevest", {"komu": vlastnik})
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, kniha)
        zkontroluj(kod == 200 and sb.owner_id == vlastnik, "převod zpátky", kod)
        zkontroluj(not A.UserSongbookAccess.query.filter_by(songbook_id=kniha, user_id=vlastnik).first(),
                   "nový vlastník už není zároveň členem")
        zkontroluj(not A.UserSongbookAccess.query.filter_by(songbook_id=kniha, user_id=dalsi[0]).first(),
                   "bez ponechání původní vlastník o zpěvník přišel")
    kod, _ = post(admin, f"/api/sprava/zpevnik/{kniha}/prevest", {"komu": 99999})
    zkontroluj(kod == 400, "neexistující uživatel", kod)

    print("\n── kopie k sobě ──")
    kod, telo = post(admin, f"/api/sprava/zpevnik/{kniha}/kopie")
    with A.app.app_context():
        kopie = A.db.session.get(A.Songbook, telo.get("id"))
        zkontroluj(kod == 200 and kopie is not None and kopie.owner_id == aid
                   and kopie.title.endswith("(kopie)"), "kopie patří adminovi", (kod, telo))
        zkontroluj(len(kopie.strany) == len(A.db.session.get(A.Songbook, kniha).strany),
                   "se všemi stranami")

    print("\n── smazání ──")
    kod, _ = post(admin, f"/api/sprava/zpevnik/{kniha}/smazat")
    with A.app.app_context():
        zkontroluj(kod == 200 and A.db.session.get(A.Songbook, kniha) is None, "zpěvník smazaný", kod)

    print("\n── záznam zásahů ──")
    with A.app.app_context():
        akce = [z.akce for z in A.ZasahAdmina.query.order_by(A.ZasahAdmina.id)]
    ocekavane = ["odebral ze společného zpěvníku", "zrušil pozvánku", "převedl zpěvník",
                 "převedl zpěvník", "zkopíroval si zpěvník", "smazal zpěvník"]
    zkontroluj(akce == ocekavane, "zapsané všechny zásahy, nepovedené ne", akce)
    html = admin.get("/sprava/zaznam").get_data(as_text=True)
    zkontroluj(html.count("<tr>") == len(ocekavane) + 1, "stránka záznamu je ukazuje")
    zkontroluj(klient(clen).get("/sprava/zaznam").status_code == 404, "běžný uživatel záznam nevidí")


def prohlizec():
    """Stránka v prohlížeči: filtry, rozbalení, okno převodu, telefon a lišta admina.
    Jen čte - server běží nad vlastní kopií databáze a na obrázky nesahá."""
    sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))
    import test_napovedy as T
    from playwright.sync_api import sync_playwright

    kopie = TMP / "prohlizec.db"
    with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
            sqlite3.connect(str(kopie)) as cil:
        zdroj.backup(cil)
    srv, base = T.start_server(kopie)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            chyby = []
            page.on("pageerror", lambda e: chyby.append(str(e)))
            page.goto(base + "/login")
            page.fill('input[name="email"]', "admin@test.com")
            page.fill('input[name="password"]', T.PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            print("\n── prohlížeč: lišta admina ──")
            for sirka in range(821, 1281, 20):
                page.set_viewport_size({"width": sirka, "height": 800})
                stav = page.evaluate("""() => {
                    const odkazy = [...document.querySelectorAll('.nav-links a')]
                        .filter(a => getComputedStyle(a).display !== 'none');
                    const r = odkazy.map(a => a.getBoundingClientRect());
                    return {radky: new Set(r.map(x => Math.round(x.top))).size,
                            presah: document.documentElement.scrollWidth - innerWidth,
                            vpravo: Math.max(...r.map(x => x.right)),
                            ucet: document.querySelector('.navbar .right').getBoundingClientRect().left};
                }""")
                if stav["radky"] > 1 or stav["presah"] > 0 or stav["vpravo"] > stav["ucet"]:
                    zkontroluj(False, f"{sirka} px: odkazy lišty na jednom řádku a mimo účet", stav)
                    break
            else:
                zkontroluj(True, "821-1280 px: odkazy lišty admina se vejdou na jeden řádek")

            print("\n── prohlížeč: Všechny zpěvníky ──")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto(base + "/sprava/zpevniky")
            page.wait_for_load_state("networkidle")
            vsech = page.locator(".sz-radek").count()
            zkontroluj(vsech > 3, "seznam má zpěvníky", vsech)
            page.check("#sz-sdilene")
            sdilenych = page.locator(".sz-radek:not([hidden])").count()
            zkontroluj(0 < sdilenych < vsech, "filtr Jen sdílené zúží seznam", (sdilenych, vsech))
            page.uncheck("#sz-sdilene")
            nazev = page.locator(".sz-radek").first.get_attribute("data-nazev")
            page.fill("#sz-hledat", nazev.upper())
            zkontroluj(page.locator(".sz-radek:not([hidden])").count() >= 1
                       and page.locator(".sz-radek:not([hidden])").count() < vsech,
                       "hledání podle názvu (bez ohledu na velikost písmen)")
            page.fill("#sz-hledat", "")
            page.click('[data-razeni="stran"]')
            stran = page.eval_on_selector_all(".sz-radek", "r => r.map(x => +x.dataset.stran)")
            zkontroluj(stran == sorted(stran, reverse=True), "řazení podle počtu stran", stran[:6])

            radek = page.locator(".sz-radek").first
            radek.locator(".sz-souhrn").click()
            zkontroluj(radek.locator(".sz-detail").is_visible(), "klik rozbalí detail s akcemi")
            radek.locator('[data-akce="prevest"]').click()
            okno = page.locator(".oznameni-zaclona .okno")
            zkontroluj(okno.is_visible(), "Převést otevře okno")
            moznosti = okno.locator("option").count()
            zkontroluj(moznosti > 2, "s výběrem uživatelů", moznosti)
            zkontroluj(okno.locator('[data-volba="ok"]').is_disabled(), "Převést čeká na výběr")
            page.keyboard.press("Escape")
            zkontroluj(page.locator(".oznameni-zaclona").count() == 0, "Esc okno zavře")
            zkontroluj(radek.locator(".sz-detail").is_visible(), "a detail zůstane otevřený")

            print("\n── prohlížeč: Upravit ze správy ──")
            zid = radek.get_attribute("data-id")
            radek.locator('.sz-akce a', has_text="Upravit").click()
            page.wait_for_function(
                "() => (document.getElementById('nb-name') || {}).value", timeout=10000)
            zkontroluj(page.input_value("#nb-name") == nazev,
                       "editor se otevře s cizím zpěvníkem", page.input_value("#nb-name"))
            zkontroluj("upravit=" not in page.url, "parametr z adresy zmizí", page.url)
            page.click("#nb-zavrit")
            page.wait_for_url("**/sprava/zpevniky*", timeout=10000)
            page.wait_for_load_state("networkidle")
            zkontroluj(page.locator(f'[id="z-{zid}"] .sz-detail').is_visible(),
                       "zavření vrátí do správy na tentýž zpěvník", page.url)

            print("\n── prohlížeč: telefon ──")
            for sirka in (320, 390):
                page.set_viewport_size({"width": sirka, "height": 740})
                page.goto(base + "/sprava/zpevniky")
                page.wait_for_load_state("networkidle")
                page.locator(".sz-souhrn").first.click()
                stav = page.evaluate("""() => {
                    const p = document.querySelector('.sprava-panel').getBoundingClientRect();
                    const ven = [...document.querySelectorAll('.sprava-panel *')].filter(e => {
                        const r = e.getBoundingClientRect();
                        return r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5);
                    }).map(e => e.className || e.tagName);
                    return {presah: document.documentElement.scrollWidth - innerWidth, ven: ven.slice(0, 5),
                            hlavicka: getComputedStyle(document.querySelector('.sz-hlavicka')).display};
                }""")
                zkontroluj(stav["presah"] <= 0 and not stav["ven"] and stav["hlavicka"] == "none",
                           f"{sirka} px: kartičky, nic nepřetéká", stav)
            zkontroluj(not chyby, "bez chyb v JavaScriptu", chyby[:3])
            browser.close()
    finally:
        srv.terminate()


def main():
    moje_zpevniky()
    hledani()
    vsechny_zpevniky()
    akce_se_zpevniky()
    prohlizec()
    print()
    if selhani:
        print(f"❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("✅ Vše prošlo")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
