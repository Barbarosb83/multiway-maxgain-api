"""Hesaplama motoru testleri."""

from __future__ import annotations

import itertools
import math
import random
from decimal import ROUND_DOWN, Decimal

import pytest

from app.services.max_gain import (
    CouponError,
    CouponInput,
    SelectionInput,
    calculate_max_gain,
    elementary_symmetric,
)
from tests.reference import brute_force_max_gain

# Gerçek katalogdan (pre-match uzayı) -- bkz. data/odd_types_pre.csv
MS = 1565  # 3way            -> Maç Sonucu (1X2)
CS = 1481  # Double Chance   -> Çift Şans
OU = 1500  # Over/Under      -> Alt / Üst (specialBetValue gerektirir)
HC = 1493  # Handicap        -> Handikap  (specialBetValue gerektirir)
UNKNOWN = 9001  # hiçbir katalogda olmayan


def s(
    match_id: str,
    odd_type_id: int,
    outcome: str,
    odds: str,
    special: str | None = None,
    is_live: int = 0,
) -> SelectionInput:
    return SelectionInput(
        match_id=match_id,
        odd_type_id=odd_type_id,
        outcome=outcome,
        odds=Decimal(odds),
        special_bet_value=special,
        is_live=is_live,
    )


def coupon(*selections: SelectionInput, amount: str = "100.00", **kwargs) -> CouponInput:
    return CouponInput(selections=selections, coupon_amount=Decimal(amount), **kwargs)


# --------------------------------------------------------------------------- #
# Kullanıcının belirttiği maç ağırlığı kuralları
# --------------------------------------------------------------------------- #


def test_same_odd_type_selections_are_exclusive_so_max_wins():
    """Aynı maçta iki kez 1X2 oynanmışsa yalnızca oranı yüksek olan sayılır."""
    result = calculate_max_gain(coupon(s("m1", MS, "1", "2.10"), s("m1", MS, "X", "3.40")))
    assert result.matches[0].weight == Decimal("3.40")
    assert len(result.matches[0].groups[0].winning_selections) == 1


def test_compatible_selections_across_odd_types_are_summed():
    """1X2 '1' ile Çift Şans '1X' ev sahibi kazanırsa ikisi de tutar -> toplanır."""
    result = calculate_max_gain(coupon(s("m1", MS, "1", "2.10"), s("m1", CS, "1X", "1.30")))
    group = result.matches[0].groups[0]
    assert result.matches[0].weight == Decimal("3.40")
    assert group.combined is True
    assert {w.outcome for w in group.winning_selections} == {"1", "1X"}
    assert group.scoreline is not None


def test_contradictory_selections_across_odd_types_are_not_summed():
    """1X2 '1' ile Çift Şans 'X2' asla birlikte tutamaz -> toplanmaz, max alınır."""
    result = calculate_max_gain(coupon(s("m1", MS, "1", "2.10"), s("m1", CS, "X2", "1.45")))
    group = result.matches[0].groups[0]
    assert result.matches[0].weight == Decimal("2.10")
    assert group.combined is False
    assert [w.outcome for w in group.winning_selections] == ["1"]


def test_three_way_overlap_picks_best_compatible_subset():
    """Üç seçim arasından, birlikte tutabilen en yüksek toplamlı alt küme seçilir.

    '1X' hem '1' hem 'X' ile uyumlu; ama '1' ile 'X' birbirini dışlar. Aday
    alt kümeler: {1, 1X} = 4.60 ve {X, 1X} = 3.40. Kazanan 4.60, senaryo ev galibiyeti.
    """
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "3.00"),
            s("m1", MS, "X", "1.80"),
            s("m1", CS, "1X", "1.60"),
        )
    )
    group = result.matches[0].groups[0]
    assert result.matches[0].weight == Decimal("4.60")
    assert {w.outcome for w in group.winning_selections} == {"1", "1X"}
    assert group.scoreline == {"full_time": "1-0"}  # sadece MS piyasaları -> 2 boyutlu uzay


def test_best_subset_can_beat_the_single_highest_odds():
    """En yüksek tekil oran, birlikte tutabilen iki seçimin toplamını geçemeyebilir."""
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "4.00"),  # en yüksek tekil oran, ama hiçbiriyle uyumlu değil
            s("m1", MS, "X", "3.20"),  # X + X2 beraberlikte birlikte tutar -> 4.70
            s("m1", CS, "X2", "1.50"),
        )
    )
    group = result.matches[0].groups[0]
    assert result.matches[0].weight == Decimal("4.70")
    assert {w.outcome for w in group.winning_selections} == {"X", "X2"}
    assert group.scoreline == {"full_time": "0-0"}


