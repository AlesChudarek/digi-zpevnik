"""Verze webu a Novinky - obojí z CHANGELOG.md, jediného zdroje.

Číslo verze je první nadpis `## X.Y.Z · datum` v CHANGELOG.md. Stránka /novinky ukáže
celý soubor po verzích. Tečka u účtu svítí, dokud účet neviděl nejnovější verzi
s novou funkcí (X.Y.0) - samotné opravy (X.Y.Z, Z > 0) ji nerozsvítí.

Stav gitu (commit, kolik změn je za poslední značkou vX.Y.Z) je pro Správu: lokálně
ukáže, co ještě není na serveru, na serveru, co přesně běží.
"""
import re
import subprocess
from functools import lru_cache
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
CHANGELOG = KOREN / 'CHANGELOG.md'
_NADPIS = re.compile(r'^## (\d+)\.(\d+)\.(\d+)\s*·\s*(.+?)\s*$')


@lru_cache(maxsize=1)
def _nacti(mtime):
    """Verze z CHANGELOG.md: [{cislo, datum, shrnuti, polozky}], nejnovější první."""
    verze, akt = [], None
    for radek in CHANGELOG.read_text(encoding='utf-8').splitlines():
        m = _NADPIS.match(radek)
        if m:
            akt = {'cislo': '.'.join(m.group(1, 2, 3)), 'trojice': tuple(int(x) for x in m.group(1, 2, 3)),
                   'datum': m.group(4), 'shrnuti': '', 'polozky': []}
            verze.append(akt)
            continue
        if akt is None:
            continue
        if radek.startswith('## '):
            akt = None
        elif radek.startswith('- '):
            akt['polozky'].append(radek[2:].strip())
        elif radek.startswith('  ') and akt['polozky']:
            akt['polozky'][-1] += ' ' + radek.strip()      # pokračování odrážky
        elif radek.strip() and not akt['polozky']:
            akt['shrnuti'] = (akt['shrnuti'] + ' ' + radek.strip()).strip()
    return verze


def vsechny_verze():
    """Soubor se čte znovu jen po změně (mtime)."""
    try:
        return _nacti(CHANGELOG.stat().st_mtime)
    except OSError:
        return []


def verze():
    v = vsechny_verze()
    return v[0]['cislo'] if v else '?'


def posledni_funkcni():
    """Nejnovější verze s novou funkcí (X.Y.0) - podle ní svítí tečka Novinek."""
    for v in vsechny_verze():
        if v['trojice'][2] == 0:
            return v['cislo']
    return None


def ma_novinky(user):
    """Svítí tečka? Jen přihlášenému účtu, který nejnovější funkční verzi neviděl."""
    posledni = posledni_funkcni()
    return bool(posledni and getattr(user, 'novinky_videne', None) != posledni)


@lru_cache(maxsize=1)
def stav_gitu():
    """{commit, datum, za_znackou, znacka} nebo None (bez gitu). Zjistí se jednou za běh
    procesu - nasazení restartuje gunicorn."""
    def git(*argy):
        return subprocess.run(['git', *argy], cwd=KOREN, capture_output=True, text=True,
                              timeout=5).stdout.strip()
    try:
        commit = git('rev-parse', '--short', 'HEAD')
        if not commit:
            return None
        datum = git('log', '-1', '--format=%cd', '--date=format:%-d. %-m. %Y %H:%M')
        popis = git('describe', '--tags', '--match', 'v[0-9]*', '--long')     # v3.0.0-4-gabc123
        m = re.match(r'^(v[\d.]+)-(\d+)-g', popis)
        return {'commit': commit, 'datum': datum, 'znacka': m.group(1) if m else None,
                'za_znackou': int(m.group(2)) if m else None}
    except (OSError, subprocess.SubprocessError):
        return None
