#!/bin/bash
#
# Uklidí obrázky lokálně a výsledek nahraje na server.
#
# Proč ne rovnou na serveru: převod 924 obrázků trvá na Macu 25 minut, na tom Oracle
# free-tier stroji odhadem 1-2 hodiny, a to celou dobu pod webem, který má obsluhovat.
# Nahrání hotových souborů trvá při naměřených 3,5 MB/s zhruba dvě minuty.
#
# Riziko téhle cesty je jediné: mezi stažením dat a nahráním výsledku by někdo mohl na
# serveru něco změnit a my bychom mu to přepsali. Proto se před nahráním pořizuje otisk
# serverových souborů (cesta, velikost, čas změny) a znovu se kontroluje. Když se cokoliv
# liší, skript skončí a nenahraje nic.
#
#   ./scripts/uklid-a-nahrat.sh              jen připraví a spočítá, na server nesáhne
#   ./scripts/uklid-a-nahrat.sh --nahrat     provede i nahrání

set -euo pipefail

SERVER="ubuntu@92.5.116.155"
KLIC="$HOME/.ssh/zpevnik-oracle.key"
VZDALENY="digitalni-zpevnik/data/public/images/songbooks"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZALOHA="$HOME/zpevnik-zalohy/aktualni"
PRACE="$HOME/zpevnik-zalohy/prace"
OTISK_PRED="$PRACE/otisk-pred.txt"
OTISK_PRED_NAHRANIM="$PRACE/otisk-pred-nahranim.txt"

NAHRAT=0
[ "${1:-}" = "--nahrat" ] && NAHRAT=1

ssh_cmd() { ssh -i "$KLIC" -o ConnectTimeout=20 -o BatchMode=yes "$SERVER" "$@"; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

otisk() {
  # Cesta, velikost a čas změny každého souboru. Tohle musí sedět před i po převodu,
  # jinak nám mezitím někdo sáhl na data a nahrávat by znamenalo přepsat mu je.
  ssh_cmd "cd $VZDALENY && find . -type f -printf '%p|%s|%T@\n' | LC_ALL=C sort"
}

log "1/6  čerstvá záloha ze serveru"
"$REPO/scripts/zaloha-ze-serveru.sh" >/dev/null
log "     hotovo"

mkdir -p "$PRACE"
log "2/6  otisk serverových souborů"
otisk > "$OTISK_PRED"
log "     $(wc -l < "$OTISK_PRED" | tr -d ' ') souborů"

log "3/6  pracovní kopie (tvrdé odkazy, místo navíc nezabere)"
rm -rf "$PRACE/songbooks"
cp -al "$ZALOHA/data/public/images/songbooks" "$PRACE/songbooks"

log "4/6  úklid stran (tohle je ta pomalá část, ~25 minut)"
"$REPO/.venv/bin/python" "$REPO/backend/scripts/uklid_obrazku.py" "$PRACE/songbooks" --apply \
  | tail -20

log "5/6  povýšení průhledných obálek"
# Skript čeká strukturu data/public/images/songbooks, tak mu ji podstrčíme.
rm -rf "$PRACE/dataroot"
mkdir -p "$PRACE/dataroot/public/images"
ln -s "$PRACE/songbooks" "$PRACE/dataroot/public/images/songbooks"
"$REPO/.venv/bin/python" "$REPO/backend/scripts/povysit_obalky.py" \
  --data "$PRACE/dataroot" --db "$ZALOHA/zpevnik.db" --apply | tail -20

PRED_MB=$(du -sm "$ZALOHA/data/public/images/songbooks" | cut -f1)
PO_MB=$(du -sm "$PRACE/songbooks" | cut -f1)
log "     $PRED_MB MB -> $PO_MB MB"

# Co na serveru zbude navíc: povýšené T soubory, které u nás už neexistují.
cd "$PRACE/songbooks"
find . -type f | LC_ALL=C sort > "$PRACE/mistni.txt"
cut -d'|' -f1 "$OTISK_PRED" | LC_ALL=C sort > "$PRACE/serverove.txt"
LC_ALL=C comm -13 "$PRACE/mistni.txt" "$PRACE/serverove.txt" > "$PRACE/ke-smazani.txt"
log "     ke smazání na serveru: $(wc -l < "$PRACE/ke-smazani.txt" | tr -d ' ') souborů"

if [ "$NAHRAT" -eq 0 ]; then
  echo
  log "HOTOVO nanečisto. Na server se nesáhlo."
  log "Výsledek leží v $PRACE/songbooks, nahraj ho pomocí --nahrat"
  exit 0
fi

log "6/6  kontrola, že je na serveru oprava exportu"
# Povýšené obálky jsou průhledné. Bez opravy _flatten_to_rgb by je PDF složilo na bílou
# a export-warm níž by rovnou předgeneroval třicet zpěvníků s bílou obálkou.
if ! ssh_cmd "grep -q '_hex_to_rgb' digitalni-zpevnik/backend/app.py"; then
  echo
  log "❌ STOP: server nemá opravu exportu. Nejdřív commit, push a git pull na serveru."
  exit 1
fi
log "     oprava nasazená"

log "     kontrola, že se server mezitím nezměnil"
otisk > "$OTISK_PRED_NAHRANIM"
if ! diff -q "$OTISK_PRED" "$OTISK_PRED_NAHRANIM" >/dev/null; then
  echo
  log "❌ STOP: data na serveru se mezitím změnila, nenahrávám nic."
  diff "$OTISK_PRED" "$OTISK_PRED_NAHRANIM" | head -20
  exit 1
fi
log "     sedí, server je nedotčený"

log "     nahrávám"
rsync -a --no-perms --no-owner --no-group \
  -e "ssh -i $KLIC -o BatchMode=yes" \
  "$PRACE/songbooks/" "$SERVER:$VZDALENY/"

if [ -s "$PRACE/ke-smazani.txt" ]; then
  log "     mažu povýšené T soubory na serveru"
  # Přes stdin, ať se to nevejde do jediné příliš dlouhé příkazové řádky.
  sed 's|^\./||' "$PRACE/ke-smazani.txt" \
    | ssh_cmd "cd $VZDALENY && xargs -d '\n' rm -f --"
fi

log "     kontrola po nahrání"
ssh_cmd "cd $VZDALENY && echo \"souborů: \$(find . -type f | wc -l), velikost: \$(du -sm . | cut -f1) MB\""

log "     předgenerování PDF (klíče cache se změnily)"
ssh_cmd "cd digitalni-zpevnik && .venv/bin/flask --app backend.app export-warm" || \
  log "     ⚠️ export-warm neprošel, PDF se dogenerují na vyžádání"

log "HOTOVO"
