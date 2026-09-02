#!/bin/bash
#
# Odešle měsíční přehled o stavu. Spouští se z cronu na serveru.
#
# Dva úkoly zároveň: udrží živý SMTP klíč, který Brevo ruší po 90 dnech nečinnosti, a
# funguje jako kontrolka - když zpráva nedorazí, něco je rozbité. Proto se posílá i tehdy,
# když je všechno v pořádku.
#
# Cron nemá prostředí přihlášeného uživatele, takže se proměnné načítají výslovně.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

set -a
# shellcheck disable=SC1091
. /etc/digitalni-zpevnik.env
set +a

exec .venv/bin/flask --app backend.app mesicni-hlaseni
