"""Sağlayıcıdan gelen gerçek bir kuponla uçtan uca doğrulama.

Kupon, sağlayıcının kendi gövde biçiminde saklanır ve teste girerken API
şemasına çevrilir. Böylece hem alan eşlemesi hem hesap birlikte sabitlenir.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# Sağlayıcı gövdesi: outcome adları Almanca ("J" = Ja), SpecialBetValue boş,
# OddId1 alanı outcome katalogundaki oddId değil (MatchId + kendi kodu).
PROVIDER_COUPON = [
    {
        "MatchId": 71960094,
        "EventName": "Hallescher FC-FC Schalke 04",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "2",
        "SpecialBetValue": None,
        "OddValue1": 1.3,
    },
    {
        "MatchId": 71960094,
        "EventName": "Hallescher FC-FC Schalke 04",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "x",
        "SpecialBetValue": None,
        "OddValue1": 6,
    },
    {
        "MatchId": 68932806,
        "EventName": "Andorra-Malta",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "2",
        "SpecialBetValue": None,
        "OddValue1": 2.5,
    },
    {
        "MatchId": 68931512,
        "EventName": "Serbien-Griechenland",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "1",
        "SpecialBetValue": None,
        "OddValue1": 2.05,
    },
    {
        "MatchId": 68931508,
        "EventName": "Niederlande-Deutschland",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "1",
        "SpecialBetValue": None,
        "OddValue1": 2.45,
    },
    {
        "MatchId": 68932720,
        "EventName": "Österreich-Israel",
        "Banko": False,
        "OddsTypeId": 1467,
        "OutCome": "J",
        "SpecialBetValue": None,
        "OddValue1": 1.85,
    },
    {
        "MatchId": 68932720,
        "EventName": "Österreich-Israel",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "1",
        "SpecialBetValue": None,
        "OddValue1": 1.33,
    },
    {
        "MatchId": 68931762,
        "EventName": "Norwegen-Dänemark",
        "Banko": False,
        "OddsTypeId": 1481,
        "OutCome": "1X",
        "SpecialBetValue": None,
        "OddValue1": 1.35,
    },
    {
        "MatchId": 68931762,
        "EventName": "Norwegen-Dänemark",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "2",
        "SpecialBetValue": None,
        "OddValue1": 3,
    },
]


def to_request(rows: list[dict], coupon_amount: str) -> dict:
    """Sağlayıcı gövdesini API şemasına çevirir."""
    payload: dict = {
        "couponAmount": coupon_amount,
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": 0,
                "oddTypeId": row["OddsTypeId"],
                "outcome": row["OutCome"],
                "specialBetValue": row["SpecialBetValue"],
                "odds": str(row["OddValue1"]),
            }
            for row in rows
        ],
    }
    bankers = sorted({row["MatchId"] for row in rows if row["Banko"]})
    if bankers:
        payload["bankerMatchIds"] = bankers
    return payload


@pytest.fixture(scope="module")
def result() -> dict:
    response = client.post("/api/v1/coupons/max-gain", json=to_request(PROVIDER_COUPON, "100.00"))
    assert response.status_code == 200, response.text
    return response.json()


def test_all_odd_types_in_the_coupon_are_mapped(result):
    """1839 (3 Way), 1481 (Double Chance), 1467 (Both Teams to Score).

    Uyarı listesinin boş olması üçünün de anlamsal olarak çözümlendiğini
    gösterir: eşlenmemiş bir id geri düşüşe girip uyarı üretirdi. Tüm seçimlerin
    tek bir SCORE grubunda toplanması da aynı sonuç uzayını paylaştıklarını
    doğrular.
    """
    assert result["warnings"] == []
    assert all(
        group["group"] == "SCORE" for match in result["matches"] for group in match["groups"]
    )


def test_german_outcome_code_is_understood(result):
    """'J' (Ja) karşılıklı gol 'var' demektir."""
    austria = next(m for m in result["matches"] if m["matchId"] == 68932720)
    outcomes = {w["outcome"] for w in austria["groups"][0]["winningSelections"]}
    assert outcomes == {"J", "1"}
    assert austria["weight"] == "3.18"  # 1.85 + 1.33, ev galibiyeti + karşılıklı gol


def test_same_market_selections_are_exclusive(result):
    """Aynı maçta 3 Way '2' ve 'x': yalnızca yüksek oranlı olan sayılır."""
    halle = next(m for m in result["matches"] if m["matchId"] == 71960094)
    assert halle["selectionCount"] == 2
    assert halle["weight"] == "6.00"
    assert [w["outcome"] for w in halle["groups"][0]["winningSelections"]] == ["x"]


def test_contradictory_selections_across_markets_are_not_summed(result):
    """Çift Şans '1X' ile 3 Way '2' birlikte tutamaz."""
    norway = next(m for m in result["matches"] if m["matchId"] == 68931762)
    assert norway["selectionCount"] == 2
    assert norway["weight"] == "3.00"
    assert [w["outcome"] for w in norway["groups"][0]["winningSelections"]] == ["2"]


def test_coupon_totals(result):
    assert result["stake"]["lineCount"] == 8  # 2 x 1 x 1 x 1 x 2 x 2
    assert result["stake"]["perLine"] == "12.500000"
    assert result["maxGain"] == "8983.99"
    assert result["maxSingleLineGain"] == "5226.53"
    assert result["effectiveMultiplier"] == "89.8400"


def test_gain_scales_linearly_with_the_coupon_amount():
    """Çarpan tutardan bağımsızdır; 10 TL için de aynı katsayı geçerli."""
    response = client.post("/api/v1/coupons/max-gain", json=to_request(PROVIDER_COUPON, "10.00"))
    body = response.json()
    assert body["effectiveMultiplier"] == "89.8400"
    assert Decimal(body["maxGain"]) == Decimal("898.39")  # 8983.99 / 10, aşağı yuvarlanmış


# --------------------------------------------------------------------------- #
# İkinci gerçek kupon -- outcome adları İngilizce, "Y"/"N" kısaltmalı
# --------------------------------------------------------------------------- #

ENGLISH_COUPON = [
    {
        "MatchId": 72221172,
        "EventName": "FC Fulham-FC Chelsea",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "2",
        "SpecialBetValue": None,
        "OddValue1": 1.8,
    },
    {
        "MatchId": 72221172,
        "EventName": "FC Fulham-FC Chelsea",
        "Banko": False,
        "OddsTypeId": 1467,
        "OutCome": "Y",
        "SpecialBetValue": None,
        "OddValue1": 1.75,
    },
    {
        "MatchId": 72221220,
        "EventName": "Crystal Palace-Manchester City",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "x",
        "SpecialBetValue": None,
        "OddValue1": 4.2,
    },
    {
        "MatchId": 72221220,
        "EventName": "Crystal Palace-Manchester City",
        "Banko": False,
        "OddsTypeId": 1467,
        "OutCome": "N",
        "SpecialBetValue": None,
        "OddValue1": 2.05,
    },
    {
        "MatchId": 72221224,
        "EventName": "FC Liverpool-Nottingham",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "1",
        "SpecialBetValue": None,
        "OddValue1": 1.4,
    },
    {
        "MatchId": 72221224,
        "EventName": "FC Liverpool-Nottingham",
        "Banko": False,
        "OddsTypeId": 1481,
        "OutCome": "12",
        "SpecialBetValue": None,
        "OddValue1": 1.14,
    },
    # Bu satırda SpecialBetValue alanı hiç yok -- opsiyonel okunmalı
    {
        "MatchId": 72221214,
        "EventName": "Bournemouth -Everton FC",
        "Banko": False,
        "OddsTypeId": 1839,
        "OutCome": "2",
        "OddValue1": 3.55,
    },
]


@pytest.fixture(scope="module")
def english_result() -> dict:
    payload = {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": 0,
                "oddTypeId": row["OddsTypeId"],
                "outcome": row["OutCome"],
                "specialBetValue": row.get("SpecialBetValue"),
                "odds": str(row["OddValue1"]),
            }
            for row in ENGLISH_COUPON
        ],
    }
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_short_yes_no_codes_are_understood(english_result):
    """Aynı piyasa bir kuponda 'J', başka kuponda 'Y' gönderebiliyor."""
    assert english_result["warnings"] == []

    fulham = next(m for m in english_result["matches"] if m["matchId"] == 72221172)
    assert fulham["weight"] == "3.55"  # deplasman galibiyeti + karşılıklı gol
    assert fulham["groups"][0]["combined"] is True

    palace = next(m for m in english_result["matches"] if m["matchId"] == 72221220)
    assert palace["weight"] == "6.25"  # beraberlik + karşılıklı gol yok -> 0-0
    assert palace["groups"][0]["scoreline"]["fullTime"] == "0-0"


def test_home_win_and_double_chance_12_are_compatible(english_result):
    """'12' beraberlik dışını kapsar; ev galibiyetiyle birlikte tutar."""
    liverpool = next(m for m in english_result["matches"] if m["matchId"] == 72221224)
    assert liverpool["weight"] == "2.54"  # 1.40 + 1.14
    assert liverpool["groups"][0]["combined"] is True


def test_missing_special_bet_value_field_is_tolerated(english_result):
    bournemouth = next(m for m in english_result["matches"] if m["matchId"] == 72221214)
    assert bournemouth["weight"] == "3.55"


def test_english_coupon_totals(english_result):
    assert english_result["stake"]["lineCount"] == 8  # 2 x 2 x 2 x 1
    assert english_result["maxGain"] == "2500.80"
    assert english_result["effectiveMultiplier"] == "25.0081"


def test_integer_odds_are_reported_with_two_decimals(result):
    """Sağlayıcı tam sayı oran gönderebiliyor (6); çıktı '6' değil '6.00' olmalı."""
    halle = next(m for m in result["matches"] if m["matchId"] == 71960094)
    assert halle["groups"][0]["winningSelections"][0]["odds"] == "6.00"
    assert halle["weight"] == "6.00"
