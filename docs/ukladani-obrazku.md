# Kam ukládat obrázky

**Stav: migrace proběhla lokálně, na serveru zatím ne.** Nový strom leží v `data/images`,
starý (`data/public/images/songbooks`, `data/private/users`) zůstal ležet jako záchranná
síť a smaže se, až si provoz sedne.

Do migrace žily v datech čtyři různé tvary cest. Vznikly postupně a každý dává smysl sám o sobě,
ale dohromady se v nich nedá vyznat a některé nesou věci, které nejsou pravda. Tenhle
dokument navrhuje cílový tvar a cestu k němu.

## Co tam je dnes

| | vzor | obrázků |
|---|---|---|
| **A** starší veřejné | `00001/page10.png` | 833 |
| **B** vytvořené webem | `<id>/songs/<song_id>/zpevnikA4-04.png` | 26 |
| **C1** soukromé, nahrané | `users/8_ales.chudarek-seznam.cz/u8-…_muj-zpevnik/songs/<song_id>/xOqzMt5D.jpg` | ~130 |
| **C2** soukromé, importované | `users/2_user-test.com/00101_antistresova-prirucka…/page1.png` | ~46 |

Sečteno: **1035 řádků v `song_images`** plus obálky ve sloupcích `songbooks`, dohromady
**1105 unikátních cest**. Na disku leží **1093 souborů** a — což je dobrá zpráva pro
migraci — **na každý z nich něco z DB ukazuje**. Zbývajících 12 cest nemá soubor; jsou to
sirotci po smazaném zpěvníku, viz TODO. Volně ležící soubory (`coverfrontoutOLD.png`,
`output-onlinepngtools*.png`) už uklidil dřívější `uklid_obrazku.py`.

## Co je na tom špatně

Nejde o nepořádek v pojmenování. Cesta dnes nese čtyři údaje, které v ní nemají co dělat,
protože se **mění nezávisle na obrázku**:

**1. Zpěvník, ze kterého obrázek pochází.** V layoutu A leží strana pod složkou zpěvníku,
ale v DB visí na písni. Písnička přitom může být ve dvou zpěvnících naráz — dnes je takových
70 (jeden soukromý zpěvník, který si půjčuje z 19 veřejných). Ten druhý zpěvník pak kreslí
stranu ze souboru pod cizí složkou. Nic to zatím nerozbíjí, protože mazání se ptá, jestli
na soubor ukazuje ještě nějaký `SongImage`. Rozbije se to ve chvíli, kdy s tím někdo začne
hýbat — a import z PDF přesně to dělat bude.

**2. Název zpěvníku.** `_book_storage_base` skládá složku ze slugu názvu. Zpěvník
`u8-1780300322` se dnes jmenuje **„Fildův a Aldův zpěvník"**, ale leží v
`u8-1780300322_muj-zpevnik` — název se přejmenoval, složka zůstala. Cesta prostě lže.

**3. E-mail uživatele.** `8_ales.chudarek-seznam.cz`. Změní se e-mail, rozejde se cesta.
Navíc e-mail leze do URL, kterou vidí prohlížeč i logy.

**4. Číslo strany ve zpěvníku.** `page10.png` míchá pořadí v písni s číslem strany ve
zpěvníku. Jenže přidání jedné strany dopředu přečísluje celý zpěvník, zatímco soubory
zůstanou. Proto dnes jméno souboru neodpovídá číslu strany.

Na to, že je derivace cest nespolehlivá, ukazuje sám kód: `_book_storage_base` má fallback,
který cestu zpětně odhaduje z už uloženého sloupce obálky, a v `app.py` žije `rewrite_path`
na přepisování cest, když se něco přesune.

## Standard

**Z cesty zmizí všechno, co se může změnit. Zůstanou jen identifikátory.**

```
data/images/
  verejne/
    songbooks/<songbook_id>/covers/front-out.png   obálky: front|back × out|in
                                  /front-in.png
                                  /back-in.png
                                  /back-out.png
    pages/001234.png                               strany, ploché, id z tabulky images
  uzivatele/<user_id>/
    songbooks/<songbook_id>/covers/front-out.png
    pages/001235.png
```

Odvozená data, která jde kdykoliv smazat a vyrobit znovu, patří stranou:

```
data/odvozene/
  nahledy/obalky/<songbook_id>-<klic>.webp
          strany/<otisk-cesty>-<klic>.webp
  exports/<songbook_id>-<varianta>.pdf
```

### Proč zrovna takhle

**Strana není majetkem písně.** Tohle je oprava dřívější verze dokumentu, která navrhovala
`songs/<song_id>/01.png`. Takový tvar neunese skutečnost, protože vztah písně a strany je
**many-to-many v obou směrech**:

