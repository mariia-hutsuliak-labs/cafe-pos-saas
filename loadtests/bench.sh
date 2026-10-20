#!/usr/bin/env bash
# Лаба 7: замір одного екземпляра (web1, порт 8000) з позначкою етапу.
# Перед кожним прогоном: збірка web1, очищення чеків, свіжі демо-дані.
# Під час прогону пише CPU і RAM контейнерів web1 та db (docker stats).
#
# Запуск:
#   LOAD_ADMIN_EMAIL=... LOAD_ADMIN_PASSWORD=... ./loadtests/bench.sh <мітка> [кількості користувачів]
# Приклад:
#   ./loadtests/bench.sh before            # 100, 200, 300
#   ./loadtests/bench.sh before 100        # тільки 100
#   BULK_SALES=100000 ./loadtests/bench.sh before_100k 100 300 600 1000
#                                          # з великим обсягом даних (100 тис. чеків)
#
# Файли: results/lab7/<мітка>_u<N>_stats.csv, _stats_history.csv, .html, _resources.csv
set -eu
cd "$(dirname "$0")/.."
: "${LOAD_ADMIN_EMAIL:?задай LOAD_ADMIN_EMAIL}"
: "${LOAD_ADMIN_PASSWORD:?задай LOAD_ADMIN_PASSWORD}"

LABEL="${1:?вкажи мітку етапу, наприклад before}"
shift || true
USERS="${*:-100 200 300}"
mkdir -p results/lab7

sample_resources() {
  # $1 - файл результату. Працює, поки його не зупинять.
  local out="$1"
  local web db start
  web="$(docker compose ps -q web1)"
  db="$(docker compose ps -q db)"
  start="$(date +%s)"
  echo "elapsed_s,container,cpu_pct,mem_usage" >"$out"
  while true; do
    now="$(date +%s)"
    docker stats --no-stream --format '{{.ID}},{{.CPUPerc}},{{.MemUsage}}' "$web" "$db" |
      while IFS=, read -r id cpu mem; do
        name="db"
        case "$web" in "$id"*) name="web1" ;; esac
        echo "$((now - start)),$name,${cpu%\%},${mem%% /*}" >>"$out"
      done
  done
}

reset() {
  docker compose up -d --build web1
  docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "TRUNCATE sale_items, sales RESTART IDENTITY;"'
  docker compose restart web1
  sleep 25
  docker compose exec -T web1 python -m cafe_pos.seed_demo_data
  if [ -n "${BULK_SALES:-}" ]; then
    echo "Додаю ${BULK_SALES} чеків для реалістичного обсягу даних..."
    docker compose exec -T db sh -c "psql -q -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v n=${BULK_SALES}" <loadtests/bulk_sales.sql
    docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM ANALYZE;"'
  fi
  # Перевірка входу до запуску Locust: без адміна тест не має сенсу.
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/auth/login \
    -d "username=${LOAD_ADMIN_EMAIL}&password=${LOAD_ADMIN_PASSWORD}")"
  if [ "$code" != "200" ]; then
    echo "Вхід під ${LOAD_ADMIN_EMAIL} не вдався (HTTP $code). Створи адміна:"
    echo "  docker compose exec web1 python -m cafe_pos.create_admin <email> <пароль>"
    exit 1
  fi
}

for u in $USERS; do
  echo "=== ${LABEL}: ${u} користувачів ==="
  reset
  sample_resources "results/lab7/${LABEL}_u${u}_resources.csv" &
  SAMPLER=$!
  locust -f loadtests/locustfile.py --host http://127.0.0.1:8000 --headless \
    -u "$u" -r 10 -t 2m --only-summary \
    --csv "results/lab7/${LABEL}_u${u}" --html "results/lab7/${LABEL}_u${u}.html" || true
  kill "$SAMPLER" 2>/dev/null || true
  wait "$SAMPLER" 2>/dev/null || true
done
echo "Готово. Файли: results/lab7/${LABEL}_u*_stats.csv"
