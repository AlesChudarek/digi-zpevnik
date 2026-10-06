"""Migrace z modelu postaveného na písních na model postavený na stranách.

Dřív v databázi strana neexistovala. Zpěvník byl seznam písní s čísly stran
(`songbook_pages`) a obrázky visely na písních (`song_images`); stranu teprve skládala
čtečka tak, že k-té číslo strany písně spárovala s k-tým obrázkem písně. Všechno, co
nebylo písní, se za ni muselo přestrojit: prázdná strana byla „píseň“ bez obrázku,
osmisměrka „píseň“ s `is_non_song`.

Teď:
    strany            (songbook_id, poradi, image_id | NULL, popisek)
    pisne_na_strane   (strana_id, song_id, poradi_v_pisni)
    songbooks.prvni_cislo_strany

Strany se skládají **přesně tím algoritmem, kterým je dosud skládala čtečka**
(`build_songbook_content_pages` před migrací), takže čtečka i export ukážou totéž.
Kde by stará data dávala nejednoznačný výsledek (mezera v číslování, písně na jedné
straně, které se neshodnou na obrázku…), skript skončí a nic nezapíše.

Spuštění:
    python backend/scripts/migrace_strany.py [cesta.db]             # jen rozbor
    python backend/scripts/migrace_strany.py [cesta.db] --zapsat    # záloha + zápis

Před zápisem udělá zálohu přes `conn.backup()` vedle databáze. Celý zápis je jedna
transakce: buď proběhne celý, nebo vůbec.
"""

import argparse
import collections
import datetime
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VYCHOZI_DB = PROJECT_ROOT / "backend" / "instance" / "zpevnik.db"

NON_SONG_TITLE = '<Prázdná strana>'

DDL = [
    """CREATE TABLE strany (
	id INTEGER NOT NULL,
	songbook_id VARCHAR NOT NULL,
	poradi INTEGER NOT NULL,
	image_id INTEGER,
	popisek VARCHAR,
	PRIMARY KEY (id),
	CONSTRAINT uq_strana_poradi UNIQUE (songbook_id, poradi),
	FOREIGN KEY(songbook_id) REFERENCES songbooks (id),
	FOREIGN KEY(image_id) REFERENCES images (id)
)""",
    "CREATE INDEX ix_strany_image_id ON strany (image_id)",
    "CREATE INDEX ix_strany_songbook_id ON strany (songbook_id)",
    """CREATE TABLE pisne_na_strane (
	id INTEGER NOT NULL,
	strana_id INTEGER NOT NULL,
	song_id VARCHAR NOT NULL,
	poradi_v_pisni INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pisen_na_strane UNIQUE (strana_id, song_id),
	FOREIGN KEY(strana_id) REFERENCES strany (id),
	FOREIGN KEY(song_id) REFERENCES songs (id)
)""",
    "CREATE INDEX ix_pisne_na_strane_strana_id ON pisne_na_strane (strana_id)",
    "CREATE INDEX ix_pisne_na_strane_song_id ON pisne_na_strane (song_id)",
]

NEPOUZITE_OBRAZKY = (
    "SELECT i.cesta FROM images i WHERE NOT EXISTS (SELECT 1 FROM strany s WHERE s.image_id = i.id) "
    "AND NOT EXISTS (SELECT 1 FROM songbooks b WHERE i.id IN (b.cover_preview_id, "
    "b.cover_front_outer_id, b.cover_front_inner_id, b.cover_back_inner_id, b.cover_back_outer_id))")

# Tabulky, které migrací končí. Poslední dvě jsou z dob seedování a jsou prázdné.
STARE_TABULKY = ["songbook_pages", "song_images", "song_parts", "songbook_intro_outro_images"]


