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
                "specialBetValue": row.get("SpecialBetValue"),
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


# --------------------------------------------------------------------------- #
# Üretim yolu: seçim yalnızca oddId ile tanımlanır
# --------------------------------------------------------------------------- #

# Katalogdaki oddId'ler (pre uzayı)
ODD_IDS = {
    (1839, "1"): 1970,
    (1839, "X"): 1971,
    (1839, "2"): 1972,
    (1467, "Yes"): 2294,
    (1467, "No"): 2295,
    (1481, "1X"): 2307,
    (1481, "12"): 2308,
}

# Sağlayıcı gövdesindeki outcome kodunun kanonik karşılığı
CANONICAL = {"x": "X", "J": "Yes", "Y": "Yes", "N": "No"}


def to_odd_id_request(rows: list[dict], coupon_amount: str) -> dict:
    """Aynı kuponu yalnızca ``oddId`` ile tanımlar -- üretimde kullanılacak yol."""
    return {
        "couponAmount": coupon_amount,
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": 0,
                "oddId": ODD_IDS[
                    (row["OddsTypeId"], CANONICAL.get(row["OutCome"], row["OutCome"]))
                ],
                "specialBetValue": row.get("SpecialBetValue"),
                "odds": str(row["OddValue1"]),
            }
            for row in rows
        ],
    }


@pytest.mark.parametrize(
    ("rows", "expected_gain"),
    [(PROVIDER_COUPON, "8983.99"), (ENGLISH_COUPON, "2500.80")],
    ids=["almanca", "ingilizce"],
)
def test_odd_id_path_matches_odd_type_and_outcome_path(rows, expected_gain):
    """İki tanımlama yolu birebir aynı sonucu vermeli.

    Üretimde seçim ``oddId`` ile gelir; ``oddTypeId`` + ``outcome`` yolu ancak
    ``oddId`` yoksa devreye girer. İkisinin aynı sonucu vermesi, çok dilli
    outcome çözümlemesinin katalogla tutarlı olduğunu da doğrular.
    """
    by_odd_id = client.post(
        "/api/v1/coupons/max-gain", json=to_odd_id_request(rows, "100.00")
    ).json()
    by_name = client.post("/api/v1/coupons/max-gain", json=to_request(rows, "100.00")).json()

    assert by_odd_id["warnings"] == []
    assert by_odd_id["maxGain"] == by_name["maxGain"] == expected_gain
    assert by_odd_id["stake"]["lineCount"] == by_name["stake"]["lineCount"]
    assert [m["weight"] for m in by_odd_id["matches"]] == [m["weight"] for m in by_name["matches"]]


def test_odd_id_path_needs_no_outcome_field():
    """oddId verildiğinde gövdede outcome hiç bulunmayabilir."""
    payload = to_odd_id_request(ENGLISH_COUPON, "100.00")
    assert all("outcome" not in selection for selection in payload["selections"])

    body = client.post("/api/v1/coupons/max-gain", json=payload).json()
    assert body["maxGain"] == "2500.80"
    # Katalogdan gelen kanonik adlar döner ("Y" değil "Yes")
    outcomes = {
        w["outcome"] for m in body["matches"] for g in m["groups"] for w in g["winningSelections"]
    }
    assert outcomes <= {"1", "X", "2", "1X", "12", "Yes", "No"}


# --------------------------------------------------------------------------- #
# Canlı kupon -- BetType = 1, negatif matchId, anlık skor taşıyan
# specialBetValue ve boş string
# --------------------------------------------------------------------------- #

LIVE_COUPON = [
    {
        "MatchId": -13996108,
        "EventName": "Jong PSV - TOP Oss",
        "Banko": False,
        "BetType": 1,
        "OddsTypeId": 3,
        "OutCome": "1",
        "SpecialBetValue": "0:0",
        "OddValue1": 1.9,
    },
    {
        "MatchId": -13996108,
        "EventName": "Jong PSV - TOP Oss",
        "Banko": False,
        "BetType": 1,
        "OddsTypeId": 11,
        "OutCome": "2",
        "SpecialBetValue": "0:0",
        "OddValue1": 2.4,
    },
    {
        "MatchId": -13996109,
        "EventName": "Jong Utrecht-Heracles Almelo",
        "Banko": False,
        "BetType": 1,
        "OddsTypeId": 708,
        "OutCome": "2",
        "SpecialBetValue": "",
        "OddValue1": 1.02,
    },
    {
        "MatchId": -13996109,
        "EventName": "Jong Utrecht-Heracles Almelo",
        "Banko": False,
        "BetType": 1,
        "OddsTypeId": 710,
        "OutCome": "Under",
        "SpecialBetValue": "3.5",
        "OddValue1": 3.25,
    },
]


