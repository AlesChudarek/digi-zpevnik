#!/usr/bin/env python3
"""Přejímací test editoru zpěvníku (/api/zpevnik, /api/strany/nahrat).

Běží na kopii databáze a na klonu obrázků, takže na skutečná data nesáhne: editor
nahrává, přepisuje obálky a maže strany. Klon je na macOS copy-on-write (cp -c), jinde
obyčejná kopie.

    python backend/scripts/test_editor.py
"""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

TMP = Path(tempfile.mkdtemp(prefix="editor-"))
DB = TMP / "editor.db"
with sqlite3.connect(str(PROJECT_ROOT / "backend" / "instance" / "zpevnik.db")) as zdroj, \
        sqlite3.connect(str(DB)) as cil:
    zdroj.backup(cil)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "editor")

import backend.app as A  # noqa: E402
from PIL import Image  # noqa: E402

OBRAZKY = TMP / "images"
if subprocess.run(["cp", "-cR", str(A.IMAGES_DIR), str(OBRAZKY)],
                  capture_output=True).returncode != 0:
    shutil.copytree(A.IMAGES_DIR, OBRAZKY)
A.IMAGES_DIR = OBRAZKY
A.schedule_export_warm = lambda *a, **k: None
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


def png(barva, rozmer=(300, 424)):
    b = io.BytesIO()
    Image.new("RGB", rozmer, barva).save(b, "PNG")
    b.seek(0)
    return b


def nahraj(c, barva, **pole):
    r = c.post("/api/strany/nahrat", data={"soubor": (png(barva), "strana.png"), **pole},
               content_type="multipart/form-data")
    return r.status_code, (r.get_json() or {})


def uloz(c, zpevnik_id=None, **pole):
    url = f"/api/zpevnik/{zpevnik_id}" if zpevnik_id else "/api/zpevnik"
    if "strany" in pole and not isinstance(pole["strany"], str):
        pole["strany"] = json.dumps(pole["strany"])
    r = c.post(url, data=pole, content_type="multipart/form-data")
    return r.status_code, (r.get_json() or {})


def nacti(c, zpevnik_id):
    return c.get(f"/api/zpevnik/{zpevnik_id}").get_json()["zpevnik"]


def strany_db(zpevnik_id):
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, zpevnik_id)
        return [(A.cislo_strany(sb, st), st.image_path, st.popisek,
                 [(v.song.title, v.poradi_v_pisni) for v in st.pisne]) for st in sb.strany]