- píseň se může táhnout přes víc stran — dnes 32 písní,
- na jedné straně můžou být dvě písně — dnes 18 stran.

U sdílené strany by `songs/<song_id>/` musel někdo vyhrát a druhá píseň by zase ukazovala
pod cizí složku. To je přesně ta chyba, kterou layout A dělá se zpěvníkem; nemá cenu ji jen
přesunout o patro níž.

A pod zpěvník ji dát nejde taky, protože **70 písní je dnes ve dvou zpěvnících naráz**.
Uložit stranu pod zpěvník je přesně to, co dělá layout A, a je to ta původní chyba.

**Strana tedy nepatří ničemu a je vlastní věc.** Dostane vlastní `id` z nové tabulky
`images` a leží plochá v `pages/`. `song_images` pak není vlastnictví, ale vazba: „tahle
strana nese tuhle píseň a je to její N-tá strana". Soubor se smaže, až na něj neukazuje
nikdo — což už dnešní `smaz_osirele_obrazky` dělá, jen se bude ptát na `image_id` místo
na cestu.

**Kořen se řídí tím, kdo obrázek nahrál, ne kdo ho čte.** Strana vzniklá u veřejného
zpěvníku leží ve `verejne/pages/` i tehdy, když ji pak používá něčí soukromý zpěvník —
přesně těch 70 dnešních případů. Nevadí to, protože o právech rozhoduje prohlížený zpěvník,
ne umístění souboru (viz výš), a místo zabrané účtem tak zůstane počítané tomu, kdo ho
opravdu zabral.

**Obálka je jiný případ a zůstává čitelná.** Obálky se nesdílejí, jsou právě čtyři na
zpěvník a každá má jinou roli, takže u nich pevné jméno pod zpěvníkem nelže a dá se
`ls`-nout.

**Co to stojí.** Ploché `pages/001234.png` se hůř prohlíží — dnes jde `ls 00001/` a je
vidět celý zpěvník. Je to vědomá výměna: čitelnost výpisu za to, že cesta nikdy netvrdí
nepravdu. Kdo potřebuje vidět zpěvník po souborech, dostane na to CLI; databáze tu
informaci má přesně a složka ji měla jen náhodou.

**Dva kořeny zůstávají, ale dělí se podle `user_id`, ne podle názvu a e-mailu.** `user_id`
je celé číslo, které se nikdy nemění. Dělení podle vlastníka drží dvě věci, které budeme
potřebovat: **kolik místa zabral jeden účet** se zjistí jedním `du` (viz úkol o stropu na
nahrávání), a záloha i předání dat jdou dělat po uživatelích.

**Přístupová práva se z cesty přestanou číst.** Dnes se `users/` prefixem rozhoduje, odkud
se soubor bere. To ale nikdy nebyla kontrola práv — soukromý zpěvník běžně ukazuje na
veřejný soubor, takže umístění souboru o právech nic neříká. Rozhoduje **zpěvník, který
uživatel prohlíží**, ne kde leží bajty. Prakticky: routa se zeptá, jestli uživatel vidí
aspoň jeden zpěvník, který ten obrázek obsahuje. Je to jeden indexovaný dotaz a řeší to
i sdílené zpěvníky (viz úkol o `serve_songbook_image` bez kontroly práv).

**Jméno souboru nikdy nepochází z uploadu.** Uživatel nahraje `co_radi_hrajeme.png` a uloží
se to jako `01.png`. Původní jméno nenese informaci, zato nese diakritiku, mezery a
překvapení. Přípona se řídí skutečným formátem, ne tím, co přišlo.

**Jméno souboru nenese pořadí vůbec.** Pořadí je dvakrát v DB a pokaždé o něčem jiném:
`song_images.poradi` je pořadí strany **v rámci písně** (u sdílené strany má každá píseň
své vlastní) a `songbook_pages.page_number` je číslo strany **ve zpěvníku**. Ani jedno
nepatří do názvu souboru, protože obojí se mění, aniž by se obrázek dotkl.

**Žádné `T`, `OLD` ani `-final`.** Varianta téhož obrázku patří buď do historie, nebo do
koše, ne vedle originálu.

**Průhledná obálka je preferovaný tvar**, protože barva zpěvníku se kreslí pod ni a jde
měnit bez překreslování.

## Co se musí opravit v databázi, ne ve složkách

Struktura složek sama nestačí. Dvě věci v DB stojí za to srovnat zároveň, protože na nich
migrace stojí:

