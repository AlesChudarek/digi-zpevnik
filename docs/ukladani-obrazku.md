# Kam ukládat obrázky

Dnes v datech žijí tři různá schémata. Vznikla postupně a každé dává smysl samo o sobě,
ale dohromady se v nich nedá vyznat a jedno z nich má věcnou chybu. Tenhle dokument říká,
které z nich je od teď to správné.

## Co tam je dnes

| | vzor | obrázků |
|---|---|---|
| **A** starší veřejné zpěvníky | `<id>/page7.png`, `<id>/coverfrontout.png`, `<id>/outro3.png` | 833 |
| **B** vytvořené přes web | `<id>/songs/<song_id>/zpevnikA4-04.png` | 36 |
| **C** soukromé uživatelské | `data/private/users/<uid>_<email>/<sbid>_<název>/co_radi_hrajeme.png` | 166 |

Plus pár souborů úplně mimo: `output-onlinepngtools9.png` u zpěvníku 00010 a
`coverfrontoutOLD.png` u 00016.

## Proč je A špatně

V layoutu A leží obrázek strany pod složkou toho zpěvníku, ze kterého náhodou pochází.
Jenže **70 písniček je dnes ve dvou zpěvnících naráz**. Druhý zpěvník tedy ukazuje na
soubor pod cizí složkou. Smazání prvního zpěvníku rozbije druhý a při přesunu písničky
mezi zpěvníky se cesta rozejde s realitou.

Layout B tenhle problém nemá, protože obrázek patří písni, ne zpěvníku. Je to i to, co
dnes vyrábí web, takže se k němu stejně přirozeně spějeme.

## Standard

**Obálka patří zpěvníku, strana patří písni.**

```
<kořen>/<songbook_id>/covers/front-out.png     obálky: front|back × out|in
<kořen>/<songbook_id>/covers/front-in.png
<kořen>/<songbook_id>/songs/<song_id>/01.png   strany písně, pořadí od 01
<kořen>/<songbook_id>/songs/<song_id>/02.png
```

Kořeny zůstávají dva, protože řeší přístupová práva, ne pojmenování:

- `data/public/images/songbooks/` pro veřejné zpěvníky
- `data/private/users/<uid>_<email>/` pro soukromé

Pravidla:

- **Jméno souboru nikdy nepochází z uploadu.** Uživatel nahraje `co_radi_hrajeme.png`
  a uloží se to jako `01.png`. Původní jméno nenese informaci, zato nese diakritiku,
  mezery a překvapení.
- **Pořadové číslo dvojmístné, od 01.** U vícestránkové písně je pořadí jediné, co dává
  smysl; `page<N>` z layoutu A míchalo pořadí v písni s číslem strany ve zpěvníku.
- **Obálky mají pevná jména**, ne pořadová čísla, protože jich jsou právě čtyři a každá
  má jinou roli.
- **Průhledná obálka je preferovaný tvar.** Barva zpěvníku se kreslí pod ni, takže jde
  měnit bez překreslování. Čtečka, přehledy i PDF ji podkreslují stejně.
- **Žádné přípony typu `T`, `OLD` nebo `-final`.** Varianta téhož obrázku patří buď do
  historie, nebo do koše, ne vedle originálu.

## Migrace

Starých 833 obrázků layoutu A **zatím nemigrujeme**. Je to mechanický přesun řízený
tabulkou `song_images` plus přepis cest v DB, ale je to samostatný zásah s vlastní zálohou
a ověřením. Do té doby platí, že **nový kód ukládá podle standardu** a layout A je jen
dědictví, které čteme.