@pytest.fixture(scope="module")
def live_result() -> dict:
    payload = {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": row["BetType"],  # BetType canlı bayrağını taşır
                "oddTypeId": row["OddsTypeId"],
                "outcome": row["OutCome"],
                "specialBetValue": row.get("SpecialBetValue"),
                "odds": str(row["OddValue1"]),
            }
            for row in LIVE_COUPON
        ],
    }
    response = client.post("/api/v1/coupons/max-gain", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_negative_match_ids_are_accepted(live_result):
    assert {m["matchId"] for m in live_result["matches"]} == {-13996108, -13996109}


def test_blank_special_bet_value_is_treated_as_absent(live_result):
    """Sağlayıcı alanı "" olarak gönderebiliyor; eşik gerektirmeyen piyasada sorun olmamalı."""
    utrecht = next(m for m in live_result["matches"] if m["matchId"] == -13996109)
    winner = next(
        w for w in utrecht["groups"][0]["winningSelections"] if w["oddTypeName"] == "Winner"
    )
    assert winner["specialBetValue"] is None


def test_live_winner_and_total_share_one_group(live_result):
    """Deplasman galibiyeti ile 3.5 alt birlikte tutabilir (0-1)."""
    utrecht = next(m for m in live_result["matches"] if m["matchId"] == -13996109)
    assert utrecht["weight"] == "4.27"  # 1.02 + 3.25
    assert utrecht["groups"][0]["combined"] is True


def test_next_goal_is_projected_onto_the_score(live_result):
    """'Sıradaki golü deplasman atar' ile 'kalanı ev kazanır' birlikte tutabilir (2-1)."""
    psv = next(m for m in live_result["matches"] if m["matchId"] == -13996108)
    assert psv["weight"] == "4.30"  # 1.90 + 2.40
    assert [g["group"] for g in psv["groups"]] == ["SCORE"]  # tek grup, yalıtılmış değil
    assert psv["groups"][0]["scoreline"]["fullTime"] == "2-1"


def test_live_selection_without_a_score_is_flagged(live_result):
    """Canlı maçta anlık skor gelmezse maç sonu skoru kısıtlanamaz."""
    assert any("anlık skor gönderilmemiş" in w for w in live_result["warnings"])


def test_live_coupon_totals(live_result):
    assert live_result["stake"]["lineCount"] == 4
    assert live_result["maxGain"] == "459.02"
    assert live_result["maxSingleLineGain"] == "195.00"


def rest_of_match(outcome: str, odds: str, current: str) -> dict:
    return {
        "matchId": 1,
        "isLive": 1,
        "oddTypeId": 3,  # live "Rest of match"
        "outcome": outcome,
        "specialBetValue": current,
        "odds": odds,
    }


def winner(outcome: str, odds: str) -> dict:
    return {"matchId": 1, "isLive": 1, "oddTypeId": 708, "outcome": outcome, "odds": odds}


@pytest.mark.parametrize(
    ("rest", "final", "expected"),
    [
        # Anlık skor 1:0 (ev önde); kalan @2.60, maç sonucu @1.50
        ("1", "2", "2.60"),  # ev kalanı da alırsa deplasman kazanamaz -> max
        ("2", "1", "2.60"),  # deplasman kalanı alırsa ev öne geçemez -> max
        ("2", "x", "4.10"),  # 1-1 mümkün -> toplanır
    ],
    ids=["kalan1-ms2", "kalan2-ms1", "kalan2-msX"],
)
def test_rest_of_match_uses_the_current_score(rest, final, expected):
    """'Maçın kalanı' bahsi anlık skordan sonrasına yatırılır.

    specialBetValue o anki skoru taşır; maç sonu skorundan düşülerek
    değerlendirilir.
    """
    payload = {
        "couponAmount": "100.00",
        "selections": [rest_of_match(rest, "2.60", "1:0"), winner(final, "1.50")],
    }
    body = client.post("/api/v1/coupons/max-gain", json=payload).json()
    assert body["matches"][0]["weight"] == expected


def test_rest_of_match_from_nil_nil_equals_full_time_result():
    """Anlık skor 0:0 iken piyasa maç sonucuyla aynıya indirgenir."""
    compatible = client.post(
        "/api/v1/coupons/max-gain",
        json={
            "couponAmount": "100.00",
            "selections": [rest_of_match("1", "1.90", "0:0"), winner("1", "1.50")],
        },
    ).json()
    assert compatible["matches"][0]["weight"] == "3.40"

    contradictory = client.post(
        "/api/v1/coupons/max-gain",
        json={
            "couponAmount": "100.00",
            "selections": [rest_of_match("1", "1.90", "0:0"), winner("2", "3.00")],
        },
    ).json()
    assert contradictory["matches"][0]["weight"] == "3.00"


# --------------------------------------------------------------------------- #
# Anlık skor kısıtı
# --------------------------------------------------------------------------- #

SCORED_LIVE_COUPON = [
    {
        "MatchId": -13996108,
        "BetType": 1,
        "OddsTypeId": 3,
        "OutCome": "1",
        "SpecialBetValue": "0:0",
        "OddValue1": 2.05,
        "Score": "0:0",
    },
    {
        "MatchId": -13996108,
        "BetType": 1,
        "OddsTypeId": 11,
        "OutCome": "2",
        "SpecialBetValue": "0:0",
        "OddValue1": 2.45,
        "Score": "0:0",
    },
    {
        "MatchId": -13996109,
        "BetType": 1,
        "OddsTypeId": 710,
        "OutCome": "Under",
        "SpecialBetValue": "3.5",
        "OddValue1": 2.7,
        "Score": "0:2",
    },
    {
        "MatchId": -13996109,
        "BetType": 1,
        "OddsTypeId": 708,
        "OutCome": "1",
        "SpecialBetValue": "",
        "OddValue1": 30,
        "Score": "0:2",
    },
]


def scored_request(*, with_score: bool) -> dict:
    return {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": row["BetType"],
                "oddTypeId": row["OddsTypeId"],
                "outcome": row["OutCome"],
                "specialBetValue": row["SpecialBetValue"],
                "odds": str(row["OddValue1"]),
                **({"currentScore": row["Score"]} if with_score else {}),
            }
            for row in SCORED_LIVE_COUPON
        ],
    }


