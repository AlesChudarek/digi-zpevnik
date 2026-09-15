"""Přesun obrázků do struktury podle docs/ukladani-obrazku.md.

Dnes žijí vedle sebe tři tvary cest a každý nese něco, co se mění nezávisle na obrázku:
název zpěvníku, e-mail uživatele, číslo strany ve zpěvníku a zpěvník, ze kterého obrázek
náhodou pochází. Tenhle skript z cest odstraní všechno měnitelné.

Cílový tvar:

    verejne/songbooks/<songbook_id>/covers/front-out.png    obálky, čitelně
    verejne/pages/001234.png                                strany, ploché
    uzivatele/<user_id>/songbooks/<songbook_id>/covers/...
    uzivatele/<user_id>/pages/001235.png

Proč strany ploché a ne pod zpěvníkem nebo pod písní: vztah je many-to-many na obě
strany. Píseň může být přes víc stran (32 písní), na jedné straně můžou být dvě písně
(18 stran) a jedna píseň může být ve dvou zpěvnících (70 písní). Ať se strana zařadí
pod kohokoliv z nich, pro někoho dalšího bude ležet pod cizí složkou - a to je přesně
dnešní chyba. Strana tedy nepatří ničemu; vazby drží `song_images` a `songbook_pages`.

Co tenhle skript NEDĚLÁ: nezavádí tabulku `images`. To je normalizace, která se souborů
netýká a sahala by na každé místo, co dnes pracuje s cestou jako s řetězcem. Dělat obojí
naráz je zbytečné riziko - identitou strany je zatím její cesta, což funguje, protože
sdílená strana má prostě tutéž cestu.

Použití:
    python backend/scripts/migrace_uloziste.py                  suchý běh, nic nesáhne
    python backend/scripts/migrace_uloziste.py --provest        zkopíruje a přepíše DB
    python backend/scripts/migrace_uloziste.py --db X --data Y  jiné umístění
"""
import argparse
import re
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VYCHOZI_DB = PROJECT_ROOT / "backend" / "instance" / "zpevnik.db"
VYCHOZI_DATA = PROJECT_ROOT / "data"

# Sloupec obálky -> role v novém jménu. `preview` tu schválně není: je to ukazatel na
# jednu z těch čtyř, ne pátý obrázek, takže se přepíše na cíl toho, na co ukazuje.
OBALKY = {
    'img_path_cover_front_outer': 'front-out',
    'img_path_cover_front_inner': 'front-in',
    'img_path_cover_back_inner': 'back-in',
    'img_path_cover_back_outer': 'back-out',
}


def uzivatel_z_cesty(rel: str):
    """Z `users/8_ales.chudarek-seznam.cz/...` vytáhne 8. Kořen se řídí tím, kdo obrázek
    nahrál - i když ho pak používá někdo jiný."""
    m = re.match(r"users/(\d+)_", rel)
    return int(m.group(1)) if m else None


