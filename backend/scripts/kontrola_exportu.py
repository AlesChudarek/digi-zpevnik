"""Které zpěvníky se opravdu stáhnou hned a které se budou skládat.

Vzniklo z konkrétní chyby: v `data/exports` leželo 29 předgenerovaných PDF veřejných
zpěvníků, na která se klíč cache netrefil, takže je nabídka správně hlásila jako
nehotová a stahování je skládalo znovu. Z výpisu adresáře to poznat nejde - soubor tam
je a vypadá správně. Jediné, co to řekne, je spočítat klíč tak, jak ho počítá aplikace,
a porovnat ho s diskem. Přesně to dělá `/songbook/<id>/export-hotove` pro nabídku; tenhle
skript se ptá stejně, jen za všechny zpěvníky naráz.

Jen čte. Nic nemaže a nic nestaví - chybějící se doplní příkazem
`flask export-warm --public-only`.

Použití:
    .venv/bin/python backend/scripts/kontrola_exportu.py
    .venv/bin/python backend/scripts/kontrola_exportu.py --jen-verejne

Na serveru:
    cd ~/digitalni-zpevnik && set -a && . /etc/digitalni-zpevnik.env && set +a \
      && .venv/bin/python backend/scripts/kontrola_exportu.py
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")]

from backend.app import (  # noqa: E402
    app, db, Songbook, EXPORTS_DIR, _export_paths,
    build_songbook_export_sequence, songbook_export_key,
)

VARIANTY = (('pdf', 'small'), ('pdf', 'high'), ('zip', 'orig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jen-verejne", action="store_true",
                        help="jen naše veřejné zpěvníky")
    args = parser.parse_args()

    with app.app_context():
        dotaz = db.select(Songbook)
        if args.jen_verejne:
            dotaz = dotaz.where(Songbook.is_public == 1)
        knihy = db.session.execute(dotaz.order_by(Songbook.id)).scalars().all()

        ocekavane = set()
        chybi_verejne = []
        print(f"{'zpěvník':15s} {'veřejný':8s} {'stran':>5s}  small  high   zip")
        for kniha in knihy:
            sekvence = build_songbook_export_sequence(kniha)
            stav = {}
            for kind, varianta in VARIANTY:
                cesty = _export_paths(kniha.id, varianta, kind,
                                      songbook_export_key(sekvence, varianta))
                ocekavane.add(cesty['final'].name)
                stav[varianta] = cesty['final'].exists()
            print(f"{kniha.id:15s} {'ano' if kniha.is_public else 'ne':8s} "
                  f"{len(sekvence):5d}  "
                  f"{'✓' if stav['small'] else '·':6s} "
                  f"{'✓' if stav['high'] else '·':6s} "
                  f"{'✓' if stav['orig'] else '·'}")
            if kniha.is_public and not stav['small']:
                chybi_verejne.append(kniha.id)

        na_disku = {p.name for p in EXPORTS_DIR.glob('*.pdf')}
        na_disku |= {p.name for p in EXPORTS_DIR.glob('*.zip')}
        # Soubory, na které se klíč nikdy netrefí. Nejsou chyba - starou verzi nechává
        # ležet každá úprava zpěvníku - ale zabírají místo a nikdo si je nevyžádá.
        sirotci = sorted(na_disku - ocekavane) if not args.jen_verejne else []

        print(f"\nsouborů v data/exports: {len(na_disku)}")
        print(f"veřejných zpěvníků bez předgenerovaného small: {len(chybi_verejne)}"
              f"{' ' + str(chybi_verejne) if chybi_verejne else ''}")
        if not args.jen_verejne:
            print(f"souborů, na které se klíč už netrefí: {len(sirotci)}")
            for s in sirotci:
                print(f"    {s}")
        if chybi_verejne:
            print("\n→ doplní je:  flask export-warm --public-only")
        return 1 if chybi_verejne else 0


if __name__ == "__main__":
    sys.exit(main())
