#!/usr/bin/env python3
"""Povýší průhlednou variantu obálky (coverXT.png) na primární (coverX.png).

Průhledná obálka je lepší tvar dat: barva zpěvníku se pod ni kreslí za běhu, takže jde
měnit bez překreslování obrázku. Vedle toho je i menší.

Povýší se ale jen obálka, u které to uživatel nemůže poznat. Musí platit všechno:
  - barevný originál existuje a má stejný rozměr jako T varianta
  - jeho pozadí je jednolité (aspoň 99 % okraje jedna barva)
  - ta barva se rovná barvě zpěvníku z DB (tolerance 8 na kanál)

Zbytek se nechává být. Naměřeno: projde 57 ze 70 obálek. Neprojdou hlavně T varianty
s jiným rozměrem než originál - ty nevznikly z těch souborů, které dnes na disku leží.

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

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

PODIL_OKRAJE = 99.0   # kolik procent okraje musí být jedna barva
TOLERANCE = 8         # o kolik se smí barva pozadí lišit od barvy v DB


def hex_na_rgb(h):
    h = (h or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (255, 255, 255)


def posudek(p_barevny: Path, p_pruhledny: Path, barva_db):
    """None když se povýšit smí, jinak důvod, proč ne."""
    if not p_barevny.exists():
        return 'barevný originál neexistuje'
    with Image.open(p_barevny) as im:
        im.load()
        rozmer_b = im.size
        plocha = Image.new('RGB', im.size, (255, 255, 255))
        rgba = im.convert('RGBA')
        plocha.paste(rgba, mask=rgba.split()[-1])
        a = np.asarray(plocha)
    with Image.open(p_pruhledny) as im:
        rozmer_t = im.size

    if rozmer_b != rozmer_t:
        return f'jiný rozměr {rozmer_b} vs {rozmer_t}'

    okraj = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]]).reshape(-1, 3)
    barvy, pocty = np.unique(okraj, axis=0, return_counts=True)
    dominantni = barvy[pocty.argmax()]
    podil = pocty.max() / len(okraj) * 100
    if podil < PODIL_OKRAJE:
        return f'pozadí není jednolité ({podil:.1f} % okraje)'

    if int(np.abs(dominantni.astype(int) - np.array(hex_na_rgb(barva_db))).max()) > TOLERANCE:
        mel = '#%02x%02x%02x' % tuple(int(x) for x in dominantni)
        return f'barva nesedí: obálka {mel} vs DB {barva_db}'
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=str(REPO_ROOT / 'data'), help='kořen dat')
    ap.add_argument('--db', default=None, help='cesta k zpevnik.db')
    ap.add_argument('--apply', action='store_true', help='opravdu přejmenovat')
    args = ap.parse_args()

    data = Path(args.data)
    img = data / 'public' / 'images' / 'songbooks'
    db = Path(args.db) if args.db else REPO_ROOT / 'backend' / 'instance' / 'zpevnik.db'
    if not db.exists():
        print(f"❌ DB nenalezena: {db}")
        return 2

    con = sqlite3.connect(str(db))
    barvy = {r[0]: (r[1] or '#FFFFFF') for r in con.execute('SELECT id, color FROM songbooks')}
    con.close()

    povysit, nechat = [], []
    for kniha in sorted(os.listdir(img)):
        d = img / kniha
        if not d.is_dir():
            continue
        for f in sorted(os.listdir(d)):
            if not (f.startswith('cover') and f.endswith('T.png')):
                continue
            p_t = d / f
            p_b = d / (f[:-5] + '.png')
            duvod = posudek(p_b, p_t, barvy.get(kniha, '#FFFFFF'))
            (nechat if duvod else povysit).append((kniha, f, duvod, p_t, p_b))

    print(f"povýšit lze: {len(povysit)}   ponechat: {len(nechat)}"
          f"{'' if args.apply else '   (NANEČISTO)'}\n")

    usetreno = 0
    podle_knihy = {}
    for kniha, f, _, p_t, p_b in povysit:
        usetreno += p_b.stat().st_size - p_t.stat().st_size
        podle_knihy.setdefault(kniha, []).append(f[5:-5])
        if args.apply:
            os.replace(p_t, p_b)
    for kniha in sorted(podle_knihy):
        print(f"  {kniha}: {', '.join(sorted(podle_knihy[kniha]))}")

    print(f"\nponecháno ({len(nechat)}):")
    for kniha, f, duvod, _, _ in nechat:
        print(f"  {kniha} {f:22} {duvod}")

    print(f"\nna disku ubude {usetreno / 1e6:.1f} MB")
    if not args.apply:
        print("nic se nezměnilo, pusť s --apply")
    return 0


if __name__ == '__main__':
    sys.exit(main())
