from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from cafe_pos import database
from cafe_pos.database import Base, get_db
from cafe_pos.models.cafe import Cafe
from cafe_pos.models.product import Product
from cafe_pos.models.sale import Sale, SaleItem
from tests.test_auth import make_admin


def seed_catalog(client):
    db = next(client.app.dependency_overrides[get_db]())
    cafe = Cafe(name="Тест", address="вул. Тестова, 1")
    coffee = Product(name="Лате", category="Напій", price=70)
    cake = Product(name="Чізкейк", category="Десерт", price=85)
    db.add_all([cafe, coffee, cake])
    db.commit()
    ids = (cafe.id, coffee.id, cake.id)
    db.close()
    return ids


def test_summary_counts_only_today(client):
    headers = make_admin(client, "admin@test.com")
    cafe_id, coffee_id, cake_id = seed_catalog(client)

    # Два чеки сьогодні: 2 * 70 = 140 і 1 * 85 = 85.
    for items in (
        [{"product_id": coffee_id, "quantity": 2}],
        [{"product_id": cake_id, "quantity": 1}],
    ):
        resp = client.post(
            "/sales/", json={"cafe_id": cafe_id, "items": items}, headers=headers
        )
        assert resp.status_code == 201

    # Чек трьома днями раніше не має потрапити в підсумок за сьогодні.
    db = next(client.app.dependency_overrides[get_db]())
    db.add(
        Sale(
            cafe_id=cafe_id,
            total_price=500,
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
    )
    db.commit()
    db.close()

    data = client.get("/analytics/summary", headers=headers).json()
    assert data["today_orders"] == 2
    assert data["today_revenue"] == 225
    assert data["avg_check"] == 112.5
    assert data["total_products"] == 2
    assert data["total_cafes"] == 1


def test_summary_without_sales(client):
    headers = make_admin(client, "admin@test.com")
    data = client.get("/analytics/summary", headers=headers).json()
    assert data["today_orders"] == 0
    assert data["today_revenue"] == 0
    assert data["avg_check"] == 0


def test_create_sale_total_and_items(client):
    headers = make_admin(client, "admin@test.com")
    cafe_id, coffee_id, cake_id = seed_catalog(client)

    resp = client.post(
        "/sales/",
        json={
            "cafe_id": cafe_id,
            "items": [
                {"product_id": coffee_id, "quantity": 2},
                {"product_id": cake_id, "quantity": 1},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["total_price"] == 225

    last = client.get("/sales/?limit=1", headers=headers).json()[0]
    assert len(last["items"]) == 2


def test_indexes_declared_on_hot_columns():
    sale_indexes = {i.name for i in Sale.__table__.indexes}
    item_indexes = {i.name for i in SaleItem.__table__.indexes}
    assert "ix_sales_created_at" in sale_indexes
    assert "ix_sales_cafe_id" in sale_indexes
    assert "ix_sale_items_sale_id" in item_indexes
    assert "ix_sale_items_product_id" in item_indexes


def test_create_missing_indexes_is_idempotent():
    Base.metadata.create_all(bind=database.engine)
    database.create_missing_indexes()
    database.create_missing_indexes()
    names = {i["name"] for i in inspect(database.engine).get_indexes("sales")}
    assert "ix_sales_created_at" in names


def test_create_sale_skips_unknown_products(client):
    headers = make_admin(client, "admin@test.com")
    cafe_id, coffee_id, _ = seed_catalog(client)

    resp = client.post(
        "/sales/",
        json={
            "cafe_id": cafe_id,
            "items": [
                {"product_id": coffee_id, "quantity": 1},
                {"product_id": 9999, "quantity": 5},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["total_price"] == 70

    last = client.get("/sales/?limit=1", headers=headers).json()[0]
    assert [i["product"] for i in last["items"]] == ["Лате"]


def test_create_sale_same_product_twice(client):
    headers = make_admin(client, "admin@test.com")
    cafe_id, coffee_id, _ = seed_catalog(client)

    resp = client.post(
        "/sales/",
        json={
            "cafe_id": cafe_id,
            "items": [
                {"product_id": coffee_id, "quantity": 1},
                {"product_id": coffee_id, "quantity": 2},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["total_price"] == 210


def test_login_works_after_releasing_connection(client):
    client.post(
        "/auth/register", json={"email": "u@test.com", "password": "Password123"}
    )
    bad = client.post(
        "/auth/login", data={"username": "u@test.com", "password": "wrong"}
    )
    missing = client.post(
        "/auth/login", data={"username": "no@test.com", "password": "Password123"}
    )
    ok = client.post(
        "/auth/login", data={"username": "u@test.com", "password": "Password123"}
    )
    assert bad.status_code == 401
    assert missing.status_code == 401
    assert ok.status_code == 200 and "access_token" in ok.json()


def test_pool_size_comes_from_settings():
    from cafe_pos.config import settings

    pg_engine = database.make_engine("postgresql://u:p@localhost/db")
    assert pg_engine.pool.size() == settings.db_pool_size
    assert pg_engine.pool.timeout() == settings.db_pool_timeout
