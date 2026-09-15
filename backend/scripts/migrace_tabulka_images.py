"""Zavede tabulku `images` a nahradí jí cesty rozeseté po šesti sloupcích.

Identitou obrázku byla jeho cesta jako text, uložená v `song_images.image_path`,
v `songbook_intro_outro_images.image_path` a v pěti sloupcích `songbooks`. Mělo to tři
následky: otázka „ukazuje na tenhle soubor ještě někdo?" se musela ptát šestkrát
a porovnávat řetězce, nic nehlídalo, že cesta vůbec na něco ukazuje, a přesun souboru
znamenal přepsat šest sloupců místo jednoho řádku.

Po migraci je jeden řádek `images` na soubor a všude se na něj ukazuje cizím klíčem.
Sdílení vychází samo: strana se dvěma písněmi je jeden řádek `images` a dva řádky
`song_images`.

Je to čistě databázová změna, souborů se nedotkne.

Použití:
    python backend/scripts/migrace_tabulka_images.py                jen ukáže, co udělá
    python backend/scripts/migrace_tabulka_images.py --provest      provede
    python backend/scripts/migrace_tabulka_images.py --db X         jiná databáze
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VYCHOZI_DB = PROJECT_ROOT / "backend" / "instance" / "zpevnik.db"

# sloupec v songbooks -> nový sloupec s cizím klíčem
OBALKY = {
    'img_path_cover_preview': 'cover_preview_id',
    'img_path_cover_front_outer': 'cover_front_outer_id',
    'img_path_cover_front_inner': 'cover_front_inner_id',
    'img_path_cover_back_inner': 'cover_back_inner_id',
    'img_path_cover_back_outer': 'cover_back_outer_id',
}


def sloupce(db, tabulka):
    return {r[1] for r in db.execute(f"PRAGMA table_info({tabulka})")}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provest", action="store_true", help="opravdu provést")
    ap.add_argument("--db", default=str(VYCHOZI_DB))
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    print(f"databáze: {args.db}")

    if 'image_id' in sloupce(db, 'song_images'):
        print("✅ už je hotovo, tabulka images existuje a sloupce ukazují na ni")
        return 0

    # --- co všechno je dnes cestou ---
    cesty = {r[0] for r in db.execute("SELECT image_path FROM song_images") if r[0]}
    cesty |= {r[0] for r in db.execute("SELECT image_path FROM songbook_intro_outro_images") if r[0]}
    for sloupec in OBALKY:
        cesty |= {r[0] for r in db.execute(f"SELECT {sloupec} FROM songbooks") if r[0]}

    pocty = {
        'song_images': db.execute("SELECT COUNT(*) FROM song_images").fetchone()[0],
        'intro_outro': db.execute("SELECT COUNT(*) FROM songbook_intro_outro_images").fetchone()[0],
    }
    odkazu_obalek = sum(
        db.execute(f"SELECT COUNT(*) FROM songbooks WHERE {s} IS NOT NULL").fetchone()[0]
        for s in OBALKY)

    print(f"\nunikátních cest (= řádků v images): {len(cesty)}")
    print(f"odkazů, které je nahradí: {pocty['song_images']} v song_images, "
          f"{pocty['intro_outro']} v intro/outro, {odkazu_obalek} v obálkách "
          f"= {pocty['song_images'] + pocty['intro_outro'] + odkazu_obalek} celkem")
    sdilene = pocty['song_images'] + pocty['intro_outro'] + odkazu_obalek - len(cesty)
    print(f"z toho {sdilene} odkazů míří na cestu, kterou už používá někdo jiný "
          f"— právě ty dnes žijí jako duplicitní řetězec")

    if not args.provest:
        print("\n(suchý běh — nic se nezměnilo, spusť s --provest)")
        return 0

    db.execute("BEGIN")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS images ("
                   "id INTEGER PRIMARY KEY, cesta TEXT NOT NULL UNIQUE)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_images_cesta ON images (cesta)")
        db.executemany("INSERT OR IGNORE INTO images (cesta) VALUES (?)",
                       [(c,) for c in sorted(cesty)])

        db.execute("ALTER TABLE song_images ADD COLUMN image_id INTEGER REFERENCES images(id)")
        db.execute("UPDATE song_images SET image_id = "
                   "(SELECT id FROM images WHERE images.cesta = song_images.image_path)")
        db.execute("ALTER TABLE songbook_intro_outro_images ADD COLUMN image_id INTEGER REFERENCES images(id)")
        db.execute("UPDATE songbook_intro_outro_images SET image_id = (SELECT id FROM images "
                   "WHERE images.cesta = songbook_intro_outro_images.image_path)")
        for stary, novy in OBALKY.items():
            db.execute(f"ALTER TABLE songbooks ADD COLUMN {novy} INTEGER REFERENCES images(id)")
            db.execute(f"UPDATE songbooks SET {novy} = "
                       f"(SELECT id FROM images WHERE images.cesta = songbooks.{stary})")

        # Kontrola PŘED zahozením starých sloupců: každý neprázdný text musí mít svůj klíč.
        chyby = []
        for tab in ('song_images', 'songbook_intro_outro_images'):
            n = db.execute(f"SELECT COUNT(*) FROM {tab} "
                           f"WHERE image_path IS NOT NULL AND image_id IS NULL").fetchone()[0]
            if n:
                chyby.append(f"{tab}: {n} řádků bez image_id")
        for stary, novy in OBALKY.items():
            n = db.execute(f"SELECT COUNT(*) FROM songbooks "
                           f"WHERE {stary} IS NOT NULL AND {novy} IS NULL").fetchone()[0]
            if n:
                chyby.append(f"songbooks.{stary}: {n} bez klíče")
        if chyby:
            raise RuntimeError("nepřevedené odkazy: " + "; ".join(chyby))

        db.execute("ALTER TABLE song_images DROP COLUMN image_path")
        db.execute("ALTER TABLE songbook_intro_outro_images DROP COLUMN image_path")
        for stary in OBALKY:
            db.execute(f"ALTER TABLE songbooks DROP COLUMN {stary}")
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    n = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    print(f"\n✅ hotovo: {n} řádků v images, staré textové sloupce zahozeny")
    return 0


if __name__ == "__main__":
    sys.exit(main())
