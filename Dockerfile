# syntax=docker/dockerfile:1

# ---------- Етап 1: збираємо залежності ----------
FROM python:3.9-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Віртуальне середовище, яке потім повністю копіюється у фінальний образ.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Спочатку тільки requirements.txt: шар із залежностями кешується,
# поки файл не змінився.
COPY requirements.txt .
RUN pip install -r requirements.txt


# ---------- Етап 2: фінальний образ ----------
FROM python:3.9-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/opt/venv/bin:$PATH"

# Запускаємо застосунок не від root.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY src ./src

USER appuser

EXPOSE 8000

CMD ["uvicorn", "cafe_pos.main:app", "--host", "0.0.0.0", "--port", "8000"]
