#!/usr/bin/env bash
# Чесне порівняння: перед кожним прогоном база очищається від чеків
# навантаження, демо-дані створюються заново, репліки перезапускаються.
# Запуск: LOAD_ADMIN_EMAIL=... LOAD_ADMIN_PASSWORD=... ./loadtests/compare.sh
set -eu
cd "$(dirname "$0")/.."
: "${LOAD_ADMIN_EMAIL:?задай LOAD_ADMIN_EMAIL}"
: "${LOAD_ADMIN_PASSWORD:?задай LOAD_ADMIN_PASSWORD}"

reset() {
  docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "TRUNCATE sale_items, sales RESTART IDENTITY;"'
  docker compose restart web1 web2 web3
  sleep 25
  docker compose restart nginx
  sleep 5
  docker compose exec -T web1 python -m cafe_pos.seed_demo_data
}

run() {
  echo "=== $1 ($3 користувачів) ==="
  reset
  locust -f loadtests/locustfile.py --host "$2" --headless -u "$3" -r 10 -t 2m \
    --only-summary --csv "results/fair_$1" --html "results/fair_$1.html" || true
}

for users in 100 200 300; do
  run "single_u$users" http://127.0.0.1:8000 "$users"
  run "lb_u$users" http://127.0.0.1:8080 "$users"
done
echo "Готово. Підсумок: див. results/fair_*_stats.csv"