def test_current_score_rules_out_an_unreachable_combination():
    """0:2'den ev galibiyeti 3+ gol gerektirir; toplam 5'i geçer, '3.5 Alt' ile çelişir.

    Skor gönderilmediğinde bu çelişki görülemez ve iki seçim toplanır.
    """
    without = client.post("/api/v1/coupons/max-gain", json=scored_request(with_score=False)).json()
    with_score = client.post(
        "/api/v1/coupons/max-gain", json=scored_request(with_score=True)
    ).json()

    utrecht_before = next(m for m in without["matches"] if m["matchId"] == -13996109)
    utrecht_after = next(m for m in with_score["matches"] if m["matchId"] == -13996109)

    assert utrecht_before["weight"] == "32.70"  # 2.70 + 30.00, çelişki görülmedi
    assert utrecht_after["weight"] == "30.00"  # yalnızca ev galibiyeti
    assert utrecht_after["groups"][0]["scoreline"]["fullTime"] == "3-2"

    assert without["maxGain"] == "3678.75"
    assert with_score["maxGain"] == "3375.00"


def test_missing_score_on_a_live_selection_is_flagged():
    body = client.post("/api/v1/coupons/max-gain", json=scored_request(with_score=False)).json()
    assert sum("anlık skor gönderilmemiş" in w for w in body["warnings"]) == 2


def test_selection_already_lost_is_dropped_with_a_warning():
    """Maç 2-0 iken '1.5 Alt' artık kazanamaz; hesaba katılmamalı."""
    payload = {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": 1,
                "isLive": 1,
                "oddTypeId": 710,
                "outcome": "Under",
                "specialBetValue": "1.5",
                "odds": "3.00",
                "currentScore": "2:0",
            },
            {
                "matchId": 1,
                "isLive": 1,
                "oddTypeId": 708,
                "outcome": "1",
                "odds": "1.50",
                "currentScore": "2:0",
            },
        ],
    }
    body = client.post("/api/v1/coupons/max-gain", json=payload).json()
    assert body["matches"][0]["weight"] == "1.50"
    assert any("artık kazanamaz" in w for w in body["warnings"])


