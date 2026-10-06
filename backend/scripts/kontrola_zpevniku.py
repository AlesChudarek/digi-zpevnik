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
  - struktura stran drží: pořadí bez mezer, každá píseň na souvislých stranách
    a s pořadím v písni 1..N, žádná píseň bez zpěvníku, žádný řádek images bez odkazu

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

from _obalky import (SLOTY, NASLEDUJE_BARVU, hex_na_rgb, podil_pruhlednych,  # noqa: E402
                     rozbor_zpevniku)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vse', action='store_true', help='vypsat i zpěvníky bez nálezu')
    args = ap.parse_args()

    from backend.app import (app, Songbook, build_songbook_export_sequence,  # noqa: E402
                             build_songbook_content_pages, _abs_image_path,
                             IMAGES_DIR)

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
            for sloupec in [f'img_path_cover_{r}' for r in SLOTY] + ['img_path_cover_preview']:
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
            ma_nejakou = any(getattr(kniha, f'img_path_cover_{r}') for r in SLOTY)
            ocekavano = 4 if ma_nejakou else 0
            if len(obalky) != ocekavano:
                nalezy.append(f"obálka má v exportu {len(obalky)} stran místo {ocekavano}")

            # --- průhledná obálka musí mít pod sebou barvu zpěvníku ---
            for i in obalky:
                if i.get('bg') != barva_hex:
                    nalezy.append(f"obálka {i['file']} nemá v exportu barvu zpěvníku "
                                  f"({i.get('bg')} místo {barva_hex})")

            # --- měnitelnost je celá, ne poloviční ---
            radek = {r: getattr(kniha, f'img_path_cover_{r}') for r in SLOTY}
            radek['color'] = barva_hex
            stav, menitelny = rozbor_zpevniku(radek, abs_cesta, kniha.color)
            nasleduje = [r for r in stav if stav[r] in NASLEDUJE_BARVU]
            if not menitelny and len(nasleduje) == 4:
                nalezy.append("nekonzistence v klasifikaci obálky")
            if menitelny:
                prazdne = [r for r in stav if stav[r] == 'prázdná']
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

        # --- struktura stran ---
        from backend.app import db, Strana, PisenNaStrane, Song, Obrazek  # noqa: E402
        from sqlalchemy import text  # noqa: E402
        nalezy = []
        for kniha in knihy:
            poradi = [st.poradi for st in kniha.strany]
            if poradi != list(range(len(poradi))):
                nalezy.append(f"{kniha.id}: pořadí stran není 0..{len(poradi) - 1} bez mezer")
            strany_pisne = {}
            for st in kniha.strany:
                for v in st.pisne:
                    strany_pisne.setdefault(v.song_id, []).append((st.poradi, v.poradi_v_pisni))
            for sid, sez in strany_pisne.items():
                por = [p for p, _ in sez]
                if por != list(range(por[0], por[0] + len(por))):
                    nalezy.append(f"{kniha.id}: píseň {sid} leží na nesouvislých stranách")
                if [v for _, v in sez] != list(range(1, len(sez) + 1)):
                    nalezy.append(f"{kniha.id}: píseň {sid} má pořadí v písni "
                                  f"{[v for _, v in sez]}")
        bez_zpevniku = (Song.query.filter(~Song.id.in_(
            db.session.query(PisenNaStrane.song_id))).count())
        if bez_zpevniku:
            nalezy.append(f"{bez_zpevniku} písní není v žádném zpěvníku")
        # Píseň ve víc zpěvnících má být všude tatáž - přidání písně do dalšího
        # zpěvníku bere její strany z prvního, kde je.
        obrazky_pisne = {}
        for st in Strana.query.order_by(Strana.songbook_id, Strana.poradi):
            for v in st.pisne:
                obrazky_pisne.setdefault(v.song_id, {}).setdefault(
                    st.songbook_id, []).append(st.image_id)
        for sid, podle_knih in obrazky_pisne.items():
            if len({tuple(x) for x in podle_knih.values()}) > 1:
                nalezy.append(f"píseň {sid} má v různých zpěvnících jiné strany: "
                              f"{sorted(podle_knih)}")
        nepouzite = db.session.execute(text(
            "SELECT cesta FROM images i WHERE NOT EXISTS (SELECT 1 FROM strany s "
            "WHERE s.image_id = i.id) AND NOT EXISTS (SELECT 1 FROM songbooks b WHERE i.id "
            "IN (b.cover_preview_id, b.cover_front_outer_id, b.cover_front_inner_id, "
            "b.cover_back_inner_id, b.cover_back_outer_id))")).fetchall()
        if nepouzite:
            nalezy.append(f"{len(nepouzite)} řádků images, na které nic neukazuje: "
                          f"{[r[0] for r in nepouzite[:5]]}")
        for radek in db.session.execute(text("PRAGMA foreign_key_check")).fetchall():
            nalezy.append(f"porušený cizí klíč: {tuple(radek)}")
        if db.session.execute(text("SELECT 1 FROM sqlite_master WHERE name = "
                                   "'songbook_pages'")).first():
            nalezy.append("databáze není po migraci na strany (existuje songbook_pages)")
        if nalezy:
            nalezy_celkem += len(nalezy)
            print("\n⚠️  struktura stran:")
            for n in nalezy[:30]:
                print(f"      {n}")
        elif args.vse:
            print(f"✓  struktura stran: {Strana.query.count()} stran, "
                  f"{PisenNaStrane.query.count()} vazeb, {Song.query.count()} písní")

        # --- soubory, na které nikdo neukazuje ---
        osirele = []
        if IMAGES_DIR.exists():
            for dirpath, _, names in os.walk(IMAGES_DIR):
                for n in names:
                    if not n.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        continue
                    p = Path(dirpath) / n
                    rel = str(p.relative_to(IMAGES_DIR))
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