def main():
    global EMAIL3, EMAIL5
    admin, u3, u4, host = klient(1), klient(3), klient(4), klient(7)
    with A.app.app_context():
        EMAIL3 = A.db.session.get(A.User, 3).email
        EMAIL5 = A.db.session.get(A.User, 5).email

    print("\n── nový zpěvník se všemi druhy stran ──")
    ids = []
    for barva in ("red", "green", "blue", "yellow"):
        kod, telo = nahraj(u3, barva)
        ids.append(telo.get("image_id"))
    zkontroluj(all(ids) and len(set(ids)) == 4, "čtyři strany se nahrají, každá jinam", ids)
    with A.app.app_context():
        cesty = [A.db.session.get(A.Obrazek, i).cesta for i in ids]
    zkontroluj(all(c.startswith("uzivatele/3/pages/") for c in cesty),
               "strany nového zpěvníku leží v kořeni toho, kdo je nahrál", cesty[0])

    strany = [
        {"typ": "pisen", "image_id": ids[0], "pisne": [
            {"nazev": "Krátká jedna", "autor": "Autor A"},
            {"nazev": "Krátká dvě", "autor": "Autor B"}]},
        {"typ": "pisen", "image_id": ids[1], "pisne": [{"nazev": "Dlouhá", "autor": "Autor C"}]},
        {"typ": "pokracovani", "image_id": ids[2]},
        {"typ": "bez", "image_id": ids[3], "popisek": "Osmisměrka"},
        {"typ": "prazdna"},
    ]
    kod, telo = uloz(u3, nazev="Test editoru", barva="#336699", prvni_cislo="5", strany=strany)
    zkontroluj(kod == 200 and telo.get("ok"), "zpěvník se založí jedním uložením", telo)
    novy = telo.get("id")
    s = strany_db(novy)
    zkontroluj([x[0] for x in s] == [5, 6, 7, 8, 9], "čísla stran začínají zadaným číslem",
               [x[0] for x in s])
    zkontroluj(s[0][3] == [("Krátká jedna", 1), ("Krátká dvě", 1)],
               "dvě písně na jedné straně", s[0][3])
    zkontroluj(s[1][3] == [("Dlouhá", 1)] and s[2][3] == [("Dlouhá", 2)],
               "pokračování napojí předchozí píseň", (s[1][3], s[2][3]))
    zkontroluj(s[3][3] == [] and s[3][2] == "Osmisměrka", "strana bez písně s popiskem", s[3])
    zkontroluj(s[4][1] is None and s[4][3] == [], "prázdná strana nemá obrázek", s[4])
    toc = u3.get(f"/api/songbook/{novy}/toc").get_json()["pages"]
    zkontroluj([(t["title"], t["page_number"]) for t in toc]
               == [("Krátká jedna", 5), ("Krátká dvě", 5), ("Dlouhá", 6)],
               "obsah ukazuje písně a jejich strany", [(t["title"], t["page_number"]) for t in toc])
    z = nacti(u3, novy)
    zkontroluj([st["typ"] for st in z["strany"]] == ["pisen", "pisen", "pokracovani", "bez", "prazdna"],
               "editor dostane typy stran zpátky", [st["typ"] for st in z["strany"]])

    print("\n── úpravy ──")
    z = nacti(u3, novy)
    st = z["strany"]
    # Prázdnou stranu na začátek, přejmenovat Dlouhou, odebrat stranu se dvěma písněmi
    st[1]["pisne"][0]["nazev"] = "Dlouhá (opravená)"
    nove_poradi = [st[4], st[1], st[2], st[3]]
    smazany_soubor = OBRAZKY / cesty[0]
    kod, telo = uloz(u3, novy, strany=nove_poradi)
    zkontroluj(kod == 200, "uložení úprav", telo)
    s = strany_db(novy)
    zkontroluj([x[3] for x in s] == [[], [("Dlouhá (opravená)", 1)], [("Dlouhá (opravená)", 2)], []],
               "přerovnání, přejmenování a odebrání stran", [x[3] for x in s])
    with A.app.app_context():
        zbyle = {p.title for p in A.Song.query.filter(A.Song.title.like("Krátká%"))}
    zkontroluj(not zbyle, "písně z odebrané strany zmizely", zbyle)
    zkontroluj(not smazany_soubor.exists(), "obrázek odebrané strany se smazal z disku")

    print("\n── nesmysly se odmítnou a nic nezmění ──")
    pred = strany_db(novy)
    for popis, data in [
        ("pokračování na první straně", [{"typ": "pokracovani", "image_id": ids[1]}]),
        ("pokračování po straně bez písně", [{"typ": "bez", "image_id": ids[3]},
                                             {"typ": "pokracovani", "image_id": ids[1]}]),
        ("píseň bez názvu", [{"typ": "pisen", "image_id": ids[1], "pisne": [{"nazev": " "}]}]),
        ("píseň bez písní", [{"typ": "pisen", "image_id": ids[1], "pisne": []}]),
        ("strana bez obrázku jako píseň", [{"typ": "pisen", "pisne": [{"nazev": "X"}]}]),
        ("neznámý typ", [{"typ": "nevim"}]),
        ("cizí id strany", [{"id": 1, "typ": "prazdna"}]),
    ]:
        kod, telo = uloz(u3, novy, strany=data)
        zkontroluj(kod == 400, f"{popis} → 400", telo.get("error"))
    zkontroluj(strany_db(novy) == pred, "po odmítnutých požadavcích je zpěvník beze změny")

    print("\n── bezpečnost ──")
    with A.app.app_context():
        cizi = A.Strana.query.join(A.Songbook).filter(A.Songbook.owner_id == 2).first()
        cizi_obrazek = cizi.image_id if cizi else None
        verejna = A.Strana.query.join(A.Songbook).filter(A.Songbook.is_public == 1).filter(
            A.Strana.pisne.any()).first()
        verejna_obrazek = verejna.image_id
        verejna_pisen = verejna.pisne[0].song_id
        verejny_nazev = verejna.pisne[0].song.title
    kod, telo = uloz(u3, novy, strany=[{"typ": "bez", "image_id": cizi_obrazek}])
    zkontroluj(kod == 400, "cizí soukromou stranu si nikdo nevloží podle čísla", telo.get("error"))
    kod, telo = uloz(u3, novy, strany=pred and [
        {"typ": "pisen", "image_id": verejna_obrazek,
         "pisne": [{"song_id": verejna_pisen, "nazev": "Přejmenováno", "autor": "x"}]}])
    zkontroluj(kod == 200, "veřejnou píseň si jde dát do svého zpěvníku", telo)
    with A.app.app_context():
        nazev = A.db.session.get(A.Song, verejna_pisen).title
    zkontroluj(nazev == verejny_nazev, "ale přejmenovat ji odtud nejde", nazev)
    zkontroluj(nacti(u3, novy)["strany"][0]["pisne"][0]["zamcena"],
               "editor ji ukáže jako zamčenou")
    kod, _ = uloz(u4, novy, strany=[])
    zkontroluj(kod == 404, "cizí zpěvník upravit nejde", kod)
    kod, _ = nahraj(host, "red")
    zkontroluj(kod == 403, "host nenahraje nic", kod)
    kod, _ = nahraj(u4, "red", zpevnik_id=novy)
    zkontroluj(kod == 404, "do cizího zpěvníku nenahraje nikdo", kod)
    kod, _ = nahraj(u3, "red", verejny="1")
    zkontroluj(kod == 403, "do veřejného nahrává jen admin", kod)
    kod, _ = uloz(u3, verejny="1", strany=[])
    zkontroluj(kod == 403, "veřejný zpěvník zakládá jen admin", kod)

    print("\n── obálky ──")
    kod, telo = uloz(u3, novy, remove_front_outer="1",
                     front_outer=(png("purple"), "obalka.png"))
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, novy)
        obalka, nahled = sb.img_path_cover_front_outer, sb.img_path_cover_preview
    zkontroluj(kod == 200 and obalka and (OBRAZKY / obalka).exists(),
               "„odebrat a nahrát jinou“ novou obálku nesmaže", obalka)
    zkontroluj(nahled == obalka, "přední obálka je i náhledem")
    kod, _ = uloz(u3, novy, remove_front_outer="1")
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, novy)
        zkontroluj(sb.img_path_cover_front_outer is None and not (OBRAZKY / obalka).exists(),
                   "odebraná obálka zmizí z DB i z disku")
        zkontroluj(A.Obrazek.query.filter_by(cesta=obalka).first() is None,
                   "i s řádkem v images")

    print("\n── nahrávání ──")
    _, a = nahraj(u3, "red")
    _, b = nahraj(u3, "red")
    with A.app.app_context():
        ca = A.db.session.get(A.Obrazek, a["image_id"]).cesta
        cb = A.db.session.get(A.Obrazek, b["image_id"]).cesta
    zkontroluj(ca != cb, "dvě nahrání téhož dostanou dvě různé cesty", (ca, cb))
    stare = time.time() - (A.NEULOZENA_STRANA_HODIN + 1) * 3600
    os.utime(OBRAZKY / ca, (stare, stare))
    nahraj(u3, "blue")
    zkontroluj(not (OBRAZKY / ca).exists() and (OBRAZKY / cb).exists(),
               "neuložená strana starší než den se uklidí, čerstvá zůstane")
    puvodni = A.MAX_USER_STORAGE_BYTES
    A.MAX_USER_STORAGE_BYTES = 1
    pred_soubory = set((OBRAZKY / "uzivatele" / "3" / "pages").iterdir())
    kod, _ = nahraj(u3, "red")
    A.MAX_USER_STORAGE_BYTES = puvodni
    po_soubory = set((OBRAZKY / "uzivatele" / "3" / "pages").iterdir())
    zkontroluj(kod == 413, "přes limit se nenahraje", kod)
    zkontroluj(po_soubory == pred_soubory, "a nezůstane po tom ani zarezervovaný soubor",
               po_soubory - pred_soubory)

    print("\n── limit podle databáze ──")
    MIN = A.MIN_CENA_STRANY

    def misto(uid):
        with A.app.app_context():
            return A.zabrane_misto(uid)

    def velikost(image_id):
        with A.app.app_context():
            return A.db.session.get(A.Obrazek, image_id).velikost

    _, x = nahraj(u4, "orange")
    zaklad = misto(4)
    kod, telo = uloz(u4, nazev="Limit", strany=[{"typ": "bez", "image_id": x["image_id"]}])
    limitni = telo.get("id")
    zkontroluj(misto(4) == zaklad, "uložením se nahraná strana nezapočítá podruhé",
               (zaklad, misto(4)))
    vlastni = max(velikost(x["image_id"]), MIN)
    pred = misto(4)
    uloz(u4, limitni, strany=[{"typ": "bez", "image_id": x["image_id"]},
                              {"typ": "bez", "image_id": x["image_id"]},
                              {"typ": "prazdna"},
                              {"typ": "bez", "image_id": verejna_obrazek}])
    zkontroluj(misto(4) - pred == vlastni + MIN + MIN,
               "duplikát stojí znovu, prázdná i veřejná strana pevnou cenu",
               (misto(4) - pred, vlastni + 2 * MIN))
    z = nacti(u4, limitni)
    puvodni_limit = A.MAX_USER_STORAGE_BYTES
    try:
        A.MAX_USER_STORAGE_BYTES = misto(4) + MIN // 2
        kod, telo = uloz(u4, limitni, strany=[
            {k: st[k] for k in ("id", "typ", "image_id", "popisek", "pisne")} for st in z["strany"]]
            + [{"typ": "prazdna"}])
        zkontroluj(kod == 413, "strana navíc přes limit se neuloží", telo.get("error"))
        kod, _ = uloz(u4, limitni, strany=[
            {k: st[k] for k in ("id", "typ", "image_id", "popisek", "pisne")} for st in z["strany"][:2]])
        zkontroluj(kod == 200, "ubírat jde vždycky, i těsně u limitu", kod)
        A.MAX_USER_STORAGE_BYTES = misto(4) - 1
        kod, _ = uloz(u4, limitni, nazev="Limit přejmenovaný")
        zkontroluj(kod == 200, "kdo je přes limit, smí dál upravovat, co místo nepřidá", kod)
        r = u4.post(f"/api/songbooks/{limitni}/add-song", json={"song_id": verejna_pisen})
        zkontroluj(r.status_code == 413, "přes limit nejde přidat ani píseň z hledání",
                   r.status_code)

        A.MAX_USER_STORAGE_BYTES = puvodni_limit
        pred3 = misto(3)
        r = u4.post(f"/api/my-songbooks/{limitni}/share", json={"email": EMAIL3})
        zkontroluj(r.status_code == 200 and misto(3) > pred3,
                   "sdílený zpěvník se počítá i tomu, s kým se sdílí", (r.status_code, misto(3) - pred3))
        A.MAX_USER_STORAGE_BYTES = misto(3) + 1
        _, y = nahraj(u4, "navy", zpevnik_id=limitni)
        z = nacti(u4, limitni)
        kod, telo = uloz(u4, limitni, strany=[
            {k: st[k] for k in ("id", "typ", "image_id", "popisek", "pisne")} for st in z["strany"]]
            + [{"typ": "bez", "image_id": y["image_id"]}])
        zkontroluj(kod == 413 and EMAIL3 in (telo.get("error") or ""),
                   "nahrání do sdíleného se odmítne, když se nevejde druhému, a řekne komu",
                   telo.get("error"))
        A.MAX_USER_STORAGE_BYTES = misto(5) + 1
        r = u4.post(f"/api/my-songbooks/{limitni}/share", json={"email": EMAIL5})
        with A.app.app_context():
            sdileno = A.UserSongbookAccess.query.filter_by(user_id=5, songbook_id=limitni).first()
        zkontroluj(r.status_code == 413 and sdileno is None,
                   "sdílet nejde tomu, komu se zpěvník nevejde", r.status_code)
    finally:
        A.MAX_USER_STORAGE_BYTES = puvodni_limit

    print("\n── veřejný zpěvník od admina ──")
    _, x = nahraj(admin, "green", verejny="1")
    kod, telo = uloz(admin, verejny="1", nazev="Veřejný test",
                     strany=[{"typ": "pisen", "image_id": x["image_id"],
                              "pisne": [{"nazev": "Veřejná píseň", "autor": "-"}]}])
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, telo.get("id"))
        zkontroluj(kod == 200 and sb.is_public == 1 and sb.owner_id is None
                   and sb.strany[0].image_path.startswith("verejne/pages/"),
                   "admin založí veřejný zpěvník se stranou ve veřejném kořeni",
                   (kod, telo, sb and sb.strany[0].image_path))

    print("\n── všechny zpěvníky tam a zpátky beze změny ──")
    with A.app.app_context():
        knihy = [b.id for b in A.Songbook.query.order_by(A.Songbook.id)]
    zmenene = []
    for b in knihy:
        pred = strany_db(b)
        z = nacti(admin, b)
        kod, _ = uloz(admin, b, nazev=z["nazev"], barva=z["barva"],
                      prvni_cislo=str(z["prvni_cislo"]), strany=[
                          {k: st[k] for k in ("id", "typ", "image_id", "popisek", "pisne")}
                          for st in z["strany"]])
        if kod != 200 or strany_db(b) != pred:
            zmenene.append(b)
    zkontroluj(not zmenene, f"{len(knihy)} zpěvníků načtených a uložených beze změny", zmenene)

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
