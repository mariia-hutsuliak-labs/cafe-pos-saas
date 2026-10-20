#!/usr/bin/env bash
# Лаба 7: EXPLAIN ANALYZE до і після індексів на поточних даних у Docker-базі.
# Спершу потрібні дані: наприклад, один прогін bench.sh з BULK_SALES=100000.
# Запуск: ./loadtests/explain.sh
# Результат: results/lab7/explain_before.txt і results/lab7/explain_after.txt
set -eu
cd "$(dirname "$0")/.."
mkdir -p results/lab7

psql_db() {
  docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}

echo "Рядків у sales: $(psql_db -t -A -c 'SELECT count(*) FROM sales')"

echo "--- Без індексів ---"
psql_db -q -c "DROP INDEX IF EXISTS ix_sales_created_at, ix_sales_cafe_id, ix_sale_items_sale_id, ix_sale_items_product_id;"
psql_db -q -c "ANALYZE;"
psql_db <loadtests/explain_queries.sql >results/lab7/explain_before.txt

echo "--- З індексами ---"
psql_db -q -c "CREATE INDEX IF NOT EXISTS ix_sales_created_at ON sales (created_at);"
psql_db -q -c "CREATE INDEX IF NOT EXISTS ix_sales_cafe_id ON sales (cafe_id);"
psql_db -q -c "CREATE INDEX IF NOT EXISTS ix_sale_items_sale_id ON sale_items (sale_id);"
psql_db -q -c "CREATE INDEX IF NOT EXISTS ix_sale_items_product_id ON sale_items (product_id);"
psql_db -q -c "ANALYZE;"
psql_db <loadtests/explain_queries.sql >results/lab7/explain_after.txt

echo "Час виконання (Execution Time) по запитах:"
for f in before after; do
  echo "[$f]"
  grep -E '^=== |Execution Time' "results/lab7/explain_$f.txt" | paste - - | sed 's/===//g'
done
