#!/usr/bin/env python3
"""Přejímací test serverové části editoru stran (TODO bod 11, krok 3): verze navržené
strany (zdroj + PDF), obrázky ve straně, limit místa, práva, úklid, kopie, styly účtu.

Běží na kopii databáze a na klonu obrázků (jako test_editor) - ukládá a maže soubory,
takže na skutečná data nesmí sáhnout.

    python backend/scripts/test_navrh_server.py
"""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

TMP = Path(tempfile.mkdtemp(prefix="navrh-server-"))
DB = TMP / "navrh.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "navrh")

import backend.app as A  # noqa: E402
from PIL import Image  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)], capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.schedule_export_warm = lambda *a, **k: None
A.dopocitej_nahledy = lambda *a, **k: None
A.app.config["TESTING"] = True

selhani = []
PDF = b"%PDF-1.7\n% testovaci strana\n" + b"0" * 90000 + b"\n%%EOF\n"     # větší než MIN_CENA_STRANY


def ok(popis, podminka, detail=""):
    print(f"  {'✅' if podminka else '❌'} {popis}{('  ' + str(detail)) if detail else ''}")
    if not podminka:
        selhani.append(popis)


def klient(uid):
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return c


def obrazek(fmt="PNG", rozmer=(400, 200)):
    b = io.BytesIO()
    Image.new("RGB", rozmer, (40, 120, 200)).save(b, fmt)
    b.seek(0)
    return b


def zdroj(obrazky=(), verze=2):
    prvky = [{"type": "ramecek", "attrs": {"x": 10, "y": 10, "w": 128, "h": 190}, "content": [
        {"type": "odstavec", "attrs": {"styl": "sloka-1"}, "content": [
            {"type": "akord", "attrs": {"n": "Ami"}}, {"type": "text", "text": "Večer u ohně"}]}]}]
    for src in obrazky:
        prvky.append({"type": "obrazek", "attrs": {"src": src, "x": 20, "y": 20, "w": 50, "h": 25}})
    return {"verze": verze, "nastaveni": {"format": "A5", "styly": {}}, "obsah": {"type": "doc", "content": prvky},
            "zlomy": {}}


def uloz_navrh(c, z, pdf=PDF):
    r = c.post("/api/navrh/strana", data={"zdroj": json.dumps(z), "pdf": (io.BytesIO(pdf), "strana.pdf")},
               content_type="multipart/form-data")
    return r.status_code, (r.get_json() or {})


def nahraj_obrazek(c, fmt="PNG"):
    r = c.post("/api/navrh/obrazek", data={"soubor": (obrazek(fmt), f"obr.{fmt.lower()}")},
               content_type="multipart/form-data")
    return r.status_code, (r.get_json() or {})


def misto(uid):
    with A.app.app_context():
        return A.zabrane_misto(uid)


