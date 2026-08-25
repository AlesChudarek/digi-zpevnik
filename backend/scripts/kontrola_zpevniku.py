#!/usr/bin/env python3
"""Projde všechny zpěvníky a hlásí jen to, co nesedí.

Čtečka a export staví stránky každý po svém: čtečka je páruje do dvoustran, export je
skládá za sebe. Sdílejí jen build_songbook_content_pages, takže obálky, prázdné strany
a pořadí se můžou rozejít, aniž by si toho někdo všiml - PDF si obvykle nikdo neotevře
vedle čtečky.

Co se kontroluje:
  - každá cesta v DB má na disku soubor
  - čtečka a export vidí stejné obsahové strany ve stejném pořadí
  - obálka má v exportu všechny čtyři strany
  - průhledná obálka má pod sebou barvu zpěvníku, ne bílou
  - barva v DB odpovídá pozadí obálky
  - měnitelnost barvy je celá, ne poloviční
  - v datech neleží soubor, na který nikdo neukazuje

    python backend/scripts/kontrola_zpevniku.py
    python backend/scripts/kontrola_zpevniku.py --vse    # vypíše i to, co je v pořádku
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent.parent))

from PIL import Image  # noqa: E402

from _obalky import SLOTY, hex_na_rgb, podil_pruhlednych, rozbor_zpevniku  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vse', action='store_true', help='vypsat i zpěvníky bez nálezu')
    args = ap.parse_args()

    from backend.app import (app, Songbook, build_songbook_export_sequence,  # noqa: E402
                             build_songbook_content_pages, _abs_image_path,
                             SONGBOOK_IMAGES_DIR, PRIVATE_USER_IMAGES_DIR)

    def abs_cesta(rel):
        return _abs_image_path(rel)

    nalezy_celkem = 0
    with app.app_context():
        knihy = Songbook.query.order_by(Songbook.id).all()
        pouzite = set()

        for kniha in knihy:
            nalezy = []
            barva_hex = kniha.color or '#FFFFFF'
            barva = hex_na_rgb(barva_hex)

            # --- cesty v DB ukazují na existující soubory ---
            for sloupec, _zaklad in SLOTY + [('img_path_cover_preview', None)]:
                rel = getattr(kniha, sloupec, None)
                if not rel:
                    continue
                pouzite.add(rel)
                p = abs_cesta(rel)
                if p is None or not p.exists():
                    nalezy.append(f"{sloupec.replace('img_path_cover_', 'obálka ')} "
                                  f"ukazuje na chybějící {rel}")

            strany = build_songbook_content_pages(kniha.id)
            for s in strany:
                if s['file'] != 'blank':
                    pouzite.add(s['file'])
                    p = abs_cesta(s['file'])
                    if p is None or not p.exists():
                        nalezy.append(f"strana {s.get('page_number')} chybí: {s['file']}")

            # --- čtečka vs export: stejné obsahové strany ve stejném pořadí ---
            sekvence = build_songbook_export_sequence(kniha)
            obsah_exportu = [i['file'] for i in sekvence if i['kind'] == 'content']
            obsah_ctecky = [s['file'] for s in strany]
            if obsah_exportu != obsah_ctecky:
                nalezy.append(f"obsah se liší: čtečka {len(obsah_ctecky)} stran, "
                              f"export {len(obsah_exportu)}")

            # --- obálka má v exportu všechny čtyři strany, nebo žádnou ---
            obalky = [i for i in sekvence if i['kind'] == 'cover']
            ma_nejakou = any(getattr(kniha, s) for s, _ in SLOTY)
            ocekavano = 4 if ma_nejakou else 0
            if len(obalky) != ocekavano:
                nalezy.append(f"obálka má v exportu {len(obalky)} stran místo {ocekavano}")

            # --- průhledná obálka musí mít pod sebou barvu zpěvníku ---
            for i in obalky:
                if i.get('bg') != barva_hex:
                    nalezy.append(f"obálka {i['file']} nemá v exportu barvu zpěvníku "
                                  f"({i.get('bg')} místo {barva_hex})")

            # --- měnitelnost je celá, ne poloviční ---
            radek = {s: getattr(kniha, s) for s, _ in SLOTY}
            radek['color'] = barva_hex
            stav, menitelny = rozbor_zpevniku(radek, abs_cesta)
            nasleduje = [s for s in stav if stav[s] in
                         ('kreslená', 'prázdná', 'průhledná', 'půjde průhledná')]
            if not menitelny and len(nasleduje) == 4:
                nalezy.append("nekonzistence v klasifikaci obálky")
            if menitelny:
                zbyva = [s.replace('img_path_cover_', '') for s in stav
                         if stav[s] == 'půjde průhledná']
                if zbyva:
                    nalezy.append(f"nepovýšené průhledné obálky: {', '.join(zbyva)}")
                prazdne = [s.replace('img_path_cover_', '') for s in stav
                           if stav[s] == 'prázdná']
                if prazdne:
                    nalezy.append(f"neodebrané prázdné obálky: {', '.join(prazdne)}")

            stav_txt = 'měnitelná' if menitelny else 'pevná'
            if nalezy:
                nalezy_celkem += len(nalezy)
                print(f"\n⚠️  {kniha.id}  {barva_hex}  barva {stav_txt}")
                for n in nalezy:
                    print(f"      {n}")
            elif args.vse:
                pruhl = sum(1 for s in stav.values() if s in ('průhledná', 'kreslená'))
                print(f"✓  {kniha.id}  {barva_hex}  barva {stav_txt}, "
                      f"{len(obsah_ctecky)} stran, {pruhl}/4 obálek následuje barvu")

        # --- soubory, na které nikdo neukazuje ---
        osirele = []
        for koren in (SONGBOOK_IMAGES_DIR, PRIVATE_USER_IMAGES_DIR):
            if not koren.exists():
                continue
            for dirpath, _, names in os.walk(koren):
                for n in names:
                    if not n.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        continue
                    p = Path(dirpath) / n
                    rel = str(p.relative_to(koren))
                    if koren == PRIVATE_USER_IMAGES_DIR:
                        rel = 'users/' + rel
                    if rel not in pouzite:
                        osirele.append((rel, p.stat().st_size))
        if osirele:
            celkem = sum(s for _, s in osirele)
            print(f"\n⚠️  {len(osirele)} souborů, na které nikdo neukazuje "
                  f"({celkem / 1e6:.1f} MB):")
            for rel, s in sorted(osirele, key=lambda x: -x[1])[:20]:
                print(f"      {rel}  {s / 1024:.0f} kB")
            if len(osirele) > 20:
                print(f"      … a dalších {len(osirele) - 20}")
            nalezy_celkem += len(osirele)

    print(f"\n{'=' * 60}")
    print("vše sedí" if nalezy_celkem == 0 else f"nálezů: {nalezy_celkem}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
