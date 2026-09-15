# Kam ukládat obrázky

Dnes v datech žijí čtyři různé tvary cest. Vznikly postupně a každý dává smysl sám o sobě,
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
    songbooks/<songbook_id>/covers/front-out.png     obálky: front|back × out|in
                                  /front-in.png
                                  /back-in.png
                                  /back-out.png
    songs/<song_id>/01.png                           strany písně, pořadí od 01
                   /02.png
  uzivatele/<user_id>/
    songbooks/<songbook_id>/covers/front-out.png
    songs/<song_id>/01.png
```

Odvozená data, která jde kdykoliv smazat a vyrobit znovu, patří stranou:

```
data/odvozene/
  nahledy/obalky/<songbook_id>-<klic>.webp
          strany/<otisk-cesty>-<klic>.webp
  exports/<songbook_id>-<varianta>.pdf
```

### Proč zrovna takhle

**Obálka patří zpěvníku, strana patří písni.** To je jediné dělení, které odpovídá
skutečnosti: obálek jsou právě čtyři na zpěvník a každá má jinou roli, zatímco strana může
být ve více zpěvnících a nepatří žádnému z nich zvlášť.

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

**Pořadí dvojmístné, od 01, a jen v rámci písně.** Číslo strany ve zpěvníku je vlastnost
zpěvníku, ne obrázku — drží ho `songbook_pages.page_number` a smí se měnit, aniž by se
sahalo na soubory.

**Žádné `T`, `OLD` ani `-final`.** Varianta téhož obrázku patří buď do historie, nebo do
koše, ne vedle originálu.

**Průhledná obálka je preferovaný tvar**, protože barva zpěvníku se kreslí pod ni a jde
měnit bez překreslování.

## Co se musí opravit v databázi, ne ve složkách

Struktura složek sama nestačí. Dvě věci v DB stojí za to srovnat zároveň, protože na nich
migrace stojí:

**`song_images` nemá sloupec pořadí.** U vícestránkové písně (dnes 32 písní) je pořadí
stran dané jen pořadím `id` řádku. Dnes to jde ještě zkontrolovat proti jménu souboru
(`page18` před `page19`), ale **standard tuhle informaci z názvu odstraňuje**. Pokud se
přejmenuje dřív, než se pořadí uloží natvrdo, nezbude už čím ověřit, že se strany písně
nepřehodily. Sloupec `poradi` je tedy nutné přidat **před** migrací a naplnit ho z dnešních
jmen, dokud ta informace existuje.

**`img_path_cover_preview` duplikuje `img_path_cover_front_outer`.** Je to ukazatel na to,
která obálka se zobrazuje v přehledech, ne samostatný obrázek. Patří to do DB jako volba,
ne jako pátá cesta, kterou je potřeba držet v souladu se čtyřmi ostatními.

## Migrace

833 + 26 + 176 řádků a k tomu sloupce obálek. Mechanické to je, ale nevratné, takže postup
je stavěný tak, aby se v každém kroku dalo couvnout.

**0. Úklid před migrací.** Smazat 12 sirotčích písní, které nejsou v žádném zpěvníku a
jejichž soubory na disku stejně nejsou. Pak je počet cest v DB a počet souborů na disku
stejný a dá se na to spolehnout jako na kontrolu.

**1. Pořadí stran do DB.** Přidat `song_images.poradi` a naplnit ho z dnešních jmen souborů
(`page18` < `page19`, `zpevnikA4-04` < `zpevnikA4-05`). U 32 vícestránkových písní ručně
překontrolovat. Tenhle krok se nesmí přeskočit ani odložit, viz výš.

**2. Suchý běh.** Vyrobit tabulku staré cesty → nová cesta a **nic nepřesouvat**. Ověřit:
každá cesta z DB se mapuje právě na jednu novou, žádné dvě se netrefí do stejné, každý
zdroj na disku existuje, a každý soubor na disku je buď v mapě, nebo na seznamu
nepoužívaných. Dnes to vychází přesně (1093 souborů, na každý něco ukazuje), takže
**jakýkoliv rozdíl v suchém běhu je chyba mapování**, ne dědictví.

**3. Kopírovat, ne přesouvat.** Nový strom se postaví vedle starého. Dokud se nepřepne, je
stav vratný smazáním jedné složky.

**4. Přepis cest v DB na kopii databáze.** Jedna transakce, `song_images` i čtyři sloupce
obálek. Teprve po ověření na ostrou.

**5. Kontrola tím, co už máme.** `backend/scripts/kontrola_zpevniku.py` porovnává čtečku
proti PDF — to je přejímací test. Musí projít se stejným výsledkem jako před migrací.
K tomu `flask nahledy-warm`, protože náhledy jsou klíčované cestou a všechny se vyrobí znovu.

**6. Starý strom nechat ležet**, dokud si pár dní nesedne provoz. Teprve pak smazat.

**Nesmí se to potkat s importem z PDF.** Import je jediná další věc, která do úložiště
zapisuje ve velkém. Dokud není migrace hotová, vyráběl by pátý tvar cest — proto v TODO
čeká na tenhle úkol.
