# Zpěvník je řada stran

Od 6. 10. 2026 (`backend/scripts/migrace_strany.py`). Do té doby v databázi strana
neexistovala.

## Schéma

```
songbooks         … prvni_cislo_strany (číslo vytištěné na první straně obsahu, obvykle 1)
strany            id, songbook_id, poradi, image_id | NULL, popisek
pisne_na_strane   id, strana_id, song_id, poradi_v_pisni
songs             id, title, author_id
images            id, cesta
```

- **Strana** je jedna fyzická strana obsahu (obálky jsou dál ve čtyřech sloupcích
  `songbooks`). `poradi` je pozice od nuly bez mezer, číslo strany je
  `prvni_cislo_strany + poradi`.
- **Prázdná strana** nemá obrázek (`image_id` NULL).
- **Nepísňová strana** (úvod, osmisměrka, předěl) nemá žádnou píseň. `popisek` jí může dát
  jméno pro editor; do obsahu ani hledání nepatří.
- **Píseň přes víc stran** = víc řádků `pisne_na_strane` s `poradi_v_pisni` 1, 2, 3…
- **Víc písní na straně** = víc řádků k jedné straně, v pořadí, v jakém přibyly (`id`).
- **Píseň ve víc zpěvnících** (veřejnou si lidé přidávají do svých) = v každém zpěvníku
  vlastní strany, ale tytéž řádky `images`. Píseň, která není v žádném zpěvníku, se maže
  (`smaz_pisne_mimo_zpevniky`), obrázek, na který nic neukazuje, taky
  (`smaz_osirele_obrazky`).

## Proč

Dřív byl zpěvník seznam písní s čísly stran (`songbook_pages`) a obrázky visely na
písních (`song_images`). Stranu skládala čtečka párováním „k-té číslo strany písně ↔
k-tý obrázek písně“ a všechno, co písní nebylo, se za ni muselo přestrojit: prázdná
strana byla „píseň“ bez obrázku, osmisměrka „píseň“ s `is_non_song` a autorem „System“.
Odtud `NON_SONG_TITLE` v hledání, kontroly názvů „Non-song page…“, dopočítávání skupin
písní na sdílené straně v editoru a obsah počítaný dvakrát dvěma různými způsoby.

Hlavní důvod ale byl editor a import: oba mají pracovat se stranami („přidej strany a urči,
co na nich je“). V novém schématu je to přímo řádek tabulky.

## Jak se ověřovalo

Diferenciálně: starý kód nad starou DB a nový kód nad migrovanou DB, každý ve vlastním
pískovišti s klonem obrázků. Porovnávalo se, co web ukáže každému z účtů (čtečka všech
zpěvníků, obsah, hledání, editor, volby přidávání písně), sekvence a klíče exportu pro
každou předvolbu i vlastní recepty, práva ke každému obrázku pro každý účet a stav disku.
Poprvé bez zápisů, podruhé po scénáři úprav (přidání písně, velké uložení editoru, odebrání
písně a obálky, převod vlastnictví, smazání zpěvníku i účtu).

Klíče exportu vyšly shodné, takže předgenerovaná PDF platí dál.

Rozdíly, které migrace přinesla záměrně (všechno opravy starých chyb):

- smazání účtu po sobě nechávalo nahrané strany na disku a písně bez zpěvníku
- smazání zpěvníku nechávalo ležet záznamy o sdílení
- odebrání obálky smazalo soubor, ale ne řádek v `images`
- `/api/songbook/<id>/toc` neměl kontrolu práv: názvy písní cizího soukromého
  zpěvníku si mohl přečíst kdokoli, kdo znal jeho id

Invarianty schématu hlídá `kontrola_zpevniku.py` (sekce „struktura stran“).

## Editor zatím mluví postaru

Odpověď `/api/my-songbooks/<id>/structure` má dál tvar „řádek = píseň“, protože editor se
bude přepisovat zvlášť (jednotné „Přidat strany“). Strana bez písně v ní vystupuje
s náhradním id `strana-<id>`, které ukládání zase pozná. S novým editorem tahle vrstva
zmizí.
