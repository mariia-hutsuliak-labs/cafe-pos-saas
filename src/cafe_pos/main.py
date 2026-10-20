import logging
import socket
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from cafe_pos import models  # noqa: F401
from cafe_pos.api import analytics, auth, cafes, products, sales, users
from cafe_pos.config import settings
from cafe_pos.database import Base, create_missing_indexes, engine
from cafe_pos.services.metrics import (
    MetricsMiddleware,
    metrics_response,
    register_pool_metrics,
)

logger = logging.getLogger("cafe_pos")

# У Docker hostname це ID контейнера, тож за ним видно, яка репліка відповіла.
INSTANCE_ID = socket.gethostname()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

app.add_middleware(MetricsMiddleware)
register_pool_metrics(engine)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(products.router)
app.include_router(analytics.router)
app.include_router(cafes.router)
app.include_router(sales.router)

app.mount(
    "/app",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)


@app.middleware("http")
async def add_instance_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Served-By"] = INSTANCE_ID
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Повний стектрейс іде тільки в лог сервера.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    # Користувач бачить коротке повідомлення без внутрішніх деталей.
    # Коли DEBUG=true, Starlette сам покаже детальну сторінку помилки.
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутрішня помилка сервера"},
    )


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    create_missing_indexes()


@app.get("/")
def read_root():
    return {
        "message": settings.app_name,
        "status": "running",
        "instance": INSTANCE_ID,
    }


@app.get("/metrics", include_in_schema=False)
def metrics():
    # Для Prometheus усередині мережі Docker. Nginx назовні цей шлях не віддає.
    return metrics_response()


@app.get("/health/db")
def check_db_connection():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"database": "connected"}
    except Exception:
        logger.exception("Database health check failed")
        return JSONResponse(status_code=503, content={"database": "error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("cafe_pos.main:app", host="127.0.0.1", port=8000, reload=settings.debug)