def test_unknown_odd_type_falls_back_and_warns():
    """Katalogda olmayan id: aynı id dışlayıcı, farklı id bağımsız."""
    result = calculate_max_gain(
        coupon(
            s("m1", UNKNOWN, "A", "2.00"),
            s("m1", UNKNOWN, "B", "3.00"),  # aynı id -> dışlayıcı, max 3.00
            s("m1", MS, "1", "1.50"),  # farklı grup -> toplanır
        )
    )
    assert result.matches[0].weight == Decimal("4.50")
    assert any("katalogunda yok" in w for w in result.warnings)


def test_unparseable_outcome_is_isolated_and_warns():
    result = calculate_max_gain(coupon(s("m1", MS, "3", "2.00"), s("m1", MS, "1", "1.50")))
    assert result.matches[0].weight == Decimal("3.50")  # yalıtıldı -> toplandı
    assert any("çözümlenemedi" in w or "geçersiz" in w for w in result.warnings)


def test_impossible_outcome_is_isolated_rather_than_zeroing_the_match():
    """Modellenen skor uzayına sığmayan eşik maçı sıfırlamamalı."""
    result = calculate_max_gain(coupon(s("m1", OU, "Üst", "2.00", "999.5")))
    assert result.matches[0].weight == Decimal("2.00")
    assert any("hiçbir senaryoyla eşleşmiyor" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# specialBetValue -- eşik ayrı alandan gelir
# --------------------------------------------------------------------------- #


def test_same_market_contradictory_lines_take_the_maximum():
    """Alt 0.5 ile Üst 2.5 birlikte tutamaz; oranı yüksek olan sayılır."""
    result = calculate_max_gain(
        coupon(s("m1", OU, "Alt", "1.90", "0.5"), s("m1", OU, "Üst", "2.40", "2.5"))
    )
    group = result.matches[0].groups[0]
    assert result.matches[0].weight == Decimal("2.40")
    assert group.combined is False
    assert [w.special_bet_value for w in group.winning_selections] == ["2.5"]


def test_same_market_compatible_lines_are_summed():
    """Üst 0.5 ile Üst 2.5: toplam 3+ olduğunda ikisi de tutar."""
    result = calculate_max_gain(
        coupon(s("m1", OU, "Üst", "1.20", "0.5"), s("m1", OU, "Üst", "2.40", "2.5"))
    )
    assert result.matches[0].weight == Decimal("3.60")
    assert result.matches[0].groups[0].combined is True


def test_result_and_total_contradiction_is_detected():
    """Deplasman kazanırsa en az bir gol vardır; 'Alt 0.5' ile çelişir."""
    result = calculate_max_gain(coupon(s("m1", MS, "2", "3.00"), s("m1", OU, "Alt", "1.90", "0.5")))
    assert result.matches[0].weight == Decimal("3.00")


def test_handicap_uses_special_bet_value():
    """Handikap 0:1 -> ev sahibi 2+ farkla kazanmalı; düz galibiyetle uyumlu."""
    result = calculate_max_gain(coupon(s("m1", HC, "1", "2.50", "0:1"), s("m1", MS, "1", "1.80")))
    assert result.matches[0].weight == Decimal("4.30")


def test_large_bound_market_uses_a_wider_score_space():
    """Basketbol sayı eşikleri futbol aralığına sığmaz; uzay maça göre büyür."""
    result = calculate_max_gain(
        coupon(s("m1", OU, "Üst", "1.85", "220.5"), s("m1", OU, "Alt", "1.95", "200.5"))
    )
    assert result.matches[0].weight == Decimal("1.95")  # çelişkili -> max
    assert not result.warnings


def test_live_and_pre_ids_are_separate_namespaces():
    """Aynı sayısal id, live ve pre kataloglarında farklı piyasalardır."""
    live = calculate_max_gain(
        coupon(s("m1", 24, "1X", "1.40", is_live=1))
    )  # live 24 = Double Chance
    assert live.matches[0].groups[0].winning_selections[0].odd_type_name == "Double Chance (ALL)"
    assert not live.warnings

    pre = calculate_max_gain(coupon(s("m1", 24, "1X", "1.40", is_live=0)))
    assert pre.warnings  # pre uzayında 24 diye bir id yok


# --------------------------------------------------------------------------- #
# Elementer simetrik polinom
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "values",
    [
        [Decimal("2")],
        [Decimal("1.5"), Decimal("2.5")],
        [Decimal("1.2"), Decimal("3.4"), Decimal("2.0"), Decimal("5.5")],
    ],
)
def test_elementary_symmetric_matches_explicit_combinations(values):
    e = elementary_symmetric(values)
    assert e[0] == Decimal(1)
    for k in range(1, len(values) + 1):
        expected = sum(
            (math.prod(subset, start=Decimal(1)) for subset in itertools.combinations(values, k)),
            Decimal(0),
        )
        assert e[k] == expected, f"e[{k}] uyuşmadı"


