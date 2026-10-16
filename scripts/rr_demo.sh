#!/usr/bin/env bash
# Робить N запитів до балансувальника і показує, яка репліка відповіла на кожен.
# Запуск: ./scripts/rr_demo.sh [кількість запитів] [адреса]
set -u

COUNT="${1:-20}"
URL="${2:-http://127.0.0.1:8080/}"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "Адреса: $URL, запитів: $COUNT"
echo "----------------------------------"
for i in $(seq 1 "$COUNT"); do
  CODE="$(curl -s -o /dev/null -D "$TMP" -w '%{http_code}' --max-time 5 "$URL")"
  SERVER="$(tr -d '\r' <"$TMP" | awk -F': ' 'tolower($1)=="x-served-by"{print $2}')"
  printf '%2d  HTTP %s  %s\n' "$i" "$CODE" "${SERVER:-немає відповіді}"
  echo "${SERVER:-немає відповіді}" >>"$TMP.count"
done

echo "----------------------------------"
echo "Підсумок (скільки запитів обробила кожна репліка):"
sort "$TMP.count" | uniq -c
rm -f "$TMP.count"
