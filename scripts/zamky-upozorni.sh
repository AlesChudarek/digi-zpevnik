#!/bin/bash
#
# Upozorní e-mailem účty, kterým se zamkly zpěvníky (přes limit místa). Spouští se
# z cronu na serveru každou hodinu; pravidla proti spamu jsou v upozorni_na_zamky().
#
# Cron nemá prostředí přihlášeného uživatele, takže se proměnné načítají výslovně.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

set -a
# shellcheck disable=SC1091
. /etc/digitalni-zpevnik.env
set +a

exec .venv/bin/flask --app backend.app zamky-upozorni