def test_rest_of_match_falls_back_to_the_match_score():
    """'Maçın kalanı' specialBetValue'suz gelirse maçın anlık skoru kullanılır."""
    payload = {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": 1,
                "isLive": 1,
                "oddTypeId": 3,
                "outcome": "1",
                "odds": "2.60",
                "currentScore": "1:0",
            },
            {
                "matchId": 1,
                "isLive": 1,
                "oddTypeId": 708,
                "outcome": "2",
                "odds": "1.50",
                "currentScore": "1:0",
            },
        ],
    }
    body = client.post("/api/v1/coupons/max-gain", json=payload).json()
    # 1:0'dan kalanı ev alırsa deplasman maçı kazanamaz -> çelişki
    assert body["matches"][0]["weight"] == "2.60"


# --------------------------------------------------------------------------- #
# Sıradaki gol -- sıralama iddiasının skor uzayına izdüşümü
# --------------------------------------------------------------------------- #


def live_selection(odd_type_id: int, outcome: str, odds: str, **extra) -> dict:
    return {
        "matchId": 1,
        "isLive": 1,
        "oddTypeId": odd_type_id,
        "outcome": outcome,
        "odds": odds,
        **extra,
    }


def weight_for(*selections: dict) -> str:
    body = client.post(
        "/api/v1/coupons/max-gain",
        json={"couponAmount": "100.00", "selections": list(selections)},
    ).json()
    return body["matches"][0]["weight"]


NEXT_GOAL = 11
CORRECT_SCORE_LIVE = 23
TOTAL_LIVE = 710
WINNER_LIVE = 708


@pytest.mark.parametrize(
    ("next_goal_outcome", "other", "expected", "why"),
    [
        # Anlık skor 0:0
        (
            "2",
            live_selection(CORRECT_SCORE_LIVE, "1:0", "8.00", currentScore="0:0"),
            "8.00",
            "deplasman gol atmamışsa sıradaki golü atmış olamaz",
        ),
        (
            "2",
            live_selection(CORRECT_SCORE_LIVE, "1:1", "9.00", currentScore="0:0"),
            "11.40",
            "1-1'e deplasman önce gol atarak ulaşılabilir",
        ),
        (
            "x",
            live_selection(TOTAL_LIVE, "Over", "1.30", specialBetValue="0.5", currentScore="0:0"),
            "2.40",
            "daha gol atılmazsa toplam 0.5 üstü olamaz",
        ),
        (
            "x",
            live_selection(TOTAL_LIVE, "Under", "3.00", specialBetValue="0.5", currentScore="0:0"),
            "5.40",
            "daha gol atılmaması 0.5 altını gerektirir",
        ),
    ],
    ids=["1-0-celiski", "1-1-uyumlu", "gol-yok-ust", "gol-yok-alt"],
)
def test_next_goal_contradictions_are_detected(next_goal_outcome, other, expected, why):
    next_goal = live_selection(
        NEXT_GOAL, next_goal_outcome, "2.40", specialBetValue="0:0", currentScore="0:0"
    )
    assert weight_for(next_goal, other) == expected, why


def test_next_goal_uses_the_current_score_not_just_zero():
    """1:0 iken 'sıradaki gol ev' + 'deplasman kazanır': 2-3 mümkün, uyumlu."""
    next_goal = live_selection(NEXT_GOAL, "1", "2.20", specialBetValue="1:0", currentScore="1:0")
    winner = live_selection(WINNER_LIVE, "2", "5.00", currentScore="1:0")
    assert weight_for(next_goal, winner) == "7.20"

    # "Daha gol yok" ise maç 1-0 biter; deplasman kazanamaz.
    no_more = live_selection(NEXT_GOAL, "x", "3.00", specialBetValue="1:0", currentScore="1:0")
    assert weight_for(no_more, winner) == "5.00"


# --------------------------------------------------------------------------- #
# isLive bayrağı ile oddTypeId'nin uyuşmadığı kupon
# --------------------------------------------------------------------------- #

