-- Лаба 7: плани виконання SQL-запитів, які генерує застосунок.
-- Запускається скриптом loadtests/explain.sh до і після додавання індексів.

\echo '=== Q0. Старий /analytics/summary: date(created_at) = сьогодні, всі рядки у Python ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM sales WHERE date(created_at) = current_date;

\echo '=== Q1. Новий /analytics/summary: count і sum за діапазоном created_at ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(id), coalesce(sum(total_price), 0)
FROM sales
WHERE created_at >= date_trunc('day', now())
  AND created_at < date_trunc('day', now()) + interval '1 day';

\echo '=== Q2. /analytics/revenue-by-day (14 днів) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT date(created_at) AS day, sum(total_price) AS revenue
FROM sales
WHERE created_at >= now() - interval '14 days'
GROUP BY day
ORDER BY day;

\echo '=== Q3. /sales/?limit=20: останні чеки ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM sales ORDER BY created_at DESC LIMIT 20;

\echo '=== Q4. Позиції останніх чеків (joinedload у /sales/) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM sale_items
WHERE sale_id IN (SELECT id FROM sales ORDER BY created_at DESC LIMIT 20);

\echo '=== Q5. /analytics/by-cafe: сума по всіх чеках ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT c.name, sum(s.total_price) AS revenue
FROM cafes c JOIN sales s ON s.cafe_id = c.id
GROUP BY c.name
ORDER BY revenue DESC;

\echo '=== Q6. /analytics/top-products: сума по всіх позиціях ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.name, sum(si.unit_price * si.quantity) AS revenue, sum(si.quantity) AS quantity
FROM products p JOIN sale_items si ON si.product_id = p.id
GROUP BY p.name
ORDER BY revenue DESC
LIMIT 5;
