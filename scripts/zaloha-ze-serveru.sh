#!/bin/bash
#
# Stáhne data z produkčního serveru na tenhle Mac.
#
# Server je jediné místo, kde žijí ostrá data: zpěvníky vytvořené přes web nikde jinde
# neexistují a v gitu je od commitu "Obsah zpěvníků ven z gitu" taky nemáme. Tenhle skript
# je proto jediná záloha mimo server.
#
# Cílem je zalohy/ uvnitř repa. Je gitignorovaná a má vlastní prázdný .git, aby ji
# nesmazalo "git clean -xdf" - to maže i ignorované soubory a vzalo by tím jedinou kopii
# dat mimo server.
#
# Drží zrcadlo v aktualni/ plus sedm denních snímků v snimky/. Snímky jsou tvrdé odkazy,
# takže nezměněný soubor nezabere místo podruhé - sedm verzí 1GB dat stojí pár desítek MB.
# Kvůli nim přežije i to, co někdo na serveru smaže: samotné zrcadlo by smazání propagovalo.
#
# Spouští se denně přes launchd (cz.chudarek.zpevnik-zaloha), ručně kdykoli bez argumentů.

set -euo pipefail

SERVER="ubuntu@92.5.116.155"
KLIC="$HOME/.ssh/zpevnik-oracle.key"
VZDALENY_REPO="digitalni-zpevnik"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CIL="$REPO/zalohy"
ZRCADLO="$CIL/aktualni"
SNIMKY="$CIL/snimky"
LOG="$CIL/log/zaloha.log"
ZAMEK="$CIL/.zamek"

DRZET_SNIMKU=7
POKUSU=3

# Keepalive: přenos 500 MB trvá minuty a spojení mezi tím občas usne. Bez tohohle
# spadne celá záloha na "Operation timed out" a den zůstane bez snímku - jednou se to
# stalo. S keepalive se ssh ozývá každých 15 s a vydrží dvě minuty ticha.
SSH_VOLBY="-i $KLIC -o ConnectTimeout=20 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=8"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }

mkdir -p "$ZRCADLO" "$SNIMKY" "$(dirname "$LOG")"

# Dva souběžné běhy by si šlapaly po zrcadle (launchd může dohnat zmeškaný běh v okamžiku,
# kdy už jeden ručně spuštěný jede). mkdir je atomický, takže poslouží jako zámek.
if ! mkdir "$ZAMEK" 2>/dev/null; then
  log "PRESKOCENO: uz bezi jina zaloha ($ZAMEK)"
  exit 0
fi
trap 'rmdir "$ZAMEK" 2>/dev/null || true' EXIT

log "=== start ==="

ssh_cmd() { ssh $SSH_VOLBY "$SERVER" "$@"; }

if ! ssh_cmd true 2>/dev/null; then
  log "CHYBA: server $SERVER neodpovida, koncim bez zmeny zalohy"
  exit 1
fi

# SQLite se nesmí zálohovat obyčejným cp - když v tu chvíli běží zápis, kopie je rozbitá.
# conn.backup() udělá konzistentní snímek i za provozu. Server nemá sqlite3 CLI, jede python.
log "delam konzistentni snimek DB na serveru"
ssh_cmd "python3 - <<'PY'
import sqlite3, os
zdroj = os.path.expanduser('~/$VZDALENY_REPO/backend/instance/zpevnik.db')
cil_dir = '/tmp/zpevnik-zaloha'
os.makedirs(cil_dir, exist_ok=True)
cil = os.path.join(cil_dir, 'zpevnik.db')
src = sqlite3.connect('file:' + zdroj + '?mode=ro', uri=True)
dst = sqlite3.connect(cil)
with dst:
    src.backup(dst)
dst.close(); src.close()
print('DB snimek OK, %d B' % os.path.getsize(cil))
PY" | tee -a "$LOG"

# --delete drží zrcadlo věrné serveru; historii řeší snímky níž, ne tenhle krok.
# exports/ je generovaná cache PDF a ZIP, dá se vyrobit znovu, tak ji netaháme.
# Opakování, protože spadlé spojení uprostřed stahování je jediná chyba, kterou tenhle
# skript reálně potkává. Rsync je přírůstkový, takže druhý pokus naváže tam, kde první
# skončil, a nestahuje znovu, co už leží na disku.
stahni() {
  local popis="$1"; shift
  local pokus=1
  while true; do
    if rsync "$@" 2>&1 | tee -a "$LOG"; then
      return 0
    fi
    if [ "$pokus" -ge "$POKUSU" ]; then
      log "CHYBA: $popis se nepovedlo ani na $POKUSU. pokus"
      return 1
    fi
    log "     $popis spadlo, zkouším znovu ($((pokus + 1))/$POKUSU)"
    pokus=$((pokus + 1))
    sleep 10
  done
}

log "stahuji data/ (bez exports)"
stahni "stahování dat" -a --delete --exclude 'exports/' -e "ssh $SSH_VOLBY" \
  "$SERVER:$VZDALENY_REPO/data/" "$ZRCADLO/data/"

log "stahuji DB"
stahni "stahování DB" -a -e "ssh $SSH_VOLBY" \
  "$SERVER:/tmp/zpevnik-zaloha/zpevnik.db" "$ZRCADLO/zpevnik.db"

POCET=$(find "$ZRCADLO" -type f | wc -l | tr -d ' ')
VELIKOST=$(du -sh "$ZRCADLO" | cut -f1)
log "zrcadlo: $POCET souboru, $VELIKOST"

# Prázdné zrcadlo by znamenalo, že se něco pokazilo. Snímek z něj nedělám, ať nevytlačí
# těch sedm dobrých.
if [ "$POCET" -lt 100 ]; then
  log "CHYBA: zrcadlo ma jen $POCET souboru, to nevypada spravne - snimek nedelam"
  exit 1
fi

DNES=$(date '+%Y-%m-%d')
SNIMEK="$SNIMKY/$DNES"
if [ -d "$SNIMEK" ]; then
  log "snimek $DNES uz existuje, prepisuji ho aktualnim stavem"
  rm -rf "$SNIMEK"
fi
cp -al "$ZRCADLO" "$SNIMEK"
log "snimek $DNES hotov"

# Mazání nejstarších. ls -1 na datových názvech řadí chronologicky.
POCET_SNIMKU=$(ls -1 "$SNIMKY" | wc -l | tr -d ' ')
if [ "$POCET_SNIMKU" -gt "$DRZET_SNIMKU" ]; then
  ls -1 "$SNIMKY" | head -n "$((POCET_SNIMKU - DRZET_SNIMKU))" | while read -r stary; do
    log "mazu stary snimek $stary"
    rm -rf "${SNIMKY:?}/$stary"
  done
fi

ssh_cmd "rm -rf /tmp/zpevnik-zaloha" || true

CELKEM=$(du -sh "$CIL" | cut -f1)
log "=== hotovo, zalohy celkem $CELKEM, snimku: $(ls -1 "$SNIMKY" | wc -l | tr -d ' ') ==="
