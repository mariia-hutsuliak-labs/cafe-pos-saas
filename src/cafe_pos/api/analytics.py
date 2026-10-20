from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from cafe_pos.config import settings
from cafe_pos.database import get_db
from cafe_pos.models.cafe import Cafe
from cafe_pos.models.product import Product
from cafe_pos.models.sale import Sale, SaleItem
from cafe_pos.models.user import User, UserRole
from cafe_pos.services.auth_service import require_role
from cafe_pos.services.cache import cached

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    return cached(
        "analytics:summary",
        settings.cache_ttl_summary_seconds,
        lambda: build_summary(db),
    )


def build_summary(db: Session):
    today = datetime.now(timezone.utc).date()
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    # Кількість і суму рахує база за діапазоном created_at, тож працює індекс,
    # а у Python не завантажуються тисячі чеків.
    today_orders, today_revenue = (
        db.query(func.count(Sale.id), func.coalesce(func.sum(Sale.total_price), 0))
        .filter(Sale.created_at >= day_start, Sale.created_at < day_end)
        .one()
    )
    avg_check = today_revenue / today_orders if today_orders else 0
    return {
        "today_revenue": round(today_revenue, 2),
        "today_orders": today_orders,
        "avg_check": round(avg_check, 2),
        "total_products": db.query(Product).count(),
        "total_cafes": db.query(Cafe).count(),
    }


@router.get("/revenue-by-day")
def revenue_by_day(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    return cached(
        f"analytics:revenue-by-day:{days}",
        settings.cache_ttl_seconds,
        lambda: build_revenue_by_day(db, days),
    )


def build_revenue_by_day(db: Session, days: int):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            func.date(Sale.created_at).label("day"),
            func.sum(Sale.total_price).label("revenue"),
        )
        .filter(Sale.created_at >= since)
        .group_by("day")
        .order_by("day")
        .all()
    )
    return [{"date": str(r.day), "revenue": round(r.revenue or 0, 2)} for r in rows]


@router.get("/top-products")
def top_products(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    return cached(
        f"analytics:top-products:{limit}",
        settings.cache_ttl_seconds,
        lambda: build_top_products(db, limit),
    )


def build_top_products(db: Session, limit: int):
    rows = (
        db.query(
            Product.name,
            func.sum(SaleItem.unit_price * SaleItem.quantity).label("revenue"),
            func.sum(SaleItem.quantity).label("quantity"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.unit_price * SaleItem.quantity).desc())
        .limit(limit)
        .all()
    )
    return [
        {"name": r.name, "revenue": round(r.revenue or 0, 2), "quantity": r.quantity}
        for r in rows
    ]


@router.get("/by-cafe")
def revenue_by_cafe(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    return cached(
        "analytics:by-cafe",
        settings.cache_ttl_seconds,
        lambda: build_revenue_by_cafe(db),
    )


def build_revenue_by_cafe(db: Session):
    rows = (
        db.query(Cafe.name, func.sum(Sale.total_price).label("revenue"))
        .join(Sale, Sale.cafe_id == Cafe.id)
        .group_by(Cafe.name)
        .order_by(func.sum(Sale.total_price).desc())
        .all()
    )
    return [{"name": r.name, "revenue": round(r.revenue or 0, 2)} for r in rows]