# --------------------------------------------------------------------------- #
# Kupon toplamı
# --------------------------------------------------------------------------- #


def test_plain_parlay_is_product_of_odds():
    result = calculate_max_gain(
        coupon(s("m1", MS, "1", "2.00"), s("m2", MS, "1", "3.00"), s("m3", MS, "1", "1.50"))
    )
    assert result.line_count == 1
    assert result.max_gain == Decimal("900.00")
    assert result.net_profit == Decimal("800.00")


def test_multiway_splits_stake_into_lines():
    """m1'de iki seçim -> 2 satır; ikisi de tutabildiği için ikisi de öder."""
    result = calculate_max_gain(
        coupon(s("m1", MS, "1", "2.10"), s("m1", CS, "1X", "1.30"), s("m2", MS, "1", "3.00"))
    )
    assert result.line_count == 2
    assert result.stake_per_line == Decimal("50.00")
    assert result.max_gain == Decimal("510.00")  # 50 * 3.00 * (2.10 + 1.30)


def test_system_2_of_3_sums_all_winning_lines():
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "2.00"),
            s("m2", MS, "1", "3.00"),
            s("m3", MS, "1", "4.00"),
            system_sizes=(2,),
        )
    )
    assert result.line_count == 3
    per_line = Decimal("100") / 3
    expected = per_line * Decimal("26")  # e_2 = 6 + 8 + 12
    assert result.max_gain == expected.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    assert result.max_gain == Decimal("866.66")
    assert result.warnings


def test_banker_is_present_in_every_line():
    result = calculate_max_gain(
        coupon(
            s("b1", MS, "1", "1.50"),
            s("m1", MS, "1", "2.00"),
            s("m2", MS, "1", "3.00"),
            banker_match_ids=frozenset({"b1"}),
            system_sizes=(1,),
            amount="10.00",
            stake_mode="per_line",
        )
    )
    assert result.line_count == 2
    assert result.total_stake == Decimal("20.00")
    assert result.max_gain == Decimal("75.00")  # 10*1.5*2 + 10*1.5*3


def test_multi_size_system_combines_sizes():
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "2.00"),
            s("m2", MS, "1", "3.00"),
            s("m3", MS, "1", "4.00"),
            system_sizes=(2, 3),
            amount="1.00",
            stake_mode="per_line",
        )
    )
    assert result.line_count == 4
    assert result.max_gain == Decimal("50.00")  # e_2 = 26, e_3 = 24
    assert [b.system_size for b in result.breakdown] == [2, 3]


def test_max_single_line_uses_individual_odds_not_match_weight():
    """Tek satır her maçtan bir seçim alır; ağırlık (toplam) değil tekil oran geçerli."""
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "2.10"),
            s("m1", CS, "1X", "1.30"),
            s("m2", MS, "1", "3.00"),
            amount="1.00",
            stake_mode="per_line",
        )
    )
    assert result.matches[0].weight == Decimal("3.40")
    assert result.max_single_line_gain == Decimal("6.30")  # 2.10 * 3.00
    assert result.max_gain == Decimal("10.20")  # 3.40 * 3.00


def test_sub_cent_stake_per_line_is_not_rounded_to_zero():
    selections = [s(f"m{i}", MS, "1", "2.00") for i in range(20)]
    result = calculate_max_gain(coupon(*selections, system_sizes=(4,), amount="10.00"))
    assert result.line_count == 4845  # C(20,4)
    assert result.stake_per_line == Decimal("0.002063")
    assert "0.002063" in result.warnings[0]


# --------------------------------------------------------------------------- #
# Bonus ve ödeme tavanı
# --------------------------------------------------------------------------- #


def test_bonus_multiplier_scales_payout():
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "2.00"),
            s("m2", MS, "1", "3.00"),
            bonus_multiplier=Decimal("1.10"),
        )
    )
    assert result.max_gain == Decimal("660.00")


