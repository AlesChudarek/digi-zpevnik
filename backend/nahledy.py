"""Zmenšené varianty obrázků: obálky do přehledů, strany do čtečky.

Originální PNG zůstává master. Všechno tady je odvozenina, kterou jde kdykoliv smazat
a nechat vyrobit znovu; v datech se nic nemění.

**Obálky.** V přehledu se obálka zobrazuje ve výšce kolem 250 px, ale posílalo se plné
1748x2480. Naměřeno: stránka Naše zpěvníky stáhla 24,1 MB na třicet obálek, jedna z nich
6,5 MB. Po nasazení náhledů 2,89 MB, tedy 8,3x méně.

**Strany.** Čtečka dostávala plné 1748x2480 a prohlížeč to zmenšoval sám - na telefonu se
strana kreslí na zhruba 400 CSS px, takže se u táboráku tahaly megabajty na každé otočení.
Naměřeno na vzorku 30 stran: 600 kB plné PNG proti 131 kB ve WebP o šířce 1100 px, tedy
4,6x méně. Plné rozlišení se ale nezahazuje: jakmile uživatel přiblíží, čtečka si originál
dotáhne a vymění (viz `zajistiOstrost` v songbook_view.html). Na akordy se zoomuje a tam je
plné rozlišení funkce, ne plýtvání.

Klíč se počítá z času změny a velikosti originálu a je součástí adresy náhledu. Má to dva
důsledky: po výměně obrázku se změní adresa, takže se náhled udělá znovu sám a nemusí se
na to pamatovat na každém místě, kde se obrázek mění. A protože daná adresa už nikdy
neponese jiný obsah, může se posílat s roční platností v cache - prohlížeč se pak ani
neptá, jestli se něco změnilo.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import NamedTuple

from PIL import Image

log = logging.getLogger(__name__)


class Profil(NamedTuple):
    """Jedna velikost odvozeniny. Je součástí klíče, takže změna čísel tady sama vyrobí
    nové adresy a staré náhledy se uklidí."""
    jmeno: str
    sirka: int
    kvalita: int


# Dvojnásobek zobrazované velikosti, ať to zůstane ostré i na retina displeji.
OBALKA = Profil('obalka', 700, 82)
# 1100 px je práh, pod kterým se při běžném zoomu ještě nepozná rozdíl od originálu;
# nad ním si čtečka stejně dotáhne plnou verzi.
STRANA = Profil('strana', 1100, 80)

# Mění se, když se změní způsob kódování. Ať se náhledy vyrobené starým kódem samy
# nahradí, místo aby zůstaly viset s platnou adresou.
VERZE = 3


def klic(cesta: Path, profil: Profil = OBALKA) -> str | None:
    """Otisk originálu v daném profilu. None, když soubor není."""
    try:
        st = cesta.stat()
    except OSError:
        return None
    # Celé sekundy, ne nanosekundy. Nanosekundy nepřežijí rsync ani zálohu, takže se
    # klíč pro tentýž soubor lišil mezi Macem a serverem - a hotové náhledy se pak nedaly
    # nahrát místo generování. Na rozlišování verzí souboru sekundy bohatě stačí, zvlášť
    # když je v podpisu i velikost.
    podpis = f"{int(st.st_mtime)}|{st.st_size}|{profil.sirka}|{profil.kvalita}|{VERZE}"
    return hashlib.sha1(podpis.encode()).hexdigest()[:12]


def otisk_cesty(rel: str) -> str:
    """Krátké jméno pro stranu. Cesta k obrázku se do jména souboru dát nedá (má
    lomítka i diakritiku), a hloubit pod náhledy stejný strom nemá cenu - náhledy jsou
    plochá odkládací složka, ne archiv."""
    return hashlib.sha1(rel.encode('utf-8')).hexdigest()[:16]


def soubor_nahledu(koren: Path, book_id: str, k: str) -> Path:
    return koren / f"{book_id}-{k}.webp"


def soubor_strany(koren: Path, otisk: str, k: str) -> Path:
    return koren / 'strany' / f"{otisk}-{k}.webp"


def vyrob(zdroj: Path, cil: Path, profil: Profil = OBALKA) -> bool:
    """Vyrobí náhled. Vrací, jestli se to povedlo."""
    try:
        cil.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(zdroj) as im:
            im.load()
            # Průhlednost si nese jen ten, kdo ji opravdu má - průhledné obálky se
            # podkreslují barvou zpěvníku. Skenům stran by alfa kanál jen přidal objem.
            ma_alfu = im.mode in ('RGBA', 'LA') or 'transparency' in im.info
            nahled = im.convert('RGBA' if ma_alfu else 'RGB')
            nahled.thumbnail((profil.sirka, profil.sirka * 4), Image.LANCZOS)
        # Zápis přes dočasný soubor: kdyby dva požadavky dorazily zároveň, ať se nikomu
        # nepodstrčí polovina souboru.
        docasny = cil.with_suffix('.rozepsany')
        nahled.save(docasny, 'WEBP', quality=profil.kvalita, method=6)
        docasny.replace(cil)
        return True
    except Exception as chyba:  # noqa: BLE001 - náhled nesmí shodit stránku
        log.error("Náhled %s se nepodařilo vyrobit: %s", zdroj, chyba)
        return False


def uklid_starych(koren: Path, book_id: str, ponechat: str) -> None:
    """Smaže náhledy téhož zpěvníku s jiným klíčem, aby se staré nehromadily."""
    _uklid(koren, f"{book_id}-*.webp", f"{book_id}-{ponechat}.webp")


def uklid_starych_stran(koren: Path, otisk: str, ponechat: str) -> None:
    """Totéž pro stranu: po výměně obrázku ať nezůstane viset náhled předchozího."""
    _uklid(koren / 'strany', f"{otisk}-*.webp", f"{otisk}-{ponechat}.webp")


def _uklid(koren: Path, vzor: str, nechat: str) -> None:
    try:
        for p in koren.glob(vzor):
            if p.name != nechat:
                p.unlink(missing_ok=True)
    except OSError:
        pass
