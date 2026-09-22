"""Které zpěvníky se opravdu stáhnou hned a které se budou skládat.

Vzniklo z konkrétní chyby: v `data/exports` leželo 29 předgenerovaných PDF veřejných
zpěvníků, na která se klíč cache netrefil, takže je nabídka správně hlásila jako
nehotová a stahování je skládalo znovu. Z výpisu adresáře to poznat nejde - soubor tam
je a vypadá správně. Jediné, co to řekne, je spočítat klíč tak, jak ho počítá aplikace,
a porovnat ho s diskem. Přesně to dělá `/songbook/<id>/export-hotove` pro nabídku; tenhle
skript se ptá stejně, jen za všechny zpěvníky naráz.

Jen čte. Nic nemaže a nic nestaví - chybějící se doplní příkazem
`flask export-warm --public-only`.

Umí navíc otevřít ty hotové soubory a ověřit, že nejsou rozbité. Stojí to za to, protože
exporty veřejných zpěvníků se z cache záměrně nevyhazují - rozbitý by tam zůstal ležet,
dokud se nezmění samotný zpěvník.

Použití:
    .venv/bin/python backend/scripts/kontrola_exportu.py
    .venv/bin/python backend/scripts/kontrola_exportu.py --jen-verejne
    .venv/bin/python backend/scripts/kontrola_exportu.py --overit-obsah

Na serveru:
    cd ~/digitalni-zpevnik && set -a && . /etc/digitalni-zpevnik.env && set +a \
      && .venv/bin/python backend/scripts/kontrola_exportu.py
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")]

from backend.app import (  # noqa: E402
    app, db, Songbook, EXPORTS_DIR, PREDVOLBY, _export_paths,
    build_songbook_export_sequence, normalizuj_recept, songbook_export_key,
)


def vada_pdf(cesta, cekano_stran):
    """Popis vady, nebo None když je soubor v pořádku.

    Počet stran se čte z posledního /Count, ne z počtu /Type /Page: Pillow zapisuje
    přírůstkově, takže v souboru zůstávají i objekty stran z dřívějších revizí (u 26
    stran jich je 351) a čtečka se řídí posledním xref. Totéž dělá test_export.py.
    """
    data = cesta.read_bytes()
    if not data.startswith(b"%PDF-"):
        return "nezačíná %PDF-"
    if b"%%EOF" not in data[-2048:]:
        return "chybí %%EOF na konci, soubor je uříznutý"
    vyskyty = re.findall(rb"/Count\s+(\d+)", data)
    if not vyskyty:
        return "nemá /Count, nedá se zjistit počet stran"
    stran = int(vyskyty[-1])
    if stran != cekano_stran:
        return f"má {stran} stran, čekáno {cekano_stran}"
    return None


def vada_zip(cesta, cekano_stran):
    try:
        with zipfile.ZipFile(cesta) as z:
            vadny = z.testzip()
            if vadny:
                return f"poškozená položka {vadny}"
            if len(z.namelist()) != cekano_stran:
                return f"má {len(z.namelist())} položek, čekáno {cekano_stran}"
    except zipfile.BadZipFile as e:
        return f"není platný ZIP ({e})"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jen-verejne", action="store_true",
                        help="jen naše veřejné zpěvníky")
    parser.add_argument("--overit-obsah", action="store_true",
                        help="otevřít hotové soubory a ověřit, že nejsou rozbité")
    args = parser.parse_args()

    with app.app_context():
        dotaz = db.select(Songbook)
        if args.jen_verejne:
            dotaz = dotaz.where(Songbook.is_public == 1)
        knihy = db.session.execute(dotaz.order_by(Songbook.id)).scalars().all()

        ocekavane = set()
        chybi_verejne = []
        vadne = []
        print(f"{'zpěvník':15s} {'veřejný':8s} {'stran':>5s}  small  high   zip")
        for kniha in knihy:
            stav = {}
            stran = 0
            for jmeno, predvolba in PREDVOLBY.items():
                recept = normalizuj_recept(**predvolba)
                sekvence = build_songbook_export_sequence(kniha, recept)
                stran = max(stran, len(sekvence))
                cesty = _export_paths(kniha.id, jmeno, recept['format'],
                                      songbook_export_key(sekvence, jmeno))
                ocekavane.add(cesty['final'].name)
                stav[jmeno] = cesty['final'].exists()
                if stav[jmeno] and args.overit_obsah:
                    vada = (vada_zip if recept['format'] == 'zip' else vada_pdf)(
                        cesty['final'], len(sekvence))
                    if vada:
                        vadne.append((cesty['final'].name, vada))
            print(f"{kniha.id:15s} {'ano' if kniha.is_public else 'ne':8s} "
                  f"{stran:5d}  "
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
        if args.overit_obsah:
            print(f"rozbitých hotových souborů: {len(vadne)}")
            for jmeno, vada in vadne:
                print(f"    {jmeno}: {vada}")
        if chybi_verejne:
            print("\n→ doplní je:  flask export-warm --public-only")
        if vadne:
            print("\n→ rozbité smaž a nech postavit znovu; z cache samy nevypadnou")
        return 1 if (chybi_verejne or vadne) else 0


if __name__ == "__main__":
    sys.exit(main())
