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
from _obalky import SLOTY as SLOTY_PARY, rozbor_zpevniku  # noqa: E402

SLOTY = [s[0] for s in SLOTY_PARY]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=str(REPO_ROOT / 'data'))
    ap.add_argument('--db', default=str(REPO_ROOT / 'backend' / 'instance' / 'zpevnik.db'))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    # Dva kořeny, stejně jako _abs_image_path v app.py: cesta s prefixem users/ míří do
    # soukromých dat, všechno ostatní do veřejných. Bez tohohle by skript soukromé
    # zpěvníky tiše přeskočil.
    verejne = Path(args.data) / 'public' / 'images' / 'songbooks'
    soukrome = Path(args.data) / 'private' / 'users'

    def abs_cesta(rel: str) -> Path:
        if rel.startswith('users/'):
            return soukrome / rel[len('users/'):]
        return verejne / rel

    db = Path(args.db)
    if not db.exists():
        print(f"❌ DB nenalezena: {db}")
        return 2

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    knihy = con.execute(
        f"SELECT id, color, img_path_cover_preview, {', '.join(SLOTY)} FROM songbooks").fetchall()

    ke_smazani = []      # (kniha, slot, rel_cesta)
    preview_fix = []     # (kniha, nova_hodnota)
    preskoceno = []

    for kniha in knihy:
        try:
            stav, menitelny = rozbor_zpevniku(kniha, abs_cesta)
        except Exception as exc:  # noqa: BLE001
            preskoceno.append((kniha['id'], '-', str(exc)))
            continue
        if not menitelny:
            spatne = [f"{s.replace('img_path_cover_', '')}={stav[s]}" for s in stav
                      if stav[s] in ('neprůhledná', 'chybí soubor')]
            preskoceno.append((kniha['id'], 'celý zpěvník',
                               'barva nebude měnitelná: ' + ', '.join(spatne)))
            continue
        mizi = set()
        for slot in SLOTY:
            if stav[slot] == 'prázdná':
                ke_smazani.append((kniha['id'], slot, kniha[slot]))
                mizi.add(kniha[slot])

        nahled = kniha['img_path_cover_preview']
        if nahled and nahled in mizi:
            zbyva = kniha['img_path_cover_front_outer']
            preview_fix.append((kniha['id'], None if zbyva in mizi else zbyva))

    print(f"prázdných obálek k odebrání: {len(ke_smazani)}"
          f"{'' if args.apply else '   (NANEČISTO)'}\n")
    podle = {}
    for kid, slot, rel in ke_smazani:
        podle.setdefault(kid, []).append(slot.replace('img_path_cover_', ''))
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

    for kid, slot, _rel in ke_smazani:
        con.execute(f"UPDATE songbooks SET {slot} = NULL WHERE id = ?", (kid,))
    for kid, nova in preview_fix:
        con.execute("UPDATE songbooks SET img_path_cover_preview = ? WHERE id = ?", (nova, kid))
    con.commit()

    # Soubor se maže až po commitu a jen tehdy, když na něj už nikdo neukazuje. Cesty se
    # mezi zpěvníky sdílet nemají, ale mazat obrázek, na který někde zbyl odkaz, by bylo
    # horší než nechat na disku pár kilobajtů navíc.
    zbyle = set()
    for r in con.execute(f"SELECT img_path_cover_preview, {', '.join(SLOTY)} FROM songbooks"):
        zbyle.update(x for x in r if x)
    smazano = 0
    for _kid, _slot, rel in ke_smazani:
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
