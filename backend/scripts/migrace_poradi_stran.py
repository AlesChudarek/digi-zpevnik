"""Naplní `song_images.poradi` pořadím stran v rámci písně.

Proč to musí proběhnout teď a ne až při migraci úložiště: u vícestránkové písně je dnes
pořadí stran dané jen pořadím `id` řádku a jedinou nezávislou kontrolou je číslo v názvu
souboru (`page18` před `page19`). Cílový standard z názvů čísla odstraňuje — viz
docs/ukladani-obrazku.md. Kdyby se přejmenovalo dřív, než se pořadí uloží natvrdo,
nezbylo by už čím poznat, že se dvěma písním přehodily strany.

Skript proto pořadí bere z názvů, porovná ho s pořadím `id` a **rozpory vypíše**. Teprve
když se obojí shoduje, je jedno, které z nich se použije.

Pozor na dva tvary, které oba musí projít: píseň přes víc stran (dnes 32) i víc písní na
jedné straně (dnes 18 stran po dvou písních). `poradi` je pořadí v rámci **písně**, takže
sdílená strana může být pro jednu píseň první a pro druhou druhá. Číslo strany ve zpěvníku
to není a nikdy nebude - to drží `songbook_pages.page_number`.

Použití:
    python backend/scripts/migrace_poradi_stran.py                 jen ukáže, co by udělal
    python backend/scripts/migrace_poradi_stran.py --zapsat        zapíše
    python backend/scripts/migrace_poradi_stran.py --db cesta.db   jiná databáze
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VYCHOZI_DB = PROJECT_ROOT / "backend" / "instance" / "zpevnik.db"

# Poslední číslo v názvu souboru: page18 -> 18, zpevnikA4-04 -> 4, xOqzMt5D -> nic.
CISLO = re.compile(r"(\d+)(?!.*\d)")


def cislo_z_nazvu(cesta: str):
    m = CISLO.search(Path(cesta).stem)
    return int(m.group(1)) if m else None


def zajisti_sloupec(db):
    sloupce = {r[1] for r in db.execute("PRAGMA table_info(song_images)")}
    if 'poradi' not in sloupce:
        db.execute("ALTER TABLE song_images ADD COLUMN poradi INTEGER NOT NULL DEFAULT 1")
        print("   sloupec `poradi` doplněn")
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zapsat", action="store_true", help="opravdu zapsat (jinak jen ukáže)")
    ap.add_argument("--db", default=str(VYCHOZI_DB), help="cesta k databázi")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    print(f"databáze: {args.db}")
    zajisti_sloupec(db)

    pisne = {}
    for r in db.execute("SELECT id, song_id, image_path FROM song_images ORDER BY id"):
        pisne.setdefault(r['song_id'], []).append(dict(r))

    sdilene = {r[0] for r in db.execute(
        "SELECT image_path FROM song_images GROUP BY image_path HAVING COUNT(*) > 1")}

    zmeny = []          # (row_id, poradi)
    rozpory = []        # písně, kde se pořadí z názvu liší od pořadí id
    bez_cisel = []      # písně, kde se z názvů pořadí vyčíst nedá
    vicestrannych = 0

    for song_id, radky in pisne.items():
        if len(radky) > 1:
            vicestrannych += 1
        cisla = [cislo_z_nazvu(r['image_path']) for r in radky]
        pouzitelna = all(c is not None for c in cisla) and len(set(cisla)) == len(cisla)

        if pouzitelna:
            podle_nazvu = [r['id'] for _, r in sorted(zip(cisla, radky), key=lambda x: x[0])]
        else:
            podle_nazvu = [r['id'] for r in radky]
            if len(radky) > 1:
                bez_cisel.append((song_id, [r['image_path'] for r in radky]))

        podle_id = [r['id'] for r in radky]
        if len(radky) > 1 and podle_nazvu != podle_id:
            rozpory.append((song_id, [r['image_path'] for r in radky]))

        for poradi, row_id in enumerate(podle_nazvu, 1):
            zmeny.append((poradi, row_id))

    print(f"\npísní celkem: {len(pisne)}, z toho vícestránkových: {vicestrannych}")
    print(f"stran nesoucích víc písní: {len(sdilene)}")
    print(f"řádků k naplnění: {len(zmeny)}")

    if rozpory:
        print(f"\n⚠️  {len(rozpory)} písní, kde se pořadí z názvu LIŠÍ od pořadí podle id.")
        print("   Tohle je přesně to, co by se po přejmenování už nedalo poznat — zkontroluj ručně:")
        for song_id, cesty in rozpory:
            print(f"   {song_id}: {', '.join(cesty)}")
    else:
        print("\n✅ u každé vícestránkové písně sedí pořadí z názvu s pořadím podle id")

    if bez_cisel:
        print(f"\n⚠️  {len(bez_cisel)} vícestránkových písní bez čísel v názvu, "
              f"použije se pořadí podle id:")
        for song_id, cesty in bez_cisel:
            print(f"   {song_id}: {', '.join(Path(c).name for c in cesty)}")

    if not args.zapsat:
        print("\n(suchý běh — nic se nezapsalo, spusť s --zapsat)")
        return 0

    db.executemany("UPDATE song_images SET poradi = ? WHERE id = ?", zmeny)
    db.commit()

    # Kontrola po zápisu: každá píseň musí mít souvislou řadu 1..N.
    spatne = 0
    for song_id, radky in pisne.items():
        mam = [r[0] for r in db.execute(
            "SELECT poradi FROM song_images WHERE song_id = ? ORDER BY poradi", (song_id,))]
        if mam != list(range(1, len(radky) + 1)):
            print(f"   ❌ {song_id}: {mam}")
            spatne += 1
    print(f"\n{'✅ zapsáno, každá píseň má souvislou řadu 1..N' if not spatne else f'❌ {spatne} písní má díru v číslování'}")
    return 1 if spatne else 0


if __name__ == "__main__":
    sys.exit(main())