def tabulky(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def je_nepisnova(title, is_non_song):
    """Stejné pravidlo jako dřívější `_is_non_song` v app.py."""
    title = title or ''
    return bool(is_non_song) or title == NON_SONG_TITLE or title.startswith('Non-song page')


def popisek_z_nazvu(title):
    """Jméno nepísňové strany, pokud nějaké má. Vygenerovaná jména se nepřenáší."""
    title = (title or '').strip()
    if not title or title == NON_SONG_TITLE or title.startswith('Non-song page'):
        return None
    return title


def sloz_strany(conn):
    """Strany všech zpěvníků podle starých dat. Vrací (strany, chyby).

    strany: {songbook_id: {'prvni': int, 'strany': [{'image_id', 'popisek', 'pisne':
    [(song_id, poradi_v_pisni)]}]}}
    """
    songs = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, title, is_non_song FROM songs")}
    obrazky_pisne = collections.defaultdict(list)
    for song_id, image_id in conn.execute(
            "SELECT song_id, image_id FROM song_images ORDER BY song_id, poradi, id"):
        obrazky_pisne[song_id].append(image_id)

    radky_zpevniku = collections.defaultdict(list)
    for rid, bid, sid, pn in conn.execute(
            "SELECT id, songbook_id, song_id, page_number FROM songbook_pages "
            "ORDER BY songbook_id, page_number, id"):
        radky_zpevniku[bid].append((rid, sid, pn))

    vysledek, chyby = {}, []
    for bid, radky in radky_zpevniku.items():
        # --- přesně jako build_songbook_content_pages ---
        strany_pisne = {}                       # pořadí vložení = pořadí prvního výskytu
        for _rid, sid, pn in radky:
            strany_pisne.setdefault(sid, []).append(pn)
        obrazek_strany = {}
        for sid, cisla in strany_pisne.items():
            obr = obrazky_pisne.get(sid, [])
            for offset, pn in enumerate(sorted(set(cisla))):
                if pn in obrazek_strany:
                    continue
                obrazek_strany[pn] = obr[offset] if offset < len(obr) else None
        # --- konec převzaté logiky ---

        cisla = sorted(obrazek_strany)
        if cisla != list(range(cisla[0], cisla[0] + len(cisla))):
            chyby.append(f"{bid}: mezera v číslování stran {cisla}")
            continue

        # Které písně jsou na které straně, v pořadí řádků (= dřívější pořadí v obsahu)
        na_strane = collections.defaultdict(list)
        for _rid, sid, pn in radky:
            if sid not in songs:
                chyby.append(f"{bid}: řádek odkazuje na neexistující píseň {sid}")
                continue
            serazene = sorted(set(strany_pisne[sid]))
            poradi_v_pisni = serazene.index(pn) + 1
            if any(s == sid for s, _ in na_strane[pn]):
                chyby.append(f"{bid}: píseň {sid} je na straně {pn} dvakrát")
                continue
            na_strane[pn].append((sid, poradi_v_pisni))

        for sid, cisla_pisne in strany_pisne.items():
            u = sorted(set(cisla_pisne))
            if u != list(range(u[0], u[0] + len(u))):
                chyby.append(f"{bid}: píseň {sid} leží na nesouvislých stranách {u}")
            obr = obrazky_pisne.get(sid, [])
            if obr and len(obr) != len(u):
                chyby.append(f"{bid}: píseň {sid} má {len(u)} stran, ale {len(obr)} obrázků")
            # Každá píseň na straně musí ukazovat na týž obrázek, jinak by výsledek
            # závisel na tom, která z nich vyhrála - a to už rozhodnout neumíme.
            for offset, pn in enumerate(u):
                vlastni = obr[offset] if offset < len(obr) else None
                if obr and vlastni != obrazek_strany[pn]:
                    chyby.append(f"{bid}: na straně {pn} se písně neshodnou na obrázku")

        strany = []
        for pn in cisla:
            pisne, popisky = [], []
            for sid, por in na_strane[pn]:
                title, is_non_song = songs[sid]
                if je_nepisnova(title, is_non_song):
                    p = popisek_z_nazvu(title)
                    if p:
                        popisky.append(p)
                else:
                    pisne.append((sid, por))
            if pisne and popisky:
                chyby.append(f"{bid}: strana {pn} nese píseň i nepísňovou stranu")
            if len(popisky) > 1:
                chyby.append(f"{bid}: strana {pn} má víc popisků {popisky}")
            strany.append({'image_id': obrazek_strany[pn],
                           'popisek': popisky[0] if popisky else None,
                           'pisne': pisne})
        vysledek[bid] = {'prvni': cisla[0], 'strany': strany}
    return vysledek, chyby


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", nargs="?", default=str(VYCHOZI_DB))
    ap.add_argument("--zapsat", action="store_true", help="opravdu zapsat (jinak jen rozbor)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"❌ databáze {db_path} neexistuje")
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = OFF")

    t = tabulky(conn)
    if "songbook_pages" not in t:
        raise SystemExit("✅ Databáze už je po migraci (songbook_pages neexistuje), není co dělat.")
    if "strany" in t and conn.execute("SELECT COUNT(*) FROM strany").fetchone()[0]:
        raise SystemExit("❌ Tabulka strany už obsahuje data, ale staré tabulky taky. "
                         "Tohle je napůl provedená migrace - obnov zálohu a pusť to znovu.")

    plan, chyby = sloz_strany(conn)
    if chyby:
        print("❌ Stará data nejdou převést jednoznačně, nic se nezapisuje:")
        for c in chyby:
            print("   ", c)
        raise SystemExit(1)

    nepisnove = [r[0] for r in conn.execute("SELECT id, title, is_non_song FROM songs")
                 if je_nepisnova(r[1], r[2])]
    vsech_pisni = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    v_planu = {sid for z in plan.values() for s in z['strany'] for sid, _ in s['pisne']}
    mimo = [r[0] for r in conn.execute("SELECT id FROM songs")
            if r[0] not in v_planu and r[0] not in set(nepisnove)]

    n_stran = sum(len(z['strany']) for z in plan.values())
    print(f"Zpěvníků s obsahem: {len(plan)}")
    print(f"Stran:              {n_stran}  "
          f"(prázdných {sum(1 for z in plan.values() for s in z['strany'] if s['image_id'] is None)}, "
          f"bez písně {sum(1 for z in plan.values() for s in z['strany'] if not s['pisne'])}, "
          f"s popiskem {sum(1 for z in plan.values() for s in z['strany'] if s['popisek'])}, "
          f"s víc písněmi {sum(1 for z in plan.values() for s in z['strany'] if len(s['pisne']) > 1)})")
    print(f"Vazeb píseň-strana: {sum(len(s['pisne']) for z in plan.values() for s in z['strany'])}")
    print(f"Písní:              {vsech_pisni} → {vsech_pisni - len(nepisnove) - len(mimo)} "
          f"(odpadne {len(nepisnove)} přestrojených nepísňových stran"
          f"{f' a {len(mimo)} písní mimo zpěvníky' if mimo else ''})")
    # Sdílení zpěvníku, který už neexistuje. Vznikalo to mazáním zpěvníku, které
    # záznamy o sdílení nechávalo ležet; nic neznamenají a porušují cizí klíč.
    osirela_sdileni = conn.execute(
        "SELECT user_id, songbook_id FROM user_songbook_access a WHERE NOT EXISTS "
        "(SELECT 1 FROM songbooks s WHERE s.id = a.songbook_id)").fetchall()
    print(f"Sdílení neexistujících zpěvníků: {len(osirela_sdileni)}"
          f"{'  ' + str(osirela_sdileni) if osirela_sdileni else ''}")
    jinak = [f"{b} od {z['prvni']}" for b, z in sorted(plan.items()) if z['prvni'] != 1]
    print(f"Číslování od jiné strany než 1: {', '.join(jinak) or 'žádné'}")

    if not args.zapsat:
        print("\nJen rozbor. Zápis: --zapsat")
        return

    cas = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    zaloha = db_path.with_name(f"{db_path.name}.pred-stranami-{cas}")
    with sqlite3.connect(str(zaloha)) as cil:
        conn.backup(cil)
    print(f"\nZáloha: {zaloha}")

    try:
        conn.execute("BEGIN")
        if "strany" in t:      # prázdné, založené create_all - nahradí se přesným DDL
            conn.execute("DROP TABLE IF EXISTS pisne_na_strane")
            conn.execute("DROP TABLE strany")
        for prikaz in DDL:
            conn.execute(prikaz)
        sloupce = {r[1] for r in conn.execute("PRAGMA table_info(songbooks)")}
        if "prvni_cislo_strany" not in sloupce:
            conn.execute("ALTER TABLE songbooks ADD COLUMN prvni_cislo_strany "
                         "INTEGER NOT NULL DEFAULT 1")

        for bid, z in sorted(plan.items()):
            conn.execute("UPDATE songbooks SET prvni_cislo_strany = ? WHERE id = ?",
                         (z['prvni'], bid))
            for poradi, s in enumerate(z['strany']):
                cur = conn.execute(
                    "INSERT INTO strany (songbook_id, poradi, image_id, popisek) "
                    "VALUES (?, ?, ?, ?)", (bid, poradi, s['image_id'], s['popisek']))
                for sid, por in s['pisne']:
                    conn.execute("INSERT INTO pisne_na_strane (strana_id, song_id, "
                                 "poradi_v_pisni) VALUES (?, ?, ?)",
                                 (cur.lastrowid, sid, por))

        for sid in nepisnove + mimo:
            conn.execute("DELETE FROM songs WHERE id = ?", (sid,))
        # Autor „System“ existoval jen kvůli přestrojeným nepísňovým stranám.
        conn.execute("DELETE FROM authors WHERE name = 'System' AND NOT EXISTS "
                     "(SELECT 1 FROM songs WHERE songs.author_id = authors.id)")
        conn.execute("DELETE FROM user_songbook_access WHERE NOT EXISTS "
                     "(SELECT 1 FROM songbooks s WHERE s.id = user_songbook_access.songbook_id)")
        for tab in STARE_TABULKY:
            conn.execute(f"DROP TABLE IF EXISTS {tab}")
        # Řádky images, na které po migraci nic neukazuje. Vznikaly odebráním obálky,
        # které smazalo soubor, ale řádek nechalo.
        nepouzite = conn.execute(NEPOUZITE_OBRAZKY).fetchall()
        conn.execute(f"DELETE FROM images WHERE cesta IN ({NEPOUZITE_OBRAZKY})")
        conn.execute("ALTER TABLE songs DROP COLUMN is_non_song")

        vadne = conn.execute("PRAGMA foreign_key_check").fetchall()
        if vadne:
            raise RuntimeError(f"porušené cizí klíče: {vadne[:5]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        print("❌ Zápis selhal, transakce vrácena, databáze je beze změny.")
        raise

    if nepouzite:
        print(f"Smazáno {len(nepouzite)} řádků images bez odkazu: {[r[0] for r in nepouzite]}")
    stav = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"integrity_check: {stav}")
    print(f"✅ Hotovo: {conn.execute('SELECT COUNT(*) FROM strany').fetchone()[0]} stran, "
          f"{conn.execute('SELECT COUNT(*) FROM pisne_na_strane').fetchone()[0]} vazeb, "
          f"{conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0]} písní.")
    conn.execute("VACUUM")


if __name__ == "__main__":
    sys.exit(main())
