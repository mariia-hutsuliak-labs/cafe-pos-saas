#!/usr/bin/env bash
# Демонстрація відмовостійкості: зупиняє одну репліку і перевіряє, що nginx
# відправляє весь трафік на живі. Після перевірки репліка запускається знову.
# Запуск: ./scripts/failover_demo.sh [репліка, за замовчуванням web2]
set -u

VICTIM="${1:-web2}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 1. Усі репліки працюють ==="
"$DIR/rr_demo.sh" 12

echo
echo "=== 2. Зупиняємо $VICTIM ==="
docker compose stop "$VICTIM"
sleep 2

echo
echo "=== 3. Запити після збою: помилок немає, $VICTIM у відповідях відсутня ==="
"$DIR/rr_demo.sh" 12

echo
echo "=== 4. Запускаємо $VICTIM назад ==="
docker compose start "$VICTIM"
echo "Чекаємо, поки nginx знову включить репліку (fail_timeout 10 с)..."
sleep 15

echo
echo "=== 5. Усі три репліки знову в розподілі ==="
"$DIR/rr_demo.sh" 12
