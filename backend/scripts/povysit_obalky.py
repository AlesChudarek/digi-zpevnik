#!/usr/bin/env python3
"""Povýší průhlednou variantu obálky (coverXT.png) na primární (coverX.png).

Průhledná obálka je lepší tvar dat: barva zpěvníku se pod ni kreslí za běhu, takže jde
měnit bez překreslování obrázku. Vedle toho je i menší.

Povyšuje se ale po celých zpěvnících, ne po jednotlivých stranách. Měnitelnost barvy je
vlastnost celé obálky: kdyby jen některé její strany zprůhledněly, přebarvení by změnilo
půlku obálky a druhou nechalo ve staré barvě. Zpěvník, kterému aspoň jedna strana zůstane
neprůhledná, tedy necháváme celý tak, jak je - barva se u něj měnit nebude nikde.

Uvnitř měnitelného zpěvníku se pak povýší jen ta obálka, u které to uživatel nemůže
poznat: originál musí mít stejný rozměr, jednolité pozadí a jeho barva se musí rovnat
barvě zpěvníku.

Cesty v DB se nemění: T soubor se přejmenuje na jméno barevného originálu, který přepíše.

POZOR: dává smysl teprve s opravou _flatten_to_rgb, která do PDF kreslí barvu zpěvníku.
Bez ní vyjdou povýšené obálky v PDF bílé.

    python backend/scripts/povysit_obalky.py            # nanečisto
    python backend/scripts/povysit_obalky.py --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _obalky import SLOTY, rozbor_zpevniku  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=str(REPO_ROOT / 'data'), help='kořen dat')
    ap.add_argument('--db', default=None, help='cesta k zpevnik.db')
    ap.add_argument('--apply', action='store_true', help='opravdu přejmenovat')
    args = ap.parse_args()

    data = Path(args.data)
    verejne = data / 'public' / 'images' / 'songbooks'
    soukrome = data / 'private' / 'users'

    def abs_cesta(rel):
        if not rel:
            return None
        return soukrome / rel[len('users/'):] if rel.startswith('users/') else verejne / rel

    db = Path(args.db) if args.db else REPO_ROOT / 'backend' / 'instance' / 'zpevnik.db'
    if not db.exists():
        print(f"❌ DB nenalezena: {db}")
        return 2

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    knihy = con.execute(
        "SELECT id, color, " + ", ".join(s[0] for s in SLOTY) + " FROM songbooks").fetchall()
    con.close()

    povysit, preskocene_zpevniky, nepovysene = [], [], []
    for kniha in knihy:
        stav, menitelny = rozbor_zpevniku(kniha, abs_cesta)
        if not menitelny:
            duvody = [f"{s.replace('img_path_cover_', '')}={stav[s]}"
                      for s in stav if stav[s] not in ('kreslená', 'prázdná', 'průhledná',
                                                       'půjde průhledná')]
            preskocene_zpevniky.append((kniha['id'], ', '.join(duvody)))
            continue
        for sloupec, zaklad in SLOTY:
            if stav[sloupec] != 'půjde průhledná':
                continue
            p_b = abs_cesta(kniha[sloupec])
            p_t = p_b.parent / (zaklad + 'T.png')
            povysit.append((kniha['id'], zaklad, p_t, p_b))

    print(f"povýšit: {len(povysit)} obálek"
          f"{'' if args.apply else '   (NANEČISTO)'}\n")
    usetreno = 0
    podle = {}
    for kid, zaklad, p_t, p_b in povysit:
        usetreno += p_b.stat().st_size - p_t.stat().st_size
        podle.setdefault(kid, []).append(zaklad.replace('cover', ''))
        if args.apply:
            os.replace(p_t, p_b)
    for kid in sorted(podle):
        print(f"  {kid}: {', '.join(podle[kid])}")

    print(f"\nzpěvníky ponechané celé beze změny ({len(preskocene_zpevniky)}) - barva u nich "
          f"měnitelná nebude, tak ať se nemění ani zpola:")
    for kid, duvod in preskocene_zpevniky:
        print(f"  {kid}: {duvod}")

    print(f"\nna disku ubude {usetreno / 1e6:.1f} MB")
    if not args.apply:
        print("nic se nezměnilo, pusť s --apply")
    return 0


if __name__ == '__main__':
    sys.exit(main())
