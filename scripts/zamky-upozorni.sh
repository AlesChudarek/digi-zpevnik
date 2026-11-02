#!/bin/bash
#
# Pravidelná údržba účtů, spouští se z cronu na serveru každou hodinu (jméno skriptu
# zůstalo z doby, kdy dělal jen první část - cron na něj ukazuje):
# - upozornění na zamčené zpěvníky (upozorni_na_zamky, pravidla proti spamu tam),
# - upozornění a mazání neaktivních účtů (neaktivni_ucty).
#
# Cron nemá prostředí přihlášeného uživatele, takže se proměnné načítají výslovně.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

set -a
# shellcheck disable=SC1091
. /etc/digitalni-zpevnik.env
set +a

.venv/bin/flask --app backend.app zamky-upozorni
.venv/bin/flask --app backend.app neaktivni-ucty
