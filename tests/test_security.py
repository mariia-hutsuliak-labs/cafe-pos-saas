import logging

from fastapi.testclient import TestClient

from cafe_pos.main import app

SECRET_TEXT = "super-secret-internal-detail"


def crash():
    raise RuntimeError(SECRET_TEXT)


app.add_api_route("/_test/crash", crash)


def test_500_hides_stacktrace_from_user(caplog):
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="cafe_pos"):
        response = client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json() == {"detail": "Внутрішня помилка сервера"}
    assert "Traceback" not in response.text
    assert SECRET_TEXT not in response.text
    assert SECRET_TEXT in caplog.text


def test_docs_are_disabled_when_debug_is_false(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_health_db_error_does_not_leak_details(client, monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError(SECRET_TEXT)

    monkeypatch.setattr("cafe_pos.main.engine", BrokenEngine())

    response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"database": "error"}
    assert SECRET_TEXT not in response.text