**~~`song_images` nemá sloupec pořadí.~~ Hotovo** — sloupec `poradi` přibyl a naplnil ho
`backend/scripts/migrace_poradi_stran.py`. Pro pořádek, proč to muselo jít první: U vícestránkové písně (dnes 32 písní) je pořadí
stran dané jen pořadím `id` řádku. Dnes to jde ještě zkontrolovat proti jménu souboru
(`page18` před `page19`), ale **standard tuhle informaci z názvu odstraňuje**. Pokud se
přejmenuje dřív, než se pořadí uloží natvrdo, nezbude už čím ověřit, že se strany písně
nepřehodily. Naplnilo se to tedy z dnešních jmen, dokud ta informace
existuje. Ověřeno, že pořadí z názvů souhlasí s pořadím podle `id` u všech 32 písní, takže
se nic nepřehodilo.

**`img_path_cover_preview` duplikuje `img_path_cover_front_outer`.** Je to ukazatel na to,
která obálka se zobrazuje v přehledech, ne samostatný obrázek. Patří to do DB jako volba,
ne jako pátá cesta, kterou je potřeba držet v souladu se čtyřmi ostatními.

## Jak migrace dopadla

> Migrační skripty (`migrace_uloziste.py`, `migrace_poradi_stran.py`,
> `migrace_tabulka_images.py`) už v repozitáři nejsou — proběhly a znovu se pustit nedají.
> Zůstaly lokálně a v historii gitu. Jména níž jsou tedy záznam, ne odkaz.

Spuštěno `backend/scripts/migrace_uloziste.py --provest` nad lokální kopií:

- **1093 souborů** přeneseno, 88 obálkových odkazů a 1005 stran
- **12 sirotčích písní** smazáno (nebyly v žádném zpěvníku a soubory stejně neměly)
- suchý běh prošel bez nálezu: každý zdroj existoval, žádné dva soubory nemířily na
  týž cíl, a **na disku nezbyl soubor, který by v mapě nebyl**
- `kontrola_zpevniku.py` hlásí jedinou věc — 1093 souborů starého stromu, na které už
  nikdo neukazuje. To je přesně to, co tam po kopii má zůstat
- struktura stran ve všech 33 zpěvnících je před i po migraci **totožná**
- všech 1023 stran a 88 obálek se servíruje přes HTTP se 200

Náhledy se musely vyrobit znovu, protože klíč obsahuje cestu. To je u odvozených dat
v pořádku a řeší to `flask nahledy-warm`.

## Postup migrace

833 + 26 + 176 řádků a k tomu sloupce obálek. Mechanické to je, ale nevratné, takže postup
je stavěný tak, aby se v každém kroku dalo couvnout.

**0. Úklid před migrací.** Smazat 12 sirotčích písní, které nejsou v žádném zpěvníku a
jejichž soubory na disku stejně nejsou. Pak je počet cest v DB a počet souborů na disku
stejný a dá se na to spolehnout jako na kontrolu.

**1. ~~Pořadí stran do DB.~~ Hotovo.** `song_images.poradi` naplněný z dnešních jmen
(`page18` < `page19`, `zpevnikA4-04` < `zpevnikA4-05`), bez jediného rozporu proti pořadí
podle `id`.

**2. Tabulka `images`.** Každé unikátní cestě jeden řádek s `id`; `song_images` a sloupce
obálek na ni začnou ukazovat. Dnes je cest 1105 a souborů 1093, takže se to musí potkat
na kus.

**3. Suchý běh.** Vyrobit tabulku staré cesty → nová cesta a **nic nepřesouvat**. Ověřit:
každá cesta z DB se mapuje právě na jednu novou, žádné dvě se netrefí do stejné, každý
zdroj na disku existuje, a každý soubor na disku je buď v mapě, nebo na seznamu
nepoužívaných. Dnes to vychází přesně (1093 souborů, na každý něco ukazuje), takže
**jakýkoliv rozdíl v suchém běhu je chyba mapování**, ne dědictví.

**4. Kopírovat, ne přesouvat.** Nový strom se postaví vedle starého. Dokud se nepřepne, je
stav vratný smazáním jedné složky.

**5. Přepis cest v DB na kopii databáze.** Jedna transakce, `song_images` i čtyři sloupce
obálek. Teprve po ověření na ostrou.

**6. Kontrola tím, co už máme.** `backend/scripts/kontrola_zpevniku.py` porovnává čtečku
proti PDF — to je přejímací test. Musí projít se stejným výsledkem jako před migrací.
K tomu `flask nahledy-warm`, protože náhledy jsou klíčované cestou a všechny se vyrobí znovu.

**7. Starý strom nechat ležet**, dokud si pár dní nesedne provoz. Teprve pak smazat.

**Nesmí se to potkat s importem z PDF.** Import je jediná další věc, která do úložiště
zapisuje ve velkém. Dokud není migrace hotová, vyráběl by pátý tvar cest — proto v TODO
čeká na tenhle úkol.
