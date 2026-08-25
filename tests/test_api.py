"""HTTP katmanı testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# Gerçek katalogdan (pre-match uzayı)
MS = 1565  # 3way          -> Maç Sonucu (1X2)
CS = 1481  # Double Chance -> Çift Şans
OU = 1500  # Over/Under    -> Alt / Üst

BASE_COUPON = {
    "couponAmount": "100.00",
    "selections": [
        {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.00"},
        {"matchId": 902, "oddTypeId": MS, "outcome": "2", "odds": "3.00"},
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
    page = client.get("/api/v1/odd-types", params={"limit": 1}).json()
    assert page["total"] == 1426  # pre + live, iki katalog birden
    assert len(page["items"]) == 1


def test_odd_type_catalog_filters_by_namespace_and_name():
    pre = client.get("/api/v1/odd-types", params={"isLive": 0, "q": "double chance"}).json()
    assert all(item["isLive"] == 0 for item in pre["items"])
    # Arama devre/çeyrek varyantlarını ve kombine piyasaları da bulur.
    mapped = {i["marketId"] for i in pre["items"] if i["mapped"]}
    assert "CIFT_SANS" in mapped
    assert all("CIFT_SANS" in market or "KOMBINE" in market for market in mapped)

    live = client.get("/api/v1/odd-types", params={"isLive": 1, "q": "double chance"}).json()
    assert all(item["isLive"] == 1 for item in live["items"])
    assert live["total"] != pre["total"]  # ayrı id uzayları


def test_odd_type_catalog_marks_markets_needing_special_bet_value():
    page = client.get("/api/v1/odd-types", params={"q": "over/under", "limit": 500}).json()
    over_under = [i for i in page["items"] if i["mapped"]]
    assert over_under
    assert all(i["needsSpecialBetValue"] for i in over_under)


# --------------------------------------------------------------------------- #
# specialBetValue
# --------------------------------------------------------------------------- #


def test_contradictory_lines_on_same_market_take_the_maximum():
    """Over/Under Alt 0.5 + Üst 2.5: ikisi birden kazanamaz."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": 901,
                    "oddTypeId": OU,
                    "outcome": "Alt",
                    "specialBetValue": "0.5",
                    "odds": "1.90",
                },
                {
                    "matchId": 901,
                    "oddTypeId": OU,
                    "outcome": "Üst",
                    "specialBetValue": "2.5",
                    "odds": "2.40",
                },
            ],
        }
    )
    group = body["matches"][0]["groups"][0]
    assert body["matches"][0]["weight"] == "2.40"
    assert group["combined"] is False
    assert group["winningSelections"][0]["specialBetValue"] == "2.5"


