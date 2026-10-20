from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cafe_pos.config import settings
from cafe_pos.database import get_db
from cafe_pos.models.product import Product
from cafe_pos.models.user import User, UserRole
from cafe_pos.schemas.product import ProductCreate, ProductRead
from cafe_pos.services.auth_service import get_current_user, require_role
from cafe_pos.services.cache import cache_delete, cached

router = APIRouter(prefix="/products", tags=["products"])

PRODUCTS_CACHE_KEY = "products:list"


@router.get("/", response_model=list[ProductRead])
def list_products(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return cached(
        PRODUCTS_CACHE_KEY,
        settings.cache_ttl_seconds,
        lambda: [
            ProductRead.model_validate(p).model_dump() for p in db.query(Product).all()
        ],
    )


@router.post("/", response_model=ProductRead, status_code=201)
def create_product(
    product_in: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    product = Product(**product_in.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    cache_delete(PRODUCTS_CACHE_KEY)
    return product


@router.put("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    product_in: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    product.name = product_in.name
    product.category = product_in.category
    product.price = product_in.price
    db.commit()
    db.refresh(product)
    cache_delete(PRODUCTS_CACHE_KEY)
    return product


@router.delete("/{product_id}", status_code=204)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    try:
        db.delete(product)
        db.commit()
        cache_delete(PRODUCTS_CACHE_KEY)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="Не можна видалити — товар уже має продажі"
        )
