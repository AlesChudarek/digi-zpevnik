"""Klasifikace obálek zpěvníku. Používá odebrat_prazdne_obalky.py.

Měnitelnost barvy je vlastnost celého zpěvníku, ne jednotlivé strany obálky. Buď ji
podporují všechny čtyři strany, nebo žádná - poloviční stav by znamenal, že přebarvení
změní jen některé z nich a obálka přestane držet pohromadě.

Proto se tady nejdřív každý slot zařadí a teprve pak se rozhodne o celém zpěvníku.

Schválně jen Pillow, bez numpy: oba skripty musí jít pustit i na serveru, a ten má 1 GB
RAM a numpy tam není. Pillow tam je, protože na něm stojí aplikace.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

# Role obálek, ne jména sloupců. Volající si sám řekne, jak se k cestám dostane - přes
# ORM je to `img_path_cover_<role>`, přes SQL cizí klíč `cover_<role>_id` do tabulky
# images. Tenhle modul o schématu vědět nepotřebuje a po dvou migracích to byl jediný
# důvod, proč se musel měnit.
SLOTY = ['front_outer', 'front_inner', 'back_inner', 'back_outer']

TOLERANCE = 8         # o kolik se smí barva lišit, aby platila za shodnou
PODIL_ALFY = 0.10     # od kolika průhledných pixelů považujeme obrázek za průhledný

# Slot buď barvu zpěvníku následuje, nebo ne. Tohle je ta hranice.
NASLEDUJE_BARVU = {'kreslená', 'prázdná', 'průhledná'}


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


def podil_pruhlednych(im) -> float:
    """Jaká část plochy není plně neprůhledná. Přes histogram, ať se nečte pixel po pixelu."""
    histogram = im.convert('RGBA').getchannel('A').histogram()
    celkem = sum(histogram)
    return sum(histogram[:255]) / celkem if celkem else 0.0


def slozit_na(im, barva_rgb):
    """Obrázek složený na danou barvu, jako to dělá čtečka i export."""
    plocha = Image.new('RGB', im.size, barva_rgb)
    rgba = im.convert('RGBA')
    plocha.paste(rgba, mask=rgba.split()[-1])
    return plocha


def je_cely_v_barve(img, barva_rgb) -> bool:
    rozdil = ImageChops.difference(img, Image.new('RGB', img.size, barva_rgb))
    return max(kanal[1] for kanal in rozdil.getextrema()) <= TOLERANCE


def klasifikuj(cesta: Path | None, barva_rgb):
    """Do jaké kategorie slot patří.

    kreslená        v DB prázdno, čtečka i export dokreslí barvou
    prázdná         soubor je celý v barvě zpěvníku, dá se zahodit
    průhledná       už dnes má alfu
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
        if podil_pruhlednych(im) > PODIL_ALFY:
            return 'průhledná'
        slozeny = slozit_na(im, barva_rgb)

    if je_cely_v_barve(slozeny, barva_rgb):
        return 'prázdná'

    # Dřív se tu hledala průhledná varianta vedle originálu jako `coverXT.png`. Takové
    # soubory po migraci úložiště neexistují - varianta téhož obrázku vedle originálu je
    # přesně to, co standard zakazuje - takže zbývá jen říct, že průhledná není.
    return 'neprůhledná'


def rozbor_zpevniku(cesty, abs_cesta, barva_hex):
    """Klasifikace všech čtyř slotů plus verdikt, jestli je zpěvník měnitelný.

    `cesty` je {role: relativní cesta nebo None}, `barva_hex` barva zpěvníku z databáze.
    Vrací (dict role -> kategorie, menitelny). Měnitelný je ten zpěvník, jehož všechny
    čtyři strany obálky barvu následují. U ostatních se obálek nedotýkáme: kdyby jen některé
    zprůhlednily, přebarvení by změnilo půlku obálky a druhou nechalo ve staré barvě.
    """
    barva = hex_na_rgb(barva_hex)
    stav = {}
    for role in SLOTY:
        rel = cesty.get(role)
        stav[role] = klasifikuj(abs_cesta(rel) if rel else None, barva)
    menitelny = all(s in NASLEDUJE_BARVU for s in stav.values())
    return stav, menitelny
