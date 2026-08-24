"""HTTP katmanı testleri."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BASE_COUPON = {
    "couponAmount": "100.00",
    "selections": [
        {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.00"},
        {"matchId": 902, "oddTypeId": 1, "outcome": "2", "odds": "3.00"},
    ],
}


def post(payload: dict) -> dict:
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Servis uçları
# --------------------------------------------------------------------------- #


def test_health():
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_root_points_to_docs():
    assert client.get("/").json()["docs"] == "/docs"


def test_openapi_schema_is_generated():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/coupons/max-gain" in schema["paths"]
    assert "/api/v1/odd-types" in schema["paths"]


def test_odd_type_catalog_is_exposed():
    rows = client.get("/api/v1/odd-types").json()
    by_id = {row["oddTypeId"]: row for row in rows}
    assert by_id[1]["name"] == "Maç Sonucu (1X2)"
    assert by_id[2]["name"] == "Çift Şans"
    assert "1X" in by_id[2]["exampleOutcomes"]


# --------------------------------------------------------------------------- #
# Maç ağırlığı kuralları uçtan uca
# --------------------------------------------------------------------------- #


def test_compatible_selections_are_summed():
    """1X2 '1' + Çift Şans '1X': ev sahibi kazanırsa ikisi de tutar."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": 2, "outcome": "1X", "odds": "1.30"},
            ],
        }
    )
    match = body["matches"][0]
    assert match["weight"] == "3.40"
    assert match["groups"][0]["combined"] is True
    assert match["groups"][0]["scoreline"]["fullTime"] == "1-0"


def test_contradictory_selections_take_the_maximum():
    """1X2 '1' + Çift Şans 'X2': birlikte tutamazlar."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": 2, "outcome": "X2", "odds": "1.45"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "2.10"
    assert body["matches"][0]["groups"][0]["combined"] is False


def test_same_odd_type_twice_takes_the_maximum():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": 1, "outcome": "X", "odds": "3.40"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "3.40"


def test_unknown_odd_type_is_accepted_with_warning():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.00"},
                {"matchId": 901, "oddTypeId": 4242, "outcome": "Üst", "odds": "1.60"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "3.60"  # farklı grup -> toplandı
    assert any("katalogda yok" in w for w in body["warnings"])


# --------------------------------------------------------------------------- #
# Kupon toplamı
# --------------------------------------------------------------------------- #


def test_simple_parlay():
    body = post(BASE_COUPON)
    assert body["maxGain"] == "600.00"
    assert body["stake"]["lineCount"] == 1
    assert body["currency"] == "TRY"


def test_match_id_type_is_preserved():
    assert post(BASE_COUPON)["matches"][0]["matchId"] == 901
    string_ids = {
        "couponAmount": "10.00",
        "selections": [{"matchId": "abc-1", "oddTypeId": 1, "outcome": "1", "odds": "2.00"}],
    }
    assert post(string_ids)["matches"][0]["matchId"] == "abc-1"


def test_multiway_with_system_and_banker():
    body = post(
        {
            "couponAmount": "10.00",
            "stakeMode": "per_line",
            "currency": "eur",
            "system": {"sizes": [2]},
            "bankerMatchIds": [900],
            "selections": [
                {"matchId": 900, "oddTypeId": 1, "outcome": "1", "odds": "1.50"},
                {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.00"},
                {"matchId": 901, "oddTypeId": 2, "outcome": "1X", "odds": "1.30"},
                {"matchId": 902, "oddTypeId": 1, "outcome": "2", "odds": "4.00"},
                {"matchId": 903, "oddTypeId": 1, "outcome": "1", "odds": "1.80"},
            ],
        }
    )
    # 2/3 sistem; 901'de 2 seçim -> e_2([2,1,1]) = 5 satır, hepsinde banko var
    assert body["stake"]["lineCount"] == 5
    assert body["stake"]["total"] == "50.00"
    assert body["currency"] == "EUR"

    weights = {m["matchId"]: m["weight"] for m in body["matches"]}
    assert weights[901] == "3.30"  # 2.00 + 1.30 (uyumlu)
    # 10 * 1.50 * (3.30*4.00 + 3.30*1.80 + 4.00*1.80) = 10 * 1.50 * 26.34
    assert body["maxGain"] == "395.10"


def test_snake_case_payload_is_also_accepted():
    body = post(
        {
            "coupon_amount": "100.00",
            "stake_mode": "total",
            "selections": [
                {"match_id": 901, "odd_type_id": 1, "outcome": "1", "odds": "2.00"},
            ],
        }
    )
    assert body["maxGain"] == "200.00"


def test_batch_endpoint():
    response = client.post("/api/v1/coupons/max-gain/batch", json=[BASE_COUPON, BASE_COUPON])
    assert response.status_code == 200
    assert [c["maxGain"] for c in response.json()] == ["600.00", "600.00"]


def test_batch_rejects_empty_list():
    assert client.post("/api/v1/coupons/max-gain/batch", json=[]).status_code == 422


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutation",
    [
        {"selections": []},
        {"couponAmount": "0"},
        {"couponAmount": "-5"},
        {"currency": "TURKISH"},
        {"system": {"sizes": [9]}},
        {"bankerMatchIds": [12345]},
        {"selections": [{"matchId": 1, "oddTypeId": 1, "outcome": "1", "odds": "0.90"}]},
        {"selections": [{"matchId": 1, "oddTypeId": 1, "odds": "2.00"}]},
        {"unknownField": 1},
    ],
)
def test_invalid_payloads_return_422(mutation):
    assert (
        client.post("/api/v1/coupons/max-gain", json={**BASE_COUPON, **mutation}).status_code == 422
    )


def test_duplicate_selection_returns_422():
    payload = {
        "couponAmount": "100.00",
        "selections": [
            {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "2.00"},
            {"matchId": 901, "oddTypeId": 1, "outcome": "1", "odds": "3.00"},
        ],
    }
    assert client.post("/api/v1/coupons/max-gain", json=payload).status_code == 422


def test_large_system_completes_quickly():
    """20 bacaklı 3/20 sistem: kombinatoryal patlamaya rağmen anında dönmeli."""
    body = post(
        {
            "couponAmount": "100.00",
            "system": {"sizes": [3]},
            "selections": [
                {"matchId": i, "oddTypeId": 1, "outcome": "1", "odds": "2.00"} for i in range(20)
            ],
        }
    )
    assert body["stake"]["lineCount"] == 1140  # C(20,3)
    assert body["maxGain"] == "800.00"
