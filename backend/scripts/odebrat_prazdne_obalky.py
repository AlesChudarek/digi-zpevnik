#!/usr/bin/env python3
"""Odebere obálky, které nejsou nic než jednolitá barevná plocha.

Taková obálka je horší než nic. Barva je v ní zapečená v pixelech, takže když se zpěvník
přebarví, průhledné vnější obálky se změní, ale tahle zůstane ve staré barvě. Prázdnou
plochu umí export i čtečka nakreslit samy - a nakreslená se přebarví se zpěvníkem.

Maže se ale po celých zpěvnících. Měnitelnost barvy je vlastnost celé obálky: kdyby se
prázdná strana zahodila u zpěvníku, jehož ostatní strany zůstávají neprůhledné, přebarvení
by změnilo jen tu dokreslenou a zbytek nechalo ve staré barvě. U takového zpěvníku tedy
prázdné obálky necháváme ležet - barva se u něj měnit nebude nikde.

Uvnitř měnitelného zpěvníku se smaže soubor, který je po složení na barvu zpěvníku celý
tou barvou.

Sloupec v DB se nastaví na prázdno. Náhled ukazující na mizející soubor se přesměruje na
přední vnější obálku, a když ani ta nezbyde, vyprázdní se taky.

POZOR: sahá do databáze, takže musí běžet proti té živé na serveru, ne proti záloze.

    python backend/scripts/odebrat_prazdne_obalky.py                nanečisto
    python backend/scripts/odebrat_prazdne_obalky.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

sys.path.insert(0, str(SCRIPT_DIR))
from _obalky import SLOTY, rozbor_zpevniku  # noqa: E402

# Role -> sloupec s cizím klíčem do images. Od migrace schématu už v songbooks není text
# s cestou, ale odkaz na řádek.
SLOUPEC = {r: f'cover_{r}_id' for r in SLOTY}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=str(REPO_ROOT / 'data'))
    ap.add_argument('--db', default=str(REPO_ROOT / 'backend' / 'instance' / 'zpevnik.db'))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    # Jediný kořen, stejně jako _abs_image_path v app.py.
    obrazky = Path(args.data) / 'images'

    def abs_cesta(rel: str) -> Path:
        return obrazky / rel

    db = Path(args.db)
    if not db.exists():
        print(f"❌ DB nenalezena: {db}")
        return 2

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    # Cesty se tahají joinem přes images, ať zbytek skriptu pracuje s cestami jako dřív.
    vybery = ", ".join(f"i_{r}.cesta AS {r}" for r in SLOTY)
    joiny = " ".join(f"LEFT JOIN images i_{r} ON i_{r}.id = s.{SLOUPEC[r]}" for r in SLOTY)
    knihy = con.execute(
        f"SELECT s.id, s.color, i_prev.cesta AS preview, {vybery} FROM songbooks s "
        f"LEFT JOIN images i_prev ON i_prev.id = s.cover_preview_id {joiny}").fetchall()

    ke_smazani = []      # (kniha, slot, rel_cesta)
    preview_fix = []     # (kniha, nova_hodnota)
    preskoceno = []

    for kniha in knihy:
        try:
            cesty = {r: kniha[r] for r in SLOTY}
            stav, menitelny = rozbor_zpevniku(cesty, abs_cesta, kniha['color'])
        except Exception as exc:  # noqa: BLE001
            preskoceno.append((kniha['id'], '-', str(exc)))
            continue
        if not menitelny:
            spatne = [f"{r}={stav[r]}" for r in stav
                      if stav[r] in ('neprůhledná', 'chybí soubor')]
            preskoceno.append((kniha['id'], 'celý zpěvník',
                               'barva nebude měnitelná: ' + ', '.join(spatne)))
            continue
        mizi = set()
        for role in SLOTY:
            if stav[role] == 'prázdná':
                ke_smazani.append((kniha['id'], role, kniha[role]))
                mizi.add(kniha[role])

        nahled = kniha['preview']
        if nahled and nahled in mizi:
            zbyva = kniha['front_outer']
            preview_fix.append((kniha['id'], None if zbyva in mizi else zbyva))

    print(f"prázdných obálek k odebrání: {len(ke_smazani)}"
          f"{'' if args.apply else '   (NANEČISTO)'}\n")
    podle = {}
    for kid, role, rel in ke_smazani:
        podle.setdefault(kid, []).append(role)
    for kid in sorted(podle):
        print(f"  {kid}: {', '.join(podle[kid])}")

    if preview_fix:
        print(f"\nnáhled ukazoval na mizející soubor u {len(preview_fix)} zpěvníků:")
        for kid, nova in preview_fix:
            print(f"  {kid} -> {nova or 'prázdno'}")
    if preskoceno:
        print(f"\npřeskočeno ({len(preskoceno)}):")
        for kid, slot, duvod in preskoceno:
            print(f"  {kid} {slot}: {duvod}")

    usetreno = sum(abs_cesta(rel).stat().st_size for _, _, rel in ke_smazani
                   if abs_cesta(rel).exists())
    print(f"\nna disku ubude {usetreno / 1024:.0f} kB")

    if not args.apply:
        print("nic se nezměnilo, pusť s --apply")
        con.close()
        return 0

    for kid, role, _rel in ke_smazani:
        con.execute(f"UPDATE songbooks SET {SLOUPEC[role]} = NULL WHERE id = ?", (kid,))
    for kid, nova in preview_fix:
        con.execute("UPDATE songbooks SET cover_preview_id = "
                    "(SELECT id FROM images WHERE cesta = ?) WHERE id = ?", (nova, kid))
    con.commit()

    # Soubor se maže až po commitu a jen tehdy, když na něj už nikdo neukazuje. Cesty se
    # mezi zpěvníky sdílet nemají, ale mazat obrázek, na který někde zbyl odkaz, by bylo
    # horší než nechat na disku pár kilobajtů navíc.
    # Odkaz může vést i ze song_images nebo z jiného zpěvníku, proto se ptáme přes images.
    zbyle = set()
    for (cesta,) in con.execute(
            "SELECT DISTINCT i.cesta FROM images i WHERE EXISTS "
            "(SELECT 1 FROM song_images si WHERE si.image_id = i.id) OR EXISTS "
            "(SELECT 1 FROM songbooks s WHERE i.id IN (s.cover_preview_id, "
            "s.cover_front_outer_id, s.cover_front_inner_id, s.cover_back_inner_id, "
            "s.cover_back_outer_id))"):
        zbyle.add(cesta)
    smazano = 0
    for _kid, _role, rel in ke_smazani:
        if rel in zbyle:
            print(f"  ponechán soubor {rel}, ještě na něj vede odkaz")
            continue
        p = abs_cesta(rel)
        if p.exists():
            p.unlink()
            smazano += 1
    con.close()
    print(f"\nhotovo: {len(ke_smazani)} sloupců vyprázdněno, {smazano} souborů smazáno")
    return 0


if __name__ == '__main__':
    sys.exit(main())
