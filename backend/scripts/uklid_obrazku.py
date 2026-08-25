#!/usr/bin/env python3
"""Zmenší obrázky zpěvníků tím, že zahodí to, co v nich nic nenese.

Naměřeno na 819 stranách veřejných zpěvníků: 815 z nich má alfa kanál, který je celý
neprůhledný, a 551 stran je čistě šedých, ale uložených jako RGBA nebo I;16. Nese se tedy
čtyřnásobek, případně dvojnásobek dat oproti tomu, co v obrázku doopravdy je.

Co dělá:
  - alfa kanál, který je celý neprůhledný, zahodí                       bezztrátové
  - obrázek, kde R=G=B, uloží jako 8bitovou šedou (L)                   bezztrátové
  - 16bitovou šedou (I;16) převede na 8bitovou                          NEVIDITELNĚ ZTRÁTOVÉ
  - barevný obrázek nechá barevný, jen mu případně zahodí prázdnou alfu bezztrátové
  - obrázek s živou alfou nechá být (jsou čtyři a alfa v nich něco znamená)

Každý zápis se ověřuje: soubor se znovu načte a porovná s originálem. Když se pixely
nesejdou nebo by nový soubor nebyl menší, originál zůstává. Bez --apply se nic nezapisuje.

    python backend/scripts/uklid_obrazku.py data/public/images/songbooks
    python backend/scripts/uklid_obrazku.py data/public/images/songbooks --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

# 16bitová šedá se dělí na 8bitovou tímhle. PNG ukládá 16bit kanál v plném rozsahu,
# takže dělitel je 65535/255, ne 256.
DELITEL_16_NA_8 = 257.0


def nacti(cesta: Path):
    """Obrázek plus fakta, která rozhodují o tom, co s ním."""
    with Image.open(cesta) as im:
        im.load()
        mode = im.mode
        alfa = None
        if mode in ('RGBA', 'LA', 'PA'):
            alfa = np.asarray(im.convert('RGBA').split()[-1])
        if mode == 'I;16':
            data = np.asarray(im)
            return mode, data, alfa, True
        # Bez alfy nebo s alfou: barevná data bereme tak, jak jsou pod ní
        base = im.convert('RGBA') if alfa is not None else im.convert('RGB')
        data = np.asarray(base)[:, :, :3]
        return mode, data, alfa, False


def rozhodni(mode, data, alfa, je16):
    """Co s obrázkem udělat a proč. Vrací (akce, cíl, důvod)."""
    if alfa is not None and (alfa < 255).any():
        return 'nechat', None, 'živá alfa'
    if je16:
        return 'na 8bit L', 'L', '16bitová šedá'
    seda = bool((data[:, :, 0] == data[:, :, 1]).all() and (data[:, :, 1] == data[:, :, 2]).all())
    if seda:
        if mode == 'L':
            return 'přeuložit', 'L', 'už je L, zkusit lepší kompresi'
        return 'na 8bit L', 'L', 'šedá uložená jako ' + mode
    if alfa is not None:
        return 'zahodit alfu', 'RGB', 'barevná s prázdnou alfou'
    if mode == 'RGB':
        return 'přeuložit', 'RGB', 'barevná, zkusit lepší kompresi'
    return 'nechat', None, 'nic k získání'


def vyrob(data, je16, cil):
    if je16:
        return Image.fromarray(np.round(data / DELITEL_16_NA_8).astype(np.uint8), 'L')
    if cil == 'L':
        return Image.fromarray(data[:, :, 0].copy(), 'L')
    return Image.fromarray(data.copy(), 'RGB')


def stejne(puvodni_data, je16, novy: Path, cil):
    """Sedí zapsaný soubor na to, co v originále bylo? U 16bit se porovnává po převodu."""
    with Image.open(novy) as im:
        im.load()
        nova = np.asarray(im.convert('L') if cil == 'L' else im.convert('RGB'))
    if je16:
        ocekavane = np.round(puvodni_data / DELITEL_16_NA_8).astype(np.uint8)
    elif cil == 'L':
        ocekavane = puvodni_data[:, :, 0]
    else:
        ocekavane = puvodni_data
    return nova.shape == ocekavane.shape and bool((nova == ocekavane).all())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('korenu', nargs='+', help='adresáře k projití')
    ap.add_argument('--apply', action='store_true', help='opravdu zapisovat (jinak jen výpis)')
    ap.add_argument('--vcetne-obalek', action='store_true',
                    help='sáhnout i na cover*.png (ve výchozím stavu se přeskakují)')
    args = ap.parse_args()

    soubory = []
    for koren in args.korenu:
        for dirpath, _, names in os.walk(koren):
            for n in sorted(names):
                if n.lower().endswith('.png'):
                    if not args.vcetne_obalek and n.startswith('cover'):
                        continue
                    soubory.append(Path(dirpath) / n)
    soubory.sort()
    print(f"souborů k projití: {len(soubory)}"
          f"{'' if args.apply else '   (NANEČISTO, nic se nezapíše)'}\n")

    stat = Counter()
    bajty = defaultdict(int)
    pred = po = 0
    problemy = []

    for i, p in enumerate(soubory, 1):
        velikost = p.stat().st_size
        pred += velikost
        try:
            mode, data, alfa, je16 = nacti(p)
        except Exception as exc:  # noqa: BLE001
            problemy.append((p, f'nešel načíst: {exc}'))
            po += velikost
            continue

        akce, cil, duvod = rozhodni(mode, data, alfa, je16)
        if akce == 'nechat':
            stat[f'nechat ({duvod})'] += 1
            bajty[f'nechat ({duvod})'] += velikost
            po += velikost
            continue

        novy_obr = vyrob(data, je16, cil)
        tmp = p.with_suffix('.png.novy')
        novy_obr.save(tmp, 'PNG', optimize=True, compress_level=9)
        nova_velikost = tmp.stat().st_size

        if not stejne(data, je16, tmp, cil):
            tmp.unlink(missing_ok=True)
            problemy.append((p, 'pixely po zápisu nesedí, originál ponechán'))
            po += velikost
            continue
        if nova_velikost >= velikost:
            tmp.unlink(missing_ok=True)
            stat[f'nechat (nový by byl větší)'] += 1
            bajty['nechat (nový by byl větší)'] += velikost
            po += velikost
            continue

        klic = f'{akce} [{mode} -> {cil}]'
        stat[klic] += 1
        bajty[klic] += velikost - nova_velikost
        po += nova_velikost
        if args.apply:
            os.replace(tmp, p)
        else:
            tmp.unlink(missing_ok=True)

        if i % 100 == 0:
            print(f"  ... {i}/{len(soubory)}", flush=True)

    print(f"\n{'akce':46} {'kusů':>6} {'ušetří MB':>11}")
    print('-' * 66)
    for k in sorted(stat, key=lambda k: -bajty[k]):
        print(f"{k:46} {stat[k]:6} {bajty[k] / 1e6:11.1f}")
    print('-' * 66)
    print(f"celkem {pred / 1e6:.1f} MB -> {po / 1e6:.1f} MB "
          f"({(1 - po / pred) * 100:.1f} % dolů)" if pred else "nic k práci")

    if problemy:
        print(f"\n⚠️  {len(problemy)} problémů:")
        for p, d in problemy[:20]:
            print(f"   {p}: {d}")
    return 1 if problemy else 0


if __name__ == '__main__':
    sys.exit(main())
