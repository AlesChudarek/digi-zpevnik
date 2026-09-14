"""Zmenšené náhledy obálek pro přehledy zpěvníků.

V přehledu se obálka zobrazuje ve výšce kolem 250 px, ale posílalo se plné 1748x2480.
Naměřeno: stránka Naše zpěvníky stáhla 24,1 MB na třicet obálek, jedna z nich 6,5 MB.
Ve WebP o šířce 700 px je to 0,7 MB, tedy 34x méně, a přitom dvojnásobek zobrazované
velikosti, takže i na retina displeji zůstane ostrý.

Čtečky se to netýká. Tam se zoomuje na akordy a plné rozlišení je funkce, ne plýtvání.

Klíč se počítá z času změny a velikosti originálu a je součástí adresy náhledu. Má to dva
důsledky: po výměně obálky se změní adresa, takže se náhled udělá znovu sám a nemusí se
na to pamatovat na každém místě, kde se obálka mění. A protože daná adresa už nikdy
neponese jiný obsah, může se posílat s roční platností v cache - prohlížeč se pak ani
neptá, jestli se něco změnilo.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

SIRKA = 700
KVALITA = 82


def klic(cesta: Path) -> str | None:
    """Otisk originálu. None, když soubor není."""
    try:
        st = cesta.stat()
    except OSError:
        return None
    return hashlib.sha1(f"{st.st_mtime_ns}|{st.st_size}|{SIRKA}".encode()).hexdigest()[:12]


def soubor_nahledu(koren: Path, book_id: str, k: str) -> Path:
    return koren / f"{book_id}-{k}.webp"


def vyrob(zdroj: Path, cil: Path) -> bool:
    """Vyrobí náhled. Vrací, jestli se to povedlo."""
    try:
        cil.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(zdroj) as im:
            im.load()
            nahled = im.convert('RGBA')
            nahled.thumbnail((SIRKA, SIRKA * 4), Image.LANCZOS)
        # Zápis přes dočasný soubor: kdyby dva požadavky dorazily zároveň, ať se nikomu
        # nepodstrčí polovina souboru.
        docasny = cil.with_suffix('.rozepsany')
        nahled.save(docasny, 'WEBP', quality=KVALITA, method=6)
        docasny.replace(cil)
        return True
    except Exception as chyba:  # noqa: BLE001 - náhled nesmí shodit stránku
        log.error("Náhled %s se nepodařilo vyrobit: %s", zdroj, chyba)
        return False


def uklid_starych(koren: Path, book_id: str, ponechat: str) -> None:
    """Smaže náhledy téhož zpěvníku s jiným klíčem, aby se staré nehromadily."""
    try:
        for p in koren.glob(f"{book_id}-*.webp"):
            if p.name != f"{book_id}-{ponechat}.webp":
                p.unlink(missing_ok=True)
    except OSError:
        pass
