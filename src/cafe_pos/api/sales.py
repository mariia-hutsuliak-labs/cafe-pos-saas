from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from cafe_pos.database import get_db
from cafe_pos.models.product import Product
from cafe_pos.models.sale import Sale, SaleItem
from cafe_pos.models.user import User, UserRole
from cafe_pos.services.auth_service import require_role

router = APIRouter(prefix="/sales", tags=["sales"])


class SaleItemIn(BaseModel):
    product_id: int
    quantity: int = 1


class SaleCreate(BaseModel):
    cafe_id: int
    items: list[SaleItemIn]


@router.get("/")
def list_sales(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    sales = (
        db.query(Sale)
        .options(
            joinedload(Sale.cafe), joinedload(Sale.items).joinedload(SaleItem.product)
        )
        .order_by(Sale.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": s.id,
            "cafe": s.cafe.name if s.cafe else "—",
            "total_price": s.total_price,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "items": [
                {
                    "product": i.product.name,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                }
                for i in s.items
            ],
        }
        for s in sales
    ]


@router.post("/", status_code=201)
def create_sale(
    sale_in: SaleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    # Усі товари чека одним запитом (раніше був окремий SELECT на кожну позицію).
    product_ids = {item.product_id for item in sale_in.items}
    products = {
        p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }

    total = 0.0
    items = []
    for item in sale_in.items:
        product = products.get(item.product_id)
        if not product:
            continue
        total += product.price * item.quantity
        items.append(
            SaleItem(
                product_id=product.id,
                quantity=item.quantity,
                unit_price=product.price,
            )
        )

    # Чек і позиції зберігаються разом, без проміжного flush, UPDATE і refresh.
    sale = Sale(cafe_id=sale_in.cafe_id, total_price=round(total, 2), items=items)
    db.add(sale)
    db.flush()
    result = {"id": sale.id, "total_price": sale.total_price}
    db.commit()
    return result
