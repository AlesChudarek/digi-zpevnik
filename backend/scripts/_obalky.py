"""Klasifikace obálek zpěvníku. Sdílené mezi povysit_obalky.py a odebrat_prazdne_obalky.py.

Měnitelnost barvy je vlastnost celého zpěvníku, ne jednotlivé strany obálky. Buď ji
podporují všechny čtyři strany, nebo žádná - poloviční stav by znamenal, že přebarvení
změní jen některé z nich a obálka přestane držet pohromadě.

Proto se tady nejdřív každý slot zařadí a teprve pak se rozhodne o celém zpěvníku.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

SLOTY = [
    ('img_path_cover_front_outer', 'coverfrontout'),
    ('img_path_cover_front_inner', 'coverfrontin'),
    ('img_path_cover_back_inner', 'coverbackin'),
    ('img_path_cover_back_outer', 'coverbackout'),
]

TOLERANCE = 8         # o kolik se smí barva lišit, aby platila za shodnou
PODIL_OKRAJE = 99.0   # kolik procent okraje musí být jedna barva, aby pozadí bylo jednolité
PODIL_ALFY = 0.10     # od kolika průhledných pixelů považujeme obrázek za průhledný

# Průhledná varianta bývá v jiném rozlišení než barevný originál - jsou to naskenované
# předlohy, které někdo cestou zmenšil. Rozhoduje proto tvar, ne počet pixelů: když sedí
# poměr stran, je to tentýž obrázek a čtečka i PDF si ho stejně škálují do své plochy.
POMER_TOLERANCE = 0.005

# Slot buď barvu zpěvníku následuje, nebo ne. Tohle je ta hranice.
NASLEDUJE_BARVU = {'kreslená', 'prázdná', 'průhledná', 'půjde průhledná'}


def hex_na_rgb(h, default=(255, 255, 255)):
    h = (h or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def slozit_na(cesta: Path, barva_rgb):
    """Obrázek složený na danou barvu, jako to dělá čtečka i export."""
    with Image.open(cesta) as im:
        im.load()
        plocha = Image.new('RGB', im.size, barva_rgb)
        rgba = im.convert('RGBA')
        plocha.paste(rgba, mask=rgba.split()[-1])
        return np.asarray(plocha), im.size


def klasifikuj(cesta: Path | None, zaklad: str, barva_rgb):
    """Do jaké kategorie slot patří.

    kreslená        v DB prázdno, čtečka i export dokreslí barvou
    prázdná         soubor je celý v barvě zpěvníku, dá se zahodit
    průhledná       už dnes má alfu
    půjde průhledná vedle leží T varianta se stejným tvarem a pozadím v barvě zpěvníku
    neprůhledná     plná grafika bez průhledné varianty, barvu následovat nebude
    chybí soubor    v DB je cesta, ale soubor tam není
    """
    if cesta is None:
        return 'kreslená'
    if not cesta.exists():
        return 'chybí soubor'

    with Image.open(cesta) as im:
        im.load()
        rozmer = im.size
        alfa = np.asarray(im.convert('RGBA').split()[-1])
    if (alfa < 255).mean() > PODIL_ALFY:
        return 'průhledná'

    a, _ = slozit_na(cesta, barva_rgb)
    cil = np.array(barva_rgb, dtype=np.int16)
    if (np.abs(a.astype(np.int16) - cil).max(axis=2) <= TOLERANCE).all():
        return 'prázdná'

    t = cesta.parent / (zaklad + 'T.png')
    if t.exists():
        with Image.open(t) as im:
            rozmer_t = im.size
        pomer = rozmer[0] / rozmer[1]
        pomer_t = rozmer_t[0] / rozmer_t[1]
        if abs(pomer - pomer_t) <= POMER_TOLERANCE:
            okraj = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]]).reshape(-1, 3)
            barvy, pocty = np.unique(okraj, axis=0, return_counts=True)
            dominantni = barvy[pocty.argmax()]
            if (pocty.max() / len(okraj) * 100 >= PODIL_OKRAJE
                    and int(np.abs(dominantni.astype(int) - np.array(barva_rgb)).max()) <= TOLERANCE):
                return 'půjde průhledná'
    return 'neprůhledná'


def rozbor_zpevniku(radek, abs_cesta):
    """Klasifikace všech čtyř slotů plus verdikt, jestli je zpěvník měnitelný.

    Vrací (dict slot -> kategorie, menitelny). Měnitelný je ten, jehož všechny čtyři
    strany obálky barvu následují. U ostatních se obálek nedotýkáme: kdyby jen některé
    zprůhlednily, přebarvení by změnilo půlku obálky a druhou nechalo ve staré barvě.
    """
    barva = hex_na_rgb(radek['color'])
    stav = {}
    for sloupec, zaklad in SLOTY:
        rel = radek[sloupec]
        stav[sloupec] = klasifikuj(abs_cesta(rel) if rel else None, zaklad, barva)
    menitelny = all(s in NASLEDUJE_BARVU for s in stav.values())
    return stav, menitelny
