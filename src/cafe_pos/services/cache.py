"""Кеш результатів у Redis з обмеженням часу життя (TTL).

Якщо REDIS_URL не задано або Redis недоступний, застосунок працює без кешу:
кожен запит іде в базу, як раніше. Кеш ніколи не повинен ламати відповідь.
"""

import json
import logging
import time
from typing import Any, Callable, Optional

import redis

from cafe_pos.config import settings
from cafe_pos.services.metrics import CACHE_LOOKUPS

logger = logging.getLogger("cafe_pos")

# Після збою Redis не опитуємо ще кілька секунд, щоб запити не чекали таймаут.
RETRY_AFTER_SECONDS = 5.0

_client: Optional[Any] = None
_disabled_until = 0.0


def get_client() -> Optional[Any]:
    global _client
    if _client is not None:
        return _client
    if not settings.redis_url:
        return None
    _client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
        max_connections=50,
    )
    return _client


def _usable_client() -> Optional[Any]:
    if time.monotonic() < _disabled_until:
        return None
    return get_client()


def _mark_failed(action: str, exc: Exception) -> None:
    global _disabled_until
    _disabled_until = time.monotonic() + RETRY_AFTER_SECONDS
    CACHE_LOOKUPS.labels("error").inc()
    logger.warning("Redis недоступний (%s): %s", action, exc)


def cache_get(key: str) -> Optional[Any]:
    client = _usable_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:
        _mark_failed("get", exc)
        return None
    return None if raw is None else json.loads(raw)


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    client = _usable_client()
    if client is None:
        return
    try:
        client.setex(key, ttl_seconds, json.dumps(value))
    except Exception as exc:
        _mark_failed("set", exc)


def cache_delete(*keys: str) -> None:
    client = _usable_client()
    if client is None:
        return
    try:
        client.delete(*keys)
    except Exception as exc:
        _mark_failed("delete", exc)


def cached(key: str, ttl_seconds: int, producer: Callable[[], Any]) -> Any:
    """Повертає значення з кешу або обчислює його через producer і кладе в кеш."""
    hit = cache_get(key)
    if hit is not None:
        CACHE_LOOKUPS.labels("hit").inc()
        return hit
    CACHE_LOOKUPS.labels("miss").inc()
    value = producer()
    cache_set(key, value, ttl_seconds)
    return value
