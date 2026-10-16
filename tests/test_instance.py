from cafe_pos.main import INSTANCE_ID


def test_root_returns_instance_id(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["instance"] == INSTANCE_ID


def test_every_response_has_served_by_header(client):
    for path in ("/", "/health/db"):
        response = client.get(path)
        assert response.headers["X-Served-By"] == INSTANCE_ID


def test_served_by_header_is_set_on_error_responses(client):
    response = client.get("/admin/home")
    assert response.status_code in (401, 403)
    assert response.headers["X-Served-By"] == INSTANCE_ID
