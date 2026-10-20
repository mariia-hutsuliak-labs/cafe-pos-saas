from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cafe_pos.config import settings
from cafe_pos.database import get_db
from cafe_pos.models.cafe import Cafe
from cafe_pos.models.user import User, UserRole
from cafe_pos.services.auth_service import get_current_user, require_role
from cafe_pos.services.cache import cache_delete, cached

router = APIRouter(prefix="/cafes", tags=["cafes"])

CAFES_CACHE_KEY = "cafes:list"


class CafeCreate(BaseModel):
    name: str
    address: str
    timezone: str = "Europe/Kyiv"


def cafe_dict(c: Cafe):
    return {"id": c.id, "name": c.name, "address": c.address, "timezone": c.timezone}


@router.get("/")
def list_cafes(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return cached(
        CAFES_CACHE_KEY,
        settings.cache_ttl_seconds,
        lambda: [cafe_dict(c) for c in db.query(Cafe).all()],
    )


@router.post("/", status_code=201)
def create_cafe(
    cafe_in: CafeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    cafe = Cafe(**cafe_in.model_dump())
    db.add(cafe)
    db.commit()
    db.refresh(cafe)
    cache_delete(CAFES_CACHE_KEY)
    return cafe_dict(cafe)


@router.put("/{cafe_id}")
def update_cafe(
    cafe_id: int,
    cafe_in: CafeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    cafe = db.get(Cafe, cafe_id)
    if not cafe:
        raise HTTPException(status_code=404, detail="Кав'ярню не знайдено")
    cafe.name = cafe_in.name
    cafe.address = cafe_in.address
    cafe.timezone = cafe_in.timezone
    db.commit()
    db.refresh(cafe)
    cache_delete(CAFES_CACHE_KEY)
    return cafe_dict(cafe)


@router.delete("/{cafe_id}", status_code=204)
def delete_cafe(
    cafe_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    cafe = db.get(Cafe, cafe_id)
    if not cafe:
        raise HTTPException(status_code=404, detail="Кав'ярню не знайдено")
    try:
        db.delete(cafe)
        db.commit()
        cache_delete(CAFES_CACHE_KEY)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="Не можна видалити — у кав'ярні є історія продажів"
        )
