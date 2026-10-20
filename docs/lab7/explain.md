### EXPLAIN ANALYZE: час виконання запиту, мс

| Запит | Без індексів | З індексами |
|---|---|---|
| Q0. Старий /analytics/summary: date(created_at) = сьогодні, всі рядки у Python | 7,47 | 7,00 |
| Q1. Новий /analytics/summary: count і sum за діапазоном created_at | 7,24 | 1,31 |
| Q2. /analytics/revenue-by-day (14 днів) | 12,96 | 6,44 |
| Q3. /sales/?limit=20: останні чеки | 5,77 | 0,01 |
| Q4. Позиції останніх чеків (joinedload у /sales/) | 18,89 | 0,05 |
| Q5. /analytics/by-cafe: сума по всіх чеках | 20,01 | 18,51 |
| Q6. /analytics/top-products: сума по всіх позиціях | 22,43 | 22,93 |
