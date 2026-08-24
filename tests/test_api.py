"""HTTP katmanı testleri."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BASE_COUPON = {
    "stake": "100.00",
    "events": [
        {"id": "m1", "name": "GS - FB", "selections": [{"id": "1", "odds": "2.00"}]},
        {"id": "m2", "selections": [{"id": "2", "odds": "3.00"}]},
    ],
}


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_points_to_docs():
    assert client.get("/").json()["docs"] == "/docs"


def test_openapi_schema_is_generated():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/coupons/max-gain" in schema["paths"]


def test_simple_parlay():
    response = client.post("/api/v1/coupons/max-gain", json=BASE_COUPON)
    assert response.status_code == 200
    body = response.json()
    assert body["max_gain"] == "600.00"
    assert body["stake"]["line_count"] == 1
    assert body["currency"] == "TRY"
    assert body["capped"] is False


def test_multiway_with_system_and_banker():
    payload = {
        "stake": "10.00",
        "stake_mode": "per_line",
        "currency": "eur",
        "system": {"sizes": [2]},
        "events": [
            {"id": "b1", "banker": True, "selections": [{"id": "1", "odds": "1.50"}]},
            {
                "id": "m1",
                "selections": [{"id": "1", "odds": "2.00"}, {"id": "X", "odds": "3.50"}],
            },
            {"id": "m2", "selections": [{"id": "2", "odds": "4.00"}]},
            {"id": "m3", "selections": [{"id": "1", "odds": "1.80"}]},
        ],
    }
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 200
    body = response.json()

    # 2/3 sistem, m1'de 2 seçim -> C(3,2)=3 alt küme; m1 içerenler 2'şer satır
    assert body["stake"]["line_count"] == 5
    assert body["currency"] == "EUR"
    picked = {p["event_id"]: p["selection_id"] for p in body["best_scenario"]}
    assert picked["m1"] == "X"  # 3.50 > 2.00
    # 15 * (3.5*4 + 3.5*1.8 + 4*1.8) = 15 * (14 + 6.3 + 7.2) = 412.50
    assert body["max_gain"] == "412.50"
    assert body["stake"]["total"] == "50.00"


def test_batch_endpoint():
    response = client.post("/api/v1/coupons/max-gain/batch", json=[BASE_COUPON, BASE_COUPON])
    assert response.status_code == 200
    assert [c["max_gain"] for c in response.json()] == ["600.00", "600.00"]


def test_batch_rejects_empty_list():
    assert client.post("/api/v1/coupons/max-gain/batch", json=[]).status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        {"events": []},
        {"stake": "0"},
        {"stake": "-5"},
        {"currency": "TURKISH"},
        {"system": {"sizes": [9]}},
        {"events": [{"id": "m1", "selections": [{"id": "1", "odds": "0.90"}]}]},
        {"events": [{"id": "m1", "selections": []}]},
        {"unknown_field": 1},
    ],
)
def test_invalid_payloads_return_422(mutation):
    payload = {**BASE_COUPON, **mutation}
    assert client.post("/api/v1/coupons/max-gain", json=payload).status_code == 422


def test_duplicate_event_ids_return_422():
    payload = {
        "stake": "100.00",
        "events": [
            {"id": "same", "selections": [{"id": "1", "odds": "2.00"}]},
            {"id": "same", "selections": [{"id": "1", "odds": "3.00"}]},
        ],
    }
    assert client.post("/api/v1/coupons/max-gain", json=payload).status_code == 422


def test_large_system_completes_quickly():
    """20 bacaklı 3/20 sistem: kombinatoryal patlamaya rağmen anında dönmeli."""
    payload = {
        "stake": "100.00",
        "system": {"sizes": [3]},
        "events": [{"id": f"m{i}", "selections": [{"id": "1", "odds": "2.00"}]} for i in range(20)],
    }
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["stake"]["line_count"] == 1140  # C(20,3)
    # her satır 2^3 = 8 kat öder -> 100 * 8 = 800
    assert body["max_gain"] == "800.00"
