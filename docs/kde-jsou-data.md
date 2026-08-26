# Kde jsou data a co je k čemu

Stav k 26. 8. 2026. Kopií dat je víc a pletou se, tak ať je jasné, která je která
a co se stane, když některou smažeš.

## Zdroj pravdy je server

```
ubuntu@92.5.116.155 : /home/ubuntu/digitalni-zpevnik/
├── data/public/  data/private/      1097 souborů, 524 MB   ← ostrá data
├── data/exports/                    174 MB                  regenerovatelná cache PDF
├── backend/instance/zpevnik.db      225 kB                  ← ostrá databáze
└── (o adresář výš) digitalni-zpevnik.backup   2,8 GB        ruční kopie z 11/2025
```

Zpěvníky vytvořené přes web existují **jen tady**. V gitu data nejsou od commitu
"Obsah zpěvníků ven z gitu".

## Na Macu jsou tři kopie a každá má jinou roli

```
~/digitalni-zpevnik/
├── data/                 583 MB    PRACOVNÍ KOPIE, z ní běží lokální aplikace
├── zalohy/               2,4 GB    ZÁLOHA - jediná kopie mimo server
│   ├── aktualni/           440 MB    zrcadlo serveru
│   ├── snimky/<datum>/     885 MB    7 denních verzí přes tvrdé odkazy
│   ├── archiv/             1,1 GB    ruční kopie z 2025, drží originály ve vyšším rozlišení
│   └── log/zaloha.log
├── ke-kontrole/           23 MB    osiřelé obrázky čekající na tvoje posouzení
├── ui-shots/             5,8 MB    screenshoty z testů
└── backend/instance/     428 kB    lokální DB (kopie serverové) + pojistky
```

`zalohy/` je gitignorovaná a má uvnitř vlastní prázdný `.git`. To ji chrání před
`git clean -xdf`, které jinak maže i ignorované soubory - a smazalo by tím jedinou kopii
dat mimo server. Ověřeno: `git clean -xdn` ji nevypisuje. Proti `git clean -xdff`
(dvojité force) ochrana není, to sebere všechno.

Pozor, `data/` v tom seznamu je - `git clean` ji smaže. Nevadí to, je to jen pracovní
kopie a stáhne se ze zálohy nebo ze serveru znovu.

**`data/` v repu není záloha.** Je to pracovní kopie, kterou lze kdykoli znovu stáhnout
ze serveru. Naopak `~/zpevnik-zalohy/` je jediné místo mimo server, kde data jsou —
kdyby ta instance zmizela, je to všechno, co zbyde.

### Proč záloha nebydlí v repu

Nabízí se dát ji do repa do gitignorované složky, ať je všechno pohromadě. Nedělá se to
schválně: `git clean -xdf` maže i ignorované soubory, takže jedno neopatrné uklizení
pracovního stromu by smazalo zálohu. Záloha uvnitř zálohovaného adresáře taky není
oddělená kopie. Když chceš mít odkaz po ruce, patří tam symlink, ne data.

## Co je z aplikace dostupné a co ne

Dostupné = ukazuje na to nějaký záznam v databázi (obálka nebo obrázek písničky).
Nedostupné soubory aplikace nikdy nezobrazí; leží na disku a zabírají místo.

| | souborů | MB |
|---|---|---|
| dostupné | 1097 | 523,8 |
| **nedostupné: testovací adresář `00009/testinf scaling/`** | 79 | 22,1 |
| **nedostupné: strany bez odkazu z DB** | 2 | 1,6 |
| **nedostupné: ručně odložené `*OLD.png`** | 2 | 0,5 |

Lokální kopie je s serverem shodná - 1008 obrázků na obou stranách, nula rozdílů - takže
tenhle seznam platí pro obě.

### Ty dvě strany bez odkazu nejsou ztracené stránky

Obě vznikly úpravou přes web a jsou to **pozůstatky, ne chybějící obsah**:

- `00022/page7.png` — strana byla ze zpěvníku odebrána. V číslování je díra: 1-6, 8-20.
- `00025/page12.png` — strana byla **nahrazena**. Databáze dnes ukazuje na
  `00025/songs/custom_26c3273d9ec8/page12.png`, tedy na novější upload. Původní soubor
  zůstal ležet vedle.

Příčina je v aplikaci: `cleanup_old` v [app.py:1502](../backend/app.py#L1502) se volá jen
pro čtyři sloty obálky. Pro strany písniček nic takového neexistuje, takže každé nahrazení
nebo odebrání strany nechá starý soubor na disku. Bude se to hromadit.

## Co je bezpečné smazat

Nic z toho není nikde jinde potřeba, ale je to na rozmyšlenou, ne na hned.

| kde | co | ušetří |
|---|---|---|
| ~~server~~ | ~~`~/digitalni-zpevnik.backup`~~ — smazáno 26. 8. 2026, unikátní zbytky přesunuty do `zalohy/archiv/` | 2,8 GB |
| server + Mac | `00009/testinf scaling/` (testovací adresář) | 22,1 MB |
| server + Mac | `00016/*OLD.png` | 0,5 MB |
| server + Mac | `00022/page7.png`, `00025/page12.png` (po kontrole) | 1,6 MB |
| ~~Mac~~ | ~~`data_backup/`~~ — **NEMAZAT**, drží originály ve vyšším rozlišení, přesunuto do `zalohy/archiv/rucni-kopie-09-2025/` | — |
| ~~Mac~~ | ~~`prace/`~~ — smazáno 26. 8. 2026 | 402 MB |

Naopak **nikdy nemazat**: `~/zpevnik-zalohy/aktualni/` a `~/zpevnik-zalohy/snimky/`.
