def test_metrics_endpoint_exposes_request_counters(client):
    client.get("/")
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert 'cafe_http_requests_total{method="GET",path="/",status="200"}' in body
    assert "cafe_http_request_duration_seconds_bucket" in body
    assert "cafe_db_pool_checked_out" in body


def test_metrics_do_not_count_themselves(client):
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'path="/metrics"' not in body


def test_unauthorized_requests_are_counted_with_route_template(client):
    client.get("/admin/home")
    body = client.get("/metrics").text
    assert 'path="/admin/home"' in body
