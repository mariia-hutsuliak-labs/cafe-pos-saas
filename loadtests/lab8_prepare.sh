#!/usr/bin/env bash
# Лаба 8: спільна підготовка для soak.sh і stress.sh. Запускається через `source`.
#   1. піднімає стек і чекає, поки всі контейнери стануть healthy;
#   2. очищає чеки і кеш, перезапускає репліки (однаковий старт для RAM);
#   3. додає демо-дані і BULK_SALES чеків (за замовчуванням 100 000, як у лабі 7);
#   4. перевіряє вхід через nginx.
# Очікує змінні: HOST, BULK_SALES, LOAD_ADMIN_EMAIL, LOAD_ADMIN_PASSWORD.

echo "=== Піднімаю стек ==="
docker compose up -d --build --wait

echo "=== Готую дані ==="
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "TRUNCATE sale_items, sales RESTART IDENTITY;"'
docker compose exec -T redis redis-cli FLUSHALL >/dev/null
docker compose restart web1 web2 web3
sleep 25
docker compose restart nginx
sleep 5
docker compose exec -T web1 python -m cafe_pos.seed_demo_data
if [ "$BULK_SALES" != "0" ]; then
  echo "Додаю ${BULK_SALES} чеків..."
  docker compose exec -T db sh -c "psql -q -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v n=${BULK_SALES}" <loadtests/bulk_sales.sql
  docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM ANALYZE;"'
fi

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${HOST}/auth/login" \
  -d "username=${LOAD_ADMIN_EMAIL}&password=${LOAD_ADMIN_PASSWORD}")"
if [ "$code" != "200" ]; then
  echo "Вхід під ${LOAD_ADMIN_EMAIL} через ${HOST} не вдався (HTTP $code). Створи адміна:"
  echo "  docker compose exec web1 python -m cafe_pos.create_admin <email> <пароль>"
  exit 1
fi
