#!/usr/bin/env bash
# Лаба 8, додаткове завдання 1: стрес-тест і раптовий пік усього стеку
# (nginx -> три репліки -> PostgreSQL + Redis).
#
#   ./loadtests/stress.sh step    # +100 користувачів кожні 60 с до 1500 або до зламу,
#                                 # потім 3 хв базового навантаження (відновлення), ~18 хв
#   ./loadtests/stress.sh spike   # 150 користувачів 2 хв -> 900 на 1 хв -> 150 на 4 хв, ~7 хв
#
# Параметри форми навантаження (змінні оточення) описані в loadtests/locustfile_stress.py,
# наприклад: MAX_USERS=2000 STEP_USERS=200 ./loadtests/stress.sh step
#
# Файли: results/lab8/stress_<режим>_*.csv. Звіт:
#   python loadtests/stress_report.py --mode step --label stress_step
set -eu
cd "$(dirname "$0")/.."
: "${LOAD_ADMIN_EMAIL:?задай LOAD_ADMIN_EMAIL}"
: "${LOAD_ADMIN_PASSWORD:?задай LOAD_ADMIN_PASSWORD}"

MODE="${1:?вкажи режим: step або spike}"
case "$MODE" in step | spike) ;; *)
  echo "Режим має бути step або spike"
  exit 1
  ;;
esac
LABEL="stress_${MODE}"
BULK_SALES="${BULK_SALES:-100000}"
HOST="http://127.0.0.1:${NGINX_PORT:-8080}"
OUT="results/lab8"
mkdir -p "$OUT"

if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -dimsu -w $$ &
fi

# shellcheck source=loadtests/lab8_prepare.sh
source loadtests/lab8_prepare.sh

echo "=== Запускаю збір метрик ==="
python loadtests/soak_monitor.py --out "${OUT}/${LABEL}_resources.csv" --interval 5 \
  --meta "mode=${MODE}" --meta "host=${HOST}" --meta "bulk_sales=${BULK_SALES}" \
  --meta "commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" &
MONITOR=$!
trap 'kill -TERM "$MONITOR" 2>/dev/null || true; wait "$MONITOR" 2>/dev/null || true' EXIT
sleep 10

echo "=== Locust: режим ${MODE} ==="
# Кількість користувачів і час задає форма навантаження (locustfile_stress.py), -u і -t не потрібні.
LOAD_TOKEN_MAX_AGE_S=3000 STRESS_MODE="$MODE" locust \
  -f loadtests/locustfile.py,loadtests/locustfile_stress.py --host "$HOST" --headless \
  --only-summary --csv-full-history \
  --csv "${OUT}/${LABEL}" --html "${OUT}/${LABEL}.html" || true

echo "Стан контейнерів після тесту:"
docker compose ps
echo "Готово. Звіт: python loadtests/stress_report.py --mode ${MODE} --label ${LABEL} --update-readme README.md"
