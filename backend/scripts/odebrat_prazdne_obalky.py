#!/usr/bin/env python3
"""Odebere obálky, které nejsou nic než jednolitá barevná plocha.

Taková obálka je horší než nic. Barva je v ní zapečená v pixelech, takže když se zpěvník
přebarví, průhledné vnější obálky se změní, ale tahle zůstane ve staré barvě. Prázdnou
plochu umí export i čtečka nakreslit samy - a nakreslená se přebarví se zpěvníkem.

Smaže se jen soubor, který splňuje všechno:
  - je to obálka (je na něj odkaz z některého sloupce img_path_cover_*)
  - po složení na barvu zpěvníku je celá plocha jedna barva (tolerance 8 na kanál)
  - ta barva se rovná barvě zpěvníku

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

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

SLOTY = ['img_path_cover_front_outer', 'img_path_cover_front_inner',
         'img_path_cover_back_inner', 'img_path_cover_back_outer']
TOLERANCE = 8


def hex_na_rgb(h):
    h = (h or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (255, 255, 255)


def je_prazdna(cesta: Path, barva_rgb):
    """Je obrázek po složení na barvu zpěvníku celý tou barvou?"""
    with Image.open(cesta) as im:
        im.load()
        plocha = Image.new('RGB', im.size, barva_rgb)
        rgba = im.convert('RGBA')
        plocha.paste(rgba, mask=rgba.split()[-1])
        a = np.asarray(plocha)
    odchylka = np.abs(a.astype(np.int16) - np.array(barva_rgb, dtype=np.int16))
    return bool((odchylka.max(axis=2) <= TOLERANCE).all())


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
        barva = hex_na_rgb(kniha['color'])
        mizi = set()
        for slot in SLOTY:
            rel = kniha[slot]
            if not rel:
                continue
            p = abs_cesta(rel)
            if not p.exists():
                preskoceno.append((kniha['id'], slot, f'{rel} - soubor neexistuje'))
                continue
            try:
                if je_prazdna(p, barva):
                    ke_smazani.append((kniha['id'], slot, rel))
                    mizi.add(rel)
            except Exception as exc:  # noqa: BLE001
                preskoceno.append((kniha['id'], slot, f'{rel} - {exc}'))

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
