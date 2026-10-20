-- Лаба 7: додає :n чеків (по 2 позиції) за останні 90 днів, щоб аналітика
-- працювала на обсязі даних, близькому до реальної мережі кав'ярень.
-- Запуск: psql -v n=100000 -f loadtests/bulk_sales.sql
INSERT INTO sales (cafe_id, total_price, status, created_at)
SELECT c.ids[1 + (g % array_length(c.ids, 1))],
       0,
       'paid',
       now() - (random() * 90) * interval '1 day'
FROM generate_series(1, :n) AS g,
     (SELECT array_agg(id ORDER BY id) AS ids FROM cafes) AS c;

INSERT INTO sale_items (sale_id, product_id, quantity, unit_price)
SELECT s.id, p.id, 1 + (s.id + k) % 3, p.price
FROM sales s
CROSS JOIN generate_series(0, 1) AS k
JOIN (
    SELECT id, price, row_number() OVER (ORDER BY id) - 1 AS rn FROM products
) p ON p.rn = (s.id + k) % (SELECT count(*) FROM products)
WHERE s.total_price = 0;

UPDATE sales s
SET total_price = t.total
FROM (
    SELECT sale_id, sum(unit_price * quantity) AS total
    FROM sale_items
    GROUP BY sale_id
) t
WHERE t.sale_id = s.id AND s.total_price = 0;
