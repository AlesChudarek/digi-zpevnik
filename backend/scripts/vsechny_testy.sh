#!/bin/bash
#
# Celá přejímací sada. Testy běží souběžně - každý má vlastní port a vlastní kopii
# databáze, takže si nepřekážejí; po sobě trvaly 10-12 minut, takhle asi 5.
# test_export jde až nakonec a sám: maže a zapisuje skutečnou složku data/exports.
#
#   backend/scripts/vsechny_testy.sh            celá sada
#   backend/scripts/vsechny_testy.sh test_mobil test_sprava    jen vybrané
#
# Výstup každého testu je v $LOGY/<test>.log, na konci souhrn.

cd "$(dirname "$0")/../.." || exit 1
PY=.venv/bin/python
LOGY="${LOGY:-$(mktemp -d)}"
mkdir -p "$LOGY"
SOUBEZNE=(test_sprava test_editor test_editor_ui kontrola_zpevniku test_mobil test_napovedy
          test_kontrast test_pratele)
NAKONEC=(test_export)
if [ $# -gt 0 ]; then
  SOUBEZNE=(); NAKONEC=()
  for t in "$@"; do
    if [ "$t" = test_export ]; then NAKONEC+=("$t"); else SOUBEZNE+=("$t"); fi
  done
fi

zacatek=$(date +%s)
for t in "${SOUBEZNE[@]}"; do
  "$PY" "backend/scripts/$t.py" > "$LOGY/$t.log" 2>&1 &
done
wait
for t in "${NAKONEC[@]}"; do
  "$PY" "backend/scripts/$t.py" > "$LOGY/$t.log" 2>&1
done

selhalo=0
for t in "${SOUBEZNE[@]}" "${NAKONEC[@]}"; do
  if [ ! -s "$LOGY/$t.log" ]; then
    echo "❌ $t (žádný výstup)"; selhalo=1; continue
  fi
  chyb=$(grep -c '❌' "$LOGY/$t.log")
  if [ "$chyb" -gt 0 ] || grep -q '^Traceback' "$LOGY/$t.log"; then
    echo "❌ $t ($chyb)"; selhalo=1
  else
    echo "✅ $t"
  fi
done
echo "za $(( $(date +%s) - zacatek )) s, logy: $LOGY"
exit $selhalo