def stara_absolutni(rel: str, data: Path) -> Path:
    if rel.startswith('users/'):
        return data / 'private' / 'users' / Path(rel).relative_to('users')
    return data / 'public' / 'images' / 'songbooks' / rel


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provest", action="store_true", help="opravdu kopírovat a přepsat DB")
    ap.add_argument("--db", default=str(VYCHOZI_DB))
    ap.add_argument("--data", default=str(VYCHOZI_DATA))
    args = ap.parse_args()

    data = Path(args.data)
    novy_koren = data / 'images'
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    print(f"databáze: {args.db}")
    print(f"data:     {data}")
    chyby = []

    # ---- 0. Sirotci: písně, které nejsou v žádném zpěvníku ----
    sirotci = [r['id'] for r in db.execute(
        "SELECT id FROM songs s WHERE NOT EXISTS "
        "(SELECT 1 FROM songbook_pages sp WHERE sp.song_id = s.id)")]
    sirotci_obrazky = [r['id'] for r in db.execute(
        "SELECT si.id FROM song_images si WHERE NOT EXISTS "
        "(SELECT 1 FROM songbook_pages sp WHERE sp.song_id = si.song_id)")]
    print(f"\n0. sirotci k odstranění: {len(sirotci)} písní, {len(sirotci_obrazky)} řádků "
          f"v song_images")

    # ---- 1. Posbírat všechny cesty, které po úklidu zůstanou ----
    strany = {}   # stará cesta -> None (zatím)
    for r in db.execute(
            "SELECT DISTINCT image_path FROM song_images si WHERE EXISTS "
            "(SELECT 1 FROM songbook_pages sp WHERE sp.song_id = si.song_id)"):
        strany[r['image_path']] = None

    obalky = {}   # (songbook_id, sloupec) -> stará cesta
    for r in db.execute("SELECT * FROM songbooks"):
        for sloupec in OBALKY:
            if r[sloupec]:
                obalky[(r['id'], sloupec)] = r[sloupec]

    # ---- 2. Namapovat na nové cesty ----
    mapa = {}     # stará cesta -> nová cesta (relativně k data/images)
    # Obálky první: jejich jméno je dané rolí, ne pořadím.
    for (sbid, sloupec), stara in sorted(obalky.items()):
        uid = uzivatel_z_cesty(stara)
        koren = f"uzivatele/{uid}" if uid else "verejne"
        pripona = Path(stara).suffix.lower() or '.png'
        nova = f"{koren}/songbooks/{sbid}/covers/{OBALKY[sloupec]}{pripona}"
        if stara in mapa and mapa[stara] != nova:
            chyby.append(f"obálka {stara} má vést na {nova} i na {mapa[stara]}")
        mapa[stara] = nova

    # Strany dostanou pořadové číslo. Řadí se podle dnešní cesty, ať je běh opakovatelný
    # a suchý běh říká totéž co ostrý.
    def prirozene(cesta: str):
        """Řadí page9 před page10, ne za něj. Id jsou sice neprůhledná, ale když už se
        přidělují, ať výsledek aspoň drží pořadí zpěvníku."""
        return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', cesta)]

    citac = Counter()
    for stara in sorted(strany, key=prirozene):
        if stara in mapa:
            continue  # už zabraná jako obálka, viz kontrola níž
        uid = uzivatel_z_cesty(stara)
        koren = f"uzivatele/{uid}" if uid else "verejne"
        citac[koren] += 1
        pripona = Path(stara).suffix.lower() or '.png'
        mapa[stara] = f"{koren}/pages/{citac[koren]:06d}{pripona}"

    # ---- 3. Kontroly, které musí projít, než se něco hne ----
    print(f"\n1. k přesunu: {len(mapa)} souborů "
          f"({len(obalky)} obálkových odkazů, {sum(citac.values())} stran)")

    chybi = [s for s in mapa if not stara_absolutni(s, data).exists()]
    if chybi:
        chyby.append(f"{len(chybi)} zdrojových souborů neexistuje: {chybi[:3]}")

    cile = Counter(mapa.values())
    kolize = [c for c, n in cile.items() if n > 1]
    if kolize:
        chyby.append(f"{len(kolize)} cílů by dostalo víc souborů: {kolize[:3]}")

    obalka_i_strana = set(obalky.values()) & set(strany)
    if obalka_i_strana:
        chyby.append(f"soubor je zároveň obálka i strana: {sorted(obalka_i_strana)[:3]}")

    # Každý soubor na disku musí být buď v mapě, nebo prokazatelně sirotek.
    na_disku = set()
    for koren in (data / 'public' / 'images' / 'songbooks', data / 'private' / 'users'):
        if koren.exists():
            na_disku |= {p for p in koren.rglob('*')
                         if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg')}
    zmapovane = {stara_absolutni(s, data) for s in mapa}
    nezname = na_disku - zmapovane
    if nezname:
        chyby.append(f"{len(nezname)} souborů na disku není v mapě: "
                     f"{[str(p.name) for p in sorted(nezname)[:5]]}")

    print(f"2. zdroje existují: {len(mapa) - len(chybi)}/{len(mapa)}")
    print(f"   cíle jsou jedinečné: {'ano' if not kolize else 'NE'}")
    print(f"   soubory na disku bez mapování: {len(nezname)}")

    print("\n3. ukázka mapování:")
    for stara in list(sorted(obalky.values()))[:2] + sorted(strany)[:3]:
        print(f"   {stara}\n     -> {mapa[stara]}")

    # Kontrola tvarů, kvůli kterým je struktura taková, jaká je
    print("\n4. kontrola vazeb many-to-many:")
    for popis, dotaz in (
        ("dvě písně na jedné straně",
         "SELECT image_path, COUNT(*) c FROM song_images GROUP BY image_path HAVING c>1"),
        ("píseň přes víc stran",
         "SELECT song_id, COUNT(*) c FROM song_images GROUP BY song_id HAVING c>1"),
    ):
        pocet = len(list(db.execute(dotaz)))
        print(f"   {popis}: {pocet}")
    sdilene_zpevniky = len(list(db.execute(
        "SELECT song_id FROM songbook_pages GROUP BY song_id HAVING COUNT(DISTINCT songbook_id)>1")))
    print(f"   píseň ve dvou zpěvnících: {sdilene_zpevniky}")
    print("   (všechny tři drží DB, ne cesty — po přesunu se nemění)")

    if chyby:
        print("\n❌ NEPOKRAČUJI:")
        for c in chyby:
            print(f"   {c}")
        return 1

    if not args.provest:
        print("\n(suchý běh — nic se nezkopírovalo ani nepřepsalo, spusť s --provest)")
        return 0

    # ---- 4. Kopírovat, ne přesouvat: starý strom zůstane ležet ----
    print(f"\n5. kopíruji do {novy_koren}")
    for stara, nova in sorted(mapa.items()):
        cil = novy_koren / nova
        cil.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stara_absolutni(stara, data), cil)
    print(f"   {len(mapa)} souborů")

    # ---- 5. Přepis DB v jedné transakci ----
    print("6. přepisuji cesty v databázi")
    db.execute("BEGIN")
    try:
        for row_id in sirotci_obrazky:
            db.execute("DELETE FROM song_images WHERE id = ?", (row_id,))
        for song_id in sirotci:
            db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        for stara, nova in mapa.items():
            db.execute("UPDATE song_images SET image_path = ? WHERE image_path = ?",
                       (nova, stara))
        for (sbid, sloupec), stara in obalky.items():
            db.execute(f"UPDATE songbooks SET {sloupec} = ? WHERE id = ?",
                       (mapa[stara], sbid))
        # preview je ukazatel: přepíše se na cíl toho, na co ukazoval
        for r in db.execute("SELECT id, img_path_cover_preview FROM songbooks "
                            "WHERE img_path_cover_preview IS NOT NULL").fetchall():
            nova = mapa.get(r['img_path_cover_preview'])
            if nova:
                db.execute("UPDATE songbooks SET img_path_cover_preview = ? WHERE id = ?",
                           (nova, r['id']))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    print("   hotovo")

    zbyly = [r['image_path'] for r in db.execute(
        "SELECT DISTINCT image_path FROM song_images WHERE image_path NOT LIKE 'verejne/%' "
        "AND image_path NOT LIKE 'uzivatele/%'")]
    print(f"\n{'✅ všechny cesty jsou v novém tvaru' if not zbyly else f'❌ zbylo {len(zbyly)} starých cest: {zbyly[:3]}'}")
    print("   starý strom zůstal ležet, smaž ho až po ověření provozem")
    return 1 if zbyly else 0


if __name__ == "__main__":
    sys.exit(main())