def main():
    with A.app.app_context():
        A.db.session.get(A.User, 3).role = "premium"
        A.db.session.get(A.User, 4).role = "user"
        # Testovací zpěvník prémiového účtu 3, sdílený s účtem 4.
        sb = A.Songbook(id="test-navrh", title="Navržené strany", owner_id=3, is_public=0)
        A.db.session.add(sb)
        A.db.session.add(A.UserSongbookAccess(user_id=4, songbook_id="test-navrh"))
        A.db.session.commit()
    premium, bezny, admin = klient(3), klient(4), klient(1)

    print("\n── práva ──")
    kod, _ = uloz_navrh(bezny, zdroj())
    ok("účet bez prémia stranu neuloží", kod == 403, kod)
    kod, _ = nahraj_obrazek(bezny)
    ok("účet bez prémia obrázek nenahraje", kod == 403, kod)

    print("\n── admin bez limitu místa (Aleš 10. 11.) ──")
    with A.app.app_context():
        u = A.db.session.get(A.User, 1)
        puvodni_role, puvodni_limit = u.role, u.limit_mista
        u.role, u.limit_mista = "admin", 1024   # vlastní limit 1 kB, zabráno víc
        A.db.session.commit()
        ok("admin nemá zamčené zpěvníky ani s malým vlastním limitem", not A.zamcene_zpevniky(u))
    kod, odp = uloz_navrh(admin, zdroj())
    ok("admin s vlastním limitem 1 kB stranu uloží", kod == 200 and odp.get("id"), (kod, odp))
    kod, odp = nahraj_obrazek(admin)
    ok("a nahraje obrázek", kod == 200, (kod, odp))
    r = admin.post("/api/sprava/uzivatel/1", json={"akce": "limit", "hodnota": "5"})
    ok("limit adminu nejde nastavit", r.status_code == 400 and "nemá" in (r.get_json() or {}).get("error", ""), (r.status_code, r.get_json()))
    with A.app.app_context():
        u = A.db.session.get(A.User, 1)
        u.role, u.limit_mista = puvodni_role, puvodni_limit
        A.db.session.commit()

    print("\n── obrázky ve straně ──")
    misto0 = misto(3)
    kod, o1 = nahraj_obrazek(premium)
    ok("prémiový účet nahraje obrázek", kod == 200 and o1.get("url", "").startswith("/navrh/obrazek/"), (kod, o1))
    ok("neuložený obrázek se počítá do místa", misto(3) - misto0 >= A.MIN_CENA_STRANY, misto(3) - misto0)
    ok("autor obrázek vidí", premium.get(o1["url"]).status_code == 200)
    ok("cizí účet obrázek nevidí", bezny.get(o1["url"]).status_code == 404)
    kod, o2 = nahraj_obrazek(premium, "WEBP")
    with A.app.app_context():
        cesta2 = A.db.session.get(A.Obrazek, o2["id"]).cesta
    ok("WEBP se převede na PNG (do PDF jde jen PNG a JPEG)", kod == 200 and cesta2.endswith(".png"), cesta2)

    print("\n── uložení verze ──")
    for popis, z, pdf in (
            ("obrázek jako data: URL se odmítne", zdroj(["data:image/png;base64,AAAA"]), PDF),
            ("neznámá verze zdroje se odmítne", zdroj(verze=99), PDF),
            ("soubor, který není PDF, se odmítne", zdroj(), b"tohle neni pdf"),
            ("odkaz na neexistující obrázek se odmítne", zdroj(["/navrh/obrazek/999999"]), PDF)):
        kod, _ = uloz_navrh(premium, z, pdf)
        ok(popis, kod == 400, kod)
    pred = misto(3)
    kod, n1 = uloz_navrh(premium, zdroj([o1["url"]]))
    ok("strana se uloží jako nová verze", kod == 200 and n1.get("id"), (kod, n1))
    po = misto(3)
    with A.app.app_context():
        velikost_obr = A.db.session.get(A.Obrazek, o1["id"]).velikost
    ocekavano = max(n1["velikost"], A.MIN_CENA_STRANY) - max(velikost_obr, A.MIN_CENA_STRANY)
    ok("místo: verze se počítá jednou, obrázek v ní už ne zvlášť", po - pred == ocekavano, (po - pred, ocekavano))
    kod, n2 = uloz_navrh(bezny, zdroj([o1["url"]]))
    ok("cizí účet nepoužije cizí obrázek", kod in (400, 403), kod)
    r = premium.get(f"/api/navrh/strana/{n1['id']}").get_json()
    ok("autor verzi načte se zdrojem", r.get("ok") and r["zdroj"]["obsah"]["content"][0]["type"] == "ramecek")
    ok("autor stáhne PDF strany", premium.get(r["pdf"]).data.startswith(b"%PDF-"))
    ok("cizí účet verzi nevidí, dokud není ve sdíleném zpěvníku",
       bezny.get(f"/api/navrh/strana/{n1['id']}").status_code == 404)

    print("\n── strana ve zpěvníku ──")
    with A.app.app_context():
        A.db.session.add(A.Strana(songbook_id="test-navrh", poradi=0, navrzena_id=n1["id"]))
        A.db.session.commit()
        cena = A.cena_zpevniku(["test-navrh"])
    ok("zpěvník počítá navrženou stranu její velikostí", cena == max(n1["velikost"], A.MIN_CENA_STRANY), (cena, n1["velikost"]))
    ok("verze ve zpěvníku se autorovi nepočítá ještě jednou", misto(3) == pred + max(n1["velikost"], A.MIN_CENA_STRANY) - max(velikost_obr, A.MIN_CENA_STRANY)
       or misto(3) == po, (misto(3), po))
    ok("člen sdíleného zpěvníku verzi vidí", bezny.get(f"/api/navrh/strana/{n1['id']}").status_code == 200)
    ok("člen vidí i obrázek ve straně a PDF", bezny.get(o1["url"]).status_code == 200
       and bezny.get(f"/navrh/strana/{n1['id']}.pdf").status_code == 200)
    ok("host verzi nevidí", klient(7).get(f"/api/navrh/strana/{n1['id']}").status_code in (302, 401, 404))

    print("\n── úklid ──")
    kod, n3 = uloz_navrh(premium, zdroj())                  # neuložená verze (žádný zpěvník)
    with A.app.app_context():
        stara = datetime.now() - timedelta(hours=A.NEULOZENA_STRANA_HODIN + 1)
        for nid in (n1["id"], n3["id"]):
            A.db.session.get(A.NavrzenaStrana, nid).vytvoreno = stara
        A.db.session.commit()
        pdf1 = A.db.session.get(A.NavrzenaStrana, n1["id"]).pdf.cesta
        pdf3 = A.db.session.get(A.NavrzenaStrana, n3["id"]).pdf.cesta
        obr1 = A.db.session.get(A.Obrazek, o1["id"]).cesta
        for c in (pdf1, pdf3, obr1):
            os.utime(OBRAZKY / c, (0, 0))                 # staré soubory
    nahraj_obrazek(premium)                                  # spustí úklid
    with A.app.app_context():
        ok("stará verze mimo zpěvník se smaže", A.db.session.get(A.NavrzenaStrana, n3["id"]) is None)
        ok("i s jejím PDF", not (OBRAZKY / pdf3).exists())
        ok("verze ve zpěvníku zůstane", A.db.session.get(A.NavrzenaStrana, n1["id"]) is not None)
        ok("její PDF a obrázek úklid nesmaže, i když jsou staré", (OBRAZKY / pdf1).exists() and (OBRAZKY / obr1).exists())

    print("\n── kopie zpěvníku ──")
    with A.app.app_context():
        nova = A.kopiruj_zpevnik(A.db.session.get(A.Songbook, "test-navrh"), 4)
        A.db.session.commit()
        kopie = [st.navrzena_id for st in A.db.session.get(A.Songbook, nova.id).strany]
    ok("kopie ukazuje na tutéž verzi (neměnná, sdílí se)", kopie == [n1["id"]], kopie)

    print("\n── knihovna stylů účtu ──")
    ok("prázdná knihovna", premium.get("/api/navrh/styly").get_json()["styly"] is None)
    styly = {"sloka": {"nazev": "Sloka", "role": "sloka", "velikost": 10, "ver": 3}}
    r = premium.put("/api/navrh/styly", json={"styly": styly})
    ok("prémiový účet uloží styly", r.status_code == 200)
    ok("a načte je zpátky", premium.get("/api/navrh/styly").get_json()["styly"] == styly)
    ok("účet bez prémia styly neuloží", bezny.put("/api/navrh/styly", json={"styly": styly}).status_code == 403)

    print("\n── zrušení účtu ──")
    with A.app.app_context():
        A.zrus_ucet(A.db.session.get(A.User, 3), "Zpěvník {nazev} byl smazán.")
        navrh = A.db.session.get(A.NavrzenaStrana, n1["id"])
        ok("verze v kopii u jiného účtu zůstane, bez vlastníka", navrh is not None and navrh.vlastnik_id is None)
        ok("knihovna stylů zrušeného účtu zmizí", A.db.session.get(A.KnihovnaStylu, 3) is None)
        ok("strana v kopii dál ukazuje na verzi",
           [st.navrzena_id for st in A.db.session.get(A.Songbook, nova.id).strany] == [n1["id"]])
    ok("PDF strany v kopii zůstalo", (OBRAZKY / pdf1).exists())

    shutil.rmtree(TMP, ignore_errors=True)
    if selhani:
        print(f"\n❌ Selhalo {len(selhani)}:", *selhani, sep="\n  ")
        sys.exit(1)
    print("\n✅ Serverová část editoru stran v pořádku.")


if __name__ == "__main__":
    main()
