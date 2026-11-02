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
A.PROFILY_DIR = TMP / "profily"     # mazání fotky nesmí sahat na skutečné profilovky
A.PROFILY_DIR.mkdir()
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None   # podproces by běžel nad skutečnými daty
A.app.config["TESTING"] = True

selhani = []


def zkontroluj(podminka, popis, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def klient(uid):
    """Přihlášený klient. Id i s verzí přihlášení - jiné testy ji účtům zvyšují."""
    with A.app.app_context():
        u = A.db.session.get(A.User, uid)
        id_relace = u.get_id() if u else str(uid)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = id_relace
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
    zkontroluj('href="/sprava/zpevniky">Správa</a>' in admin.get("/dashboard").get_data(as_text=True),
               "admin má v liště odkaz Správa")
    zkontroluj("/sprava/" not in klient(uzivatel).get("/dashboard").get_data(as_text=True),
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

    print("\n── staré cesty bez záznamu jsou adminovi u cizích zavřené ──")
    r = admin.delete(f"/api/my-songbooks/{kniha}")
    with A.app.app_context():
        zkontroluj(r.status_code == 403 and A.db.session.get(A.Songbook, kniha) is not None,
                   "cizí soukromý zpěvník přes Moje zpěvníky nesmaže", r.status_code)
    kod, _ = post(admin, f"/api/zpevnik/{kniha}/clenove/{clen}/odebrat")
    zkontroluj(kod == 403, "člena přes okno sdílení neodebere", kod)
    zasahu = pocet_zasahu()
    r = admin.post(f"/api/zpevnik/{kniha}", data={"nazev": "Upraveno správcem"},
                   content_type="multipart/form-data")
    with A.app.app_context():
        posledni = A.ZasahAdmina.query.order_by(A.ZasahAdmina.id.desc()).first()
        zkontroluj(r.status_code == 200 and pocet_zasahu() == zasahu + 1
                   and posledni.akce == "upravil zpěvník" and posledni.zpevnik == "Upraveno správcem",
                   "úprava cizího zpěvníku v editoru je v záznamu", (r.status_code, posledni and posledni.akce))
        vlastni_admina = A.Songbook.query.filter_by(owner_id=aid, is_public=0).first().id
    admin.post(f"/api/zpevnik/{vlastni_admina}", data={"nazev": "Můj"}, content_type="multipart/form-data")
    zkontroluj(pocet_zasahu() == zasahu + 1, "úprava vlastního zpěvníku ne")

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
    ocekavane = ["upravil zpěvník", "odebral ze společného zpěvníku", "zrušil pozvánku", "převedl zpěvník",
                 "převedl zpěvník", "zkopíroval si zpěvník", "smazal zpěvník"]
    zkontroluj(akce == ocekavane, "zapsané všechny zásahy, nepovedené ne", akce)
    html = admin.get("/sprava/zaznam").get_data(as_text=True)
    zkontroluj(html.count("<tr>") == len(ocekavane) + 1, "stránka záznamu je ukazuje")
    zkontroluj(klient(clen).get("/sprava/zaznam").status_code == 404, "běžný uživatel záznam nevidí")


def uzivatele():
    print("\n── Uživatelé: přehled ──")
    from werkzeug.security import generate_password_hash
    aid, _, _ = admin_a_cizi()
    admin = klient(aid)
    with A.app.app_context():
        u = A.User.query.filter_by(email="user@test.com").first()
        uid = u.id
        u.password = generate_password_hash("heslo-sprava", method="pbkdf2:sha256")
        u.fotka = "test-fotka.webp"
        u.role, u.email_verified, u.zablokovan, u.limit_mista = "user", False, False, None
        (A.PROFILY_DIR / u.fotka).write_bytes(b"x")
        host = A.User.query.filter_by(role="guest").first()
        A.db.session.commit()
        host_id = host.id if host else None
        zabrano = A.zabrane_misto(uid)
    zkontroluj(klient(uid).get("/sprava/uzivatele").status_code == 404,
               "běžný uživatel stránku Uživatelé nenajde")
    html = admin.get("/sprava/uzivatele").get_data(as_text=True)
    radky = set(re.findall(r'class="sz-radek" id="u-(\d+)"', html))
    zkontroluj(str(uid) in radky and str(aid) in radky, "seznam má uživatele i admina", len(radky))
    zkontroluj(host_id is None or str(host_id) not in radky, "host v seznamu není")
    zkontroluj('href="/sprava/uzivatele"' in admin.get("/sprava/zpevniky").get_data(as_text=True),
               "záložka Uživatelé ve správě")

    def uprav(akce, hodnota=None, kdo=uid):
        return post(admin, f"/api/sprava/uzivatel/{kdo}", {"akce": akce, "hodnota": hodnota})

    def ja():
        with A.app.app_context():
            return A.db.session.get(A.User, uid)

    print("\n── Uživatelé: úpravy ──")
    zasahu = pocet_zasahu()
    kod, _ = uprav("role", "premium")
    zkontroluj(kod == 200 and ja().role == "premium", "změna role", kod)
    kod, _ = uprav("role", "bubak")
    zkontroluj(kod == 400, "neznámá role neprojde", kod)
    kod, _ = uprav("role", "user", kdo=aid)
    with A.app.app_context():
        zkontroluj(kod == 400 and A.db.session.get(A.User, aid).role == "admin",
                   "vlastní roli admin nezmění", kod)
    uprav("role", "user")
    kod, _ = uprav("overit")
    zkontroluj(kod == 200 and ja().email_verified, "ověření e-mailu", kod)
    kod, _ = uprav("prezdivka", "  Nová přezdívka  ")
    zkontroluj(kod == 200 and ja().prezdivka == "Nová přezdívka", "přezdívka", ja().prezdivka)
    kod, _ = uprav("fotka")
    zkontroluj(kod == 200 and ja().fotka is None and not (A.PROFILY_DIR / "test-fotka.webp").exists(),
               "smazání fotky i se souborem", kod)

    print("\n── Uživatelé: limit místa ──")
    kod, _ = uprav("limit", "abc")
    zkontroluj(kod == 400, "nesmyslný limit neprojde", kod)
    mensi = max(1, zabrano // (1024 * 1024) - 1)
    kod, _ = uprav("limit", str(mensi))
    zkontroluj(kod == 200 and ja().limit_mista == mensi * 1024 * 1024, "vlastní limit", ja().limit_mista)
    with A.app.app_context():
        zkontroluj(A.limit_uzivatele(uid) == mensi * 1024 * 1024, "limit_uzivatele ho vrací")
    with A.app.app_context():
        cizi = A.Songbook.query.filter(A.Songbook.is_public == 0, A.Songbook.owner_id != uid).first().id
    kod, telo = post(admin, f"/api/sprava/zpevnik/{cizi}/prevest", {"komu": uid})
    with A.app.app_context():
        zkontroluj(kod == 413 and A.db.session.get(A.Songbook, cizi).owner_id != uid,
                   "převod přes jeho limit neprojde", (kod, telo.get("error", "")[:80]))
    html = klient(uid).get("/my-songbooks").get_data(as_text=True)
    zkontroluj(A.mb(mensi * 1024 * 1024) in html, "uživatel vidí svůj limit", A.mb(mensi * 1024 * 1024))
    kod, _ = uprav("limit", " 1,5 MB")
    zkontroluj(kod == 200 and ja().limit_mista == int(1.5 * 1024 * 1024), "limit s čárkou i jednotkou",
               ja().limit_mista)
    kod, _ = uprav("limit", "")
    zkontroluj(kod == 200 and ja().limit_mista is None, "prázdný limit = výchozí")

    print("\n── Uživatelé: blokování ──")
    prihlaseny = klient(uid)
    zkontroluj(prihlaseny.get("/my-songbooks").status_code == 200, "před zablokováním funguje")
    kod, _ = uprav("zablokovat")
    zkontroluj(kod == 200 and ja().zablokovan, "zablokování", kod)
    r = prihlaseny.get("/my-songbooks")
    zkontroluj(r.status_code == 302 and "/login" in r.headers.get("Location", ""),
               "už přihlášená relace skončí na přihlášení", r.status_code)
    zkontroluj(prihlaseny.get("/my-songbooks").status_code == 302, "a zůstane odhlášená")
    kod, telo = post(klient(uid), "/api/zpevnik/x/kopie")
    zkontroluj(kod == 403 and "zablokovaný" in telo.get("error", ""), "API odmítne", (kod, telo))
    novy = A.app.test_client()
    r = novy.post("/login", data={"email": "user@test.com", "password": "heslo-sprava"})
    zkontroluj(r.status_code == 200 and "zablokovaný" in r.get_data(as_text=True),
               "přihlásit se nejde, se správným heslem hláška")
    zkontroluj(novy.get("/my-songbooks").status_code == 302, "a dovnitř se nedostane")
    r = A.app.test_client().post("/login", data={"email": "user@test.com", "password": "spatne"})
    zkontroluj("zablokovaný" not in r.get_data(as_text=True), "se špatným heslem se o bloku nedozví")
    kod, _ = uprav("zablokovat", kdo=aid)
    zkontroluj(kod == 400, "správce zablokovat nejde", kod)
    kod, _ = uprav("odblokovat")
    zkontroluj(kod == 200 and not ja().zablokovan, "odblokování", kod)

    print("\n── Uživatelé: registrace a přihlášení ──")
    pred = A.datetime.now().replace(microsecond=0)
    r = A.app.test_client().post("/login", data={"email": "user@test.com", "password": "heslo-sprava"})
    zkontroluj(r.status_code == 302 and ja().posledni_prihlaseni and ja().posledni_prihlaseni >= pred,
               "přihlášení zapíše čas", ja().posledni_prihlaseni)
    A.posli_overovaci_email = lambda u: True
    A.app.test_client().post("/register", data={"email": "novy-sprava@test.cz",
                                                "password": "Heslo-sprava-123", "password2": "Heslo-sprava-123"})
    with A.app.app_context():
        n = A.User.query.filter_by(email="novy-sprava@test.cz").first()
        zkontroluj(n is not None and n.vytvoreno and n.vytvoreno >= pred, "registrace zapíše datum",
                   n and n.vytvoreno)

    with A.app.app_context():
        akce = [z.akce for z in A.ZasahAdmina.query.order_by(A.ZasahAdmina.id)][zasahu:]
    zkontroluj(akce == ["změnil roli", "změnil roli", "ověřil e-mail", "změnil přezdívku",
                        "smazal fotku", "změnil limit místa", "změnil limit místa", "změnil limit místa",
                        "zablokoval účet", "odblokoval účet"], "úpravy jsou v záznamu", akce)


def odhlaseni():
    print("\n── odhlášení ze všech zařízení ──")
    aid, _, _ = admin_a_cizi()
    admin = klient(aid)
    with A.app.app_context():
        uid = A.User.query.filter_by(email="user2@test.com").first().id
        from werkzeug.security import generate_password_hash
        A.db.session.get(A.User, uid).password = generate_password_hash("heslo-1-Sprava", method="pbkdf2:sha256")
        A.db.session.commit()

    def prihlas(heslo="heslo-1-Sprava"):
        c = A.app.test_client()
        r = c.post("/login", data={"email": "user2@test.com", "password": heslo})
        return c if r.status_code == 302 else None

    def uvnitr(c):
        return c.get("/my-songbooks").status_code == 200

    stara_relace = A.app.test_client()   # relace z doby před verzí: jen id
    with stara_relace.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    pocitac, telefon = prihlas(), prihlas()
    zkontroluj(pocitac and telefon and uvnitr(pocitac) and uvnitr(telefon) and uvnitr(stara_relace),
               "přihlášený na dvou zařízeních i ve staré relaci")
    kod, _ = post(admin, f"/api/sprava/uzivatel/{uid}", {"akce": "odhlasit"})
    zkontroluj(kod == 200, "admin odhlásí ze všech zařízení", kod)
    zkontroluj(not uvnitr(pocitac) and not uvnitr(telefon) and not uvnitr(stara_relace),
               "všechny relace skončily, i ta stará")
    znovu = prihlas()
    zkontroluj(znovu and uvnitr(znovu), "přihlásit se jde hned znovu")
    kod, _ = post(admin, f"/api/sprava/uzivatel/{aid}", {"akce": "odhlasit"})
    zkontroluj(kod == 400 and uvnitr(admin), "sám sebe admin takhle neodhlásí", kod)

    print("\n── změna hesla odhlásí ostatní zařízení ──")
    druhe = prihlas()
    r = znovu.post("/profil/zmena-hesla", json={"stare": "heslo-1-Sprava", "nove": "heslo-2-Sprava",
                                                "nove2": "heslo-2-Sprava"})
    zkontroluj(r.status_code == 200 and (r.get_json() or {}).get("ok"), "heslo změněno", r.get_json())
    zkontroluj(uvnitr(znovu), "zařízení, kde se heslo měnilo, zůstane přihlášené")
    zkontroluj(not uvnitr(druhe), "ostatní zařízení vypadnou")

    print("\n── obnova hesla přes e-mail odhlásí všude ──")
    with A.app.app_context():
        from backend.tokeny import token_hesla
        token = token_hesla(A.db.session.get(A.User, uid))
    r = A.app.test_client().post(f"/obnova-hesla/{token}", data={"password": "heslo-3-Sprava"})
    zkontroluj(r.status_code == 302, "nové heslo nastavené", r.status_code)
    zkontroluj(not uvnitr(znovu), "přihlášené zařízení vypadne")
    zkontroluj(prihlas("heslo-3-Sprava") is not None, "s novým heslem se přihlásí")
    with A.app.app_context():
        akce = A.ZasahAdmina.query.order_by(A.ZasahAdmina.id.desc()).first().akce
    zkontroluj(akce == "odhlásil ze všech zařízení", "zásah je v záznamu", akce)


def prihlaseni_jako():
    print("\n── přihlášení jako uživatel ──")
    aid, _, _ = admin_a_cizi()
    with A.app.app_context():
        u = A.User.query.filter_by(email="user@test.com").first()
        uid = u.id
        u.zablokovan = False
        jeho = {sb.id for sb in A.Songbook.query.filter_by(owner_id=uid, is_public=0)}
        jiny = A.User.query.filter(A.User.role == "user", A.User.id != uid).first().id
        A.db.session.commit()
    kod, _ = post(klient(jiny), f"/api/sprava/uzivatel/{uid}/prihlasit")
    zkontroluj(kod == 403, "běžný uživatel se za jiného nepřihlásí", kod)
    admin = klient(aid)
    kod, _ = post(admin, f"/api/sprava/uzivatel/{aid}/prihlasit")
    zkontroluj(kod == 400, "za správce ani sám za sebe ne", kod)

    kod, telo = post(admin, f"/api/sprava/uzivatel/{uid}/prihlasit")
    zkontroluj(kod == 200 and telo.get("kam") == "/my-songbooks", "admin se přihlásí jako uživatel", (kod, telo))
    html = admin.get("/my-songbooks").get_data(as_text=True)
    dlazdice = set(re.findall(r'class="songbook-card" data-book-id="([^"]+)"', html))
    zkontroluj(jeho and jeho <= dlazdice, "vidí jeho Moje zpěvníky", (len(jeho), len(dlazdice)))
    zkontroluj("pruh-spravce" in html and "Zpět do svého účtu" in html, "pruh s návratem")
    zkontroluj(admin.get("/sprava/zpevniky").status_code == 404, "správa je teď nedostupná (je to on)")
    r = admin.post("/profil/zmena-hesla", json={"stare": "x", "nove": "Nove-heslo-123", "nove2": "Nove-heslo-123"})
    zkontroluj(r.status_code == 403, "změna hesla zamčená", r.status_code)
    r = admin.post("/profil/email", json={"email": "jiny@test.cz", "heslo": "x"})
    zkontroluj(r.status_code == 403, "změna e-mailu zamčená", r.status_code)

    r = admin.post("/sprava/zpet")
    zkontroluj(r.status_code == 302 and r.headers["Location"].endswith(f"/sprava/uzivatele#u-{uid}"),
               "návrat do správy na řádek uživatele", r.headers.get("Location"))
    zkontroluj(admin.get("/sprava/zpevniky").status_code == 200, "a je zase admin")
    zkontroluj("pruh-spravce" not in admin.get("/my-songbooks").get_data(as_text=True), "pruh zmizel")
    zkontroluj(admin.post("/sprava/zpet").status_code == 302
               and admin.get("/sprava/zpevniky").status_code == 200, "druhý návrat adminovi neublíží")

    print("\n── přihlášení jako: pojistky ──")
    post(admin, f"/api/sprava/uzivatel/{uid}/prihlasit")
    with A.app.app_context():
        A.odhlas_vsude(A.db.session.get(A.User, aid))
        A.db.session.commit()
    r = admin.post("/sprava/zpet")
    zkontroluj(r.status_code == 302 and "/login" in r.headers["Location"],
               "admin odhlášený mezitím ze všech zařízení se zpátky nedostane", r.headers.get("Location"))
    zkontroluj(admin.get("/my-songbooks").status_code == 302, "ani v účtu uživatele nezůstane")
    with A.app.app_context():
        aid_klient = A.db.session.get(A.User, aid).get_id()
    admin = A.app.test_client()
    with admin.session_transaction() as sess:
        sess["_user_id"] = aid_klient
        sess["_fresh"] = True
    post(admin, f"/api/sprava/uzivatel/{uid}/prihlasit")
    admin.post("/logout")
    with admin.session_transaction() as sess:
        zkontroluj("spravce_zpet" not in sess, "odhlášení zapomene i návrat do správy")
    with A.app.app_context():
        akce = [z.akce for z in A.ZasahAdmina.query.order_by(A.ZasahAdmina.id.desc()).limit(4)][::-1]
    zkontroluj(akce == ["přihlásil se jako uživatel", "vrátil se z účtu uživatele",
                        "přihlásil se jako uživatel", "přihlásil se jako uživatel"],
               "začátky a návraty v záznamu", akce)


def smazani_uctu():
    aid, _, _ = admin_a_cizi()
    admin = klient(aid)

    def stav(email):
        with A.app.app_context():
            u = A.User.query.filter_by(email=email).first()
            if u is None:
                return None
            vlastni = [sb.id for sb in A.Songbook.query.filter_by(owner_id=u.id)]
            clenove = {sid: [a.user_id for a in A.UserSongbookAccess.query.filter_by(songbook_id=sid)]
                       for sid in vlastni}
            return {"id": u.id, "vlastni": vlastni, "clenove": clenove}

    def oznameni(uids):
        with A.app.app_context():
            return A.Oznameni.query.filter(A.Oznameni.user_id.in_(uids)).count()

    print("\n── smazání účtu adminem: pojistky ──")
    s1 = stav("user@test.com")
    kod, _ = post(klient(s1["id"]), f"/api/sprava/uzivatel/{s1['id']}/smazat", {"potvrzeni": "user@test.com"})
    zkontroluj(kod == 403, "běžný uživatel mazat nesmí", kod)
    kod, _ = post(admin, f"/api/sprava/uzivatel/{aid}/smazat", {"potvrzeni": "admin@test.com"})
    zkontroluj(kod == 400, "účet správce ne", kod)
    kod, _ = post(admin, f"/api/sprava/uzivatel/{s1['id']}/smazat", {"potvrzeni": "user@test"})
    zkontroluj(kod == 400 and stav("user@test.com"), "bez přesného e-mailu nic", kod)

    print("\n── smazání účtu se zpěvníky (jako by ho zrušil sám) ──")
    clenove = sorted({c for cl in s1["clenove"].values() for c in cl})
    zkontroluj(s1["vlastni"] and clenove, "má zpěvníky i členy společných úprav",
               (len(s1["vlastni"]), clenove))
    pred = oznameni(clenove)
    kod, _ = post(admin, f"/api/sprava/uzivatel/{s1['id']}/smazat", {"potvrzeni": "User@Test.com "})
    with A.app.app_context():
        zbylo = A.Songbook.query.filter(A.Songbook.id.in_(s1["vlastni"])).count()
    zkontroluj(kod == 200 and stav("user@test.com") is None, "účet je pryč", kod)
    zkontroluj(zbylo == 0, "jeho zpěvníky taky", zbylo)
    zkontroluj(oznameni(clenove) > pred, "členové dostali kopii s upozorněním", (pred, oznameni(clenove)))

    print("\n── smazání účtu s převodem zpěvníků ke správci ──")
    with A.app.test_request_context():
        # Svůj zpěvník smazaly už předchozí testy - založit mu nový, i s členem.
        u2 = A.User.query.filter_by(email="user2@test.com").first()
        zdroj = A.Songbook.query.filter(A.Songbook.is_public == 0).first()
        kopie = A.kopiruj_zpevnik(zdroj, u2.id, "Převáděný zpěvník")
        clen = A.User.query.filter(A.User.role == "user", A.User.id != u2.id).first().id
        A.db.session.add(A.UserSongbookAccess(user_id=clen, songbook_id=kopie.id, permission="edit"))
        A.db.session.commit()
    s2 = stav("user2@test.com")
    zkontroluj(bool(s2["vlastni"]) and any(s2["clenove"].values()), "má vlastní zpěvník se členem", s2)
    kod, _ = post(admin, f"/api/sprava/uzivatel/{s2['id']}/smazat",
                  {"potvrzeni": "user2@test.com", "prevest": True})
    with A.app.app_context():
        knihy = [A.db.session.get(A.Songbook, sid) for sid in s2["vlastni"]]
        zkontroluj(kod == 200 and stav("user2@test.com") is None, "účet je pryč", kod)
        zkontroluj(all(k is not None and k.owner_id == aid for k in knihy), "zpěvníky patří správci")
        zkontroluj(all([a.user_id for a in A.UserSongbookAccess.query.filter_by(songbook_id=k.id)]
                       == s2["clenove"][k.id] for k in knihy), "členové u nich zůstali")
        zasah = A.ZasahAdmina.query.order_by(A.ZasahAdmina.id.desc()).first()
        zkontroluj(zasah.akce == "smazal účet" and "převedeny" in (zasah.podrobnosti or ""),
                   "zásah je v záznamu", (zasah.akce, zasah.podrobnosti))

    print("\n── uživatel si účet zruší sám (stejná cesta) ──")
    s3 = stav("user3@test.com")
    with A.app.app_context():
        A.db.session.add(A.ExportPokus(user_id=s3["id"], den="2026-11-02", pocet=3))
        A.db.session.commit()
    c = klient(s3["id"])
    r = c.post("/smazat-ucet", data={"potvrzeni": "user3@test.com"})
    zkontroluj(r.status_code == 302 and r.headers["Location"].endswith("/")
               and stav("user3@test.com") is None, "zrušení vlastního účtu", r.status_code)
    with A.app.app_context():
        zkontroluj(A.ExportPokus.query.filter_by(user_id=s3["id"]).count() == 0,
                   "s účtem zmizí i jeho počty skládání")

    print("\n── admin přihlášený jako uživatel jeho účet zruší ──")
    s4 = stav("user4@test.com")
    post(admin, f"/api/sprava/uzivatel/{s4['id']}/prihlasit")
    r = admin.post("/smazat-ucet", data={"potvrzeni": "user4@test.com"})
    zkontroluj(r.status_code == 302 and r.headers["Location"].endswith("/sprava/uzivatele")
               and stav("user4@test.com") is None, "účet zrušený, zpátky ve správě", r.headers.get("Location"))
    zkontroluj(admin.get("/sprava/uzivatele").status_code == 200, "a je zase admin")
    with A.app.app_context():
        zasah = A.ZasahAdmina.query.order_by(A.ZasahAdmina.id.desc()).first()
    zkontroluj(zasah.akce == "smazal účet (přihlášený jako uživatel)" and "user4" in (zasah.komu or ""),
               "zásah je v záznamu", (zasah.akce, zasah.komu))


def zamky():
    print("\n── zámky: příprava ──")
    aid, _, _ = admin_a_cizi()
    with A.app.test_request_context():
        ucty = {}
        for jmeno in ("x", "y", "z"):
            u = A.User(email=f"zamek-{jmeno}@test.cz", password="x", role="user", email_verified=True)
            A.db.session.add(u)
            A.db.session.flush()
            ucty[jmeno] = u.id
        zdroje = sorted(A.Songbook.query.filter(A.Songbook.is_public == 0).all(),
                        key=lambda sb: -A.cena_zpevniku([sb.id]))
        zdroje = [sb for sb in zdroje if A.cena_zpevniku([sb.id]) > 0]
        velka = A.kopiruj_zpevnik(zdroje[0], ucty["y"], "Rodinný zpěvník")
        vlastni1 = A.kopiruj_zpevnik(zdroje[0], ucty["x"], "Velký vlastní")
        vlastni2 = A.kopiruj_zpevnik(zdroje[-1], ucty["x"], "Malý vlastní")
        for kdo in ("x", "z"):
            A.db.session.add(A.UserSongbookAccess(user_id=ucty[kdo], songbook_id=velka.id, permission="edit"))
        A.db.session.commit()
        ids = {"rodina": velka.id, "v1": vlastni1.id, "v2": vlastni2.id}
        ceny = {k: A.cena_zpevniku([v]) for k, v in ids.items()}
        verejna_pisen = A.db.session.execute(A.text(
            "SELECT ps.song_id FROM pisne_na_strane ps JOIN strany s ON s.id = ps.strana_id "
            "JOIN songbooks b ON b.id = s.songbook_id WHERE b.is_public = 1 LIMIT 1")).scalar()
    zkontroluj(ceny["v1"] > ceny["v2"] > 0, "zpěvníky různé velikosti", ceny)

    def nastav_limit(kdo, bajtu):
        with A.app.app_context():
            A.db.session.get(A.User, ucty[kdo]).limit_mista = bajtu
            A.db.session.commit()

    def zamcene(kdo):
        with A.app.app_context():
            return list(A.zamcene_zpevniky(A.db.session.get(A.User, ucty[kdo])))

    celkem_x = ceny["rodina"] + ceny["v1"] + ceny["v2"]
    print("\n── zámky: pořadí ──")
    nastav_limit("x", celkem_x)
    zkontroluj(zamcene("x") == [], "v limitu nic zamčené")
    nastav_limit("x", celkem_x - 1)
    zkontroluj(zamcene("x") == [ids["rodina"]], "o bajt přes: zamkne se sdílený", zamcene("x"))
    nastav_limit("x", ceny["v2"])
    zkontroluj(zamcene("x") == [ids["rodina"], ids["v1"]],
               "hodně přes: sdílený, pak vlastní od největšího, malý zůstane", zamcene("x"))

    print("\n── zámky: co zamčený zpěvník znamená ──")
    nastav_limit("x", celkem_x - 1)
    x, y, z = klient(ucty["x"]), klient(ucty["y"]), klient(ucty["z"])
    r = x.get(f"/songbook/{ids['rodina']}")
    zkontroluj(r.status_code == 302 and r.headers["Location"].endswith("/my-songbooks"),
               "zamčený se neotevře, vrátí do Mých zpěvníků", r.status_code)
    zkontroluj(x.get(f"/songbook/{ids['v1']}").status_code == 200, "nezamčený ano")
    html = x.get("/my-songbooks").get_data(as_text=True)
    zkontroluj(f'songbook-card zamceny" data-book-id="{ids["rodina"]}"' in html
               and "lock.svg" in html, "šedá dlaždice se zámkem")
    zkontroluj("zamčené zpěvníky: 1" in html, "v nabídce účtu počet zamčených")
    polozky = [p["id"] for p in x.get("/api/my-songbooks/options").get_json()["items"]]
    zkontroluj(ids["rodina"] not in polozky and ids["v1"] in polozky, "není v nabídce Přidat do zpěvníku")
    zkontroluj(f'data-songbook-id="{ids["rodina"]}"' not in x.get("/search").get_data(as_text=True),
               "není v hledání")
    r = x.post(f"/api/zpevnik/{ids['rodina']}", data={"nazev": "x"}, content_type="multipart/form-data")
    zkontroluj(r.status_code == 404, "neupraví ho", r.status_code)
    kod, telo = post(x, f"/api/songbooks/{ids['v2']}/add-song", {"song_id": verejna_pisen})
    zkontroluj(kod == 413, "přes limit nepřidá nic ani do vlastního", (kod, telo.get("error", "")[:60]))

    print("\n── zámky: člen přes limit rodinu neblokuje ──")
    kod, telo = post(y, f"/api/songbooks/{ids['rodina']}/add-song", {"song_id": verejna_pisen})
    zkontroluj(kod == 200, "vlastník přidá, i když je člen přes limit", (kod, telo))
    with A.app.app_context():
        zabrano_y = A.zabrane_misto(ucty["y"])
    nastav_limit("y", zabrano_y)
    with A.app.app_context():
        jina = A.db.session.execute(A.text(
            "SELECT ps.song_id FROM pisne_na_strane ps JOIN strany s ON s.id = ps.strana_id "
            "JOIN songbooks b ON b.id = s.songbook_id WHERE b.is_public = 1 AND ps.song_id != :p "
            "LIMIT 1"), {"p": verejna_pisen}).scalar()
    kod, telo = post(z, f"/api/songbooks/{ids['rodina']}/add-song", {"song_id": jina})
    zkontroluj(kod == 413 and "vlastníka" in telo.get("error", ""),
               "člen nezvětší zpěvník vlastníkovi přes jeho limit", (kod, telo.get("error", "")[:90]))
    nastav_limit("y", None)

    print("\n── zámky: odemknutí ──")
    nastav_limit("x", None)
    zkontroluj(zamcene("x") == [] and x.get(f"/songbook/{ids['rodina']}").status_code == 200,
               "s větším limitem se sám odemkne")
    nastav_limit("x", ceny["v1"] + ceny["v2"])
    zkontroluj(zamcene("x") == [ids["rodina"]], "znovu přes limit")
    r = x.delete(f"/api/my-songbooks/{ids['rodina']}")
    zkontroluj(r.status_code == 200 and (r.get_json() or {}).get("odesel") and zamcene("x") == [],
               "zamčený sdílený jde opustit a nic dalšího se nezamkne", r.get_json())

    print("\n── zámky: správa ──")
    admin = klient(aid)
    kod, telo = post(admin, f"/api/sprava/uzivatel/{ucty['x']}",
                     {"akce": "limit", "hodnota": str(max(1, ceny['v2'] // (1024 * 1024))), "nahled": True})
    with A.app.app_context():
        limit_po = A.db.session.get(A.User, ucty["x"]).limit_mista
    zkontroluj(kod == 200 and any("Velký vlastní" in z for z in telo.get("zamkne", []))
               and limit_po == ceny["v1"] + ceny["v2"], "náhled řekne, co se zamkne, a nic neuloží", telo)
    nastav_limit("x", ceny["v2"])
    html = admin.get("/sprava/uzivatele").get_data(as_text=True)
    zkontroluj("zamčené zpěvníky: 1" in html, "v Uživatelích je vidět počet zamčených")
    zkontroluj(admin.get(f"/songbook/{ids['v1']}").status_code == 200, "admin zamčený zpěvník vidí")


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
            # Pod ~1100 px se texty odkazů lámou na dva řádky i běžnému uživateli (starší
            # stav). Hlídá se, že odkaz Správa nic navíc nerozbije: nic nepřeteče, odkazy
            # nevjedou do účtu a žádný nemá víc než dva řádky.
            for sirka in range(821, 1281, 20):
                page.set_viewport_size({"width": sirka, "height": 800})
                stav = page.evaluate("""() => {
                    const odkazy = [...document.querySelectorAll('.nav-links a')]
                        .filter(a => getComputedStyle(a).display !== 'none');
                    const r = odkazy.map(a => a.getBoundingClientRect());
                    return {presah: document.documentElement.scrollWidth - innerWidth,
                            vpravo: Math.max(...r.map(x => x.right)),
                            ucet: document.querySelector('.navbar .right').getBoundingClientRect().left,
                            radku: Math.max(...odkazy.map(a => {
                                const g = document.createRange(); g.selectNodeContents(a);
                                return new Set([...g.getClientRects()].map(x => Math.round(x.top))).size;
                            }))};
                }""")
                if stav["presah"] > 0 or stav["vpravo"] > stav["ucet"] or stav["radku"] > 2:
                    zkontroluj(False, f"{sirka} px: lišta admina se vejde", stav)
                    break
                if sirka == 821:
                    zkontroluj(stav["radku"] == 2, "821 px: měření řádků funguje (dva, jako u uživatele)", stav["radku"])
            else:
                zkontroluj(True, "821-1280 px: lišta admina s odkazem Správa se vejde")

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
            print("\n── prohlížeč: Uživatelé ──")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto(base + "/sprava/uzivatele")
            page.wait_for_load_state("networkidle")
            vsech = page.locator(".sz-radek").count()
            page.fill("#su-hledat", "user2")
            zkontroluj(page.locator(".sz-radek:not([hidden])").count() == 1, "hledání podle e-mailu",
                       page.locator(".sz-radek:not([hidden])").count())
            page.fill("#su-hledat", "")
            page.select_option("#su-role", "admin")
            zkontroluj(page.locator(".sz-radek:not([hidden])").count() == 1, "filtr role Správce")
            page.select_option("#su-role", "")
            page.fill("#su-zpevnik", "zzz-nic-takoveho")
            zkontroluj(page.locator(".sz-radek:not([hidden])").count() == 0
                       and page.locator("#su-prazdne").is_visible(), "filtr zpěvníku a prázdný stav")
            page.fill("#su-zpevnik", "")
            zkontroluj(page.locator(".sz-radek:not([hidden])").count() == vsech, "bez filtrů všichni", vsech)
            radek = page.locator('.sz-radek[data-email="user2@test.com"]')
            radek.locator(".sz-souhrn").click()
            radek.locator('[data-pole="prezdivka"]').fill("Přezdívka z prohlížeče")
            radek.locator('[data-pole="prezdivka"]').press("Enter")
            # Po uložení se stránka načte znovu; řádek má být otevřený už s novým jménem.
            # (expect_navigation nestačí - za navigaci bere už replaceState kotvy.)
            try:
                page.wait_for_selector('.sz-radek.otevreny[data-jmeno="Přezdívka z prohlížeče"] .sz-detail',
                                       state="visible", timeout=10000)
                ulozeno = True
            except Exception:
                ulozeno = False
            radek = page.locator('.sz-radek[data-email="user2@test.com"]')
            zkontroluj(ulozeno, "Enter v přezdívce uloží, řádek zůstane otevřený",
                       radek.get_attribute("data-jmeno"))
            radek.locator('[data-akce="zablokovat"]').click()
            zkontroluj(page.locator("#app-confirm-overlay").is_visible(), "zablokování se ptá")
            page.keyboard.press("Escape")
            zkontroluj(radek.get_attribute("data-zablokovan") == "0", "Esc nic nezablokuje")
            print("\n── prohlížeč: okno smazání účtu ──")
            radek.locator('[data-akce="smazat"]').click()
            okno = page.locator(".oznameni-zaclona .okno")
            tlacitko = okno.locator('[data-volba="ok"]')
            zkontroluj(okno.is_visible() and tlacitko.is_disabled(), "okno se otevře, Smazat čeká na e-mail")
            okno.locator(".su-potvrzeni").fill("user2@test.co")
            zkontroluj(tlacitko.is_disabled(), "s neúplným e-mailem pořád ne")
            okno.locator(".su-potvrzeni").fill("USER2@test.com")
            zkontroluj(tlacitko.is_enabled(), "s opsaným e-mailem ano (velikost písmen nevadí)")
            page.keyboard.press("Escape")
            zkontroluj(page.locator(".oznameni-zaclona").count() == 0, "Esc okno zavře, nic se nesmaže")

            print("\n── prohlížeč: přihlášení jako ──")
            radek.locator('[data-akce="prihlasit"]').click()
            page.wait_for_url("**/my-songbooks", timeout=10000)
            zkontroluj(page.locator(".pruh-spravce").is_visible(), "pruh nahoře je vidět")
            sirka_pruhu = page.evaluate("() => document.querySelector('.pruh-spravce').getBoundingClientRect().top")
            zkontroluj(sirka_pruhu >= page.evaluate("() => document.querySelector('.navbar').getBoundingClientRect().bottom") - 1,
                       "pruh je pod lištou, ne schovaný pod ní", sirka_pruhu)
            page.click(".pruh-spravce button")
            page.wait_for_url("**/sprava/uzivatele*", timeout=10000)
            page.wait_for_selector(".sz-radek.otevreny", timeout=10000)
            zkontroluj(page.locator(".sz-radek.otevreny").get_attribute("data-email") == "user2@test.com",
                       "zpět ve správě na jeho řádku")
            for sirka in (320, 390):
                page.set_viewport_size({"width": sirka, "height": 740})
                page.goto(base + "/sprava/uzivatele#u-" + radek.get_attribute("data-id"))
                page.wait_for_load_state("networkidle")
                stav = page.evaluate("""() => {
                    const p = document.querySelector('.sprava-panel').getBoundingClientRect();
                    const ven = [...document.querySelectorAll('.sprava-panel *')].filter(e => {
                        const r = e.getBoundingClientRect();
                        return r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5);
                    }).map(e => e.className || e.tagName);
                    return {presah: document.documentElement.scrollWidth - innerWidth, ven: ven.slice(0, 5),
                            otevreny: !!document.querySelector('.sz-radek.otevreny')};
                }""")
                zkontroluj(stav["presah"] <= 0 and not stav["ven"] and stav["otevreny"],
                           f"{sirka} px: Uživatelé jako kartičky, nic nepřetéká", stav)
            zkontroluj(not chyby, "bez chyb v JavaScriptu", chyby[:3])
            browser.close()
    finally:
        srv.terminate()


def srovnej_kopii():
    """Lokální data si Aleš ručně mění (limity, blokace). V kopii DB se srovnají na
    výchozí, ať testy nezávisí na tom, s čím si zrovna hrál."""
    with A.app.app_context():
        A.User.query.update({A.User.limit_mista: None, A.User.zablokovan: False})
        A.ZasahAdmina.query.delete()
        A.db.session.commit()


def main():
    srovnej_kopii()
    moje_zpevniky()
    hledani()
    vsechny_zpevniky()
    akce_se_zpevniky()
    uzivatele()
    odhlaseni()
    prihlaseni_jako()
    zamky()
    smazani_uctu()
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