MISMATCHED_COUPON = [
    {
        "MatchId": -13978035,
        "BetType": 1,
        "OddsType": "1X2",
        "OddsTypeId": 708,
        "OutCome": "1",
        "SpecialBetValue": None,
        "OddValue1": 1.6667,
    },
    {
        "MatchId": -13978035,
        "BetType": 1,
        "OddsType": "NG",
        "OddsTypeId": 11,
        "OutCome": "2",
        "SpecialBetValue": "0:0",
        "OddValue1": 2.75,
    },
    # Bu iki satır BetType=1 olmasına rağmen pre id'si (1839) taşıyor ve
    # birbirinden farklı piyasalar olduğu hâlde aynı id'yi kullanıyor.
    {
        "MatchId": 72478500,
        "BetType": 1,
        "OddsType": "1X2",
        "OddsTypeId": 1839,
        "OutCome": "x",
        "SpecialBetValue": None,
        "OddValue1": 2,
    },
    {
        "MatchId": 72478500,
        "BetType": 1,
        "OddsType": "NG",
        "OddsTypeId": 1839,
        "OutCome": "1",
        "SpecialBetValue": "0:0",
        "OddValue1": 2.25,
    },
]

LIVE_IDS = {"1X2": 708, "NG": 11}


def mismatched_request(*, corrected: bool) -> dict:
    return {
        "couponAmount": "100.00",
        "selections": [
            {
                "matchId": row["MatchId"],
                "isLive": row["BetType"],
                "oddTypeId": (
                    LIVE_IDS[row["OddsType"]]
                    if corrected and row["MatchId"] == 72478500
                    else row["OddsTypeId"]
                ),
                "outcome": row["OutCome"],
                "specialBetValue": row["SpecialBetValue"],
                "odds": str(row["OddValue1"]),
                "currentScore": "0:0",
            }
            for row in MISMATCHED_COUPON
        ],
    }


def test_id_from_the_other_namespace_is_pinpointed():
    """Canlı seçimde pre id'si gelirse uyarı bunu açıkça söylemeli."""
    body = client.post("/api/v1/coupons/max-gain", json=mismatched_request(corrected=False)).json()
    warning = next(w for w in body["warnings"] if "1839" in w)
    assert "pre katalogunda '3 Way' olarak var" in warning
    assert "isLive bayrağı seçime uymuyor olabilir" in warning


def test_mismatched_ids_collapse_two_markets_into_one():
    """Aynı id taşıyan iki farklı piyasa dışlayıcı sayılır; sonuç düşük çıkar."""
    as_sent = client.post(
        "/api/v1/coupons/max-gain", json=mismatched_request(corrected=False)
    ).json()
    corrected = client.post(
        "/api/v1/coupons/max-gain", json=mismatched_request(corrected=True)
    ).json()

    osasuna_sent = next(m for m in as_sent["matches"] if m["matchId"] == 72478500)
    osasuna_fixed = next(m for m in corrected["matches"] if m["matchId"] == 72478500)

    assert osasuna_sent["weight"] == "2.25"  # tek grup, dışlayıcı -> max
    assert osasuna_fixed["weight"] == "4.25"  # beraberlik + sıradaki gol ev -> 1-1
    assert osasuna_fixed["groups"][0]["scoreline"]["fullTime"] == "1-1"

    assert as_sent["maxGain"] == "248.43"
    assert corrected["maxGain"] == "469.27"
    assert corrected["warnings"] == []


def test_four_decimal_odds_are_preserved():
    """1.6667 gibi dört ondalıklı oranlar yuvarlanmamalı."""
    body = client.post("/api/v1/coupons/max-gain", json=mismatched_request(corrected=True)).json()
    rome = next(m for m in body["matches"] if m["matchId"] == -13978035)
    assert rome["weight"] == "4.4167"  # 1.6667 + 2.75
    odds = {w["odds"] for w in rome["groups"][0]["winningSelections"]}
    assert "1.6667" in odds


def test_positive_match_id_can_still_be_live():
    """matchId'nin işareti canlılığı belirlemez; BetType belirler."""
    body = client.post("/api/v1/coupons/max-gain", json=mismatched_request(corrected=True)).json()
    assert {m["matchId"] for m in body["matches"]} == {-13978035, 72478500}
