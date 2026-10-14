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
    stara_data = (OBRAZKY / obalka).read_bytes()
    puvodni = A.MAX_USER_STORAGE_BYTES
    A.MAX_USER_STORAGE_BYTES = 1
    try:
        kod, _ = uloz(u3, novy, front_outer=(png("orange"), "jina.png"))
    finally:
        A.MAX_USER_STORAGE_BYTES = puvodni
    soubory_obalek = sorted(p.name for p in (OBRAZKY / obalka).parent.iterdir())
    zkontroluj(kod == 413 and (OBRAZKY / obalka).read_bytes() == stara_data
               and len(soubory_obalek) == 1,
               "výměna obálky přes limit nechá starou obálku netknutou a nic nepřidá",
               (kod, soubory_obalek))
    kod, _ = uloz(u3, novy, front_outer=(png("orange"), "jina.png"))
    with A.app.app_context():
        nova_obalka = A.db.session.get(A.Songbook, novy).img_path_cover_front_outer
    zkontroluj(kod == 200 and nova_obalka != obalka and (OBRAZKY / nova_obalka).exists()
               and not (OBRAZKY / obalka).exists(),
               "výměna obálky zapíše nový soubor a starý po uložení smaže", (obalka, nova_obalka))
    obalka = nova_obalka
    kod, _ = uloz(u3, novy, remove_front_outer="1")
    with A.app.app_context():
        sb = A.db.session.get(A.Songbook, novy)
        zkontroluj(sb.img_path_cover_front_outer is None and not (OBRAZKY / obalka).exists(),
                   "odebraná obálka zmizí z DB i z disku")
        zkontroluj(A.Obrazek.query.filter_by(cesta=obalka).first() is None,
                   "i s řádkem v images")

    print("\n── nahrávání ──")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    r = u3.post("/api/strany/nahrat", data={"soubor": (io.BytesIO(svg), "obrazek.png")},
                content_type="multipart/form-data")
    zkontroluj(r.status_code == 400 and "Podporované" in (r.get_json() or {}).get("error", ""),
               "SVG (i s příponou .png) se odmítne hned při nahrání", r.get_json())
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
        u4.post(f"/api/my-songbooks/{limitni}/share", json={"email": EMAIL3, "druh": "spolecny"})
        with A.app.app_context():
            pid = A.Pozvanka.query.filter_by(komu_id=3, songbook_id=limitni).first().id
        r = u3.post(f"/api/pozvanky/{pid}/prijmout")
        zkontroluj(r.status_code == 200 and misto(3) > pred3,
                   "společný zpěvník se počítá i tomu, kdo ho přijal", (r.status_code, misto(3) - pred3))
        A.MAX_USER_STORAGE_BYTES = misto(3) + 1
        _, y = nahraj(u4, "navy", zpevnik_id=limitni)
        z = nacti(u4, limitni)
        kod, telo = uloz(u4, limitni, strany=[
            {k: st[k] for k in ("id", "typ", "image_id", "popisek", "pisne")} for st in z["strany"]]
            + [{"typ": "bez", "image_id": y["image_id"]}])
        zkontroluj(kod == 413 and EMAIL3 in (telo.get("error") or ""),
                   "nahrání do sdíleného se odmítne, když se nevejde druhému, a řekne komu",
                   telo.get("error"))
    finally:
        A.MAX_USER_STORAGE_BYTES = puvodni_limit
    u4.delete(f"/api/my-songbooks/{limitni}/songs/x")
    with A.app.app_context():
        # sdílení z testu limitu by se pletlo do testů sdílení níž
        A.UserSongbookAccess.query.filter_by(songbook_id=limitni).delete()
        A.db.session.commit()

    print("\n── sdílení: kopie a společné úpravy ──")
    u5 = klient(5)
    with A.app.app_context():
        EMAIL4 = A.db.session.get(A.User, 4).email
    _, v = nahraj(u3, "teal")
    kod, telo = uloz(u3, nazev="Originál", front_outer=(png("pink"), "obalka.png"), strany=[
        {"typ": "pisen", "image_id": v["image_id"], "pisne": [{"nazev": "Vlastní píseň", "autor": "Já"}]},
        {"typ": "pisen", "image_id": verejna_obrazek,
         "pisne": [{"song_id": verejna_pisen, "nazev": verejny_nazev, "autor": ""}]}])
    original = telo.get("id")

    def stav_knihy(kid):
        with A.app.app_context():
            A.db.session.expire_all()
            sb = A.db.session.get(A.Songbook, kid)
            if sb is None:
                return None
            obalka = sb.img_path_cover_front_outer
            return {"strany": [(st.image_id, [(v.song_id, v.song.title) for v in st.pisne]) for st in sb.strany],
                    "obalka": obalka, "obalka_bajty": (OBRAZKY / obalka).read_bytes() if obalka else None,
                    "vlastnik": sb.owner_id}

    def pozvanky(uid, druh=None):
        with A.app.app_context():
            q = A.Pozvanka.query.filter_by(komu_id=uid)
            return q.filter_by(druh=druh).all() if druh else q.all()

    r = u3.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL3})
    zkontroluj(r.status_code == 400, "sám sobě sdílet nejde (na to je Vytvořit kopii)", r.status_code)
    pred4 = misto(4)
    r = u3.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL4, "druh": "kopie"})
    zkontroluj(r.status_code == 200 and len(pozvanky(4, "kopie")) == 1, "pozvánka s kopií se odešle",
               r.get_json())
    zkontroluj(u4.get(f"/songbook/{original}").status_code == 403 and misto(4) == pred4,
               "dokud ji nepřijme, nic nevidí a nic mu nezabírá")
    pid = pozvanky(4, "kopie")[0].id
    r = u4.post(f"/api/pozvanky/{pid}/prijmout")
    kopie = (r.get_json() or {}).get("id")
    o, k = stav_knihy(original), stav_knihy(kopie or "x")
    zkontroluj(r.status_code == 200 and k and k["vlastnik"] == 4 and not pozvanky(4),
               "přijetím vznikne kopie, kterou vlastní příjemce", r.get_json())
    zkontroluj(k and [x[0] for x in k["strany"]] == [x[0] for x in o["strany"]],
               "kopie ukazuje na tytéž obrázky stran - na disku nic nepřibude")
    zkontroluj(k and k["strany"][0][1][0][0] != o["strany"][0][1][0][0]
               and k["strany"][0][1][0][1] == "Vlastní píseň",
               "vlastní píseň dostane kopie vlastní (ať jde přejmenovat)")
    zkontroluj(k and k["strany"][1][1][0][0] == verejna_pisen,
               "píseň z veřejného zpěvníku zůstane sdílená")
    zkontroluj(k and k["obalka"] != o["obalka"] and k["obalka_bajty"] == o["obalka_bajty"],
               "obálka je zkopírovaná jako vlastní soubor")
    zkontroluj(misto(4) > pred4, "kopie se počítá do místa příjemce", misto(4) - pred4)
    zk = nacti(u4, kopie)
    zk["strany"][0]["pisne"][0]["nazev"] = "Přejmenovaná v kopii"
    uloz(u4, kopie, strany=[{kk: st[kk] for kk in ("id", "typ", "image_id", "popisek", "pisne")}
                            for st in zk["strany"]])
    uloz(u3, original, front_outer=(png("black"), "jina.png"))
    o, k = stav_knihy(original), stav_knihy(kopie)
    zkontroluj(o["strany"][0][1][0][1] == "Vlastní píseň" and k["strany"][0][1][0][1] == "Přejmenovaná v kopii",
               "přejmenování v kopii originál nezmění")
    zkontroluj(o["obalka_bajty"] != k["obalka_bajty"], "a výměna obálky v originálu nezmění kopii")
    kod, _ = uloz(u4, kopie, nazev="Kopie, moje")
    zkontroluj(kod == 200, "kopii si příjemce upravuje sám")

    r = u4.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL3, "druh": "spolecny"})
    zkontroluj(r.status_code in (403, 404), "ke společným úpravám nezve nikdo jiný než vlastník",
               r.status_code)
    r = u3.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL4, "druh": "spolecny"})
    zkontroluj(r.status_code == 200 and u4.get(f"/api/zpevnik/{original}").status_code == 404,
               "pozvánka ke společným úpravám ještě nedává přístup")
    pred4 = misto(4)
    r = u4.post(f"/api/pozvanky/{pozvanky(4, 'spolecny')[0].id}/prijmout")
    zkontroluj(r.status_code == 200 and u4.get(f"/api/zpevnik/{original}").status_code == 200,
               "po přijetí zpěvník upravují společně")
    zkontroluj(misto(4) > pred4, "a počítá se do místa i jemu", misto(4) - pred4)
    r = u3.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL4, "druh": "spolecny"})
    zkontroluj(r.status_code == 400, "podruhé pozvat člena nejde", r.get_json())

    r = u3.post(f"/api/my-songbooks/{original}/share", json={"email": EMAIL5, "druh": "kopie"})
    r = u5.post(f"/api/pozvanky/{pozvanky(5)[0].id}/odmitnout")
    with A.app.app_context():
        knih5 = A.Songbook.query.filter_by(owner_id=5).count()
    zkontroluj(not pozvanky(5) and knih5 == 0, "odmítnutá pozvánka zmizí a nic nevznikne")
    puvodni_strop = A.MAX_POZVANEK
    A.MAX_POZVANEK = 2
    try:
        kody = [u3.post(f"/api/my-songbooks/{kid}/share", json={"email": EMAIL5, "druh": "kopie"}).status_code
                for kid in (original, kopie if False else original, novy)]
        u4.post(f"/api/my-songbooks/{kopie}/share", json={"email": EMAIL5, "druh": "kopie"})
        r = u4.post(f"/api/my-songbooks/{kopie}/share", json={"email": EMAIL5, "druh": "kopie"})
        zkontroluj(len(pozvanky(5)) == 2, "nevyřízených pozvánek má každý nejvýš strop",
                   (kody, len(pozvanky(5))))
    finally:
        A.MAX_POZVANEK = puvodni_strop
    A.MAX_USER_STORAGE_BYTES = misto(5) + 1
    def soubory_uctu(uid):
        koren = OBRAZKY / "uzivatele" / str(uid)
        return {str(p) for p in koren.rglob("*") if p.is_file()} if koren.exists() else set()
    try:
        pred = soubory_uctu(5)
        r = u5.post(f"/api/pozvanky/{pozvanky(5)[0].id}/prijmout")
        zkontroluj(r.status_code == 413 and len(pozvanky(5)) == 2,
                   "pozvánku, která se nevejde, přijmout nejde a zůstane", r.status_code)
        zkontroluj(soubory_uctu(5) == pred,
                   "nepřijatá kopie po sobě nenechá soubory obálek", sorted(soubory_uctu(5) - pred))
    finally:
        A.MAX_USER_STORAGE_BYTES = puvodni_limit
    u5.post("/api/pozvanky/odmitnout-vse")
    zkontroluj(not pozvanky(5), "odmítnout všechny")

    r = u3.post(f"/api/zpevnik/{original}/kopie")
    vlastni_kopie = (r.get_json() or {}).get("id")
    with A.app.app_context():
        vk = A.db.session.get(A.Songbook, vlastni_kopie) if vlastni_kopie else None
        zkontroluj(r.status_code == 200 and vk and vk.title == "Originál (kopie)" and vk.owner_id == 3,
                   "Vytvořit kopii vlastního zpěvníku", r.get_json())
    # Mazání zpěvníku se členy společných úprav přepíše krok 3 (nabídka kopie); tady
    # se maže zpěvník bez členů - vlastní kopie, která sdílí obrázky stran s ostatními.
    u3.post(f"/api/my-songbooks/{vlastni_kopie}/share", json={"email": EMAIL5, "druh": "kopie"})
    r = u3.delete(f"/api/my-songbooks/{vlastni_kopie}")
    k = stav_knihy(kopie)
    zkontroluj(r.status_code == 200 and stav_knihy(vlastni_kopie) is None and not pozvanky(5),
               "smazáním zpěvníku zmizí i jeho nevyřízené pozvánky", r.status_code)
    zkontroluj(k and k["obalka_bajty"] and (OBRAZKY / k["obalka"]).exists(),
               "smazání jiné kopie nerozbije tuhle (obálka i strany zůstanou)")
    with A.app.app_context():
        chybi = [st.image_path for st in A.db.session.get(A.Songbook, kopie).strany
                 if st.image_path and not (OBRAZKY / st.image_path).exists()]
    zkontroluj(not chybi, "žádná strana kopie nepřišla o obrázek", chybi)

    print("\n── konec společných úprav ──")
    def pozvi_a_prijmi(vlastnik, kid, uid, email):
        vlastnik.post(f"/api/my-songbooks/{kid}/share", json={"email": email, "druh": "spolecny"})
        with A.app.app_context():
            pid = A.Pozvanka.query.filter_by(komu_id=uid, songbook_id=kid).first().id
        return klient(uid).post(f"/api/pozvanky/{pid}/prijmout").status_code

    def oznameni(uid):
        with A.app.app_context():
            return [(o.id, o.songbook_id, o.text) for o in A.Oznameni.query.filter_by(user_id=uid)]

    def knihy(uid):
        with A.app.app_context():
            return sorted(b.id for b in A.Songbook.query.filter_by(owner_id=uid))

    def clen(uid, kid):
        with A.app.app_context():
            return A.UserSongbookAccess.query.filter_by(user_id=uid, songbook_id=kid).first() is not None

    _, w = nahraj(u3, "olive")
    kod, telo = uloz(u3, nazev="Společný", strany=[
        {"typ": "pisen", "image_id": w["image_id"], "pisne": [{"nazev": "Naše", "autor": "My"}]}])
    spolecny = telo.get("id")
    zkontroluj(pozvi_a_prijmi(u3, spolecny, 4, EMAIL4) == 200 and clen(4, spolecny), "u4 je členem")
    pred_knihy = knihy(4)
    r = u4.post(f"/api/zpevnik/{spolecny}/clenove/4/odebrat")
    zkontroluj(r.status_code == 403, "člena odebírá jen vlastník", r.status_code)
    r = u3.get(f"/api/zpevnik/{spolecny}/clenove").get_json()
    zkontroluj(r["jsem_vlastnik"] and [c["email"] for c in r["clenove"]] == [EMAIL4],
               "vlastník vidí seznam členů", r)
    r = u3.post(f"/api/zpevnik/{spolecny}/clenove/4/odebrat")
    nove = sorted(set(knihy(4)) - set(pred_knihy))
    oz = oznameni(4)
    zkontroluj(r.status_code == 200 and not clen(4, spolecny) and len(nove) == 1,
               "odebraný člen přijde o přístup a dostane kopii", (r.status_code, nove))
    zkontroluj(len(oz) == 1 and oz[0][1] == nove[0] and "ukončil společné úpravy" in oz[0][2],
               "a upozornění s ní", oz)
    u4.post(f"/api/oznameni/{oz[0][0]}/ponechat")
    zkontroluj(not oznameni(4) and nove[0] in knihy(4), "Ponechat: upozornění zmizí, kopie zůstane")

    pozvi_a_prijmi(u3, spolecny, 4, EMAIL4)
    pred_knihy = knihy(4)
    r = u4.delete(f"/api/my-songbooks/{spolecny}")
    zkontroluj(r.status_code == 200 and not clen(4, spolecny) and knihy(4) == pred_knihy
               and stav_knihy(spolecny) is not None,
               "člen odejde bez kopie; zpěvník vlastníkovi zůstane")
    pozvi_a_prijmi(u3, spolecny, 4, EMAIL4)
    r = u4.delete(f"/api/my-songbooks/{spolecny}?kopie=1")
    zkontroluj(r.status_code == 200 and not clen(4, spolecny) and len(knihy(4)) == len(pred_knihy) + 1,
               "člen odejde a nechá si kopii")

    pozvi_a_prijmi(u3, spolecny, 5, EMAIL5)
    pred5 = knihy(5)
    r = u3.delete(f"/api/my-songbooks/{spolecny}")
    nove5 = sorted(set(knihy(5)) - set(pred5))
    zkontroluj(r.status_code == 200 and stav_knihy(spolecny) is None and len(nove5) == 1 and oznameni(5),
               "smazaný společný zpěvník: člen dostane kopii a upozornění (dřív na něj zpěvník přešel)",
               (r.status_code, nove5))
    with A.app.app_context():
        kk = A.db.session.get(A.Songbook, nove5[0])
        zkontroluj(all((OBRAZKY / st.image_path).exists() for st in kk.strany if st.image_path),
                   "kopie po smazání originálu nepřišla o obrázky")
    u5.post(f"/api/oznameni/{oznameni(5)[0][0]}/zahodit")
    zkontroluj(not oznameni(5) and nove5[0] not in knihy(5), "Zahodit kopii: zpěvník zmizí")

    _, q = nahraj(u5, "maroon")
    kod, telo = uloz(u5, nazev="Pátého", strany=[
        {"typ": "pisen", "image_id": q["image_id"], "pisne": [{"nazev": "Pátá", "autor": "-"}]}])
    pateho = telo.get("id")
    pozvi_a_prijmi(u5, pateho, 4, EMAIL4)
    pred4 = knihy(4)
    with A.app.app_context():
        email5 = A.db.session.get(A.User, 5).email
    r = u5.post("/smazat-ucet", data={"potvrzeni": email5})
    nove4 = sorted(set(knihy(4)) - set(pred4))
    with A.app.app_context():
        zkontroluj(A.db.session.get(A.User, 5) is None and stav_knihy(pateho) is None,
                   "zrušení účtu smaže jeho zpěvníky")
        kk = A.db.session.get(A.Songbook, nove4[0]) if nove4 else None
        zkontroluj(kk is not None and all((OBRAZKY / st.image_path).exists() for st in kk.strany if st.image_path),
                   "členové o zpěvník nepřijdou - mají kopii i s obrázky", nove4)

    print("\n── admin a cizí zpěvník ──")
    z = admin.get(f"/api/zpevnik/{kopie}").get_json()["zpevnik"]
    zkontroluj(z["cizi"] and z["vlastnik"] == EMAIL4,
               "editor ví, že admin upravuje cizí zpěvník a čí", (z["cizi"], z["vlastnik"]))
    z = u4.get(f"/api/zpevnik/{kopie}").get_json()["zpevnik"]
    zkontroluj(not z["cizi"], "vlastníkovi se nic nehlásí")

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
