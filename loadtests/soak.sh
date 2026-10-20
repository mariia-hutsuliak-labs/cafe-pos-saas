#!/usr/bin/env bash
# Лаба 8: тривале тестування стабільності (Soak Test) усього стеку:
# nginx -> три репліки застосунку -> PostgreSQL + Redis.
#
# Що робить:
#   1. готує стек і дані (loadtests/lab8_prepare.sh);
#   2. запускає збір метрик (loadtests/soak_monitor.py) на весь час тесту;
#   3. запускає Locust через nginx (порт 8080) на задану тривалість;
#   4. зупиняє збір метрик і лишає файли в results/lab8/.
#
# Запуск:
#   export LOAD_ADMIN_EMAIL=load@example.com LOAD_ADMIN_PASSWORD=loadpass123
#   ./loadtests/soak.sh 1h 350        # тривалість і кількість користувачів
#   ./loadtests/soak.sh 3m 100        # пробний прогін, щоб перевірити, що все працює
#
# Змінні: SOAK_LABEL (назва файлів, за замовчуванням soak), BULK_SALES, NGINX_PORT.
set -eu
cd "$(dirname "$0")/.."
: "${LOAD_ADMIN_EMAIL:?задай LOAD_ADMIN_EMAIL}"
: "${LOAD_ADMIN_PASSWORD:?задай LOAD_ADMIN_PASSWORD}"

DURATION="${1:-1h}"
USERS="${2:-350}"
LABEL="${SOAK_LABEL:-soak}"
BULK_SALES="${BULK_SALES:-100000}"
HOST="http://127.0.0.1:${NGINX_PORT:-8080}"
OUT="results/lab8"
mkdir -p "$OUT"

# Mac не повинен засинати посеред тесту: caffeinate живе, поки живий цей скрипт.
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -dimsu -w $$ &
fi

# shellcheck source=loadtests/lab8_prepare.sh
source loadtests/lab8_prepare.sh

echo "=== Запускаю збір метрик ==="
python loadtests/soak_monitor.py --out "${OUT}/${LABEL}_resources.csv" --interval 10 \
  --meta "users=${USERS}" --meta "duration=${DURATION}" --meta "host=${HOST}" \
  --meta "bulk_sales=${BULK_SALES}" --meta "commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" &
MONITOR=$!
trap 'kill -TERM "$MONITOR" 2>/dev/null || true; wait "$MONITOR" 2>/dev/null || true' EXIT
sleep 15

echo "=== Locust: ${USERS} користувачів, ${DURATION} ==="
# LOAD_TOKEN_MAX_AGE_S: токен живе 60 хв, тож клієнти входять повторно через 50 хв.
# --csv-full-history: без нього перцентилі в *_stats_history.csv накопичувальні
# (за весь час тесту), а для графіка затримки в часі потрібні перцентилі за останні 10 с.
LOAD_TOKEN_MAX_AGE_S=3000 locust -f loadtests/locustfile.py --host "$HOST" --headless \
  -u "$USERS" -r 10 -t "$DURATION" --only-summary --csv-full-history \
  --csv "${OUT}/${LABEL}" --html "${OUT}/${LABEL}.html" || true

echo "Готово. Файли: ${OUT}/${LABEL}_*.csv"
echo "Звіт: python loadtests/soak_report.py --label ${LABEL} --update-readme README.md"
