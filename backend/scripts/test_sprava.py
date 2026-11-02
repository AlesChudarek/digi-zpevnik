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


def main():
    moje_zpevniky()
    hledani()
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
