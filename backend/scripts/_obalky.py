"""Klasifikace obálek zpěvníku. Sdílené mezi povysit_obalky.py a odebrat_prazdne_obalky.py.

Měnitelnost barvy je vlastnost celého zpěvníku, ne jednotlivé strany obálky. Buď ji
podporují všechny čtyři strany, nebo žádná - poloviční stav by znamenal, že přebarvení
změní jen některé z nich a obálka přestane držet pohromadě.

Proto se tady nejdřív každý slot zařadí a teprve pak se rozhodne o celém zpěvníku.

Schválně jen Pillow, bez numpy: oba skripty musí jít pustit i na serveru, a ten má 1 GB
RAM a numpy tam není. Pillow tam je, protože na něm stojí aplikace.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

SLOTY = [
    ('img_path_cover_front_outer', 'coverfrontout'),
    ('img_path_cover_front_inner', 'coverfrontin'),
    ('img_path_cover_back_inner', 'coverbackin'),
    ('img_path_cover_back_outer', 'coverbackout'),
]

TOLERANCE = 8         # o kolik se smí barva lišit, aby platila za shodnou
PODIL_ALFY = 0.10     # od kolika průhledných pixelů považujeme obrázek za průhledný

# Průhledná varianta se přijme, když složená na barvu zpěvníku vypadá jako barevný originál.
# Naměřený rozptyl u dobrých dvojic je 1,05 až 4,52; dvojice, kde barva nesedí, vychází přes
# 80. Práh 6 tedy leží v prázdném pásmu mezi tím.
PRAH_ODCHYLKY = 6.0

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


def odchylka_po_slozeni(cesta_t: Path, original, barva_rgb) -> float:
    """Průměrná odchylka průhledné varianty složené na barvu zpěvníku od originálu.

    Tohle je ta otázka, na které záleží: vypadá to po složení stejně? Dřív se místo toho
    zkoumalo, jestli je okraj originálu jednolitý, což je jen náhražka - a špatná. Obálka
    17 má podél pravého kraje tmavý proužek ze skenu, takže tím testem propadla, přestože
    její průhledná varianta sedí líp než čtyři jiné, které prošly.

    Přímé srovnání navíc nahrazuje i kontrolu barvy: když se barva zpěvníku s pozadím
    originálu rozchází, odchylka vyskočí o řád.
    """
    with Image.open(cesta_t) as im:
        im.load()
        t = im.convert('RGBA')
        if t.size != original.size:
            t = t.resize(original.size, Image.LANCZOS)
    slozene = Image.new('RGB', original.size, barva_rgb)
    slozene.paste(t, mask=t.split()[-1])
    return sum(ImageStat.Stat(ImageChops.difference(original, slozene)).mean) / 3


def klasifikuj(cesta: Path | None, zaklad: str, barva_rgb):
    """Do jaké kategorie slot patří.

    kreslená        v DB prázdno, čtečka i export dokreslí barvou
    prázdná         soubor je celý v barvě zpěvníku, dá se zahodit
    průhledná       už dnes má alfu
    půjde průhledná vedle leží T varianta, která po složení na barvu vypadá jako originál
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

    t = cesta.parent / (zaklad + 'T.png')
    if t.exists():
        with Image.open(t) as im:
            rozmer_t = im.size
        # Poměr stran jako levná pojistka: obrázek jiného tvaru by se škálováním zkreslil
        # a srovnávat by se nemělo co.
        if abs(rozmer[0] / rozmer[1] - rozmer_t[0] / rozmer_t[1]) <= POMER_TOLERANCE:
            if odchylka_po_slozeni(t, slozeny, barva_rgb) <= PRAH_ODCHYLKY:
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