def test_compatible_lines_on_same_market_are_summed():
    """Üst 0.5 + Üst 2.5: toplam 3+ olduğunda ikisi de tutar."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": 901,
                    "oddTypeId": OU,
                    "outcome": "Üst",
                    "specialBetValue": "0.5",
                    "odds": "1.20",
                },
                {
                    "matchId": 901,
                    "oddTypeId": OU,
                    "outcome": "Üst",
                    "specialBetValue": "2.5",
                    "odds": "2.40",
                },
            ],
        }
    )
    assert body["matches"][0]["weight"] == "3.60"
    assert body["matches"][0]["groups"][0]["combined"] is True


def test_live_flag_selects_the_live_namespace():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": 901,
                    "oddTypeId": 24,
                    "isLive": 1,
                    "outcome": "1X",
                    "odds": "1.40",
                    "currentScore": "0:0",
                }
            ],
        }
    )
    winner = body["matches"][0]["groups"][0]["winningSelections"][0]
    assert winner["oddTypeName"] == "Double Chance (ALL)"
    assert winner["isLive"] == 1
    assert body["warnings"] == []


# --------------------------------------------------------------------------- #
# Maç ağırlığı kuralları uçtan uca
# --------------------------------------------------------------------------- #


def test_compatible_selections_are_summed():
    """1X2 '1' + Çift Şans '1X': ev sahibi kazanırsa ikisi de tutar."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": CS, "outcome": "1X", "odds": "1.30"},
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
                {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": CS, "outcome": "X2", "odds": "1.45"},
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
                {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.10"},
                {"matchId": 901, "oddTypeId": MS, "outcome": "X", "odds": "3.40"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "3.40"


def test_unknown_odd_type_is_accepted_with_warning():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.00"},
                {"matchId": 901, "oddTypeId": 99999, "outcome": "Üst", "odds": "1.60"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "3.60"  # farklı grup -> toplandı
    assert any("katalogunda yok" in w for w in body["warnings"])


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
        "selections": [{"matchId": "abc-1", "oddTypeId": MS, "outcome": "1", "odds": "2.00"}],
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
                {"matchId": 900, "oddTypeId": MS, "outcome": "1", "odds": "1.50"},
                {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.00"},
                {"matchId": 901, "oddTypeId": CS, "outcome": "1X", "odds": "1.30"},
                {"matchId": 902, "oddTypeId": MS, "outcome": "2", "odds": "4.00"},
                {"matchId": 903, "oddTypeId": MS, "outcome": "1", "odds": "1.80"},
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
        {"selections": [{"matchId": 1, "oddTypeId": MS, "outcome": "1", "odds": "0.90"}]},
        {"selections": [{"matchId": 1, "oddTypeId": MS, "odds": "2.00"}]},
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
            {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "2.00"},
            {"matchId": 901, "oddTypeId": MS, "outcome": "1", "odds": "3.00"},
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
                {"matchId": i, "oddTypeId": MS, "outcome": "1", "odds": "2.00"} for i in range(20)
            ],
        }
    )
    assert body["stake"]["lineCount"] == 1140  # C(20,3)
    assert body["maxGain"] == "800.00"


# --------------------------------------------------------------------------- #
# oddId ile seçim tanımlama
# --------------------------------------------------------------------------- #

# Outcome katalogundan (pre): 1571 = 3way "1", 2307 = Double Chance "1X",
# 2309 = Double Chance "X2", 1541 = Over/Under "Over", 1542 = "Under"
ODD_HOME = 1571
ODD_1X = 2307
ODD_X2 = 2309
ODD_OVER = 1541
ODD_UNDER = 1542


def test_odd_id_fills_in_odd_type_and_outcome():
    """Sadece oddId gönderilse yeter; ad ve piyasa katalogdan çözülür."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddId": ODD_HOME, "odds": "2.10"},
                {"matchId": 901, "oddId": ODD_1X, "odds": "1.30"},
            ],
        }
    )
    group = body["matches"][0]["groups"][0]
    assert body["matches"][0]["weight"] == "3.40"  # uyumlu -> toplandı
    assert group["combined"] is True
    assert {w["oddId"] for w in group["winningSelections"]} == {ODD_HOME, ODD_1X}
    assert {w["outcome"] for w in group["winningSelections"]} == {"1", "1X"}
    assert body["warnings"] == []


def test_odd_id_makes_outcome_language_irrelevant():
    """Çağıran outcome'u başka dilde yollasa da katalog esas alınır."""
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": 901,
                    "oddId": ODD_OVER,
                    "outcome": "Üst",
                    "specialBetValue": "0.5",
                    "odds": "1.90",
                },
                {
                    "matchId": 901,
                    "oddId": ODD_UNDER,
                    "outcome": "Über",
                    "specialBetValue": "2.5",
                    "odds": "2.40",
                },
            ],
        }
    )
    # Üst 0.5 ile Alt 2.5: toplam hem 1+ hem 2- olabilir -> uyumlu
    assert body["matches"][0]["weight"] == "4.30"
    assert [w["outcome"] for w in body["matches"][0]["groups"][0]["winningSelections"]] == [
        "Over",
        "Under",
    ]


def test_odd_id_contradiction_is_detected():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddId": ODD_HOME, "odds": "2.10"},
                {"matchId": 901, "oddId": ODD_X2, "odds": "1.45"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "2.10"


def test_mismatched_odd_type_id_is_overridden_with_a_warning():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddId": ODD_HOME, "oddTypeId": 9999, "odds": "2.10"},
            ],
        }
    )
    assert body["matches"][0]["groups"][0]["winningSelections"][0]["oddTypeId"] == 1565
    assert any("katalog esas alındı" in w for w in body["warnings"])


def test_unknown_odd_id_falls_back_with_a_warning():
    body = post(
        {
            "couponAmount": "100.00",
            "selections": [
                {"matchId": 901, "oddId": 999999, "oddTypeId": MS, "outcome": "1", "odds": "2.10"},
            ],
        }
    )
    assert body["matches"][0]["weight"] == "2.10"
    assert any("outcome katalogunda yok" in w for w in body["warnings"])


def test_selection_without_odd_id_requires_odd_type_and_outcome():
    payload = {
        "couponAmount": "100.00",
        "selections": [{"matchId": 901, "odds": "2.10"}],
    }
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 422
    assert "oddId verilmediğinde" in response.text


def test_every_swagger_example_is_a_valid_coupon():
    """Dökümandaki hazır gövdeler çalışır durumda kalmalı.

    Swagger'daki örnekler elle yazıldığı için şema değiştiğinde sessizce
    bozulabilir; burada hepsi gerçekten hesaplatılır.
    """
    schema = client.get("/openapi.json").json()
    examples = schema["paths"]["/api/v1/coupons/max-gain"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert set(examples) == {"multiway", "canli", "sistem-banko"}

    for name, example in examples.items():
        response = client.post("/api/v1/coupons/max-gain", json=example["value"])
        assert response.status_code == 200, f"{name}: {response.text}"
        body = response.json()
        assert body["warnings"] == [], f"{name} uyarı üretti: {body['warnings']}"
        assert Decimal(body["maxGain"]) > 0


def test_live_example_carries_the_current_score():
    """Canlı örnek, skorun nereye yazıldığını göstermeli."""
    schema = client.get("/openapi.json").json()
    live = schema["paths"]["/api/v1/coupons/max-gain"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]["canli"]["value"]

    assert all(selection["isLive"] == 1 for selection in live["selections"])
    assert all("currentScore" in selection for selection in live["selections"])
    assert {s["currentScore"] for s in live["selections"]} == {"0:0", "0:2"}
