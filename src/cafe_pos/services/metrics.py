"""Метрики Prometheus для моніторингу під час тривалого тесту (лаба 8).

Що вимірюємо:
  - кількість запитів і їхні статуси (з цього рахується RPS і відсоток помилок);
  - час відповіді (гістограма, з неї рахуються p50/p95/p99);
  - скільки запитів зараз у роботі;
  - скільки підключень до бази зараз зайнято (ріст без повернення = витік підключень);
  - влучання і промахи кешу Redis.

Крім цих метрик, бібліотека сама віддає process_resident_memory_bytes (RAM процесу),
process_open_fds (відкриті файли і сокети) та process_cpu_seconds_total.
Якщо RAM або відкриті файли ростуть увесь тест без повернення, це ознака витоку.

Мітка path це шаблон маршруту (/sales/{sale_id}), а не справжній URL,
щоб кількість часових рядів не росла разом з кількістю чеків.
"""

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

REQUESTS = Counter(
    "cafe_http_requests_total",
    "Кількість HTTP-запитів",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "cafe_http_request_duration_seconds",
    "Час обробки HTTP-запиту, секунди",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
IN_PROGRESS = Gauge(
    "cafe_http_requests_in_progress",
    "Запити, які обробляються просто зараз",
)
CACHE_LOOKUPS = Counter(
    "cafe_cache_lookups_total",
    "Звернення до кешу Redis: hit, miss або error",
    ["result"],
)
DB_POOL_CHECKED_OUT = Gauge(
    "cafe_db_pool_checked_out",
    "Підключення до бази, які зараз зайняті запитами",
)
DB_POOL_SIZE = Gauge(
    "cafe_db_pool_size",
    "Підключення до бази, відкриті в пулі цієї репліки",
)

# Шлях /metrics не рахуємо, інакше сам Prometheus накручує статистику.
SKIP_PATHS = {"/metrics"}


class MetricsMiddleware:
    """Чистий ASGI-middleware: легший за @app.middleware("http")."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        status = {"code": 500}  # якщо виняток, відповідь буде 500

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        started = time.perf_counter()
        IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            IN_PROGRESS.dec()
            route = scope.get("route")
            path = getattr(route, "path", None) or "other"
            method = scope["method"]
            REQUESTS.labels(method, path, str(status["code"])).inc()
            LATENCY.labels(method, path).observe(time.perf_counter() - started)


def register_pool_metrics(engine) -> None:
    """Підключає лічильники пулу SQLAlchemy. Для SQLite (тести) віддає нулі."""

    def checked_out():
        return getattr(engine.pool, "checkedout", lambda: 0)()

    def size():
        pool = engine.pool
        return pool.checkedout() + pool.checkedin() if hasattr(pool, "checkedin") else 0

    DB_POOL_CHECKED_OUT.set_function(checked_out)
    DB_POOL_SIZE.set_function(size)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