def test_payout_cap_clamps_result_and_warns():
    result = calculate_max_gain(
        coupon(
            s("m1", MS, "1", "10.00"),
            s("m2", MS, "1", "10.00"),
            max_payout_cap=Decimal("5000.00"),
        )
    )
    assert result.capped is True
    assert result.max_gain == Decimal("5000.00")
    assert result.max_single_line_gain == Decimal("5000.00")


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"system_sizes": (4,)}, "Geçersiz sistem boyutu"),
        ({"system_sizes": (0,)}, "Geçersiz sistem boyutu"),
        ({"coupon_amount": Decimal("0")}, "Kupon tutarı"),
        ({"stake_mode": "nope"}, "stake_mode"),
        ({"banker_match_ids": frozenset({"yok"})}, "kuponda yok"),
    ],
)
def test_invalid_coupons_raise(kwargs, fragment):
    base = {
        "selections": (s("m1", MS, "1", "2.00"), s("m2", MS, "1", "3.00")),
        "coupon_amount": Decimal("100"),
    }
    base.update(kwargs)
    with pytest.raises(CouponError, match=fragment):
        calculate_max_gain(CouponInput(**base))


def test_duplicate_selection_rejected():
    with pytest.raises(CouponError, match="iki kez"):
        calculate_max_gain(coupon(s("m1", MS, "1", "2.00"), s("m1", MS, "1", "3.00")))


def test_odds_must_exceed_one():
    with pytest.raises(CouponError, match="1.00'den büyük"):
        calculate_max_gain(coupon(s("m1", MS, "1", "1.00")))


def test_all_banker_coupon_needs_no_system():
    result = calculate_max_gain(
        coupon(
            s("b1", MS, "1", "2.00"),
            s("b2", MS, "1", "3.00"),
            banker_match_ids=frozenset({"b1", "b2"}),
        )
    )
    assert result.line_count == 1
    assert result.max_gain == Decimal("600.00")


def test_system_with_all_bankers_rejected():
    with pytest.raises(CouponError, match="Tüm maçlar banko"):
        calculate_max_gain(
            coupon(s("b1", MS, "1", "2.00"), banker_match_ids=frozenset({"b1"}), system_sizes=(1,))
        )


# --------------------------------------------------------------------------- #
# Kaba kuvvet çapraz doğrulama
# --------------------------------------------------------------------------- #

_OUTCOMES = [
    (MS, "1", None),
    (MS, "X", None),
    (MS, "2", None),
    (CS, "1X", None),
    (CS, "12", None),
    (CS, "X2", None),
    (OU, "Alt", "2.5"),
    (OU, "Üst", "2.5"),
    (OU, "Alt", "0.5"),
    (OU, "Üst", "3.5"),
    (HC, "1", "0:1"),
    (HC, "2", "1:0"),
    (UNKNOWN, "A", None),
]


def _random_coupon(rng: random.Random) -> CouponInput:
    selections: list[SelectionInput] = []
    match_ids = [f"m{i}" for i in range(rng.randint(1, 3))]

    for match_id in match_ids:
        picks = rng.sample(_OUTCOMES, rng.randint(1, 3))
        for odd_type_id, outcome, special in picks:
            selections.append(
                s(match_id, odd_type_id, outcome, str(round(rng.uniform(1.05, 6.0), 2)), special)
            )

    bankers = frozenset(m for m in match_ids if rng.random() < 0.25)
    combinable_count = len(match_ids) - len(bankers)
    if combinable_count and rng.random() < 0.7:
        pool = list(range(1, combinable_count + 1))
        sizes = tuple(sorted(rng.sample(pool, rng.randint(1, len(pool)))))
    else:
        sizes = None

    return CouponInput(
        selections=tuple(selections),
        coupon_amount=Decimal(str(rng.choice([1, 5, 10, 100, 250]))),
        stake_mode=rng.choice(["total", "per_line"]),
        system_sizes=sizes,
        banker_match_ids=bankers,
    )


@pytest.mark.parametrize("seed", range(150))
def test_matches_brute_force_reference(seed):
    """Motor, satırları ve senaryoları tek tek dolaşan referansla birebir aynı olmalı."""
    rng = random.Random(seed)
    c = _random_coupon(rng)

    try:
        result = calculate_max_gain(c)
    except CouponError:
        pytest.skip("rastgele üretilen kupon geçersiz")

    expected_gain, expected_lines = brute_force_max_gain(c)
    assert result.line_count == expected_lines
    assert result.max_gain == expected_gain
